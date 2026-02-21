"""Remove data files older than retention_days."""

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
    retention_days = settings.get("retention_days", 90)
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=retention_days)

    removed = 0
    for jsonl_file in data_dir.rglob("*.jsonl"):
        # filename is YYYY-MM-DD.jsonl
        try:
            file_date = datetime.strptime(jsonl_file.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            jsonl_file.unlink()
            removed += 1
            # remove empty parent dirs
            for parent in [jsonl_file.parent, jsonl_file.parent.parent]:
                try:
                    parent.rmdir()
                except OSError:
                    pass

    print(f"Cleanup: removed {removed} file(s) older than {cutoff}")


def main() -> None:
    cleanup()


if __name__ == "__main__":
    main()
