from typing import Any
from googleapiclient.discovery import build

from .auth import authenticate


class DriveService:
    """
    Wrapper for the Google Drive API service.
    """

    def __init__(self, credentials_path: str, token_path: str):
        """
        Initializes the DriveService by authenticating and building the API client.
        """
        self.creds = authenticate(credentials_path, token_path)
        # Build the Drive v3 API service
        self.service = build("drive", "v3", credentials=self.creds)

    def get_service(self) -> Any:
        """Returns the raw service object."""
        return self.service


if __name__ == "__main__":
    ds = DriveService("credentials.json", "token.json")
    print("Drive Service initialized successfully.")
