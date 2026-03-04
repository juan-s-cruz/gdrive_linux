import os.path
from typing import Optional
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.exceptions import RefreshError

# If modifying these scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/drive"]


def authenticate(credentials_file: str, token_file: str) -> Optional[Credentials]:
    """
    Authenticates the user with Google Drive API using OAuth2.
    Saves the credentials to the specified token file for future use.
    """
    creds = None
    # The token file stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                print("Token has expired or been revoked. Re-authenticating...")
                creds = None

        if not creds:
            if not os.path.exists(credentials_file):
                print(f"Error: {credentials_file} not found.")
                print(
                    "Please download your OAuth 2.0 Client ID JSON from Google Cloud Console"
                )
                print(f"and save it as '{credentials_file}' in this directory.")
                return None

            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            creds = flow.run_local_server(port=0)

        # Save the credentials for the next run
        with open(token_file, "w") as token:
            token.write(creds.to_json())
            print(f"Authentication successful. Token saved to {token_file}")

    return creds


if __name__ == "__main__":
    authenticate("credentials.json", "token.json")
