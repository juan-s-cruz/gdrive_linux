import pytest
from unittest.mock import MagicMock, patch
from googleapiclient.errors import HttpError

from src.drive_ops import DriveOps


def make_http_error():
    """Helper to generate a mock HttpError to test exception handling."""
    resp = MagicMock()
    resp.status = 500
    resp.reason = "Internal Server Error"
    return HttpError(resp, b"Mock Error Content")


@pytest.fixture
def mock_service():
    """Provides a mocked Google Drive service client."""
    return MagicMock()


@pytest.fixture
def drive_ops(mock_service):
    """Provides a DriveOps instance with the mocked service."""
    return DriveOps(mock_service)


# --- list_files tests ---
def test_list_files_root(drive_ops, mock_service):
    """Tests listing files in the root directory."""
    mock_service.files().list().execute.return_value = {"files": [{"id": "1"}]}
    res = drive_ops.list_files()
    assert len(res) == 1
    mock_service.files().list.assert_called_with(
        q="trashed = false",
        fields="nextPageToken, files(id, name, mimeType, md5Checksum, parents)",
        pageToken=None,
    )


def test_list_files_folder_with_pagination(drive_ops, mock_service):
    """Tests listing files within a folder, ensuring pagination is handled."""
    mock_service.files().list().execute.side_effect = [
        {"files": [{"id": "1"}], "nextPageToken": "page2"},
        {"files": [{"id": "2"}]},
    ]
    res = drive_ops.list_files("folder1")
    assert len(res) == 2
    mock_service.files().list.assert_any_call(
        q="trashed = false and 'folder1' in parents",
        fields="nextPageToken, files(id, name, mimeType, md5Checksum, parents)",
        pageToken=None,
    )


def test_list_files_http_error(drive_ops, mock_service):
    """Tests that HttpErrors during list_files return an empty list safely."""
    mock_service.files().list().execute.side_effect = make_http_error()
    assert drive_ops.list_files() == []


# --- download_file tests ---
@patch("src.drive_ops.os.replace")
@patch("src.drive_ops.MediaIoBaseDownload")
@patch("src.drive_ops.io.FileIO")
def test_download_file_success(
    mock_fileio, mock_download, mock_replace, drive_ops, mock_service
):
    """Tests a successful file download and atomic replacement."""
    mock_service.files().get_media.return_value = MagicMock()
    mock_download.return_value.next_chunk.side_effect = [
        ("status", False),
        ("status", True),
    ]

    assert drive_ops.download_file("file1", "/path") is True
    mock_replace.assert_called_once_with("/path.gdrive_tmp", "/path")


def test_download_file_http_error(drive_ops, mock_service):
    """Tests that API errors during download initiation are caught."""
    mock_service.files().get_media.side_effect = make_http_error()
    assert drive_ops.download_file("file1", "/path") is False


@patch("src.drive_ops.os.remove")
@patch("src.drive_ops.os.path.exists")
@patch("src.drive_ops.MediaIoBaseDownload")
@patch("src.drive_ops.io.FileIO")
def test_download_file_general_exception(
    mock_fileio, mock_download, mock_exists, mock_remove, drive_ops, mock_service
):
    """Tests that generic exceptions during download chunking trigger temp file cleanup."""
    mock_service.files().get_media.return_value = MagicMock()
    mock_download.return_value.next_chunk.side_effect = Exception("Chunk error")
    mock_exists.return_value = True

    with pytest.raises(Exception, match="Chunk error"):
        drive_ops.download_file("file1", "/path")

    mock_remove.assert_called_once_with("/path.gdrive_tmp")


@patch("src.drive_ops.os.path.exists")
@patch("src.drive_ops.MediaIoBaseDownload")
@patch("src.drive_ops.io.FileIO")
def test_download_file_general_exception_no_cleanup(
    mock_fileio, mock_download, mock_exists, drive_ops, mock_service
):
    """Tests download cleanup correctly bypasses if the tmp file wasn't created."""
    mock_service.files().get_media.return_value = MagicMock()
    mock_download.return_value.next_chunk.side_effect = Exception("Chunk error")
    mock_exists.return_value = False

    with pytest.raises(Exception, match="Chunk error"):
        drive_ops.download_file("file1", "/path")


# --- upload_file tests ---
@patch("src.drive_ops.MediaFileUpload")
def test_upload_file_success_no_parent(mock_media, drive_ops, mock_service):
    """Tests successful file upload to the root directory."""
    # Notice we use .create.return_value instead of .create()
    mock_service.files().create.return_value.execute.return_value = {"id": "1"}

    res = drive_ops.upload_file("/path", "name", mime_type="text/plain")
    assert res == {"id": "1"}

    mock_service.files().create.assert_called_once_with(
        body={"name": "name"},
        media_body=mock_media.return_value,
        fields="id, name, md5Checksum, parents",
    )


@patch("src.drive_ops.MediaFileUpload")
def test_upload_file_success_with_parent(mock_media, drive_ops, mock_service):
    """Tests successful file upload with a specific parent ID."""
    mock_service.files().create().execute.return_value = {"id": "1"}
    res = drive_ops.upload_file("/path", "name", parent_id="parent1")
    assert res == {"id": "1"}


@patch("src.drive_ops.MediaFileUpload")
def test_upload_file_http_error(mock_media, drive_ops, mock_service):
    """Tests HttpError handling during upload."""
    mock_service.files().create().execute.side_effect = make_http_error()
    assert drive_ops.upload_file("/path", "name") is None


@patch("src.drive_ops.MediaFileUpload")
def test_upload_file_os_error(mock_media, drive_ops, mock_service):
    """Tests handling of local filesystem errors (e.g., file deleted before upload)."""
    mock_media.side_effect = OSError("File not found")
    assert drive_ops.upload_file("/path", "name") is None


# --- update_file tests ---
@patch("src.drive_ops.MediaFileUpload")
def test_update_file_success(mock_media, drive_ops, mock_service):
    mock_service.files().update().execute.return_value = {"id": "1"}
    res = drive_ops.update_file("1", "/path", mime_type="text/plain")
    assert res == {"id": "1"}


@patch("src.drive_ops.MediaFileUpload")
def test_update_file_http_error(mock_media, drive_ops, mock_service):
    mock_service.files().update().execute.side_effect = make_http_error()
    assert drive_ops.update_file("1", "/path") is None


@patch("src.drive_ops.MediaFileUpload")
def test_update_file_os_error(mock_media, drive_ops, mock_service):
    mock_media.side_effect = OSError("Access denied")
    assert drive_ops.update_file("1", "/path") is None


# --- move_file tests ---
def test_move_file_rename_only(drive_ops, mock_service):
    mock_service.files().update().execute.return_value = {"id": "1"}
    res = drive_ops.move_file("1", new_name="new_name")
    assert res == {"id": "1"}


def test_move_file_move_with_parents(drive_ops, mock_service):
    mock_service.files().get().execute.return_value = {"parents": ["old_parent"]}
    mock_service.files().update().execute.return_value = {"id": "1"}
    res = drive_ops.move_file("1", new_name="new_name", new_parent_id="new_parent")
    assert res == {"id": "1"}


def test_move_file_move_no_old_parents(drive_ops, mock_service):
    mock_service.files().get().execute.return_value = {}
    mock_service.files().update().execute.return_value = {"id": "1"}
    res = drive_ops.move_file("1", new_parent_id="new_parent")
    assert res == {"id": "1"}


def test_move_file_http_error(drive_ops, mock_service):
    mock_service.files().update().execute.side_effect = make_http_error()
    assert drive_ops.move_file("1", "new_name") is None


# --- delete_file tests ---
def test_delete_file_success(drive_ops, mock_service):
    assert drive_ops.delete_file("1") is True
    mock_service.files().delete.assert_called_once_with(fileId="1")


def test_delete_file_http_error(drive_ops, mock_service):
    mock_service.files().delete().execute.side_effect = make_http_error()
    assert drive_ops.delete_file("1") is False


# --- create_folder tests ---
def test_create_folder_success(drive_ops, mock_service):
    mock_service.files().create().execute.return_value = {"id": "folder1"}
    res = drive_ops.create_folder("folder_name", parent_id="parent1")
    assert res == "folder1"


def test_create_folder_http_error(drive_ops, mock_service):
    mock_service.files().create().execute.side_effect = make_http_error()
    assert drive_ops.create_folder("folder_name") is None


# --- get_metadata tests ---
def test_get_metadata_success(drive_ops, mock_service):
    mock_service.files().get().execute.return_value = {"id": "1", "name": "file"}
    assert drive_ops.get_metadata("1") == {"id": "1", "name": "file"}


def test_get_metadata_http_error(drive_ops, mock_service):
    mock_service.files().get().execute.side_effect = make_http_error()
    assert drive_ops.get_metadata("1") is None


# --- get_start_page_token & list_changes tests ---
def test_get_start_page_token_success(drive_ops, mock_service):
    mock_service.changes().getStartPageToken().execute.return_value = {
        "startPageToken": "token1"
    }
    assert drive_ops.get_start_page_token() == "token1"


def test_list_changes_success(drive_ops, mock_service):
    mock_service.changes().list().execute.side_effect = [
        {"changes": [{"fileId": "1"}], "nextPageToken": "token2"},
        {"changes": [{"fileId": "2"}], "newStartPageToken": "token3"},
    ]
    res = drive_ops.list_changes("token1")
    assert res["newStartPageToken"] == "token3"
