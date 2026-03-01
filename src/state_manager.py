import json
import os
import threading
from typing import Dict, Optional


class StateManager:
    """
    Manages the local state of files, mapping local paths to remote IDs and checksums.
    Thread-safe to allow concurrent access from Monitor and Poller.
    """

    def __init__(self, state_path: str = "state.json"):
        self.state_path = state_path
        self.lock = threading.Lock()
        self.state = self._load_state()

    def _load_state(self) -> Dict[str, Dict[str, str]]:
        if not os.path.exists(self.state_path):
            return {}
        try:
            with open(self.state_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            # Return empty state if file is corrupted or unreadable
            return {}

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
            print(f"Error saving state: {e}")

    def get_file(self, relative_path: str) -> Optional[Dict[str, str]]:
        """Returns the metadata for a given file path."""
        with self.lock:
            return self.state.get(relative_path)

    def set_file(self, relative_path: str, file_id: str, md5: Optional[str]) -> None:
        """Updates or adds a file to the state."""
        with self.lock:
            self.state[relative_path] = {"id": file_id, "md5": md5}
            self._save_state_unsafe()

    def remove_file(self, relative_path: str) -> None:
        """Removes a file from the state."""
        with self.lock:
            if relative_path in self.state:
                del self.state[relative_path]
                self._save_state_unsafe()

    def get_all_files(self) -> Dict[str, Dict[str, str]]:
        """Returns a copy of the entire state."""
        with self.lock:
            return self.state.copy()


if __name__ == "__main__":
    # Simple test
    sm = StateManager("test_state.json")
    sm.set_file("folder/test.txt", "12345", "abcde")
    print(f"Retrieved: {sm.get_file('folder/test.txt')}")
    sm.remove_file("folder/test.txt")
    if os.path.exists("test_state.json"):
        os.remove("test_state.json")
    print("State Manager test complete.")
