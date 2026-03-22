import hashlib
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


def _calculate_local_md5(file_path: str) -> Optional[str]:
    """
    Calculates the MD5 checksum of a local file in chunks.
    Returns None if the file does not exist or cannot be read.

    Args:
        file_path (str): The absolute path to the local file.

    Returns:
        Optional[str]: The computed MD5 hex digest, or None if the file is inaccessible.
    """
    if not os.path.exists(file_path):
        return None
    try:
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except OSError as e:
        logger.error(f"Error calculating MD5 for {file_path}: {e}")
        return None


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
        self.selective_sync_folders = self._load_selective_sync_rules()
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

        if rel_path == ".":
            return True

        for folder in self.selective_sync_folders:
            if folder == ".":
                return True
            if rel_path == folder or rel_path.startswith(folder + os.sep):
                return True
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

        is_tracked = self.state_manager.get_path_by_id(file_id) is not None

        rel_path = self._construct_relative_path(name, parents)
        was_moved = self._handle_remote_move(file_id, rel_path, mime_type)

        if not self.is_path_allowed(rel_path):
            return

        if mime_type == "application/vnd.google-apps.folder":
            if not is_tracked and not was_moved:
                self._sync_folder(rel_path, file_id)
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
    ) -> bool:
        """
        Handles local file moving and state updating if a file was moved remotely.

        Compares the new relative path to the previously tracked path in the state.
        If they differ, performs a local file rename and updates all relevant
        state mappings to prevent unnecessary re-downloads.

        Args:
            file_id (str): The Google Drive ID of the file or folder.
            rel_path (str): The newly computed relative path.
            mime_type (Optional[str]): The MIME type of the item, used to check for folders.

        Returns:
            bool: True if a local move successfully occurred and state was mapped, False otherwise.
        """
        old_rel_path = self.state_manager.get_path_by_id(file_id)
        if not old_rel_path or old_rel_path == rel_path:
            return False

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
                return True

            except OSError as e:
                logger.error(
                    f"Failed to move locally {old_local_path} to {new_local_path}: {e}"
                )
                self._delete_local(old_rel_path)
        elif old_allowed and not new_allowed:
            self._delete_local(old_rel_path)

        return False

    def _sync_recursive(self, parent_id: str, current_rel_path: str) -> None:
        """
        Recursively lists files from Drive and syncs them locally.

        Args:
            parent_id (str): The Drive ID of the folder to list.
            current_rel_path (str): The relative path of the current folder from the root.
        """
        items = self.drive_ops.list_files(parent_id)
        remote_names = set()

        for item in items:
            name = item["name"]
            item_id = item["id"]
            mime_type = item["mimeType"]
            remote_md5 = item.get("md5Checksum")

            name = name.replace("/", "_").replace("\\", "_")
            if name in (".", ".."):
                name = f"_{name}_"

            if current_rel_path:
                rel_path = os.path.join(current_rel_path, name)
            else:
                rel_path = name

            if not self.is_path_allowed(rel_path):
                continue

            remote_names.add(name)

            if mime_type == "application/vnd.google-apps.folder":
                self._sync_folder(rel_path, item_id)
            else:
                self._sync_file(rel_path, item_id, remote_md5)

        self._handle_deletions(current_rel_path, remote_names)

    def _handle_deletions(self, current_rel_path: str, remote_names: Set[str]) -> None:
        """
        Checks for local files that are missing remotely and deletes them if they were previously synced.
        New local files waiting to be uploaded (untracked in state) are ignored and safely kept.

        Args:
            current_rel_path (str): The relative path of the current folder.
            remote_names (Set[str]): A set of filenames present on the remote side.
        """
        local_dir = os.path.join(self.config_manager.get_local_root(), current_rel_path)
        if os.path.exists(local_dir):
            for local_name in os.listdir(local_dir):
                if local_name not in remote_names:
                    local_rel_path = os.path.join(current_rel_path, local_name)
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

        self.state_manager.set_file(rel_path, folder_id, "folder")
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

        if os.path.exists(local_path) and not self.state_manager.get_file(rel_path):
            self._resolve_conflict(local_path)

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
        if not os.path.exists(local_path):
            return True

        entry = self.state_manager.get_file(rel_path)
        if entry and entry.get("md5") == remote_md5:
            return False

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

        self.state_manager.remove_file(rel_path)

        prefix = rel_path + os.sep
        for child_path in list(self.state_manager.get_all_files().keys()):
            if child_path.startswith(prefix):
                self.state_manager.remove_file(child_path)

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

    def _get_remote_md5(self, file_id: str) -> Optional[str]:
        """
        Fetches the current remote MD5 checksum for a given file ID.

        Args:
            file_id (str): The Google Drive file ID.

        Returns:
            Optional[str]: The MD5 checksum if available, None otherwise.
        """
        metadata = self.drive_ops.get_metadata(file_id)
        if metadata and not metadata.get("trashed"):
            return metadata.get("md5Checksum")
        return None

    def _write_startup_report(self, report_dict: Dict[str, List[str]]) -> None:
        """
        Writes the startup scan report to a dedicated log file.

        Args:
            report_dict (Dict[str, List[str]]): The tracking dictionary containing categorized paths.
        """
        report_logger = logging.getLogger("startup_report")
        report_logger.setLevel(logging.INFO)
        report_logger.propagate = False

        if report_logger.hasHandlers():
            report_logger.handlers.clear()

        log_dir = os.path.expanduser("~/.gdrive_client")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "startup_report.log")

        file_handler = logging.FileHandler(log_file, mode="w")
        formatter = logging.Formatter("%(asctime)s - %(message)s")
        file_handler.setFormatter(formatter)
        report_logger.addHandler(file_handler)

        report_logger.info("=== Startup Scan Report ===")
        for category, paths in report_dict.items():
            report_logger.info(f"{category.upper()}: {len(paths)} items")
            for path in paths:
                report_logger.info(f"  - {path}")
        report_logger.info("===========================")

    def _resolve_remote_path(self, rel_path: str) -> Optional[str]:
        """
        Resolves a local relative path to its corresponding Google Drive folder ID.

        Args:
            rel_path (str): The relative path to resolve.

        Returns:
            Optional[str]: The Google Drive file ID if found, None otherwise.
        """
        if not rel_path or rel_path == ".":
            return "root"

        parts = rel_path.split(os.sep)
        current_parent_id = "root"

        for part in parts:
            if not part:
                continue

            items = self.drive_ops.list_files(current_parent_id)
            found_id = None
            for item in items:
                if (
                    item.get("name") == part
                    and item.get("mimeType") == "application/vnd.google-apps.folder"
                    and not item.get("trashed")
                ):
                    found_id = item.get("id")
                    break

            if not found_id:
                return None
            current_parent_id = found_id

        return current_parent_id

    def _process_config_changes(self) -> None:
        """
        Detects changes in selective sync rules, triggering targeted down-syncs for
        new folders and local deletions for removed folders.
        """
        logger.info("Evaluating configuration for selective sync changes...")

        if not hasattr(self.state_manager, "get_selective_sync_rules"):
            logger.debug("StateManager does not support rule tracking yet.")
            return

        previous_rules = self.state_manager.get_selective_sync_rules()
        current_rules = self.selective_sync_folders

        if previous_rules is None:
            # If no start page token exists, it's a fresh install; _sync_recursive will handle downloads.
            if self.state_manager.get_start_page_token() is None:
                if hasattr(self.state_manager, "set_selective_sync_rules"):
                    self.state_manager.set_selective_sync_rules(current_rules)
                return
            else:
                # Upgrade scenario: state exists but rules weren't tracked yet.
                # Default to empty list so current rules evaluate as "new".
                previous_rules = []

        previous_set = set(previous_rules)
        current_set = set(current_rules)

        removed_folders = previous_set - current_set
        new_folders = current_set - previous_set

        for folder in removed_folders:
            logger.info(
                f"Selective sync folder removed from config: {folder}. Purging local copy."
            )
            self._delete_local(folder)

        for folder in new_folders:
            logger.info(
                f"New selective sync folder detected: {folder}. Resolving remote path..."
            )
            folder_id = self._resolve_remote_path(folder)
            if folder_id:
                self._sync_folder(folder, folder_id)
            else:
                logger.warning(
                    f"Could not resolve remote path for {folder}. It may not exist on Drive."
                )

        if hasattr(self.state_manager, "set_selective_sync_rules"):
            self.state_manager.set_selective_sync_rules(current_rules)

    def scan_local_changes(self) -> None:
        """
        Scans the local directory for offline changes (creations, modifications)
        and reconciles them with the remote state before starting the real-time monitor.

        Reconciliation phases executed:
        1. Traverses the local file system (pruning ignored paths via selective sync).
        2. Evaluates local files against state to push new creations or modified files.
        3. Identifies missing local files to conservatively restore from Drive.
        4. Detects and resolves conflicts where files changed on both sides.
        """
        logger.info("Starting robust initial startup scan...")

        report = {
            "uploaded": [],
            "updated_remote": [],
            "restored_local": [],
            "conflicts": [],
            "deleted_locally": [],
        }
        seen_local_paths: Set[str] = set()
        local_root = self.config_manager.get_local_root()

        for root, dirs, files in os.walk(local_root, topdown=True):
            rel_dir = os.path.relpath(root, local_root)
            if rel_dir == ".":
                rel_dir = ""

            dirs[:] = [
                d
                for d in dirs
                if self.is_path_allowed(os.path.join(rel_dir, d) if rel_dir else d)
            ]

            for file_name in files:
                rel_path = os.path.join(rel_dir, file_name) if rel_dir else file_name

                if not self.is_path_allowed(rel_path):
                    continue

                seen_local_paths.add(rel_path)

                local_path = os.path.join(local_root, rel_path)
                state_entry = self.state_manager.get_file(rel_path)

                if not state_entry:
                    parent_dir = os.path.dirname(rel_path)
                    parent_id = "root"
                    if parent_dir:
                        p_state = self.state_manager.get_file(parent_dir)
                        if p_state:
                            parent_id = p_state.get("id", "root")

                    try:
                        upload_result = self.drive_ops.upload_file(
                            local_path, file_name, parent_id
                        )
                        if upload_result and "id" in upload_result:
                            file_id = upload_result.get("id")
                            local_md5 = _calculate_local_md5(local_path)
                            self.state_manager.set_file(rel_path, file_id, local_md5)
                            report["uploaded"].append(rel_path)
                    except Exception as e:
                        logger.error(
                            f"Failed to upload new file {rel_path} during scan: {e}"
                        )

                else:
                    local_md5 = _calculate_local_md5(local_path)
                    state_md5 = state_entry.get("md5")
                    file_id = state_entry.get("id")

                    if local_md5 and local_md5 != state_md5:
                        remote_md5 = self._get_remote_md5(file_id)

                        if remote_md5 == state_md5 or remote_md5 is None:
                            try:
                                self.drive_ops.update_file(file_id, local_path)
                                self.state_manager.set_file(
                                    rel_path, file_id, local_md5
                                )
                                report["updated_remote"].append(rel_path)
                            except Exception as e:
                                logger.error(
                                    f"Failed to update remote file {rel_path} during scan: {e}"
                                )
                        elif remote_md5 != state_md5:
                            self._resolve_conflict(local_path)
                            success = self.drive_ops.download_file(file_id, local_path)
                            if success:
                                self.state_manager.set_file(
                                    rel_path, file_id, remote_md5
                                )
                            report["conflicts"].append(rel_path)

        all_tracked_paths = set(self.state_manager.get_all_files().keys())
        missing_local_paths = all_tracked_paths - seen_local_paths

        for rel_path in missing_local_paths:
            if not self.is_path_allowed(rel_path):
                continue

            state_entry = self.state_manager.get_file(rel_path)
            if not state_entry:
                continue

            file_id = state_entry.get("id")
            local_path = os.path.join(local_root, rel_path)

            metadata = self.drive_ops.get_metadata(file_id)
            if metadata and not metadata.get("trashed"):
                if metadata.get("mimeType") == "application/vnd.google-apps.folder":
                    os.makedirs(local_path, exist_ok=True)
                    report["restored_local"].append(rel_path)
                else:
                    success = self.drive_ops.download_file(file_id, local_path)
                    if success:
                        remote_md5 = metadata.get("md5Checksum")
                        self.state_manager.set_file(rel_path, file_id, remote_md5)
                        report["restored_local"].append(rel_path)
            else:
                self.state_manager.remove_file(rel_path)
                report["deleted_locally"].append(rel_path)

        self._write_startup_report(report)
        logger.info(
            "Robust initial startup scan complete. See startup_report.log for details."
        )

    def start(self, interval: int = 60) -> None:
        """
        Starts the polling loop in a blocking manner.

        Args:
            interval (int): Seconds to wait between sync cycles.
        """
        self._process_config_changes()
        self.scan_local_changes()

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
