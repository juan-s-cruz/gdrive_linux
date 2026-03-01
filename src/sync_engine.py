import logging
import os
import time
from typing import List, Optional

from .config_manager import ConfigManager
from .state_manager import StateManager
from .drive_ops import DriveOps

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
    ):
        """
        Initializes the SyncEngine.

        Args:
            config_manager: Instance of ConfigManager.
            state_manager: Instance of StateManager.
            drive_ops: Instance of DriveOps.
        """
        self.config_manager = config_manager
        self.state_manager = state_manager
        self.drive_ops = drive_ops

        # Load and normalize selective sync folders
        self.selective_sync_folders = self._load_selective_sync_rules()

    def _load_selective_sync_rules(self) -> List[str]:
        """
        Loads selective sync folders from config and normalizes paths.
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
            # Case 1: Path is the allowed folder or inside it
            # e.g. folder="Photos", path="Photos/2023.jpg"
            if rel_path == folder or rel_path.startswith(folder + os.sep):
                return True

            # Case 2: Path is a parent of the allowed folder (needed to reach the child)
            # e.g. folder="Photos/2023", path="Photos"
            if folder.startswith(rel_path + os.sep):
                return True

        return False

    def sync(self):
        """
        Main entry point for the synchronization process.
        Performs a one-way sync from Drive to Local (Down-Sync).
        """
        logger.info("Starting Down-Sync cycle...")
        # Start syncing from the root folder
        self._sync_recursive("root", "")
        logger.info("Down-Sync cycle complete.")

    def _sync_recursive(self, parent_id: str, current_rel_path: str):
        """
        Recursively lists files from Drive and syncs them locally.
        """
        # List remote files in this folder
        items = self.drive_ops.list_files(parent_id)

        for item in items:
            name = item["name"]
            item_id = item["id"]
            mime_type = item["mimeType"]
            remote_md5 = item.get("md5Checksum")

            # Construct relative path
            if current_rel_path:
                rel_path = os.path.join(current_rel_path, name)
            else:
                rel_path = name

            # Check Selective Sync
            if not self.is_path_allowed(rel_path):
                continue

            # Handle Folder
            if mime_type == "application/vnd.google-apps.folder":
                self._sync_folder(rel_path, item_id)
            # Handle File
            else:
                self._sync_file(rel_path, item_id, remote_md5)

    def _sync_folder(self, rel_path: str, folder_id: str):
        """Handles folder creation and recursion."""
        local_path = os.path.join(self.config_manager.get_local_root(), rel_path)

        if not os.path.exists(local_path):
            os.makedirs(local_path, exist_ok=True)
            logger.info(f"Created local folder: {rel_path}")

        # Update state
        self.state_manager.set_file(rel_path, folder_id, "folder")

        # Recurse
        self._sync_recursive(folder_id, rel_path)

    def _sync_file(self, rel_path: str, file_id: str, remote_md5: str):
        """Handles file download if needed."""
        local_path = os.path.join(self.config_manager.get_local_root(), rel_path)

        # Check if download is required
        if self._should_download(rel_path, local_path, remote_md5):
            success = self.drive_ops.download_file(file_id, local_path)
            if success:
                self.state_manager.set_file(rel_path, file_id, remote_md5)

    def _should_download(self, rel_path: str, local_path: str, remote_md5: str) -> bool:
        """Decides if a file should be downloaded."""
        # If local file doesn't exist, download
        if not os.path.exists(local_path):
            return True

        # If state matches remote, we are up to date
        entry = self.state_manager.get_file(rel_path)
        if entry and entry.get("md5") == remote_md5:
            return False

        # If state differs (or no state), download
        return True

    def start(self, interval: int = 60):
        """
        Starts the polling loop in a blocking manner.

        Args:
            interval (int): Seconds to wait between sync cycles.
        """
        logger.info(f"Starting Sync Engine polling loop (Interval: {interval}s)...")
        while True:
            try:
                self.sync()
            except Exception as e:
                logger.error(f"Error during sync cycle: {e}")
            time.sleep(interval)
