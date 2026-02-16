import json
import os


class ConfigManager:
    """
    Handles loading and validation of the configuration file.
    """

    def __init__(self, config_path="config.json"):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self):
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

    def get_local_root(self):
        return self.config["local_root_path"]

    def get_selective_sync_folders(self):
        return self.config.get("selective_sync_folders", [])


if __name__ == "__main__":
    try:
        cm = ConfigManager()
        print(f"Configuration loaded successfully.")
        print(f"Local Root: {cm.get_local_root()}")
        print(f"Selective Sync: {cm.get_selective_sync_folders()}")
    except Exception as e:
        print(f"Error loading config: {e}")
