import logging
import os
import shutil
import time
from typing import List, Optional, Set, Dict, Any

from .config_manager import ConfigManager
from .state_manager import StateManager
from .drive_ops import DriveOps
from .monitor import LocalMonitor

logger = logging.getLogger(__name__)


class SyncEngine:
    """
    Core logic for synchronization, including selective sync filtering,
    polling coordination, and conflict resolution.

    This engine orchestrates the "Down-Sync" process (Drive to Local),
    periodically polling the Google Drive API for changes and applying
    them to the local file system while respecting configuration rules.
    """

    def __init__(
        self,
        config_manager: ConfigManager,
        state_manager: StateManager,
        drive_ops: DriveOps,
    ) -> None:
        """
        Initializes the SyncEngine.

        Args:
            config_manager (ConfigManager): Instance of ConfigManager.
            state_manager (StateManager): Instance of StateManager.
            drive_ops (DriveOps): Instance of DriveOps.
        """
        self.config_manager = config_manager
        self.state_manager = state_manager
        self.drive_ops = drive_ops

        # Load and normalize selective sync folders
        self.selective_sync_folders = self._load_selective_sync_rules()

        # Initialize Local Monitor for Up-Sync
        self.monitor = LocalMonitor(
            self.config_manager, self.state_manager, self.drive_ops
        )

    def _load_selective_sync_rules(self) -> List[str]:
        """
        Loads selective sync folders from config and normalizes paths.

        Returns:
            List[str]: A list of normalized folder paths.
        """
        folders = self.config_manager.get_selective_sync_folders()
        if not folders:
            return []

        # Normalize paths (remove trailing slashes, resolve . and ..)
        return [os.path.normpath(f) for f in folders]

    def is_path_allowed(self, rel_path: str) -> bool:
        """
        Determines if a relative path is allowed based on selective sync rules.

        Logic:
        1. If no rules are defined, allow everything.
        2. If path matches or is inside an allowed folder -> Allow.
        3. If path is a parent of an allowed folder -> Allow (to enable traversal).
        4. Otherwise -> Deny.

        Args:
            rel_path (str): Relative path to check.

        Returns:
            bool: True if allowed, False otherwise.
        """
        if not self.selective_sync_folders:
            return True

        rel_path = os.path.normpath(rel_path)

        # Root is always allowed to enable traversal
        if rel_path == ".":
            return True

        for folder in self.selective_sync_folders:
            # Case 0: If rule is root, allow everything
            if folder == ".":
                return True

            # Case 1: Path is the allowed folder or inside it
            # e.g. folder="Photos", path="Photos/2023.jpg"
            if rel_path == folder or rel_path.startswith(folder + os.sep):
                return True

            # Case 2: Path is a parent of the allowed folder (needed to reach the child)
            # e.g. folder="Photos/2023", path="Photos"
            if folder.startswith(rel_path + os.sep):
                return True

        return False

    def sync(self) -> None:
        """
        Main entry point for the synchronization process.
        Performs a one-way sync from Drive to Local (Down-Sync).
        """
        logger.info("Starting Down-Sync cycle...")

        saved_token = self.state_manager.get_start_page_token()

        if saved_token is None:
            logger.info("No Start Page Token found. Performing full recursive sync.")
            # Start syncing from the root folder
            self._sync_recursive("root", "")

            new_token = self.drive_ops.get_start_page_token()
            if new_token:
                self.state_manager.set_start_page_token(new_token)
                logger.info("Saved initial Start Page Token.")
        else:
            logger.info("Found Start Page Token. Performing delta sync.")
            self._sync_changes(saved_token)

        logger.info("Down-Sync cycle complete.")

    def _sync_changes(self, start_token: str) -> None:
        """
        Fetches and processes incremental changes from Google Drive.

        Args:
            start_token (str): The token to start listing changes from.
        """
        response = self.drive_ops.list_changes(start_token)
        if not response:
            logger.error("Failed to retrieve changes. Delta sync aborted.")
            return

        changes = response.get("changes", [])
        new_token = response.get("newStartPageToken")

        logger.info(f"Retrieved {len(changes)} changes from Drive.")

        for change in changes:
            self._process_change(change)

        if new_token and new_token != start_token:
            self.state_manager.set_start_page_token(new_token)
            logger.info("Updated Start Page Token.")

    def _process_change(self, change: Dict[str, Any]) -> None:
        """
        Processes a single change event from the Google Drive API.

        Args:
            change (Dict[str, Any]): A dictionary containing the change details
                                     as returned by the Drive API.
        """
        file_id = change.get("fileId")
        if not file_id:
            return

        removed = change.get("removed", False)
        file_info = change.get("file", {})

        # Handle Deletions / Trashed
        if removed or file_info.get("trashed"):
            local_path = self.state_manager.get_path_by_id(file_id)
            if local_path:
                self._delete_local(local_path)
            return

        if not file_info:
            return

        name = file_info.get("name")
        mime_type = file_info.get("mimeType")
        remote_md5 = file_info.get("md5Checksum")
        parents = file_info.get("parents", [])

        if not name:
            return

        rel_path = self._construct_relative_path(name, parents)
        self._handle_remote_move(file_id, rel_path, mime_type)

        # Check Selective Sync
        if not self.is_path_allowed(rel_path):
            return

        # Handle Folder
        if mime_type == "application/vnd.google-apps.folder":
            self._sync_folder(rel_path, file_id)
        # Handle File
        else:
            self._sync_file(rel_path, file_id, remote_md5)

    def _construct_relative_path(self, name: str, parents: List[str]) -> str:
        """
        Constructs a sanitized relative path using tracked parents.

        Args:
            name (str): The raw filename from Google Drive.
            parents (List[str]): A list of parent folder IDs for the file.

        Returns:
            str: The constructed and sanitized relative path.
        """
        name = name.replace("/", "_").replace("\\", "_")
        if name in (".", ".."):
            name = f"_{name}_"

        rel_path = None
        for parent_id in parents:
            parent_path = self.state_manager.get_path_by_id(parent_id)
            if parent_path is not None:
                rel_path = os.path.join(parent_path, name) if parent_path else name
                break

        if rel_path is None:
            rel_path = name

        return rel_path

    def _handle_remote_move(
        self, file_id: str, rel_path: str, mime_type: Optional[str]
    ) -> None:
        """
        Handles local file moving and state updating if a file was moved remotely.

        Compares the new relative path to the previously tracked path in the state.
        If they differ, performs a local file rename and updates all relevant
        state mappings to prevent unnecessary re-downloads.

        Args:
            file_id (str): The Google Drive ID of the file or folder.
            rel_path (str): The newly computed relative path.
            mime_type (Optional[str]): The MIME type of the item, used to check for folders.
        """
        old_rel_path = self.state_manager.get_path_by_id(file_id)
        if not old_rel_path or old_rel_path == rel_path:
            return

        old_local_path = os.path.join(
            self.config_manager.get_local_root(), old_rel_path
        )
        new_local_path = os.path.join(self.config_manager.get_local_root(), rel_path)

        old_allowed = self.is_path_allowed(old_rel_path)
        new_allowed = self.is_path_allowed(rel_path)

        if old_allowed and new_allowed and os.path.exists(old_local_path):
            self.monitor.ignore_path(old_local_path)
            self.monitor.ignore_path(new_local_path)

            dir_name = os.path.dirname(new_local_path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)

            try:
                os.rename(old_local_path, new_local_path)
                logger.info(
                    f"Moved local item (Remote move): {old_rel_path} -> {rel_path}"
                )

                old_state = self.state_manager.get_file(old_rel_path)
                old_md5 = old_state.get("md5") if old_state else None

                self.state_manager.remove_file(old_rel_path)
                self.state_manager.set_file(rel_path, file_id, old_md5)

                if mime_type == "application/vnd.google-apps.folder":
                    prefix = old_rel_path + os.sep
                    for child_path, child_data in list(
                        self.state_manager.get_all_files().items()
                    ):
                        if child_path.startswith(prefix):
                            new_child_path = rel_path + child_path[len(old_rel_path) :]
                            self.state_manager.set_file(
                                new_child_path,
                                child_data["id"],
                                child_data.get("md5"),
                            )
                            self.state_manager.remove_file(child_path)

            except OSError as e:
                logger.error(
                    f"Failed to move locally {old_local_path} to {new_local_path}: {e}"
                )
                self._delete_local(old_rel_path)
        elif old_allowed and not new_allowed:
            self._delete_local(old_rel_path)

    def _sync_recursive(self, parent_id: str, current_rel_path: str) -> None:
        """
        Recursively lists files from Drive and syncs them locally.

        Args:
            parent_id (str): The Drive ID of the folder to list.
            current_rel_path (str): The relative path of the current folder from the root.
        """
        # List remote files in this folder
        items = self.drive_ops.list_files(parent_id)

        # Track names found on remote to detect deletions later
        remote_names = set()

        for item in items:
            name = item["name"]
            item_id = item["id"]
            mime_type = item["mimeType"]
            remote_md5 = item.get("md5Checksum")

            # Sanitize filename to prevent path traversal
            name = name.replace("/", "_").replace("\\", "_")
            if name in (".", ".."):
                name = f"_{name}_"

            # Construct relative path
            if current_rel_path:
                rel_path = os.path.join(current_rel_path, name)
            else:
                rel_path = name

            # Check Selective Sync
            if not self.is_path_allowed(rel_path):
                continue

            remote_names.add(name)

            # Handle Folder
            if mime_type == "application/vnd.google-apps.folder":
                self._sync_folder(rel_path, item_id)
            # Handle File
            else:
                self._sync_file(rel_path, item_id, remote_md5)

        self._handle_deletions(current_rel_path, remote_names)

    def _handle_deletions(self, current_rel_path: str, remote_names: Set[str]) -> None:
        """
        Checks for local files that are missing remotely and deletes them if they were previously synced.

        Args:
            current_rel_path (str): The relative path of the current folder.
            remote_names (Set[str]): A set of filenames present on the remote side.
        """
        local_dir = os.path.join(self.config_manager.get_local_root(), current_rel_path)
        if os.path.exists(local_dir):
            for local_name in os.listdir(local_dir):
                if local_name not in remote_names:
                    local_rel_path = os.path.join(current_rel_path, local_name)
                    # Only delete if it was previously synced (exists in state)
                    # If not in state, it's a new local file waiting for upload -> Keep it
                    if self.state_manager.get_file(local_rel_path):
                        self._delete_local(local_rel_path)

    def _sync_folder(self, rel_path: str, folder_id: str) -> None:
        """
        Handles folder creation and recursion.

        Args:
            rel_path (str): The relative path of the folder.
            folder_id (str): The Drive ID of the folder.
        """
        local_path = os.path.join(self.config_manager.get_local_root(), rel_path)

        if not os.path.exists(local_path):
            self.monitor.ignore_path(local_path)
            os.makedirs(local_path, exist_ok=True)
            logger.info(f"Created local folder: {rel_path}")

        # Update state
        self.state_manager.set_file(rel_path, folder_id, "folder")

        # Recurse
        self._sync_recursive(folder_id, rel_path)

    def _sync_file(
        self, rel_path: str, file_id: str, remote_md5: Optional[str]
    ) -> None:
        """
        Handles file download if needed.

        Args:
            rel_path (str): The relative path of the file.
            file_id (str): The Drive ID of the file.
            remote_md5 (str, optional): The MD5 checksum of the remote file.
        """
        local_path = os.path.join(self.config_manager.get_local_root(), rel_path)

        # Conflict Resolution:
        # If local file exists but is NOT tracked in state, it's a collision.
        # Rename the local file to preserve it before downloading the remote one.
        if os.path.exists(local_path) and not self.state_manager.get_file(rel_path):
            self._resolve_conflict(local_path)

        # Check if download is required
        if self._should_download(rel_path, local_path, remote_md5):
            self.monitor.ignore_path(local_path)
            success = self.drive_ops.download_file(file_id, local_path)
            if success:
                self.state_manager.set_file(rel_path, file_id, remote_md5)

    def _should_download(
        self, rel_path: str, local_path: str, remote_md5: Optional[str]
    ) -> bool:
        """
        Decides if a file should be downloaded.

        Args:
            rel_path (str): The relative path of the file.
            local_path (str): The absolute local path of the file.
            remote_md5 (str, optional): The MD5 checksum of the remote file.

        Returns:
            bool: True if the file should be downloaded, False otherwise.
        """
        # If local file doesn't exist, download
        if not os.path.exists(local_path):
            return True

        # If state matches remote, we are up to date
        entry = self.state_manager.get_file(rel_path)
        if entry and entry.get("md5") == remote_md5:
            return False

        # If state differs (or no state), download
        return True

    def _delete_local(self, rel_path: str) -> None:
        """
        Deletes a local file or folder and updates state.

        Args:
            rel_path (str): The relative path of the item to delete.
        """
        local_path = os.path.join(self.config_manager.get_local_root(), rel_path)
        if os.path.exists(local_path):
            try:
                self.monitor.ignore_path(local_path)
                if os.path.isdir(local_path):
                    shutil.rmtree(local_path)
                else:
                    os.remove(local_path)
                logger.info(f"Deleted local item (Remote deletion): {rel_path}")
            except OSError as e:
                logger.error(f"Failed to delete {local_path}: {e}")

        # Remove from state
        self.state_manager.remove_file(rel_path)

    def _resolve_conflict(self, local_path: str) -> None:
        """
        Renames a local file to avoid overwriting it during download.

        Args:
            local_path (str): The absolute path of the local file causing conflict.
        """
        base, ext = os.path.splitext(local_path)
        timestamp = int(time.time())
        new_path = f"{base}_conflict_{timestamp}{ext}"
        self.monitor.ignore_path(local_path)
        self.monitor.ignore_path(new_path)
        os.rename(local_path, new_path)
        logger.warning(
            f"Conflict detected. Renamed local file to: {os.path.basename(new_path)}"
        )

    def start(self, interval: int = 60) -> None:
        """
        Starts the polling loop in a blocking manner.

        Args:
            interval (int): Seconds to wait between sync cycles.
        """
        # Start the Local Monitor (Up-Sync)
        self.monitor.start()

        logger.info(f"Starting Sync Engine polling loop (Interval: {interval}s)...")
        while True:
            try:
                self.sync()
            except Exception as e:
                logger.error(f"Error during sync cycle: {e}")
            time.sleep(interval)

    def stop(self) -> None:
        """
        Stops the local monitor and gracefully shuts down the sync engine components.
        """
        self.monitor.stop()
