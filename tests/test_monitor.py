import unittest
from unittest.mock import MagicMock, Mock

from watchdog.events import DirDeletedEvent, DirMovedEvent

from src.monitor import LocalFileHandler


class TestLocalFileHandlerDirectoryEvents(unittest.TestCase):
    def setUp(self):
        self.mock_config_manager = Mock()
        self.mock_state_manager = Mock()
        self.mock_drive_ops = Mock()
        self.mock_path_filter = Mock()

        self.mock_config_manager.get_local_root.return_value = "/test/root"
        self.mock_path_filter.should_ignore.return_value = False

        self.handler = LocalFileHandler(
            self.mock_config_manager,
            self.mock_state_manager,
            self.mock_drive_ops,
            self.mock_path_filter,
        )
        # Disable debouncing for tests
        self.handler.debounce_seconds = 0
        # Mock away the timer logic for on_deleted
        self.handler.timers_lock = MagicMock()

    def test_on_deleted_directory(self):
        # Arrange
        dir_path = "/test/root/my_folder"
        rel_path = "my_folder"
        event = DirDeletedEvent(dir_path)

        self.mock_state_manager.get_file.return_value = {"id": "folder_id_123"}

        # Act
        self.handler.on_deleted(event)

        # Assert
        self.mock_state_manager.get_file.assert_called_once_with(rel_path)
        self.mock_drive_ops.delete_file.assert_called_once_with("folder_id_123")
        self.mock_state_manager.remove_path_recursive.assert_called_once_with(rel_path)

    def test_on_moved_directory(self):
        # Arrange
        src_path = "/test/root/old_folder"
        dest_path = "/test/root/new_folder"
        src_rel_path = "old_folder"
        dest_rel_path = "new_folder"

        event = DirMovedEvent(src_path, dest_path)

        self.mock_state_manager.get_file.return_value = {
            "id": "folder_id_456",
            "md5": "folder",
        }
        self.handler._resolve_parent_id = MagicMock(return_value="parent_id_789")

        # Act
        self.handler.on_moved(event)

        # Assert
        self.mock_state_manager.get_file.assert_called_once_with(src_rel_path)
        self.handler._resolve_parent_id.assert_called_once_with(dest_rel_path)

        self.mock_drive_ops.move_file.assert_called_once_with(
            "folder_id_456", "new_folder", "parent_id_789"
        )
        self.mock_state_manager.move_path_recursive.assert_called_once_with(
            old_rel_path=src_rel_path,
            new_rel_path=dest_rel_path,
            file_id="folder_id_456",
            md5="folder",
            is_folder=True,
        )
