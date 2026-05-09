import unittest
from unittest.mock import patch
from src.filtering import PathFilter


class TestPathFilter(unittest.TestCase):
    def test_ignore_by_pattern(self):
        """Test that files are ignored based on glob patterns."""
        patterns = ["*.tmp", ".~*", "Thumbs.db"]
        path_filter = PathFilter(patterns)

        # Should be ignored
        self.assertTrue(path_filter.should_ignore("file.tmp", "/abs/path/file.tmp"))
        self.assertTrue(
            path_filter.should_ignore(
                "folder/another.tmp", "/abs/path/folder/another.tmp"
            )
        )
        self.assertTrue(
            path_filter.should_ignore(".~lock.file.xlsx", "/abs/path/.~lock.file.xlsx")
        )
        self.assertTrue(path_filter.should_ignore("Thumbs.db", "/abs/path/Thumbs.db"))

        # Should NOT be ignored
        self.assertFalse(path_filter.should_ignore("file.txt", "/abs/path/file.txt"))
        self.assertFalse(
            path_filter.should_ignore("temporary.txt", "/abs/path/temporary.txt")
        )

    def test_ignore_by_mime_type(self):
        """Test that Google Workspace files are ignored by MIME type."""
        path_filter = PathFilter([])  # No patterns

        # Should be ignored
        self.assertTrue(
            path_filter.should_ignore(
                "My Doc",
                "/abs/My Doc",
                mime_type="application/vnd.google-apps.document",
            )
        )
        self.assertTrue(
            path_filter.should_ignore(
                "My Sheet",
                "/abs/My Sheet",
                mime_type="application/vnd.google-apps.spreadsheet",
            )
        )

        # Should NOT be ignored
        self.assertFalse(
            path_filter.should_ignore(
                "My PDF", "/abs/My PDF", mime_type="application/pdf"
            )
        )
        self.assertFalse(
            path_filter.should_ignore(
                "My Image.jpg", "/abs/My Image.jpg", mime_type="image/jpeg"
            )
        )
        self.assertFalse(
            path_filter.should_ignore("NoMime", "/abs/NoMime", mime_type=None)
        )

    @patch("src.filtering.os.path.lexists", return_value=True)
    @patch("src.filtering.os.path.islink")
    def test_ignore_symlink(self, mock_islink, mock_lexists):
        """Test that symbolic links are ignored."""
        path_filter = PathFilter([])

        # Simulate a symlink
        mock_islink.return_value = True
        self.assertTrue(path_filter.should_ignore("a_link", "/abs/path/a_link"))
        mock_islink.assert_called_with("/abs/path/a_link")

        # Simulate a regular file
        mock_islink.return_value = False
        self.assertFalse(path_filter.should_ignore("a_file", "/abs/path/a_file"))

    def test_pattern_matches_basename(self):
        """Test that patterns only match the filename, not the full path."""
        path_filter = PathFilter(["folder*"])
        # Should not match because 'folder' is part of the path, not the filename
        self.assertFalse(
            path_filter.should_ignore("folder/file.txt", "/abs/path/folder/file.txt")
        )
        # Should match because the filename starts with 'folder'
        self.assertTrue(
            path_filter.should_ignore(
                "folder_report.csv", "/abs/path/folder_report.csv"
            )
        )
