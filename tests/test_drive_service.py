from unittest.mock import patch, MagicMock

from src.drive_service import DriveService


@patch("src.drive_service.build")
@patch("src.drive_service.authenticate")
def test_drive_service_initialization(mock_authenticate, mock_build):
    """Test that DriveService initializes and returns the built service."""
    mock_creds = MagicMock()
    mock_authenticate.return_value = mock_creds
    mock_service_instance = MagicMock()
    mock_build.return_value = mock_service_instance

    ds = DriveService("dummy_creds.json", "dummy_token.json")

    mock_authenticate.assert_called_once_with("dummy_creds.json", "dummy_token.json")
    mock_build.assert_called_once_with("drive", "v3", credentials=mock_creds)
    assert ds.get_service() == mock_service_instance
