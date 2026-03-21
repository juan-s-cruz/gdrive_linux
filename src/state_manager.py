import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class StateManager:
    """
    Manages the local state of files, mapping local paths to remote IDs and checksums.
    Thread-safe to allow concurrent access from Monitor and Poller.
    """

    def __init__(self, state_path: str = "state.json"):
        """
        Initializes the StateManager.

        Args:
            state_path (str): Path to the JSON file storing the state.
        """
        self.state_path = state_path
        self.lock = threading.Lock()
        self.state = self._load_state()

        # Initialize in-memory reverse lookup map (file_id -> relative_path)
        self.id_to_path: Dict[str, str] = {}
        for path, data in self.state["files"].items():
            if "id" in data:
                self.id_to_path[data["id"]] = path

    def _load_state(self) -> Dict[str, Any]:
        """
        Loads the state from the JSON file and migrates old formats if necessary.

        Returns:
            dict: The loaded state dictionary containing 'meta' and 'files' keys.
        """
        default_state = {"meta": {}, "files": {}}
        if not os.path.exists(self.state_path):
            return default_state
        try:
            with open(self.state_path, "r") as f:
                raw_state = json.load(f)

                # Migrate old flat structure if needed
                if "files" not in raw_state and "meta" not in raw_state:
                    return {"meta": {}, "files": raw_state}

                # Ensure keys exist for partially formed state
                if "meta" not in raw_state:
                    raw_state["meta"] = {}
                if "files" not in raw_state:
                    raw_state["files"] = {}

                return raw_state
        except (json.JSONDecodeError, IOError):
            return default_state

    def save_state(self) -> None:
        """Persists the current state to disk."""
        with self.lock:
            self._save_state_unsafe()

    def _save_state_unsafe(self) -> None:
        """Internal helper to save state without re-acquiring lock."""
        try:
            with open(self.state_path, "w") as f:
                json.dump(self.state, f, indent=4)
        except IOError as e:
            logger.error(f"Error saving state: {e}")

    def get_file(self, relative_path: str) -> Optional[Dict[str, str]]:
        """Returns the metadata for a given file path."""
        with self.lock:
            return self.state["files"].get(relative_path)

    def set_file(self, relative_path: str, file_id: str, md5: Optional[str]) -> None:
        """Updates or adds a file to the state."""
        with self.lock:
            self.state["files"][relative_path] = {"id": file_id, "md5": md5}
            self.id_to_path[file_id] = relative_path
            self._save_state_unsafe()

    def remove_file(self, relative_path: str) -> None:
        """Removes a file from the state."""
        with self.lock:
            if relative_path in self.state["files"]:
                file_id = self.state["files"][relative_path].get("id")
                del self.state["files"][relative_path]
                if file_id and file_id in self.id_to_path:
                    del self.id_to_path[file_id]
                self._save_state_unsafe()

    def get_all_files(self) -> Dict[str, Dict[str, str]]:
        """Returns a copy of the entire state."""
        with self.lock:
            return self.state["files"].copy()

    def get_start_page_token(self) -> Optional[str]:
        """Retrieves the saved start page token for the Changes API."""
        with self.lock:
            return self.state["meta"].get("startPageToken")

    def set_start_page_token(self, token: str) -> None:
        """Saves the start page token for the Changes API."""
        with self.lock:
            self.state["meta"]["startPageToken"] = token
            self._save_state_unsafe()

    def get_path_by_id(self, file_id: str) -> Optional[str]:
        """Returns the local relative path for a given remote file ID."""
        with self.lock:
            return self.id_to_path.get(file_id)

    def get_selective_sync_rules(self) -> Optional[List[str]]:
        """Retrieves the last known selective sync configuration from the meta block."""
        with self.lock:
            return self.state["meta"].get("selective_sync_folders")

    def set_selective_sync_rules(self, rules: List[str]) -> None:
        """Saves the current selective sync configuration to the meta block."""
        with self.lock:
            self.state["meta"]["selective_sync_folders"] = rules
            self._save_state_unsafe()
