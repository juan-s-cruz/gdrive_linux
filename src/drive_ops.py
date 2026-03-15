import io
import logging
import threading
from typing import List, Dict, Optional, Any
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


class DriveOps:
    """
    Handles Google Drive API operations: listing, downloading, uploading,
    creating folders, and fetching metadata.
    """

    def __init__(self, service: Any):
        """
        Args:
            service: Authenticated Google Drive service resource.
        """
        self.service = service
        self.lock = threading.Lock()

    def list_files(self, folder_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Lists all files in a specific folder (non-recursive).

        Args:
            folder_id (str, optional): The ID of the folder to list.
                                       If None, lists root.

        Returns:
            list: A list of file resources (dicts).
        """
        with self.lock:
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

    def download_file(self, file_id: str, local_path: str) -> bool:
        """
        Downloads a file's content to a local path.

        Args:
            file_id (str): The Drive file ID.
            local_path (str): The full local path to save the file.
        """
        with self.lock:
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

    def upload_file(
        self,
        local_path: str,
        name: str,
        parent_id: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
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
        with self.lock:
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
            except OSError as error:
                logger.warning(
                    f"Skipping upload for {local_path} (file may have moved/deleted): {error}"
                )
                return None

    def update_file(
        self, file_id: str, local_path: str, mime_type: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Updates an existing file's content on Drive.

        Args:
            file_id (str): The ID of the file to update.
            local_path (str): Path to the local file.
            mime_type (str, optional): MIME type of the file.

        Returns:
            dict: The updated file resource, or None on failure.
        """
        with self.lock:
            try:
                media = MediaFileUpload(local_path, mimetype=mime_type, resumable=True)

                file = (
                    self.service.files()
                    .update(
                        fileId=file_id,
                        media_body=media,
                        fields="id, name, md5Checksum, parents",
                    )
                    .execute()
                )
                logger.info(f"Updated file {file_id}")
                return file
            except HttpError as error:
                logger.error(f"An error occurred updating file {local_path}: {error}")
                return None
            except OSError as error:
                logger.warning(
                    f"Skipping update for {local_path} (file may have moved/deleted): {error}"
                )
                return None

    def move_file(
        self,
        file_id: str,
        new_name: Optional[str] = None,
        new_parent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Moves or renames a file on Drive.

        Args:
            file_id (str): The ID of the file to move/rename.
            new_name (str, optional): The new name for the file.
            new_parent_id (str, optional): The ID of the new parent folder.

        Returns:
            dict: The updated file resource, or None on failure.
        """
        with self.lock:
            try:
                metadata = {}
                if new_name:
                    metadata["name"] = new_name

                if new_parent_id:
                    # Retrieve current parents to remove them
                    file = (
                        self.service.files()
                        .get(fileId=file_id, fields="parents")
                        .execute()
                    )
                    previous_parents = ",".join(file.get("parents") or [])

                    request = self.service.files().update(
                        fileId=file_id,
                        body=metadata,
                        addParents=new_parent_id,
                        removeParents=previous_parents,
                        fields="id, name, parents",
                    )
                else:
                    # Just rename (or move within same parent if logic allows)
                    request = self.service.files().update(
                        fileId=file_id, body=metadata, fields="id, name, parents"
                    )

                updated_file = request.execute()
                logger.info(f"Moved/Renamed file {file_id}")
                return updated_file
            except HttpError as error:
                logger.error(f"An error occurred moving file {file_id}: {error}")
                return None

    def delete_file(self, file_id: str) -> bool:
        """
        Permanently deletes a file from Drive.

        Args:
            file_id (str): The ID of the file to delete.

        Returns:
            bool: True if successful, False otherwise.
        """
        with self.lock:
            try:
                self.service.files().delete(fileId=file_id).execute()
                logger.info(f"Deleted file {file_id}")
                return True
            except HttpError as error:
                logger.error(f"An error occurred deleting file {file_id}: {error}")
                return False

    def create_folder(
        self, name: str, parent_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Creates a folder on Drive.

        Args:
            name (str): Name of the folder.
            parent_id (str, optional): ID of the parent folder.

        Returns:
            str: The ID of the created folder, or None on failure.
        """
        with self.lock:
            try:
                file_metadata = {
                    "name": name,
                    "mimeType": "application/vnd.google-apps.folder",
                }
                if parent_id:
                    file_metadata["parents"] = [parent_id]

                file = (
                    self.service.files()
                    .create(body=file_metadata, fields="id")
                    .execute()
                )
                folder_id = file.get("id")
                logger.info(f"Created folder {name} (ID: {folder_id})")
                return folder_id
            except HttpError as error:
                logger.error(f"An error occurred creating folder {name}: {error}")
                return None

    def get_metadata(self, file_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves metadata for a file.

        Args:
            file_id (str): The Drive file ID.

        Returns:
            dict: The file resource, or None on failure.
        """
        with self.lock:
            try:
                file = (
                    self.service.files()
                    .get(
                        fileId=file_id,
                        fields="id, name, mimeType, md5Checksum, parents",
                    )
                    .execute()
                )
                return file
            except HttpError as error:
                logger.error(
                    f"An error occurred fetching metadata for {file_id}: {error}"
                )
                return None
