import logging
import os
from typing import Optional
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from src.config_manager import ConfigManager
from src.state_manager import StateManager
from src.drive_ops import DriveOps

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

    def _get_relative_path(self, abs_path: str) -> str:
        """Converts an absolute path to a path relative to the local root."""
        return os.path.relpath(abs_path, self.local_root)

    def on_created(self, event):
        if event.is_directory:
            return

        rel_path = self._get_relative_path(event.src_path)
        logger.info(f"Event: Created - {rel_path}")
        # TODO: Implement upload logic (Step 4.2)

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        rel_path = self._get_relative_path(event.src_path)
        logger.info(f"Event: Modified - {rel_path}")
        # TODO: Implement upload logic (Step 4.2)

    def on_moved(self, event):
        if event.is_directory:
            return

        src_rel_path = self._get_relative_path(event.src_path)
        dest_rel_path = self._get_relative_path(event.dest_path)
        logger.info(f"Event: Moved - {src_rel_path} to {dest_rel_path}")
        # TODO: Implement move/rename logic (Step 4.3)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        rel_path = self._get_relative_path(event.src_path)
        logger.info(f"Event: Deleted - {rel_path}")
        # TODO: Implement delete logic (Step 4.4)


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
        self.config_manager = config_manager
        self.handler = LocalFileHandler(config_manager, state_manager, drive_ops)
        self.observer = Observer()

    def start(self) -> None:
        """Starts the directory monitoring."""
        path = self.config_manager.get_local_root()
        logger.info(f"Starting LocalMonitor on: {path}")
        self.observer.schedule(self.handler, path, recursive=True)
        self.observer.start()

    def stop(self) -> None:
        """Stops the directory monitoring."""
        logger.info("Stopping LocalMonitor...")
        self.observer.stop()
        self.observer.join()
