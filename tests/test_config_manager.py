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
