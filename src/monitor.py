import logging
import os
import mimetypes
import threading
from threading import Timer
from typing import Optional, Dict
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from .config_manager import ConfigManager
from .state_manager import StateManager
from .drive_ops import DriveOps

logger = logging.getLogger(__name__)


class LocalFileHandler(FileSystemEventHandler):
    """
    Handles file system events (create, modify, move, delete) within the watched directory.
    Inherits from watchdog.events.FileSystemEventHandler.
    """

    def __init__(
        self,
        config_manager: ConfigManager,
        state_manager: StateManager,
        drive_ops: DriveOps,
    ):
        """
        Args:
            config_manager: Instance of ConfigManager.
            state_manager: Instance of StateManager.
            drive_ops: Instance of DriveOps.
        """
        self.config_manager = config_manager
        self.state_manager = state_manager
        self.drive_ops = drive_ops
        self.local_root = self.config_manager.get_local_root()
        self.timers: Dict[str, Timer] = {}
        self.timers_lock = threading.Lock()
        self.debounce_seconds = 1.0
        self.ignored_paths = set()
        self.ignored_extensions = {
            ".part",
            ".tmp",
            ".crdownload",
            ".swp",
            ".goutputstream",
        }

    def _should_ignore(self, path: str) -> bool:
        """Checks if a path should be ignored based on extension or explicit ignore list."""
        if path in self.ignored_paths:
            return True
        return os.path.splitext(path)[1] in self.ignored_extensions

    def _get_relative_path(self, abs_path: str) -> str:
        """Converts an absolute path to a path relative to the local root."""
        return os.path.relpath(abs_path, self.local_root)

    def _resolve_parent_id(self, rel_path: str) -> Optional[str]:
        """Finds the remote parent ID for a given relative path."""
        dirname = os.path.dirname(rel_path)
        if not dirname:
            return None

        entry = self.state_manager.get_file(dirname)
        if entry:
            return entry.get("id")
        return None

    def ignore_path(self, path: str) -> None:
        """Temporarily ignores events for a specific path."""
        self.ignored_paths.add(path)
        # Remove after delay to allow event to process (5 seconds TTL)
        Timer(5.0, self._unignore_path, args=[path]).start()

    def _unignore_path(self, path: str) -> None:
        if path in self.ignored_paths:
            self.ignored_paths.remove(path)

    def on_created(self, event: FileSystemEvent) -> None:
        """
        Called when a file or directory is created.

        Args:
            event: The event object containing data about the operation.
        """
        if self._should_ignore(event.src_path):
            return

        rel_path = self._get_relative_path(event.src_path)
        parent_id = self._resolve_parent_id(rel_path)
        name = os.path.basename(rel_path)

        if event.is_directory:
            logger.info(f"Event: Created Folder - {rel_path}")
            folder_id = self.drive_ops.create_folder(name, parent_id)
            if folder_id:
                self.state_manager.set_file(rel_path, folder_id, "folder")
            return

        logger.info(f"Event: Created File - {rel_path}")
        mime_type, _ = mimetypes.guess_type(event.src_path)
        file_meta = self.drive_ops.upload_file(
            event.src_path, name, parent_id, mime_type
        )
        if file_meta:
            self.state_manager.set_file(
                rel_path, file_meta["id"], file_meta.get("md5Checksum")
            )

    def on_modified(self, event: FileSystemEvent) -> None:
        """
        Called when a file or directory is modified.
        Debounces the event to prevent duplicate uploads.

        Args:
            event: The event object containing data about the operation.
        """
        if self._should_ignore(event.src_path):
            return

        if event.is_directory:
            return

        with self.timers_lock:
            if event.src_path in self.timers:
                self.timers[event.src_path].cancel()

            timer = Timer(self.debounce_seconds, self._process_modified, args=[event])
            self.timers[event.src_path] = timer
            timer.start()

    def _process_modified(self, event: FileSystemEvent) -> None:
        """
        Processes the modified event after the debounce timer expires.

        Args:
            event: The event object containing data about the operation.
        """
        with self.timers_lock:
            if event.src_path in self.timers:
                del self.timers[event.src_path]

        if not os.path.exists(event.src_path):
            return

        rel_path = self._get_relative_path(event.src_path)
        logger.info(f"Event: Modified - {rel_path}")

        entry = self.state_manager.get_file(rel_path)
        mime_type, _ = mimetypes.guess_type(event.src_path)

        if entry:
            file_meta = self.drive_ops.update_file(
                entry["id"], event.src_path, mime_type
            )
            if file_meta:
                self.state_manager.set_file(
                    rel_path, file_meta["id"], file_meta.get("md5Checksum")
                )
        else:
            # Treat as new upload if not in state
            parent_id = self._resolve_parent_id(rel_path)
            name = os.path.basename(rel_path)
            file_meta = self.drive_ops.upload_file(
                event.src_path, name, parent_id, mime_type
            )
            if file_meta:
                self.state_manager.set_file(
                    rel_path, file_meta["id"], file_meta.get("md5Checksum")
                )

    def on_moved(self, event: FileSystemEvent) -> None:
        """
        Called when a file or directory is moved or renamed.

        Args:
            event: The event object containing data about the operation.
        """
        if event.src_path in self.ignored_paths or self._should_ignore(event.dest_path):
            return

        if event.is_directory:
            return

        with self.timers_lock:
            if event.src_path in self.timers:
                self.timers[event.src_path].cancel()
                del self.timers[event.src_path]

        src_rel_path = self._get_relative_path(event.src_path)
        dest_rel_path = self._get_relative_path(event.dest_path)
        logger.info(f"Event: Moved - {src_rel_path} to {dest_rel_path}")

        entry = self.state_manager.get_file(src_rel_path)
        if entry:
            file_id = entry["id"]
            new_name = os.path.basename(dest_rel_path)
            new_parent_id = self._resolve_parent_id(dest_rel_path)

            self.drive_ops.move_file(file_id, new_name, new_parent_id)

            # Update state: remove old path, add new path
            self.state_manager.remove_file(src_rel_path)
            self.state_manager.set_file(dest_rel_path, file_id, entry["md5"])
        else:
            # Source not in state (e.g. was ignored temp file), treat as new upload
            parent_id = self._resolve_parent_id(dest_rel_path)
            name = os.path.basename(dest_rel_path)
            mime_type, _ = mimetypes.guess_type(event.dest_path)
            file_meta = self.drive_ops.upload_file(
                event.dest_path, name, parent_id, mime_type
            )
            if file_meta:
                self.state_manager.set_file(
                    dest_rel_path, file_meta["id"], file_meta.get("md5Checksum")
                )

    def on_deleted(self, event: FileSystemEvent) -> None:
        """
        Called when a file or directory is deleted.

        Args:
            event: The event object containing data about the operation.
        """
        if self._should_ignore(event.src_path):
            return

        if event.is_directory:
            return

        with self.timers_lock:
            if event.src_path in self.timers:
                self.timers[event.src_path].cancel()
                del self.timers[event.src_path]

        rel_path = self._get_relative_path(event.src_path)
        logger.info(f"Event: Deleted - {rel_path}")

        entry = self.state_manager.get_file(rel_path)
        if entry:
            self.drive_ops.delete_file(entry["id"])
            self.state_manager.remove_file(rel_path)

    def stop(self) -> None:
        """Cancels all pending debounce timers."""
        with self.timers_lock:
            for timer in self.timers.values():
                timer.cancel()
            self.timers.clear()


class LocalMonitor:
    """
    Manages the Watchdog Observer to monitor the local file system.
    """

    def __init__(
        self,
        config_manager: ConfigManager,
        state_manager: StateManager,
        drive_ops: DriveOps,
    ):
        """
        Args:
            config_manager: Instance of ConfigManager.
            state_manager: Instance of StateManager.
            drive_ops: Instance of DriveOps.
        """
        self.config_manager = config_manager
        self.handler = LocalFileHandler(config_manager, state_manager, drive_ops)
        self.observer = Observer()

    def start(self) -> None:
        """Starts the directory monitoring."""
        path = self.config_manager.get_local_root()
        logger.info(f"Starting LocalMonitor on: {path}")
        self.observer.schedule(self.handler, path, recursive=True)
        self.observer.start()

    def ignore_path(self, path: str) -> None:
        """Temporarily ignores events for a specific path."""
        self.handler.ignore_path(path)

    def stop(self) -> None:
        """Stops the directory monitoring."""
        logger.info("Stopping LocalMonitor...")
        self.handler.stop()
        self.observer.stop()
        self.observer.join()
