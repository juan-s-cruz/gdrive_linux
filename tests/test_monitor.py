import os
import pytest
from unittest.mock import MagicMock, patch
from watchdog.events import (
    FileCreatedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileDeletedEvent,
    DirCreatedEvent,
)

from src.monitor import LocalFileHandler, LocalMonitor


@pytest.fixture
def mock_config():
    cm = MagicMock()
    cm.get_local_root.return_value = "/mock/root"
    return cm


@pytest.fixture
def mock_state():
    sm = MagicMock()
    return sm


@pytest.fixture
def mock_drive():
    ops = MagicMock()
    return ops


def test_should_ignore(mock_config, mock_state, mock_drive):
    handler = LocalFileHandler(mock_config, mock_state, mock_drive)
    assert handler._should_ignore("/mock/root/test.part") is True

    handler.ignored_paths.add("/mock/root/test.txt")
    assert handler._should_ignore("/mock/root/test.txt") is True

    with patch("os.path.islink", return_value=True):
        assert handler._should_ignore("/mock/root/symlink") is True


def test_get_relative_path(mock_config, mock_state, mock_drive):
    handler = LocalFileHandler(mock_config, mock_state, mock_drive)
    rel = handler._get_relative_path("/mock/root/folder/file.txt")
    assert rel == "folder/file.txt"


def test_resolve_parent_id(mock_config, mock_state, mock_drive):
    handler = LocalFileHandler(mock_config, mock_state, mock_drive)
    assert handler._resolve_parent_id("file.txt") is None

    mock_state.get_file.return_value = {"id": "parent_id"}
    assert handler._resolve_parent_id("folder/file.txt") == "parent_id"


def test_ignore_path(mock_config, mock_state, mock_drive):
    handler = LocalFileHandler(mock_config, mock_state, mock_drive)
    with patch("src.monitor.Timer") as mock_timer:
        handler.ignore_path("/mock/root/ignore_me.txt")
        assert "/mock/root/ignore_me.txt" in handler.ignored_paths
        handler._unignore_path("/mock/root/ignore_me.txt")
        assert "/mock/root/ignore_me.txt" not in handler.ignored_paths


def test_on_created_folder(mock_config, mock_state, mock_drive):
    handler = LocalFileHandler(mock_config, mock_state, mock_drive)
    event = DirCreatedEvent("/mock/root/new_folder")
    mock_drive.create_folder.return_value = "folder_id"
    handler.on_created(event)
    mock_drive.create_folder.assert_called_once_with("new_folder", None)
    mock_state.set_file.assert_called_once_with("new_folder", "folder_id", "folder")


def test_on_created_file(mock_config, mock_state, mock_drive):
    handler = LocalFileHandler(mock_config, mock_state, mock_drive)
    event = FileCreatedEvent("/mock/root/new_file.txt")
    mock_drive.upload_file.return_value = {"id": "file_id", "md5Checksum": "md5"}
    handler.on_created(event)
    mock_drive.upload_file.assert_called_once()


def test_on_modified_debounce(mock_config, mock_state, mock_drive):
    handler = LocalFileHandler(mock_config, mock_state, mock_drive)
    event = FileModifiedEvent("/mock/root/mod_file.txt")

    with patch("src.monitor.Timer") as mock_timer:
        handler.on_modified(event)
        # Should cancel previous if exists
        mock_timer_instance = MagicMock()
        handler.timers["/mock/root/mod_file.txt"] = mock_timer_instance
        handler.on_modified(event)
        mock_timer_instance.cancel.assert_called_once()


def test_process_modified_tracked(mock_config, mock_state, mock_drive):
    handler = LocalFileHandler(mock_config, mock_state, mock_drive)
    event = MagicMock()
    event.src_path = "/mock/root/mod_file.txt"
    handler.timers[event.src_path] = MagicMock()

    with patch("os.path.exists", return_value=True):
        mock_state.get_file.return_value = {"id": "file_id"}
        mock_drive.update_file.return_value = {
            "id": "file_id",
            "md5Checksum": "new_md5",
        }
        handler._process_modified(event)
        mock_drive.update_file.assert_called_once()


def test_process_modified_untracked(mock_config, mock_state, mock_drive):
    handler = LocalFileHandler(mock_config, mock_state, mock_drive)
    event = MagicMock()
    event.src_path = "/mock/root/untracked.txt"

    with patch("os.path.exists", return_value=True):
        mock_state.get_file.return_value = None
        mock_drive.upload_file.return_value = {"id": "file_id", "md5Checksum": "md5"}
        handler._process_modified(event)
        mock_drive.upload_file.assert_called_once()


def test_on_moved_tracked(mock_config, mock_state, mock_drive):
    handler = LocalFileHandler(mock_config, mock_state, mock_drive)
    event = FileMovedEvent("/mock/root/old.txt", "/mock/root/new.txt")
    mock_state.get_file.return_value = {"id": "file_id", "md5": "md5"}
    handler.on_moved(event)
    mock_drive.move_file.assert_called_once()


def test_on_moved_untracked(mock_config, mock_state, mock_drive):
    handler = LocalFileHandler(mock_config, mock_state, mock_drive)
    event = FileMovedEvent("/mock/root/old.txt", "/mock/root/new.txt")
    mock_state.get_file.return_value = None
    mock_drive.upload_file.return_value = {"id": "file_id", "md5Checksum": "md5"}
    handler.on_moved(event)
    mock_drive.upload_file.assert_called_once()


def test_on_deleted(mock_config, mock_state, mock_drive):
    handler = LocalFileHandler(mock_config, mock_state, mock_drive)
    event = FileDeletedEvent("/mock/root/del.txt")

    # Add timer to verify it is cancelled on delete
    mock_timer = MagicMock()
    handler.timers["/mock/root/del.txt"] = mock_timer

    mock_state.get_file.return_value = {"id": "file_id"}
    handler.on_deleted(event)

    mock_drive.delete_file.assert_called_once_with("file_id")
    mock_state.remove_file.assert_called_once_with("del.txt")
    mock_timer.cancel.assert_called_once()
    assert "/mock/root/del.txt" not in handler.timers


def test_local_monitor_start_stop(mock_config, mock_state, mock_drive):
    monitor = LocalMonitor(mock_config, mock_state, mock_drive)
    with patch.object(monitor.observer, "schedule") as mock_schedule, patch.object(
        monitor.observer, "start"
    ) as mock_start, patch.object(monitor.observer, "stop") as mock_stop, patch.object(
        monitor.observer, "join"
    ) as mock_join:

        monitor.start()
        mock_schedule.assert_called_once()
        mock_start.assert_called_once()

        # Test handler stop via monitor stop
        mock_timer = MagicMock()
        monitor.handler.timers["dummy"] = mock_timer
        monitor.stop()

        mock_stop.assert_called_once()
        mock_join.assert_called_once()
        mock_timer.cancel.assert_called_once()
