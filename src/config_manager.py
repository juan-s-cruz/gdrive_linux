import json
import os
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

        return config

    def get_local_root(self) -> str:
        """Returns the absolute path to the local root directory."""
        return self.config["local_root_path"]

    def get_selective_sync_folders(self) -> List[str]:
        """Returns the list of folders enabled for selective sync."""
        return self.config.get("selective_sync_folders", [])


if __name__ == "__main__":
    try:
        cm = ConfigManager()
        print(f"Configuration loaded successfully.")
        print(f"Local Root: {cm.get_local_root()}")
        print(f"Selective Sync: {cm.get_selective_sync_folders()}")
    except Exception as e:
        print(f"Error loading config: {e}")
