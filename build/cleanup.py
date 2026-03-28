"""Archive data files older than retention_days."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def cleanup() -> None:
    config = load_config()
    settings = config["settings"]
    data_dir = ROOT / settings["data_dir"]
    archive_dir = ROOT / settings.get("archive_dir", "archive")
    retention_days = settings.get("retention_days", 90)
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=retention_days)

    archived = 0
    for jsonl_file in data_dir.rglob("*.jsonl"):
        # skip files already under archive_dir
        if archive_dir in jsonl_file.parents:
            continue
        # filename is YYYY-MM-DD.jsonl
        try:
            file_date = datetime.strptime(jsonl_file.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            # mirror the YYYY/MM directory structure under archive_dir
            relative = jsonl_file.relative_to(data_dir)
            dest = archive_dir / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            jsonl_file.rename(dest)
            archived += 1
            # remove empty parent dirs in data_dir
            for parent in [jsonl_file.parent, jsonl_file.parent.parent]:
                try:
                    parent.rmdir()
                except OSError:
                    pass

    print(f"Cleanup: archived {archived} file(s) older than {cutoff} to {archive_dir}")


def main() -> None:
    cleanup()


if __name__ == "__main__":
    main()
