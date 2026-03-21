import os
import json
import pytest

from src.state_manager import StateManager

TEST_STATE_FILE = "test_state_manager.json"


@pytest.fixture
def state_manager():
    """
    Pytest fixture to provide a clean StateManager instance for each test.
    Handles setup and teardown of the test state file.
    """
    # Setup: ensure no old state file exists
    if os.path.exists(TEST_STATE_FILE):
        os.remove(TEST_STATE_FILE)

    sm = StateManager(TEST_STATE_FILE)
    yield sm  # Provide the instance to the test

    # Teardown: clean up the state file after the test
    if os.path.exists(TEST_STATE_FILE):
        os.remove(TEST_STATE_FILE)


def test_initialization_no_file(state_manager):
    """Tests that StateManager initializes with a default empty state."""
    assert state_manager.get_all_files() == {}
    assert state_manager.get_start_page_token() is None


def test_set_and_get_file(state_manager):
    """Tests setting and retrieving file metadata."""
    state_manager.set_file("folder/test.txt", "12345", "abcde")
    assert state_manager.get_file("folder/test.txt") == {"id": "12345", "md5": "abcde"}


def test_set_and_get_token(state_manager):
    """Tests setting and retrieving the start page token."""
    state_manager.set_start_page_token("token_98765")
    assert state_manager.get_start_page_token() == "token_98765"


def test_reverse_lookup(state_manager):
    """Tests the reverse lookup from file ID to path."""
    state_manager.set_file("docs/report.pdf", "id_report", "md5_report")
    assert state_manager.get_path_by_id("id_report") == "docs/report.pdf"


def test_remove_file(state_manager):
    """Tests that removing a file also removes it from the reverse lookup."""
    state_manager.set_file("folder/test.txt", "12345", "abcde")
    assert state_manager.get_path_by_id("12345") == "folder/test.txt"

    state_manager.remove_file("folder/test.txt")
    assert state_manager.get_file("folder/test.txt") is None
    assert state_manager.get_path_by_id("12345") is None


def test_migration_from_old_format():
    """Tests that StateManager correctly migrates an old, flat state file."""
    old_state = {"file.txt": {"id": "id1", "md5": "md5_1"}}
    with open(TEST_STATE_FILE, "w") as f:
        json.dump(old_state, f)
    try:
        sm = StateManager(TEST_STATE_FILE)
        assert sm.get_all_files() == old_state
        assert sm.get_start_page_token() is None
        assert sm.get_path_by_id("id1") == "file.txt"
    finally:
        if os.path.exists(TEST_STATE_FILE):
            os.remove(TEST_STATE_FILE)


def test_load_state_corrupt_json():
    """Tests that a corrupted state file safely falls back to a default empty state."""
    with open(TEST_STATE_FILE, "w") as f:
        f.write("{invalid_json_missing_quotes: true")

    # Initialize a new instance to trigger load
    sm = StateManager(TEST_STATE_FILE)
    assert sm.get_all_files() == {}
    assert sm.get_start_page_token() is None


def test_load_state_partial_keys():
    """Tests migration when state has 'meta' but is missing 'files'."""
    with open(TEST_STATE_FILE, "w") as f:
        json.dump({"meta": {"startPageToken": "token123"}}, f)

    sm = StateManager(TEST_STATE_FILE)
    assert sm.get_all_files() == {}
    assert sm.get_start_page_token() == "token123"


def test_save_state_io_error(monkeypatch, state_manager, caplog):
    """Tests that IOErrors during save are caught and logged."""

    def mock_open(*args, **kwargs):
        raise IOError("Mock permission denied")

    # Intercept built-in open function
    monkeypatch.setattr("builtins.open", mock_open)

    # Trigger a save. It shouldn't crash, but it should log the error.
    state_manager.save_state()

    # Verify the error was logged via the logger
    assert "Error saving state: Mock permission denied" in caplog.text


def test_explicit_save_state(state_manager):
    """Tests the public save_state method directly."""
    # Manipulate inner state directly to bypass set_file's auto-save
    state_manager.state["meta"]["startPageToken"] = "manual_token"
    state_manager.save_state()

    # Reload from disk to verify
    sm2 = StateManager(TEST_STATE_FILE)
    assert sm2.get_start_page_token() == "manual_token"
