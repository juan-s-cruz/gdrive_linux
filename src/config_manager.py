import json
import os
import threading
from typing import List, Dict, Any


class ConfigManager:
    """
    Handles loading and validation of the configuration file.
    """

    def __init__(self, config_path: str = "config.json"):
        """
        Initializes the ConfigManager.

        Args:
            config_path (str): Path to the configuration JSON file.
        """
        self.config_path = config_path
        self.lock = threading.Lock()
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """
        Loads and validates the configuration from the JSON file.

        Returns:
            dict: The configuration dictionary.

        Raises:
            FileNotFoundError: If the config file does not exist.
            ValueError: If required keys are missing.
        """
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        with open(self.config_path, "r") as f:
            config = json.load(f)

        # Validate and expand path
        if "local_root_path" not in config:
            raise ValueError("Missing 'local_root_path' in config")

        # Expand ~ to full user path and resolve absolute path
        expanded_path = os.path.expanduser(config["local_root_path"])
        config["local_root_path"] = os.path.abspath(expanded_path)

        # Create the directory if it doesn't exist
        if not os.path.exists(config["local_root_path"]):
            os.makedirs(config["local_root_path"])

        # Restrict permissions to the owner to prevent local data leaks
        os.chmod(config["local_root_path"], 0o700)

        # Ensure the loaded configuration is free of duplicates and redundant children
        if "selective_sync_folders" in config:
            config["selective_sync_folders"] = self._clean_folders(
                config["selective_sync_folders"]
            )

        return config

    def _save_config(self) -> None:
        """
        Writes the current configuration dictionary back to the JSON file securely.
        Maintains 0o600 restricted permissions. Protected by the instance lock.
        """
        with self.lock:
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            mode = 0o600
            fd = os.open(self.config_path, flags, mode)
            with os.fdopen(fd, "w") as f:
                json.dump(self.config, f, indent=4)

    @staticmethod
    def _clean_folders(folders: List[str]) -> List[str]:
        """
        Removes exact duplicates and redundant child paths from a list of folders.
        """
        if not folders:
            return []

        # Sort paths so parents always precede their children
        unique_folders = sorted(list(set(folders)))
        cleaned = []
        for folder in unique_folders:
            # A folder is redundant if it is a child of any already processed (parent) folder
            if not any(folder.startswith(parent + os.sep) for parent in cleaned):
                cleaned.append(folder)
        return cleaned

    def add_sync_folder(self, path: str) -> None:
        """
        Safely appends a new folder path to the selective sync configuration.
        Uses subset-filtering to prevent cluttering the config file with redundant paths.
        """
        should_save = False
        with self.lock:
            folders = self.config.get("selective_sync_folders", [])
            original_folders = list(folders)

            new_folders = list(folders)
            new_folders.append(path)
            cleaned = self._clean_folders(new_folders)

            if cleaned != original_folders:
                self.config["selective_sync_folders"] = cleaned
                should_save = True

        if should_save:
            self._save_config()

    def rename_sync_folder(self, old_path: str, new_path: str) -> None:
        """
        Updates the tracked paths in the selective sync configuration when a directory is renamed locally.
        """
        should_save = False
        with self.lock:
            folders = self.config.get("selective_sync_folders", [])
            original_folders = list(folders)
            new_folders = []
            for folder in folders:
                # Update exact matches
                if folder == old_path:
                    new_folders.append(new_path)
                # Prefix replacement logic: Handle nested tracked folders
                # (e.g., updating 'old_parent/child' to 'new_parent/child')
                elif folder.startswith(old_path + os.sep):
                    updated_folder = new_path + folder[len(old_path) :]
                    new_folders.append(updated_folder)
                else:
                    new_folders.append(folder)

            cleaned = self._clean_folders(new_folders)
            if cleaned != original_folders:
                self.config["selective_sync_folders"] = cleaned
                should_save = True

        if should_save:
            self._save_config()

    def get_local_root(self) -> str:
        """Returns the absolute path to the local root directory."""
        with self.lock:
            return self.config["local_root_path"]

    def get_selective_sync_folders(self) -> List[str]:
        """Returns the list of folders enabled for selective sync."""
        with self.lock:
            return self.config.get("selective_sync_folders", [])

    def get_ignore_patterns(self) -> List[str]:
        """
        Returns the list of file patterns to ignore.
        Provides a default list of common temporary/system files if not specified.
        """
        with self.lock:
            return self.config.get(
                "ignore_patterns",
                [
                    "*.tmp",
                    "*.part",
                    "*.swp",
                    ".*.swp",
                    "~$*",
                    ".DS_Store",
                    "Thumbs.db",
                    "*.crdownload",
                ],
            )


if __name__ == "__main__":
    try:
        cm = ConfigManager()
        print(f"Configuration loaded successfully.")
        print(f"Local Root: {cm.get_local_root()}")
        print(f"Selective Sync: {cm.get_selective_sync_folders()}")
    except Exception as e:
        print(f"Error loading config: {e}")
