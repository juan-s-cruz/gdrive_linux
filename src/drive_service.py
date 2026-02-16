from googleapiclient.discovery import build

from .auth import authenticate


class DriveService:
    """
    Wrapper for the Google Drive API service.
    """

    def __init__(self):
        self.creds = authenticate()
        # Build the Drive v3 API service
        self.service = build("drive", "v3", credentials=self.creds)

    def get_service(self):
        """Returns the raw service object."""
        return self.service


if __name__ == "__main__":
    ds = DriveService()
    print("Drive Service initialized successfully.")
