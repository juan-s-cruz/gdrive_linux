import fnmatch
import logging
import os
from typing import List, Optional, Set

logger = logging.getLogger(__name__)

# Google Workspace MIME types that do not have downloadable content
GOOGLE_MIME_TYPES: Set[str] = {
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.spreadsheet",
    "application/vnd.google-apps.presentation",
    "application/vnd.google-apps.form",
    "application/vnd.google-apps.site",
    "application/vnd.google-apps.map",
    "application/vnd.google-apps.drawing",
    "application/vnd.google-apps.jam",
    "application/vnd.google-apps.script",
}


class PathFilter:
    """
    A centralized utility for filtering file paths based on various criteria,
    including glob patterns, MIME types, and file system attributes like symlinks.
    """

    def __init__(self, ignore_patterns: List[str]):
        """
        Initializes the PathFilter.

        Args:
            ignore_patterns (List[str]): A list of glob-style patterns to ignore.
        """
        self.ignore_patterns = ignore_patterns
        logger.debug(f"PathFilter initialized with patterns: {self.ignore_patterns}")

    def should_ignore(
        self, rel_path: str, abs_path: str, mime_type: Optional[str] = None
    ) -> bool:
        """
        Determines if a file or path should be ignored.

        Args:
            rel_path (str): The relative path of the file/folder.
            abs_path (str): The absolute path of the file/folder.
            mime_type (Optional[str]): The MIME type of the file, if known.

        Returns:
            bool: True if the path should be ignored, False otherwise.
        """
        if mime_type and mime_type in GOOGLE_MIME_TYPES:
            logger.debug(
                f"Ignoring '{rel_path}' due to Google Workspace MIME type: {mime_type}"
            )
            return True

        filename = os.path.basename(rel_path)
        for pattern in self.ignore_patterns:
            if fnmatch.fnmatch(filename, pattern):
                logger.debug(f"Ignoring '{rel_path}' due to pattern match: {pattern}")
                return True

        if os.path.lexists(abs_path) and os.path.islink(abs_path):
            logger.debug(f"Ignoring '{rel_path}' because it is a symbolic link.")
            return True

        return False
