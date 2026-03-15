# Google Drive Linux Client

This repository contains a minimal Google Drive client for Linux designed to keep a local directory in sync with your cloud storage.

## Strategy

We utilize **Python** to build a **continuously running daemon**. This background process ensures that changes are detected and propagated as they happen, rather than relying on manual execution.

## Functionality

*   **Two-way Sync**: Keeps local and remote folders identical.
*   **Uploads**: Automatically uploads files created or modified locally.
*   **Downloads**: Fetches new or updated files from Google Drive.
*   **Authentication**: Secure access using Google OAuth2.
*   **Selective Sync**: Ability to specify which remote folders should be synced locally, ignoring others.

## Installation & Usage

### 1. Install via Virtual Environment (Recommended)

To avoid polluting your system-wide Python environment, it is highly recommended to install the client in an isolated virtual environment.

```bash
# Navigate to the project root
cd gdrive_linux

# Create a virtual environment
python3 -m venv venv

# Activate the environment
source venv/bin/activate

# Install the package
pip install .
```

### 2. Setup Configuration

The application stores configuration and credentials in `~/.gdrive_client`.

1.  **Create the directory**:
    ```bash
    mkdir -p ~/.gdrive_client
    ```

2.  **Credentials**: Download your OAuth 2.0 Client ID JSON from Google Cloud Console, rename it to `credentials.json`, and move it to `~/.gdrive_client/`.

3.  **Config**: Create `~/.gdrive_client/config.json`:
    ```json
    {
        "local_root_path": "~/GoogleDrive",
        "selective_sync_folders": []
    }
    ```

### 3. Run

Start the daemon using the installed console script:

```bash
gdrive-client
```

On the first run, it will open a browser to authenticate.

You may want to create a service on boot or send the job to the background.

## Design

The architecture consists of three main components:

1.  **Local Monitor**: Uses the `watchdog` library to listen for file system events (via `inotify` on Linux). This triggers immediate uploads when files change locally.
2.  **Remote Poller**: A scheduled task that queries the Google Drive API for changes that occurred on the cloud side to trigger downloads.
3.  **Sync Engine**: Handles the logic to resolve paths, manage file IDs, filter content based on selective sync rules, and prevent infinite sync loops (e.g., downloading a file shouldn't trigger an upload event).

### Tech Stack
*   **Language**: Python 3
*   **Libraries**: `google-api-python-client`, `google-auth-oauthlib`, `watchdog`

## Minimalist Design Choices

*   **No GUI**: Configuration is handled via a simple JSON file.
*   **Simple Conflict Resolution**: We use a "rename on conflict" strategy rather than complex file merging.
*   **Stateless Logic**: We rely on the file system as the source of truth where possible (e.g., calculating MD5 checksums on the fly) to avoid stale cache issues.