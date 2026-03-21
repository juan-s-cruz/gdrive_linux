# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0]

### Added
- **Delta Down-Sync**: Replaced full recursive polling with the Google Drive Changes API for efficient remote updates.
- **Targeted Selective Sync**: Configuration changes to `selective_sync_folders` now trigger targeted downloads for new paths and localized deletions for removed paths without requiring a full synchronization cycle.
- **Robust Startup Scan**: The client now reliably reconciles offline local changes (creations, modifications, conflicts) with the remote state before engaging real-time monitoring.

### Optimized
- **Folder Moves**: Remote folder renames or moves are now handled efficiently via local renaming, without unnecessarily triggering recursive API directory listings.

### Fixed
- **Child State Orphaning**: Resolved a state leak where deleting a parent directory would leave orphaned child file records in the tracking ledger.
- **Local Move Event Handling**: Fixed an issue where remote folder moves could trigger unnecessary local upload events for child files.

## [0.1.0]

### Security
- **Fixed Path Traversal Vulnerability**: Sanitized remote filenames fetched from the Google Drive API to prevent writing files outside of the designated sync directory. Malicious names like `../../.bashrc` are now neutralized.
- **Fixed Insecure Local Credential Storage**: The OAuth `token.json` file is now saved with `600` permissions, restricting access to the file owner.
- **Fixed Insecure Default Directory Permissions**: The application data directory (`~/.gdrive_client`) and the local sync root directory (e.g., `~/GoogleDrive`) are now created with `700` permissions, preventing other users on the system from accessing configuration, logs, or synced files.
- **Fixed Symlink Download Vulnerability**: The file download process now writes to a temporary file before atomically replacing the destination. This prevents the application from following a symlink and overwriting an arbitrary system file.
- **Fixed Symlink Upload Vulnerability**: The local file monitor now explicitly ignores symbolic links, preventing accidental or malicious uploads of sensitive files linked into the sync directory (e.g., a symlink to `~/.ssh/id_rsa`).
