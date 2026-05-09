import pytest
from unittest.mock import MagicMock, patch
import os

from src.sync_engine import SyncEngine


@pytest.fixture
def mock_path_filter():
    pf = MagicMock()
    pf.should_ignore.return_value = False
    return pf


@pytest.fixture
def engine(mock_path_filter):
    config_manager = MagicMock()
    config_manager.get_local_root.return_value = "/mock/root"
    config_manager.get_selective_sync_folders.return_value = []

    state_manager = MagicMock()
    drive_ops = MagicMock()

    engine = SyncEngine(config_manager, state_manager, drive_ops, mock_path_filter)
    # Prevent LocalMonitor from actually starting/binding in tests
    engine.monitor = MagicMock()
    return engine


@patch("os.walk")
@patch("src.sync_engine._calculate_local_md5")
def test_case_a_new_local_file(mock_md5, mock_walk, engine, mock_path_filter):
    # Simulate finding one new file
    mock_walk.return_value = [("/mock/root", [], ["new_file.txt"])]
    # File is NOT in state
    engine.state_manager.get_file.return_value = None
    mock_path_filter.should_ignore.return_value = False  # Ensure not ignored by filter
    engine.drive_ops.upload_file.return_value = {"id": "new_id_123"}
    mock_md5.return_value = "md5_new"

    engine.scan_local_changes()

    # Verify it was uploaded and state was updated
    engine.drive_ops.upload_file.assert_called_once_with(
        "/mock/root/new_file.txt", "new_file.txt", "root"
    )
    engine.state_manager.set_file.assert_called_once_with(
        "new_file.txt", "new_id_123", "md5_new"
    )


@patch("os.walk")
@patch("src.sync_engine._calculate_local_md5")
def test_case_c1_updated_local_unchanged_remote(
    mock_md5, mock_walk, engine, mock_path_filter
):
    # Simulate finding an existing file
    mock_walk.return_value = [("/mock/root", [], ["updated_file.txt"])]
    engine.state_manager.get_file.return_value = {
        "id": "id_updated",
        "md5": "md5_state",
    }

    mock_md5.return_value = "md5_local_new"  # Local MD5 is different
    engine._get_remote_md5 = MagicMock(return_value="md5_state")

    engine.scan_local_changes()

    # Verify it was updated remotely and state was synced
    engine.drive_ops.update_file.assert_called_once_with(
        "id_updated", "/mock/root/updated_file.txt"
    )
    engine.state_manager.set_file.assert_called_once_with(
        "updated_file.txt", "id_updated", "md5_local_new"
    )


@patch("os.walk")
@patch("src.sync_engine._calculate_local_md5")
def test_case_c2_conflict_changed_both(mock_md5, mock_walk, engine, mock_path_filter):
    # Simulate finding a file
    mock_walk.return_value = [("/mock/root", [], ["conflict_file.txt"])]
    engine.state_manager.get_file.return_value = {
        "id": "id_conflict",
        "md5": "md5_state",
    }

    # Local changed
    mock_md5.return_value = "md5_local_new"
    # Remote changed too
    engine._get_remote_md5 = MagicMock(return_value="md5_remote_new")

    engine._resolve_conflict = MagicMock()
    engine.drive_ops.download_file.return_value = True

    engine.scan_local_changes()

    # Verify conflict resolution triggered and file downloaded
    engine._resolve_conflict.assert_called_once_with("/mock/root/conflict_file.txt")
    engine.drive_ops.download_file.assert_called_once_with(
        "id_conflict", "/mock/root/conflict_file.txt"
    )
    engine.state_manager.set_file.assert_called_once_with(
        "conflict_file.txt", "id_conflict", "md5_remote_new"
    )
