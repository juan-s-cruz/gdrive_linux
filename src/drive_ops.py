import io
import logging
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


class DriveOps:
    """
    Handles Google Drive API operations: listing, downloading, uploading,
    creating folders, and fetching metadata.
    """

    def __init__(self, service):
        """
        Args:
            service: Authenticated Google Drive service resource.
        """
        self.service = service

    def list_files(self, folder_id=None):
        """
        Lists all files in a specific folder (non-recursive).

        Args:
            folder_id (str, optional): The ID of the folder to list.
                                       If None, lists root.

        Returns:
            list: A list of file resources (dicts).
        """
        query = "trashed = false"
        if folder_id:
            query += f" and '{folder_id}' in parents"

        results = []
        page_token = None

        try:
            while True:
                response = (
                    self.service.files()
                    .list(
                        q=query,
                        fields="nextPageToken, files(id, name, mimeType, md5Checksum, parents)",
                        pageToken=page_token,
                    )
                    .execute()
                )

                results.extend(response.get("files", []))
                page_token = response.get("nextPageToken", None)
                if page_token is None:
                    break
            return results
        except HttpError as error:
            logger.error(f"An error occurred listing files: {error}")
            return []

    def download_file(self, file_id, local_path):
        """
        Downloads a file's content to a local path.

        Args:
            file_id (str): The Drive file ID.
            local_path (str): The full local path to save the file.
        """
        try:
            request = self.service.files().get_media(fileId=file_id)
            with io.FileIO(local_path, "wb") as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while done is False:
                    status, done = downloader.next_chunk()
            logger.info(f"Downloaded file {file_id} to {local_path}")
            return True
        except HttpError as error:
            logger.error(f"An error occurred downloading file {file_id}: {error}")
            return False

    def upload_file(self, local_path, name, parent_id=None, mime_type=None):
        """
        Uploads a new file to Drive.

        Args:
            local_path (str): Path to the local file.
            name (str): Name of the file on Drive.
            parent_id (str, optional): ID of the parent folder.
            mime_type (str, optional): MIME type of the file.

        Returns:
            dict: The created file resource, or None on failure.
        """
        try:
            file_metadata = {"name": name}
            if parent_id:
                file_metadata["parents"] = [parent_id]

            media = MediaFileUpload(local_path, mimetype=mime_type, resumable=True)

            file = (
                self.service.files()
                .create(
                    body=file_metadata,
                    media_body=media,
                    fields="id, name, md5Checksum, parents",
                )
                .execute()
            )
            logger.info(f"Uploaded file {name} (ID: {file.get('id')})")
            return file
        except HttpError as error:
            logger.error(f"An error occurred uploading file {local_path}: {error}")
            return None

    def create_folder(self, name, parent_id=None):
        """
        Creates a folder on Drive.

        Args:
            name (str): Name of the folder.
            parent_id (str, optional): ID of the parent folder.

        Returns:
            str: The ID of the created folder, or None on failure.
        """
        try:
            file_metadata = {
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
            }
            if parent_id:
                file_metadata["parents"] = [parent_id]

            file = (
                self.service.files().create(body=file_metadata, fields="id").execute()
            )
            folder_id = file.get("id")
            logger.info(f"Created folder {name} (ID: {folder_id})")
            return folder_id
        except HttpError as error:
            logger.error(f"An error occurred creating folder {name}: {error}")
            return None

    def get_metadata(self, file_id):
        """
        Retrieves metadata for a file.

        Args:
            file_id (str): The Drive file ID.

        Returns:
            dict: The file resource, or None on failure.
        """
        try:
            file = (
                self.service.files()
                .get(fileId=file_id, fields="id, name, mimeType, md5Checksum, parents")
                .execute()
            )
            return file
        except HttpError as error:
            logger.error(f"An error occurred fetching metadata for {file_id}: {error}")
            return None
