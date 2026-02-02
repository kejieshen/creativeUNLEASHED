#!/usr/bin/env python3
"""Inventory iCloud Drive (or any directory) without forcing downloads.

This script walks directories and collects filesystem stats plus optional
Spotlight metadata (via mdls). It avoids reading file contents unless the
--content-scan flag is provided.
"""

import argparse
import csv
import json
import mimetypes
import os
from pathlib import Path
import pwd
import sqlite3
import subprocess
from typing import Iterable, Optional

AI_KEYWORDS = {
    "openai",
    "chatgpt",
    "gpt",
    "anthropic",
    "claude",
    "cohere",
    "gemini",
    "bard",
    "copilot",
    "midjourney",
    "stability",
    "stable diffusion",
    "dalle",
    "dall-e",
}

DEFAULT_MDLS_FIELDS = [
    "kMDItemFSName",
    "kMDItemFSCreationDate",
    "kMDItemFSContentChangeDate",
    "kMDItemKind",
    "kMDItemContentType",
    "kMDItemCreator",
    "kMDItemAuthors",
    "kMDItemWhereFroms",
    "kMDItemUserTags",
    "kMDItemDescription",
    "kMDItemFSSize",
    "kMDItemIsDownloadable",
    "kMDItemIsDownloaded",
    "kMDItemDownloadingStatus",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inventory iCloud Drive directories without downloading files. "
            "Outputs JSON Lines or CSV with metadata and optional AI signals."
        )
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for roots/output/format instead of relying on defaults.",
    )
    parser.add_argument(
        "--roots",
        nargs="+",
        default=[str(Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs")],
        help="Root directories to scan (space separated).",
    )
    parser.add_argument(
        "--output",
        default="icloud_inventory.jsonl",
        help="Output file path (jsonl or csv).",
    )
    parser.add_argument(
        "--format",
        choices=["jsonl", "csv", "sqlite"],
        default="jsonl",
        help="Output format.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Stop after processing this many files (0 = no limit).",
    )
    parser.add_argument(
        "--include-directories",
        action="store_true",
        help="Include directories in the output.",
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include hidden files and folders.",
    )
    parser.add_argument(
        "--skip-mdls",
        action="store_true",
        help="Skip Spotlight metadata (mdls) lookup.",
    )
    parser.add_argument(
        "--mdls-timeout",
        type=float,
        default=5.0,
        help="Timeout (seconds) for each mdls lookup.",
    )
    parser.add_argument(
        "--mdls-fields",
        default=",".join(DEFAULT_MDLS_FIELDS),
        help="Comma-separated list of mdls fields to collect.",
    )
    parser.add_argument(
        "--content-scan",
        action="store_true",
        help="Scan local text file contents for AI keywords (may download).",
    )
    parser.add_argument(
        "--max-content-bytes",
        type=int,
        default=262144,
        help="Max bytes to read from a file when --content-scan is set.",
    )
    parser.add_argument(
        "--text-extensions",
        default=".txt,.md,.rtf,.csv,.tsv,.json,.yaml,.yml,.log",
        help="Comma-separated list of extensions to content-scan.",
    )
    return parser.parse_args()


def is_hidden(path: Path) -> bool:
    parts = path.parts
    return any(part.startswith(".") for part in parts)


def get_owner_name(stat_result: os.stat_result) -> Optional[str]:
    try:
        return pwd.getpwuid(stat_result.st_uid).pw_name
    except KeyError:
        return None


def run_mdls(path: Path, fields: Iterable[str], timeout: float) -> dict:
    cmd = ["mdls"]
    for field in fields:
        cmd.extend(["-name", field])
    cmd.append(str(path))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"mdls_error": f"timeout after {timeout}s"}
    if result.returncode != 0:
        return {"mdls_error": result.stderr.strip()}
    metadata = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        metadata[key.strip()] = value.strip()
    return metadata


def detect_ai_signals(values: Iterable[str]) -> list[str]:
    detected = set()
    for value in values:
        if not value:
            continue
        lowered = value.lower()
        for keyword in AI_KEYWORDS:
            if keyword in lowered:
                detected.add(keyword)
    return sorted(detected)


def read_text_snippet(path: Path, max_bytes: int) -> str:
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        return handle.read(max_bytes)


def should_scan_content(path: Path, text_extensions: set[str]) -> bool:
    return path.suffix.lower() in text_extensions


def iter_paths(
    roots: list[str],
    include_hidden: bool,
    include_directories: bool,
) -> Iterable[Path]:
    for root in roots:
        root_path = Path(os.path.expanduser(root)).resolve()
        if not root_path.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root_path):
            current_dir = Path(dirpath)
            if not include_hidden:
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                filenames = [f for f in filenames if not f.startswith(".")]
            if include_directories:
                for dirname in dirnames:
                    dir_path = current_dir / dirname
                    if not include_hidden and is_hidden(dir_path):
                        continue
                    yield dir_path
            for filename in filenames:
                path = current_dir / filename
                if not include_hidden and is_hidden(path):
                    continue
                yield path


def format_epoch(timestamp: Optional[float]) -> Optional[str]:
    if timestamp is None:
        return None
    return __import__("datetime").datetime.fromtimestamp(timestamp).isoformat()


def build_record(
    path: Path,
    stat_result: os.stat_result,
    mdls_data: dict,
    ai_signals: list[str],
) -> dict:
    mime_type, _ = mimetypes.guess_type(path.name)
    created_epoch = stat_result.st_birthtime if hasattr(stat_result, "st_birthtime") else None
    modified_epoch = stat_result.st_mtime
    return {
        "path": str(path),
        "name": path.name,
        "extension": path.suffix.lower(),
        "is_dir": path.is_dir(),
        "size_bytes": stat_result.st_size,
        "created_epoch": created_epoch,
        "created_iso": format_epoch(created_epoch),
        "modified_epoch": modified_epoch,
        "modified_iso": format_epoch(modified_epoch),
        "owner": get_owner_name(stat_result),
        "mime_type": mime_type,
        "mdls": mdls_data,
        "ai_signals": ai_signals,
    }


def write_json_line(handle, record: dict) -> None:
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def csv_row(record: dict) -> dict:
    flattened = dict(record)
    flattened["mdls"] = json.dumps(record.get("mdls", {}), ensure_ascii=False)
    flattened["ai_signals"] = ",".join(record.get("ai_signals", []))
    return flattened


def main() -> int:
    args = parse_args()
    if args.interactive:
        roots_input = input(
            "Roots to scan (comma-separated) "
            f"[{','.join(args.roots)}]: "
        ).strip()
        if roots_input:
            args.roots = [item.strip() for item in roots_input.split(",") if item.strip()]
        output_input = input(f"Output path [{args.output}]: ").strip()
        if output_input:
            args.output = output_input
        format_input = input(f"Format jsonl/csv/sqlite [{args.format}]: ").strip().lower()
        if format_input in {"jsonl", "csv", "sqlite"}:
            args.format = format_input
    output_path = Path(args.output).expanduser().resolve()
    text_extensions = {ext.strip().lower() for ext in args.text_extensions.split(",") if ext.strip()}
    mdls_fields = [field.strip() for field in args.mdls_fields.split(",") if field.strip()]

    processed = 0
    written = 0

    def record_stream() -> Iterable[dict]:
        nonlocal processed
        for path in iter_paths(args.roots, args.include_hidden, args.include_directories):
            try:
                stat_result = path.stat()
            except FileNotFoundError:
                continue
            mdls_data = {} if args.skip_mdls else run_mdls(path, mdls_fields, args.mdls_timeout)
            signal_sources = []
            for value in mdls_data.values():
                if isinstance(value, str):
                    signal_sources.append(value)
            if args.content_scan and path.is_file() and should_scan_content(path, text_extensions):
                try:
                    snippet = read_text_snippet(path, args.max_content_bytes)
                    signal_sources.append(snippet)
                except (OSError, UnicodeError):
                    pass
            ai_signals = detect_ai_signals(signal_sources)
            record = build_record(path, stat_result, mdls_data, ai_signals)
            processed += 1
            yield record
            if args.max_files and processed >= args.max_files:
                break

    if args.format == "sqlite":
        connection = sqlite3.connect(output_path)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory (
                path TEXT,
                name TEXT,
                extension TEXT,
                is_dir INTEGER,
                size_bytes INTEGER,
                created_epoch REAL,
                created_iso TEXT,
                modified_epoch REAL,
                modified_iso TEXT,
                owner TEXT,
                mime_type TEXT,
                mdls TEXT,
                ai_signals TEXT
            )
            """
        )
        insert_sql = (
            "INSERT INTO inventory "
            "(path, name, extension, is_dir, size_bytes, created_epoch, created_iso, "
            "modified_epoch, modified_iso, owner, mime_type, mdls, ai_signals) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        with connection:
            for record in record_stream():
                row = csv_row(record)
                connection.execute(
                    insert_sql,
                    (
                        record["path"],
                        record["name"],
                        record["extension"],
                        int(record["is_dir"]),
                        record["size_bytes"],
                        record["created_epoch"],
                        record["created_iso"],
                        record["modified_epoch"],
                        record["modified_iso"],
                        record["owner"],
                        record["mime_type"],
                        row["mdls"],
                        row["ai_signals"],
                    ),
                )
                written += 1
    else:
        with output_path.open("w", encoding="utf-8", newline="" if args.format == "csv" else None) as handle:
            writer = None
            for record in record_stream():
                if args.format == "jsonl":
                    write_json_line(handle, record)
                else:
                    if writer is None:
                        writer = csv.DictWriter(handle, fieldnames=list(record.keys()))
                        writer.writeheader()
                    writer.writerow(csv_row(record))
                written += 1

    print(f"Wrote {written} records to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
