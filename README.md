# creativeUNLEASHED

## iCloud inventory helper

This repo includes a lightweight CLI script to inventory iCloud Drive folders on macOS without forcing downloads.

### Requirements

- macOS (tested with Sonoma)
- Python 3.9+
- Spotlight metadata enabled (for `mdls`)

### Usage

```bash
python3 icloud_inventory.py \
  --roots ~/Library/Mobile\ Documents/com~apple~CloudDocs \
  --output ~/Desktop/icloud_inventory.jsonl \
  --format jsonl
```

### Optional flags

- `--skip-mdls`: avoid Spotlight metadata lookup.
- `--mdls-timeout 5`: timeout for each `mdls` call.
- `--mdls-fields ...`: customize the Spotlight fields collected.
- `--content-scan`: read local text files to detect AI keywords (may download cloud-only files).
- `--max-files 5000`: limit scanned files.
- `--include-hidden`: include hidden files and folders.
- `--include-directories`: include directories in the output.

### Output

Each record includes path, size, timestamps, owner, MIME type, Spotlight metadata, and AI keyword hints.

> **Note**: AI detection is heuristic. It scans Spotlight fields (and optional content snippets) for known AI-related keywords.
