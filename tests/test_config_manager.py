import json
import os
import pytest
from unittest.mock import patch

from src.config_manager import ConfigManager


def test_config_manager_file_not_found():
    """Test that it raises an error if config doesn't exist."""
    with pytest.raises(FileNotFoundError, match="Config file not found"):
        ConfigManager("non_existent_config.json")


def test_config_manager_missing_local_root(tmp_path):
    """Test that it raises an error if local_root_path is missing."""
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({}))
    with pytest.raises(ValueError, match="Missing 'local_root_path' in config"):
        ConfigManager(str(config_path))


@patch("src.config_manager.os.chmod")
def test_config_manager_creates_and_expands_path(mock_chmod, tmp_path):
    """Test that it correctly expands ~ and creates the directory."""
    config_path = tmp_path / "config.json"
    fake_root = "~/my_gdrive_sync"
    config_path.write_text(
        json.dumps(
            {"local_root_path": fake_root, "selective_sync_folders": ["folder1"]}
        )
    )

    with patch("src.config_manager.os.makedirs") as mock_makedirs:
        # Side effects: Config exists, Root directory does NOT exist yet
        with patch("src.config_manager.os.path.exists", side_effect=[True, False]):
            cm = ConfigManager(str(config_path))

            expanded_path = os.path.abspath(os.path.expanduser(fake_root))
            assert cm.get_local_root() == expanded_path
            assert cm.get_selective_sync_folders() == ["folder1"]
            mock_makedirs.assert_called_once_with(expanded_path)
            mock_chmod.assert_called_once_with(expanded_path, 0o700)


@patch("src.config_manager.os.chmod")
def test_config_manager_add_sync_folder(mock_chmod, tmp_path):
    """Test adding new folders with subset-filtering and exact match duplication prevention."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "local_root_path": str(tmp_path / "root"),
                "selective_sync_folders": ["folder1"],
            }
        )
    )
    cm = ConfigManager(str(config_path))

    # Add completely new folder
    cm.add_sync_folder("folder2")
    assert "folder2" in cm.get_selective_sync_folders()

    # Subset filtering: ignore children if parent is tracked
    cm.add_sync_folder("folder1/child")
    assert "folder1/child" not in cm.get_selective_sync_folders()

    # Deduplicate exact matches
    cm.add_sync_folder("folder1")
    assert cm.get_selective_sync_folders().count("folder1") == 1

    # Parent override: adding parent when child is already tracked
    cm.add_sync_folder("folder3/child")
    cm.add_sync_folder("folder3")
    folders = cm.get_selective_sync_folders()
    assert "folder3" in folders
    assert "folder3/child" not in folders


@patch("src.config_manager.os.chmod")
def test_config_manager_rename_sync_folder(mock_chmod, tmp_path):
    """Test renaming a tracked folder exactly and by prefix replacements."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "local_root_path": str(tmp_path / "root"),
                "selective_sync_folders": [
                    "old_folder",
                    "old_parent/child",
                    "other_folder",
                ],
            }
        )
    )
    cm = ConfigManager(str(config_path))

    cm.rename_sync_folder("old_folder", "new_folder")
    cm.rename_sync_folder("old_parent", "new_parent")
    folders = cm.get_selective_sync_folders()
    assert "new_folder" in folders
    assert "new_parent/child" in folders
    assert "old_folder" not in folders
    assert "old_parent/child" not in folders
    assert "other_folder" in folders
