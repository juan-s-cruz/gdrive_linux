import os
import stat
import json
from unittest.mock import MagicMock, patch, call

import pytest
from watchdog.events import FileCreatedEvent

# Assuming the project structure allows this import path
from src.sync_engine import SyncEngine
from src.drive_ops import DriveOps
from src.monitor import LocalFileHandler
from src.config_manager import ConfigManager
from src.auth import authenticate


@pytest.fixture
def mock_config_manager(tmp_path):
    """Fixture for a mocked ConfigManager."""
    manager = MagicMock(spec=ConfigManager)
    manager.get_local_root.return_value = str(tmp_path)
    manager.get_selective_sync_folders.return_value = []
    return manager


@pytest.fixture
def mock_state_manager():
    """Fixture for a mocked StateManager."""
    return MagicMock()


@pytest.fixture
def mock_drive_ops():
    """Fixture for a mocked DriveOps."""
    return MagicMock(spec=DriveOps)


def test_sync_engine_path_traversal_sanitization(
    mock_config_manager, mock_state_manager, mock_drive_ops
):
    """Verify that remote filenames are sanitized to prevent path traversal."""
    # Arrange
    sync_engine = SyncEngine(mock_config_manager, mock_state_manager, mock_drive_ops)

    malicious_filename = "../../.bashrc"
    sanitized_filename = ".._.._.bashrc"

    # Mock the API to return a file with a malicious name
    mock_drive_ops.list_files.return_value = [
        {
            "id": "malicious_file_id",
            "name": malicious_filename,
            "mimeType": "text/plain",
            "md5Checksum": "12345",
        }
    ]

    # Patch the internal method that handles file syncing to inspect its arguments
    with patch.object(sync_engine, "_sync_file") as mock_sync_file:
        # Act
        sync_engine._sync_recursive("root", "some_folder")

        # Assert
        # Check that _sync_file was called with the SANITIZED relative path
        mock_sync_file.assert_called_once()
        call_args, _ = mock_sync_file.call_args
        rel_path_arg = call_args[0]

        # The expected path is 'some_folder/.._.._.bashrc'
        assert os.path.basename(rel_path_arg) == sanitized_filename


def test_drive_ops_download_does_not_follow_symlinks(tmp_path):
    """Verify that downloading a file does not overwrite a symlink's target."""
    # Arrange
    # 1. Create a sensitive file that should NOT be touched
    sensitive_file = tmp_path / "sensitive.txt"
    sensitive_file.write_text("secret_data")

    # 2. Create a symlink in the sync dir pointing to the sensitive file
    sync_dir = tmp_path / "sync"
    sync_dir.mkdir()
    symlink_path = sync_dir / "link_to_sensitive.txt"
    os.symlink(sensitive_file, symlink_path)

    # 3. Setup DriveOps with a mocked service that simulates a download
    mock_service = MagicMock()

    # Simulate the downloader writing "new_data"
    def download_side_effect(fh, request):
        fh.write(b"new_data")
        # Mock the downloader's next_chunk behavior
        downloader_instance = MagicMock()
        downloader_instance.next_chunk.side_effect = [(None, True)]  # status, done
        return downloader_instance

    with patch("src.drive_ops.MediaIoBaseDownload", side_effect=download_side_effect):
        drive_ops = DriveOps(mock_service)

        # Act
        # Attempt to "download" a file to the location of the symlink
        drive_ops.download_file("some_file_id", str(symlink_path))

    # Assert
    # 1. The original sensitive file is unchanged
    assert sensitive_file.read_text() == "secret_data"

    # 2. The path is no longer a symlink
    assert not os.path.islink(symlink_path)

    # 3. The path is now a regular file with the new content
    assert os.path.isfile(symlink_path)
    assert symlink_path.read_text() == "new_data"


def test_monitor_ignores_symlinks_on_upload(
    mock_config_manager, mock_state_manager, mock_drive_ops
):
    """Verify that the local monitor ignores symlinks to prevent data leaks."""
    # Arrange
    handler = LocalFileHandler(mock_config_manager, mock_state_manager, mock_drive_ops)

    # Path to a symlink within the monitored directory
    symlink_path = os.path.join(mock_config_manager.get_local_root(), "my_symlink")

    # Create a mock creation event for the symlink
    event = FileCreatedEvent(src_path=symlink_path)

    # Patch os.path.islink to simulate that the path is a symlink
    with patch("os.path.islink", return_value=True) as mock_islink:
        # Act
        handler.on_created(event)

        # Assert
        mock_islink.assert_called_once_with(symlink_path)
        # The core assertion: no upload operation should have been started
        mock_drive_ops.upload_file.assert_not_called()


@patch("src.auth.os.chmod")
@patch("src.auth.open")
@patch("src.auth.InstalledAppFlow")
@patch("src.auth.Credentials")
@patch("src.auth.os.path.exists")
def test_auth_saves_token_with_secure_permissions(
    mock_exists, mock_creds, mock_flow, mock_open, mock_chmod
):
    """Verify that token.json is saved with 0o600 permissions."""
    # Arrange
    mock_exists.side_effect = (
        lambda p: p == "credentials.json"
    )  # Simulate missing token, but present credentials
    mock_creds_instance = MagicMock()
    mock_creds_instance.to_json.return_value = '{"token": "dummy"}'
    mock_flow.from_client_secrets_file.return_value.run_local_server.return_value = (
        mock_creds_instance
    )
    # Act
    authenticate("credentials.json", "token.json")
    # Assert
    mock_chmod.assert_called_once_with("token.json", 0o600)


@patch("src.config_manager.os.chmod")
def test_config_manager_creates_root_with_secure_permissions(mock_chmod, tmp_path):
    """Verify that the sync root directory is created with 0o700 permissions."""
    # Arrange
    sync_root = tmp_path / "GoogleDrive"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"local_root_path": str(sync_root)}))
    # Act
    ConfigManager(str(config_path))
    # Assert
    assert sync_root.is_dir()
    mock_chmod.assert_called_once_with(str(sync_root), 0o700)
