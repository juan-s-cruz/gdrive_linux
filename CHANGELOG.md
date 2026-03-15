# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- **Fixed Path Traversal Vulnerability**: Sanitized remote filenames fetched from the Google Drive API to prevent writing files outside of the designated sync directory. Malicious names like `../../.bashrc` are now neutralized.
- **Fixed Insecure Local Credential Storage**: The OAuth `token.json` file is now saved with `600` permissions, restricting access to the file owner.
- **Fixed Insecure Default Directory Permissions**: The application data directory (`~/.gdrive_client`) and the local sync root directory (e.g., `~/GoogleDrive`) are now created with `700` permissions, preventing other users on the system from accessing configuration, logs, or synced files.
- **Fixed Symlink Download Vulnerability**: The file download process now writes to a temporary file before atomically replacing the destination. This prevents the application from following a symlink and overwriting an arbitrary system file.
- **Fixed Symlink Upload Vulnerability**: The local file monitor now explicitly ignores symbolic links, preventing accidental or malicious uploads of sensitive files linked into the sync directory (e.g., a symlink to `~/.ssh/id_rsa`).
