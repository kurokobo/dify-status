"""Run all configured checks and append results to daily JSONL file."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from checks.registry import CHECK_TYPES

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def run_checks() -> None:
    load_dotenv(ROOT / ".env")
    config = load_config()
    settings = config["settings"]
    data_dir = ROOT / settings["data_dir"]

    now = datetime.now(timezone.utc)
    day_file = data_dir / now.strftime("%Y/%m/%Y-%m-%d.jsonl")
    day_file.parent.mkdir(parents=True, exist_ok=True)

    results = []
    for check_def in config["checks"]:
        check_type = check_def["type"]
        cls = CHECK_TYPES.get(check_type)
        if cls is None:
            print(f"Unknown check type: {check_type}", file=sys.stderr)
            continue

        check = cls(check_def)
        result = await check.run()
        if isinstance(result, list):
            results.extend(result)
            for r in result:
                print(f"  {r.check_id}: {r.status.value} ({r.message})")
            if not result:
                print(f"  {check.check_id}: (no result this cycle)")
        else:
            results.append(result)
            print(f"  {result.check_id}: {result.status.value} ({result.message})")

    with open(day_file, "a", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")

    print(f"Wrote {len(results)} results to {day_file}")


def main() -> None:
    asyncio.run(run_checks())


if __name__ == "__main__":
    main()
