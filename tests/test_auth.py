import pytest
from unittest.mock import patch, MagicMock
from google.auth.exceptions import RefreshError

from src.auth import authenticate


@patch("src.auth.Credentials")
@patch("src.auth.os.path.exists")
def test_authenticate_valid_existing_token(mock_exists, mock_credentials):
    """Test authentication when a valid token already exists."""
    mock_exists.return_value = True
    mock_creds = MagicMock()
    mock_creds.valid = True
    mock_credentials.from_authorized_user_file.return_value = mock_creds

    creds = authenticate("creds.json", "token.json")

    assert creds == mock_creds
    mock_credentials.from_authorized_user_file.assert_called_once_with(
        "token.json", ["https://www.googleapis.com/auth/drive"]
    )


@patch("src.auth.Request")
@patch("src.auth.Credentials")
@patch("src.auth.os.path.exists")
def test_authenticate_refresh_expired_token(
    mock_exists, mock_credentials, mock_request
):
    """Test refreshing an expired token."""
    mock_exists.side_effect = lambda p: p == "token.json"

    mock_creds = MagicMock()
    mock_creds.valid = False
    mock_creds.expired = True
    mock_creds.refresh_token = "some_refresh_token"
    mock_credentials.from_authorized_user_file.return_value = mock_creds

    with patch("src.auth.os.chmod"), patch("builtins.open"):
        creds = authenticate("creds.json", "token.json")

    mock_creds.refresh.assert_called_once()
    assert creds == mock_creds


@patch("src.auth.Credentials")
@patch("src.auth.os.path.exists")
def test_authenticate_refresh_fails_and_no_creds(mock_exists, mock_credentials):
    """Test fallback when refreshing fails and no creds file is present."""
    mock_exists.side_effect = lambda p: p == "token.json"

    mock_creds = MagicMock()
    mock_creds.valid = False
    mock_creds.expired = True
    mock_creds.refresh_token = "some_refresh_token"
    mock_creds.refresh.side_effect = RefreshError("Revoked")
    mock_credentials.from_authorized_user_file.return_value = mock_creds

    creds = authenticate("creds.json", "token.json")
    # Fails gracefully
    assert creds is None


@patch("src.auth.os.path.exists")
def test_authenticate_no_token_no_creds(mock_exists):
    """Test authentication fails gracefully when neither file exists."""
    mock_exists.return_value = False

    creds = authenticate("creds.json", "token.json")
    assert creds is None
