import unittest
from unittest.mock import MagicMock, patch, call
import os

from src.sync_engine import SyncEngine, _calculate_local_md5


class TestSyncEngine(unittest.TestCase):
    def setUp(self):
        # Mock dependencies
        self.mock_config_manager = MagicMock()
        self.mock_state_manager = MagicMock()
        self.mock_state_manager.get_start_page_token.return_value = None
        self.mock_drive_ops = MagicMock()

        # Mock LocalMonitor to prevent actual thread creation and verify ignore calls
        self.mock_monitor_patcher = patch("src.sync_engine.LocalMonitor")
        self.mock_monitor_class = self.mock_monitor_patcher.start()

        # Default config behavior
        self.mock_config_manager.get_selective_sync_folders.return_value = []
        self.mock_config_manager.get_local_root.return_value = "/tmp/gdrive"

    def test_is_path_allowed_no_rules(self):
        """Test that all paths are allowed when no selective sync rules exist."""
        self.mock_config_manager.get_selective_sync_folders.return_value = []

    def tearDown(self):
        self.mock_monitor_patcher.stop()

        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )

    def test_is_path_allowed_with_rules(self):
        """Test selective sync filtering logic with specific rules."""
        # Setup: Allow 'Photos' and 'Documents/Work'
        self.mock_config_manager.get_selective_sync_folders.return_value = [
            "Photos",
            "Documents/Work",
        ]

        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )

        # 1. Exact matches
        self.assertTrue(engine.is_path_allowed("Photos"))
        self.assertTrue(engine.is_path_allowed("Documents/Work"))

        # 2. Children (Inside allowed folders)
        self.assertTrue(engine.is_path_allowed("Photos/2023/vacation.jpg"))
        self.assertTrue(engine.is_path_allowed("Documents/Work/budget.xlsx"))

        # 3. Parents (Traversal needed to reach allowed folders)
        self.assertTrue(engine.is_path_allowed("Documents"))
        self.assertTrue(engine.is_path_allowed("."))  # Root

        # 4. Denied (Outside allowed folders)
        self.assertFalse(engine.is_path_allowed("Videos"))
        self.assertFalse(engine.is_path_allowed("Documents/Personal"))
        self.assertFalse(engine.is_path_allowed("notes.txt"))

    def test_normalization(self):
        """Test that paths are normalized correctly (handling trailing slashes)."""
        self.mock_config_manager.get_selective_sync_folders.return_value = ["Folder/"]

        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )

        # Should handle missing trailing slash in check
        self.assertTrue(engine.is_path_allowed("Folder"))
        # Should handle child
        self.assertTrue(engine.is_path_allowed("Folder/file.txt"))

    def test_should_download(self):
        """Test logic for determining if a file should be downloaded."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )

        # Case 1: Local file does not exist -> Should download
        with patch("os.path.exists", return_value=False):
            self.assertTrue(
                engine._should_download("file.txt", "/loc/file.txt", "remote_md5")
            )

        # Case 2: Local exists, state matches remote -> Should NOT download
        with patch("os.path.exists", return_value=True):
            self.mock_state_manager.get_file.return_value = {"md5": "remote_md5"}
            self.assertFalse(
                engine._should_download("file.txt", "/loc/file.txt", "remote_md5")
            )

        # Case 3: Local exists, state differs -> Should download
        with patch("os.path.exists", return_value=True):
            self.mock_state_manager.get_file.return_value = {"md5": "old_md5"}
            self.assertTrue(
                engine._should_download("file.txt", "/loc/file.txt", "remote_md5")
            )

    def test_sync_flow(self):
        """Test the recursive sync flow (folders and files)."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )

        # Mock list_files to return:
        # 1. Root: [Folder, File]
        # 2. Folder: [] (Empty)
        self.mock_drive_ops.list_files.side_effect = [
            [
                {
                    "id": "folder_id",
                    "name": "MyFolder",
                    "mimeType": "application/vnd.google-apps.folder",
                    "md5Checksum": None,
                },
                {
                    "id": "file_id",
                    "name": "doc.txt",
                    "mimeType": "text/plain",
                    "md5Checksum": "abc123md5",
                },
            ],
            [],
        ]

        # Mock os operations to simulate clean download (files don't exist locally)
        self.mock_drive_ops.get_start_page_token.return_value = "token_123"

        with patch("os.path.exists", return_value=False), patch(
            "os.makedirs"
        ) as mock_makedirs:

            engine.sync()

            # Verify Folder Processing
            expected_folder_path = os.path.join("/tmp/gdrive", "MyFolder")
            mock_makedirs.assert_called_with(expected_folder_path, exist_ok=True)
            self.mock_state_manager.set_file.assert_any_call(
                "MyFolder", "folder_id", "folder"
            )
            # Verify ignore_path called for folder
            engine.monitor.ignore_path.assert_any_call(expected_folder_path)

            # Verify File Processing
            expected_file_path = os.path.join("/tmp/gdrive", "doc.txt")
            self.mock_drive_ops.download_file.assert_called_with(
                "file_id", expected_file_path
            )
            self.mock_state_manager.set_file.assert_any_call(
                "doc.txt", "file_id", "abc123md5"
            )
            # Verify ignore_path called for file
            engine.monitor.ignore_path.assert_any_call(expected_file_path)

            self.mock_state_manager.set_start_page_token.assert_called_with("token_123")

    def test_sync_with_token(self):
        """Test that sync uses delta sync when a token is available."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        self.mock_state_manager.get_start_page_token.return_value = "token_456"
        self.mock_drive_ops.list_changes.return_value = {
            "changes": [{"fileId": "changed_id"}],
            "newStartPageToken": "token_789",
        }

        engine.sync()

        # Verify delta sync is called
        self.mock_drive_ops.list_changes.assert_called_with("token_456")
        self.mock_state_manager.set_start_page_token.assert_called_with("token_789")
        # Verify recursive sync is bypassed
        self.mock_drive_ops.list_files.assert_not_called()

    def test_sync_changes_addition(self):
        """Test processing of a new file addition during delta sync."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        self.mock_drive_ops.list_changes.return_value = {
            "changes": [
                {
                    "fileId": "file1",
                    "removed": False,
                    "file": {
                        "id": "file1",
                        "name": "new_doc.txt",
                        "mimeType": "text/plain",
                        "md5Checksum": "md5",
                        "parents": ["folder1"],
                    },
                }
            ],
            "newStartPageToken": "token_2",
        }

        # Parent is tracked
        self.mock_state_manager.get_path_by_id.side_effect = lambda fid: (
            "my_folder" if fid == "folder1" else None
        )

        with patch.object(engine, "_sync_file") as mock_sync_file:
            engine._sync_changes("token_1")

            mock_sync_file.assert_called_once_with(
                "my_folder/new_doc.txt", "file1", "md5"
            )
            self.mock_state_manager.set_start_page_token.assert_called_with("token_2")

    def test_sync_changes_deletion(self):
        """Test processing of a file deletion during delta sync."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        self.mock_drive_ops.list_changes.return_value = {
            "changes": [{"fileId": "file1", "removed": True}],
            "newStartPageToken": "token_2",
        }

        # File is tracked
        self.mock_state_manager.get_path_by_id.return_value = "deleted_doc.txt"

        with patch.object(engine, "_delete_local") as mock_delete_local:
            engine._sync_changes("token_1")

            mock_delete_local.assert_called_once_with("deleted_doc.txt")

    def test_sync_changes_trashed(self):
        """Test processing of a file that is trashed (removed=False, trashed=True) during delta sync."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        self.mock_drive_ops.list_changes.return_value = {
            "changes": [
                {
                    "fileId": "file1",
                    "removed": False,
                    "file": {"id": "file1", "trashed": True, "name": "trashed_doc.txt"},
                }
            ],
            "newStartPageToken": "token_2",
        }

        # File is tracked
        self.mock_state_manager.get_path_by_id.return_value = "trashed_doc.txt"

        with patch.object(engine, "_delete_local") as mock_delete_local:
            engine._sync_changes("token_1")

            mock_delete_local.assert_called_once_with("trashed_doc.txt")

    def test_sync_changes_move(self):
        """Test processing of a file move/rename during delta sync."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        self.mock_drive_ops.list_changes.return_value = {
            "changes": [
                {
                    "fileId": "file1",
                    "removed": False,
                    "file": {
                        "id": "file1",
                        "name": "renamed_doc.txt",
                        "mimeType": "text/plain",
                        "md5Checksum": "md5",
                        "parents": [],
                    },
                }
            ],
            "newStartPageToken": "token_2",
        }

        # File is tracked under old name
        def get_path_mock(fid):
            if fid == "file1":
                return "old_doc.txt"
            return None

        self.mock_state_manager.get_path_by_id.side_effect = get_path_mock
        self.mock_state_manager.get_file.return_value = {"id": "file1", "md5": "md5"}

        with patch("os.path.exists", return_value=True), patch(
            "os.makedirs"
        ) as mock_makedirs, patch("os.rename") as mock_rename, patch.object(
            engine, "_sync_file"
        ) as mock_sync_file:

            engine._sync_changes("token_1")

            mock_rename.assert_called_once_with(
                os.path.join("/tmp/gdrive", "old_doc.txt"),
                os.path.join("/tmp/gdrive", "renamed_doc.txt"),
            )
            self.mock_state_manager.remove_file.assert_called_once_with("old_doc.txt")
            self.mock_state_manager.set_file.assert_any_call(
                "renamed_doc.txt", "file1", "md5"
            )
            mock_sync_file.assert_called_once_with("renamed_doc.txt", "file1", "md5")

    def test_sync_changes_folder_move_skips_recursion(self):
        """Test that a moved folder skips recursive sync if successfully renamed."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        self.mock_drive_ops.list_changes.return_value = {
            "changes": [
                {
                    "fileId": "folder1",
                    "removed": False,
                    "file": {
                        "id": "folder1",
                        "name": "renamed_folder",
                        "mimeType": "application/vnd.google-apps.folder",
                        "parents": [],
                    },
                }
            ],
            "newStartPageToken": "token_2",
        }

        # Folder is tracked under old name
        def get_path_mock(fid):
            if fid == "folder1":
                return "old_folder"
            return None

        self.mock_state_manager.get_path_by_id.side_effect = get_path_mock
        self.mock_state_manager.get_file.return_value = {
            "id": "folder1",
            "md5": "folder",
        }
        self.mock_state_manager.get_all_files.return_value = {
            "old_folder/child.txt": {"id": "child1", "md5": "md5_1"},
            "unrelated/file.txt": {"id": "child2", "md5": "md5_2"},
        }

        with patch("os.path.exists", return_value=True), patch("os.makedirs"), patch(
            "os.rename"
        ) as mock_rename, patch.object(engine, "_sync_folder") as mock_sync_folder:

            engine._sync_changes("token_1")

            mock_rename.assert_called_once_with(
                os.path.join("/tmp/gdrive", "old_folder"),
                os.path.join("/tmp/gdrive", "renamed_folder"),
            )
            # The key assertion: _sync_folder must NOT be called because it was moved
            mock_sync_folder.assert_not_called()

    def test_sync_changes_tracked_folder_skips_recursion(self):
        """Test that an already tracked folder that was not moved skips recursive sync."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        self.mock_drive_ops.list_changes.return_value = {
            "changes": [
                {
                    "fileId": "folder3",
                    "removed": False,
                    "file": {
                        "id": "folder3",
                        "name": "existing_folder",
                        "mimeType": "application/vnd.google-apps.folder",
                        "parents": [],
                    },
                }
            ],
            "newStartPageToken": "token_2",
        }

        # Tracked folder
        self.mock_state_manager.get_path_by_id.return_value = "existing_folder"

        with patch.object(engine, "_sync_folder") as mock_sync_folder:
            engine._sync_changes("token_1")
            mock_sync_folder.assert_not_called()

    def test_sync_changes_new_folder_recursions(self):
        """Test that an untracked (new) folder triggers recursive sync."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        self.mock_drive_ops.list_changes.return_value = {
            "changes": [
                {
                    "fileId": "folder2",
                    "removed": False,
                    "file": {
                        "id": "folder2",
                        "name": "new_folder",
                        "mimeType": "application/vnd.google-apps.folder",
                        "parents": [],
                    },
                }
            ],
            "newStartPageToken": "token_2",
        }

        # Not tracked
        self.mock_state_manager.get_path_by_id.return_value = None

        with patch.object(engine, "_sync_folder") as mock_sync_folder:
            engine._sync_changes("token_1")
            mock_sync_folder.assert_called_once_with("new_folder", "folder2")

    def test_sync_skips_disallowed_paths(self):
        """Test that sync respects selective sync rules."""
        self.mock_config_manager.get_selective_sync_folders.return_value = ["Allowed"]

        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )

        # Mock list_files: Root contains one allowed folder and one ignored folder
        self.mock_drive_ops.list_files.side_effect = [
            [
                {
                    "id": "1",
                    "name": "Allowed",
                    "mimeType": "application/vnd.google-apps.folder",
                    "md5Checksum": None,
                },
                {
                    "id": "2",
                    "name": "Ignored",
                    "mimeType": "application/vnd.google-apps.folder",
                    "md5Checksum": None,
                },
            ],
            [],  # Recursion for "Allowed" returns empty
        ]

        with patch("os.path.exists", return_value=False), patch("os.makedirs"):
            engine.sync()

        # Verify "Allowed" was processed
        self.mock_state_manager.set_file.assert_any_call("Allowed", "1", "folder")

        # Verify "Ignored" was NOT processed
        calls = [c.args[0] for c in self.mock_state_manager.set_file.call_args_list]
        self.assertNotIn("Ignored", calls)

    def test_download_failure_does_not_update_state(self):
        """Test that state is not updated if download fails."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )

        self.mock_drive_ops.list_files.return_value = [
            {
                "id": "f1",
                "name": "fail.txt",
                "mimeType": "text/plain",
                "md5Checksum": "md5",
            }
        ]

        # Simulate download failure
        self.mock_drive_ops.download_file.return_value = False

        with patch("os.path.exists", return_value=False):
            engine.sync()

        # Verify download attempted
        self.mock_drive_ops.download_file.assert_called()

        # Verify state NOT updated
        self.mock_state_manager.set_file.assert_not_called()

    def test_start_loop(self):
        """Test that start() runs the loop and handles exceptions."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )

        # Mock sync to do nothing, and sleep to raise exception to break the infinite loop
        engine.sync = MagicMock()
        with patch("time.sleep", side_effect=InterruptedError("Stop Loop")):
            with self.assertRaises(InterruptedError):
                engine.start(interval=1)

        engine.sync.assert_called()

    # --- New Tests for Deletion and Conflict Resolution ---

    def test_handle_deletions_removes_synced_file(self):
        """Test that local files missing remotely are deleted if they are tracked in state."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )

        current_rel_path = "folder"
        remote_names = {"keep.txt"}

        # Mock os.listdir to return local files
        with patch("os.path.exists", return_value=True), patch(
            "os.listdir", return_value=["keep.txt", "delete_me.txt"]
        ), patch.object(engine, "_delete_local") as mock_delete:

            # Mock state manager: delete_me.txt is tracked
            def get_file_side_effect(path):
                if path == "folder/delete_me.txt":
                    return {"id": "1", "md5": "abc"}
                return None

            self.mock_state_manager.get_file.side_effect = get_file_side_effect

            engine._handle_deletions(current_rel_path, remote_names)

            mock_delete.assert_called_once_with("folder/delete_me.txt")
            # Note: _delete_local calls ignore_path, verified in test_delete_local_file

    def test_handle_deletions_ignores_new_local_file(self):
        """Test that new local files (not in state) are NOT deleted."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )

        current_rel_path = ""
        remote_names = set()

        with patch("os.path.exists", return_value=True), patch(
            "os.listdir", return_value=["new_file.txt"]
        ), patch.object(engine, "_delete_local") as mock_delete:

            # Not tracked in state
            self.mock_state_manager.get_file.return_value = None

            engine._handle_deletions(current_rel_path, remote_names)

            mock_delete.assert_not_called()

    def test_resolve_conflict_renames_file(self):
        """Test that conflict resolution renames the local file."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )

        local_path = "/tmp/gdrive/conflict.txt"

        with patch("os.rename") as mock_rename, patch(
            "time.time", return_value=1234567890
        ):

            engine._resolve_conflict(local_path)

            expected_new_path = "/tmp/gdrive/conflict_conflict_1234567890.txt"
            mock_rename.assert_called_once_with(local_path, expected_new_path)

            # Verify ignore calls for both paths
            engine.monitor.ignore_path.assert_any_call(local_path)
            engine.monitor.ignore_path.assert_any_call(expected_new_path)

    def test_sync_file_triggers_conflict_resolution(self):
        """Test that _sync_file detects conflicts and calls resolution."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )

        rel_path = "conflict.txt"
        local_path = "/tmp/gdrive/conflict.txt"

        # Setup: Local file exists, but NOT in state -> Conflict
        with patch("os.path.exists", return_value=True), patch.object(
            engine, "_resolve_conflict"
        ) as mock_resolve, patch.object(engine, "_should_download", return_value=True):

            self.mock_state_manager.get_file.return_value = None  # Not tracked

            engine._sync_file(rel_path, "file_id", "remote_md5")

            mock_resolve.assert_called_once_with(local_path)
            # Should still proceed to download after rename
            self.mock_drive_ops.download_file.assert_called()

    def test_delete_local_file(self):
        """Test _delete_local for a file."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        rel_path = "file.txt"
        local_path = "/tmp/gdrive/file.txt"

        with patch("os.path.exists", return_value=True), patch(
            "os.path.isdir", return_value=False
        ), patch("os.remove") as mock_remove:

            engine._delete_local(rel_path)

            mock_remove.assert_called_once_with(local_path)
            self.mock_state_manager.remove_file.assert_called_once_with(rel_path)
            engine.monitor.ignore_path.assert_called_with(local_path)

    def test_delete_local_folder(self):
        """Test _delete_local for a folder."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        rel_path = "folder"
        local_path = "/tmp/gdrive/folder"

        # Mock tracked files to simulate child state orphaning
        self.mock_state_manager.get_all_files.return_value = {
            "folder": {"id": "1", "type": "folder"},
            "folder/child1.txt": {"id": "2", "md5": "abc"},
            "folder/sub/child2.txt": {"id": "3", "md5": "def"},
            "folder_other": {"id": "4", "type": "folder"},
        }

        with patch("os.path.exists", return_value=True), patch(
            "os.path.isdir", return_value=True
        ), patch("shutil.rmtree") as mock_rmtree:

            engine._delete_local(rel_path)

            mock_rmtree.assert_called_once_with(local_path)
            self.mock_state_manager.remove_file.assert_any_call(rel_path)
            self.mock_state_manager.remove_file.assert_any_call("folder/child1.txt")
            self.mock_state_manager.remove_file.assert_any_call("folder/sub/child2.txt")
            engine.monitor.ignore_path.assert_called_with(local_path)

            # Ensure folder_other wasn't deleted
            calls = [
                c.args[0] for c in self.mock_state_manager.remove_file.call_args_list
            ]
            self.assertNotIn("folder_other", calls)

    @patch("src.sync_engine.os.walk")
    @patch("src.sync_engine._calculate_local_md5")
    @patch.object(SyncEngine, "_write_startup_report")
    def test_scan_local_changes_new_file(self, mock_report, mock_md5, mock_walk):
        """Test Case A: New local file uploaded during startup scan."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        mock_walk.return_value = [("/tmp/gdrive", [], ["new.txt"])]

        self.mock_state_manager.get_file.return_value = None
        self.mock_state_manager.get_all_files.return_value = {}

        self.mock_drive_ops.upload_file.return_value = {"id": "new_id"}
        mock_md5.return_value = "local_md5"

        engine.scan_local_changes()

        self.mock_drive_ops.upload_file.assert_called_once_with(
            os.path.join("/tmp/gdrive", "new.txt"), "new.txt", "root"
        )
        self.mock_state_manager.set_file.assert_called_once_with(
            "new.txt", "new_id", "local_md5"
        )
        mock_report.assert_called_once()
        report_dict = mock_report.call_args[0][0]
        self.assertIn("new.txt", report_dict["uploaded"])

    @patch("src.sync_engine.os.walk")
    @patch("src.sync_engine._calculate_local_md5")
    @patch.object(SyncEngine, "_get_remote_md5")
    @patch.object(SyncEngine, "_write_startup_report")
    def test_scan_local_changes_updated_local(
        self, mock_report, mock_remote_md5, mock_local_md5, mock_walk
    ):
        """Test Case B/C1: Local file modified, remote unchanged. Upload changes."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        mock_walk.return_value = [("/tmp/gdrive", [], ["mod.txt"])]

        self.mock_state_manager.get_file.return_value = {"id": "f1", "md5": "old_md5"}
        self.mock_state_manager.get_all_files.return_value = {
            "mod.txt": {"id": "f1", "md5": "old_md5"}
        }

        mock_local_md5.return_value = "new_local_md5"
        mock_remote_md5.return_value = "old_md5"

        engine.scan_local_changes()

        self.mock_drive_ops.update_file.assert_called_once_with(
            "f1", os.path.join("/tmp/gdrive", "mod.txt")
        )
        self.mock_state_manager.set_file.assert_called_once_with(
            "mod.txt", "f1", "new_local_md5"
        )
        report_dict = mock_report.call_args[0][0]
        self.assertIn("mod.txt", report_dict["updated_remote"])

    @patch("src.sync_engine.os.walk")
    @patch.object(SyncEngine, "_write_startup_report")
    def test_scan_local_changes_missing_local(self, mock_report, mock_walk):
        """Test Phase 4: Missing local file is conservatively restored."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        mock_walk.return_value = [("/tmp/gdrive", [], [])]  # Empty local directory

        self.mock_state_manager.get_all_files.return_value = {
            "missing.txt": {"id": "f2"}
        }
        self.mock_state_manager.get_file.return_value = {"id": "f2"}

        self.mock_drive_ops.get_metadata.return_value = {
            "trashed": False,
            "md5Checksum": "rem_md5",
            "mimeType": "text/plain",
        }
        self.mock_drive_ops.download_file.return_value = True

        engine.scan_local_changes()

        self.mock_drive_ops.download_file.assert_called_once_with(
            "f2", os.path.join("/tmp/gdrive", "missing.txt")
        )
        self.mock_state_manager.set_file.assert_called_once_with(
            "missing.txt", "f2", "rem_md5"
        )
        report_dict = mock_report.call_args[0][0]
        self.assertIn("missing.txt", report_dict["restored_local"])

    @patch("src.sync_engine.os.walk")
    @patch("src.sync_engine._calculate_local_md5")
    @patch.object(SyncEngine, "_get_remote_md5")
    @patch.object(SyncEngine, "_resolve_conflict")
    @patch.object(SyncEngine, "_write_startup_report")
    def test_scan_local_changes_conflict(
        self,
        mock_report,
        mock_resolve_conflict,
        mock_remote_md5,
        mock_local_md5,
        mock_walk,
    ):
        """Test Sub-case C2: Conflict detected during scan."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        mock_walk.return_value = [("/tmp/gdrive", [], ["conflict.txt"])]

        self.mock_state_manager.get_file.return_value = {"id": "f3", "md5": "old_md5"}
        self.mock_state_manager.get_all_files.return_value = {
            "conflict.txt": {"id": "f3", "md5": "old_md5"}
        }

        mock_local_md5.return_value = "new_local"
        mock_remote_md5.return_value = "new_remote"

        self.mock_drive_ops.download_file.return_value = True

        engine.scan_local_changes()

        mock_resolve_conflict.assert_called_once_with(
            os.path.join("/tmp/gdrive", "conflict.txt")
        )
        self.mock_drive_ops.download_file.assert_called_once_with(
            "f3", os.path.join("/tmp/gdrive", "conflict.txt")
        )
        self.mock_state_manager.set_file.assert_called_once_with(
            "conflict.txt", "f3", "new_remote"
        )
        report_dict = mock_report.call_args[0][0]
        self.assertIn("conflict.txt", report_dict["conflicts"])

    def test_resolve_remote_path(self):
        """Test resolving a nested path to a Drive folder ID."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        self.mock_drive_ops.list_files.side_effect = [
            [
                {
                    "id": "dir1",
                    "name": "App",
                    "mimeType": "application/vnd.google-apps.folder",
                    "trashed": False,
                }
            ],
            [
                {
                    "id": "dir2",
                    "name": "Data",
                    "mimeType": "application/vnd.google-apps.folder",
                    "trashed": False,
                }
            ],
        ]

        result = engine._resolve_remote_path(os.path.join("App", "Data"))
        self.assertEqual(result, "dir2")

        expected_calls = [call("root"), call("dir1")]
        self.mock_drive_ops.list_files.assert_has_calls(expected_calls)

    def test_process_config_changes_new_folder(self):
        """Test targeted sync for a newly added selective sync folder."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        self.mock_state_manager.get_selective_sync_rules.return_value = ["OldFolder"]
        engine.selective_sync_folders = ["OldFolder", "NewFolder"]

        with patch.object(
            engine, "_resolve_remote_path", return_value="new_id"
        ) as mock_resolve, patch.object(
            engine, "_sync_folder"
        ) as mock_sync_folder, patch.object(
            engine, "_delete_local"
        ) as mock_delete_local:

            engine._process_config_changes()

            mock_resolve.assert_called_once_with("NewFolder")
            mock_sync_folder.assert_called_once_with("NewFolder", "new_id")
            mock_delete_local.assert_not_called()
            self.mock_state_manager.set_selective_sync_rules.assert_called_once_with(
                ["OldFolder", "NewFolder"]
            )

    def test_process_config_changes_removed_folder(self):
        """Test local deletion for a removed selective sync folder."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        self.mock_state_manager.get_selective_sync_rules.return_value = [
            "OldFolder",
            "RemovedFolder",
        ]
        engine.selective_sync_folders = ["OldFolder"]

        with patch.object(engine, "_resolve_remote_path") as mock_resolve, patch.object(
            engine, "_sync_folder"
        ) as mock_sync_folder, patch.object(
            engine, "_delete_local"
        ) as mock_delete_local:

            engine._process_config_changes()

            mock_resolve.assert_not_called()
            mock_sync_folder.assert_not_called()
            mock_delete_local.assert_called_once_with("RemovedFolder")
            self.mock_state_manager.set_selective_sync_rules.assert_called_once_with(
                ["OldFolder"]
            )

    @patch("src.sync_engine.os.path.exists", return_value=True)
    def test_calculate_local_md5_oserror(self, mock_exists):
        """Test MD5 calculation returns None on OSError."""
        with patch("builtins.open", side_effect=OSError("Read error")):
            result = _calculate_local_md5("/mock/file.txt")
            self.assertIsNone(result)

    def test_stop(self):
        """Test that the engine properly stops the monitor."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        engine.stop()
        engine.monitor.stop.assert_called_once()

    def test_construct_relative_path_no_parents(self):
        """Test relative path construction when parents are empty or not tracked."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        self.mock_state_manager.get_path_by_id.return_value = None
        rel = engine._construct_relative_path("file.txt", [])
        self.assertEqual(rel, "file.txt")

        rel2 = engine._construct_relative_path("file.txt", ["untracked_parent"])
        self.assertEqual(rel2, "file.txt")

    def test_process_change_missing_info(self):
        """Test _process_change handles incomplete change payloads safely."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        # Missing fileId
        engine._process_change({})
        # Missing file metadata
        engine._process_change({"fileId": "1"})
        # Missing name
        engine._process_change({"fileId": "1", "file": {"mimeType": "text/plain"}})

    def test_handle_remote_move_oserror(self):
        """Test that a local rename failure falls back to deletion."""
        engine = SyncEngine(
            self.mock_config_manager, self.mock_state_manager, self.mock_drive_ops
        )
        self.mock_state_manager.get_path_by_id.return_value = "old_path"

        with patch("os.path.exists", return_value=True), patch("os.makedirs"), patch(
            "os.rename", side_effect=OSError("Rename failed")
        ), patch.object(engine, "_delete_local") as mock_del:

            result = engine._handle_remote_move("file_id", "new_path", "text/plain")
            self.assertFalse(result)
            mock_del.assert_called_once_with("old_path")


if __name__ == "__main__":
    unittest.main()
