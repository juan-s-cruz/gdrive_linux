import logging
import os
import shutil
import time
from typing import List, Optional, Set

from .config_manager import ConfigManager
from .state_manager import StateManager
from .drive_ops import DriveOps
from .monitor import LocalMonitor

logger = logging.getLogger(__name__)


class SyncEngine:
    """
    Core logic for synchronization, including selective sync filtering,
    polling coordination, and conflict resolution.
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
        # Start syncing from the root folder
        self._sync_recursive("root", "")
        logger.info("Down-Sync cycle complete.")

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
        """Stops the local monitor."""
        self.monitor.stop()
