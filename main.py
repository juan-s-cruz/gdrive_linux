import logging
import os
import signal
import sys
from typing import Any

from src.config_manager import ConfigManager
from src.drive_ops import DriveOps
from src.drive_service import DriveService
from src.state_manager import StateManager
from src.sync_engine import SyncEngine

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)


def main() -> None:
    """
    Main entry point for the Google Drive Linux Client.
    Initializes configuration, state, API connection, and starts the synchronization engine.
    """
    logger.info("Initializing Google Drive Linux Client...")

    # Define paths in user's home directory
    app_dir = os.path.expanduser("~/.gdrive_client")
    if not os.path.exists(app_dir):
        logger.info(f"Created application directory: {app_dir}")

    config_path = os.path.join(app_dir, "config.json")
    state_path = os.path.join(app_dir, "state.json")
    credentials_path = os.path.join(app_dir, "credentials.json")
    token_path = os.path.join(app_dir, "token.json")

    # 1. Initialize Configuration
    if not os.path.exists(config_path):
        logger.error(f"Configuration file not found: {config_path}")
        logger.info(f"Please create a config.json file in {app_dir}")
        sys.exit(1)

    try:
        config_manager = ConfigManager(config_path)
        logger.info("Configuration loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)

    # 2. Initialize State Manager
    try:
        state_manager = StateManager(state_path)
        logger.info("State manager initialized.")
    except Exception as e:
        logger.error(f"Failed to initialize state manager: {e}")
        sys.exit(1)

    # 3. Initialize Drive Operations (API Wrapper)
    try:
        # Initialize DriveService to handle authentication and client building
        drive_service = DriveService(credentials_path, token_path)
        drive_ops = DriveOps(drive_service.get_service())
        logger.info("Google Drive API connection established.")
    except Exception as e:
        logger.error(f"Failed to connect to Google Drive API: {e}")
        sys.exit(1)

    # 4. Initialize Synchronization Engine
    sync_engine = SyncEngine(config_manager, state_manager, drive_ops)

    # 5. Setup Signal Handling for Graceful Shutdown
    def signal_handler(sig: int, frame: Any) -> None:
        sig_name = signal.Signals(sig).name
        logger.info(f"Received signal {sig_name}. Shutting down...")
        sync_engine.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 6. Start the Sync Engine
    try:
        # This starts the LocalMonitor and enters the polling loop (blocking)
        sync_engine.start()
    except KeyboardInterrupt:
        logger.info("Interrupted by user. Shutting down...")
        sync_engine.stop()
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Unexpected error in main loop: {e}", exc_info=True)
        sync_engine.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()
