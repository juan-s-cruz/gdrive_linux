import json
import os
import unittest
from unittest.mock import mock_open, patch

from src.state_manager import StateManager


class TestStateManager(unittest.TestCase):
    def setUp(self):
        # Mock the open call to avoid actual file I/O
        self.mock_open_patcher = patch("builtins.open", new_callable=mock_open)
        self.mock_file = self.mock_open_patcher.start()

        # Mock os.path.exists to simulate the state file exists, which is crucial
        # for the _load_state method to attempt to read the file.
        self.mock_exists_patcher = patch(
            "src.state_manager.os.path.exists", return_value=True
        )
        self.mock_exists = self.mock_exists_patcher.start()

        self.state_path = "fake_state.json"

    def tearDown(self):
        self.mock_open_patcher.stop()
        self.mock_exists_patcher.stop()

    def _get_state_manager_with_data(self, data):
        # Helper to initialize StateManager with specific data
        self.mock_file.return_value.read.return_value = json.dumps(data)
        sm = StateManager(self.state_path)
        # Reset read data to avoid side effects in subsequent open calls (e.g., for saving)
        self.mock_file.return_value.read.return_value = ""
        return sm

    def test_remove_path_recursive_single_file(self):
        initial_state = {
            "meta": {},
            "files": {
                "file1.txt": {"id": "id1", "md5": "md5_1"},
                "folder/file2.txt": {"id": "id2", "md5": "md5_2"},
            },
        }
        sm = self._get_state_manager_with_data(initial_state)

        sm.remove_path_recursive("file1.txt")

        self.assertNotIn("file1.txt", sm.state["files"])
        self.assertIn("folder/file2.txt", sm.state["files"])
        self.assertNotIn("id1", sm.id_to_path)
        self.assertIn("id2", sm.id_to_path)
        self.mock_file().write.assert_called()

    def test_remove_path_recursive_folder(self):
        initial_state = {
            "meta": {},
            "files": {
                "file1.txt": {"id": "id1", "md5": "md5_1"},
                "folder": {"id": "id_folder", "md5": "folder"},
                "folder/file2.txt": {"id": "id2", "md5": "md5_2"},
                "folder/sub/file3.txt": {"id": "id3", "md5": "md5_3"},
                "other/file4.txt": {"id": "id4", "md5": "md5_4"},
            },
        }
        sm = self._get_state_manager_with_data(initial_state)

        sm.remove_path_recursive("folder")

        self.assertIn("file1.txt", sm.state["files"])
        self.assertNotIn("folder", sm.state["files"])
        self.assertNotIn("folder/file2.txt", sm.state["files"])
        self.assertNotIn("folder/sub/file3.txt", sm.state["files"])
        self.assertIn("other/file4.txt", sm.state["files"])

        self.assertNotIn("id_folder", sm.id_to_path)
        self.assertNotIn("id2", sm.id_to_path)
        self.assertNotIn("id3", sm.id_to_path)
        self.assertIn("id4", sm.id_to_path)
        self.mock_file().write.assert_called()

    def test_move_path_recursive_file(self):
        initial_state = {
            "meta": {},
            "files": {
                "old_name.txt": {"id": "id1", "md5": "md5_1"},
            },
        }
        sm = self._get_state_manager_with_data(initial_state)

        sm.move_path_recursive(
            old_rel_path="old_name.txt",
            new_rel_path="new_name.txt",
            file_id="id1",
            md5="md5_1",
            is_folder=False,
        )

        self.assertNotIn("old_name.txt", sm.state["files"])
        self.assertIn("new_name.txt", sm.state["files"])
        self.assertEqual(
            sm.state["files"]["new_name.txt"], {"id": "id1", "md5": "md5_1"}
        )
        self.assertEqual(sm.id_to_path["id1"], "new_name.txt")
        self.mock_file().write.assert_called()

    def test_move_path_recursive_folder(self):
        initial_state = {
            "meta": {},
            "files": {
                "old_folder": {"id": "id_folder", "md5": "folder"},
                "old_folder/file1.txt": {"id": "id1", "md5": "md5_1"},
                "old_folder/sub/file2.txt": {"id": "id2", "md5": "md5_2"},
                "other.txt": {"id": "id3", "md5": "md5_3"},
            },
        }
        sm = self._get_state_manager_with_data(initial_state)

        sm.move_path_recursive(
            old_rel_path="old_folder",
            new_rel_path="new_folder",
            file_id="id_folder",
            md5="folder",
            is_folder=True,
        )

        # Check old paths are gone
        self.assertNotIn("old_folder", sm.state["files"])
        self.assertNotIn("old_folder/file1.txt", sm.state["files"])
        self.assertNotIn("old_folder/sub/file2.txt", sm.state["files"])

        # Check new paths are present
        self.assertIn("new_folder", sm.state["files"])
        self.assertIn("new_folder/file1.txt", sm.state["files"])
        self.assertIn("new_folder/sub/file2.txt", sm.state["files"])
        self.assertIn("other.txt", sm.state["files"])

        # Check id_to_path mapping
        self.assertEqual(sm.id_to_path["id_folder"], "new_folder")
        self.assertEqual(sm.id_to_path["id1"], "new_folder/file1.txt")
        self.assertEqual(sm.id_to_path["id2"], "new_folder/sub/file2.txt")
        self.assertEqual(sm.id_to_path["id3"], "other.txt")

        self.mock_file().write.assert_called()
