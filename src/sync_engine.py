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
