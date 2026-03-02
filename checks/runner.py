"""Run all configured checks and append results to daily JSONL file."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

from checks.registry import CHECK_TYPES

ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger("checks")


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def run_checks() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

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
            logger.error("Unknown check type: %s", check_type)
            continue

        check = cls(check_def)
        logger.info("[%s] Running (%s)...", check.check_id, check_type)
        result = await check.run()
        if isinstance(result, list):
            results.extend(result)
            for r in result:
                logger.info("[%s] Result: %s (%s)", r.check_id, r.status.value, r.message)
            if not result:
                logger.info("[%s] Result: (no result this cycle)", check.check_id)
        else:
            results.append(result)
            logger.info("[%s] Result: %s (%s)", result.check_id, result.status.value, result.message)

    with open(day_file, "a", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")

    logger.info("Wrote %d results to %s", len(results), day_file)

    # Fetch Dify version (independent of check results)
    version = await fetch_dify_version()
    version_file = data_dir / ".dify_version"
    if version:
        # Check if version changed and update history
        old_version = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else None
        if old_version != version:
            _append_version_history(data_dir, version, now)
            logger.info("Dify version changed: %s -> %s", old_version, version)

        version_file.write_text(version, encoding="utf-8")
        logger.info("Dify version: %s", version)
    else:
        logger.warning("Failed to fetch Dify version")


def _append_version_history(data_dir: Path, version: str, now: datetime) -> None:
    """Prepend a new entry to the version history file."""
    history_file = data_dir / ".dify_version_history.json"
    history: list[dict] = []
    if history_file.exists():
        try:
            history = json.loads(history_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            pass
    entry = {"version": version, "since": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    history.insert(0, entry)
    history_file.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def fetch_dify_version(timeout: int = 10) -> str | None:
    """Fetch the current Dify Cloud version from the x-version response header."""
    url = "https://cloud.dify.ai/console/api/system-features"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            version = resp.headers.get("x-version")
            return version if version else None
    except Exception:
        return None


def main() -> None:
    asyncio.run(run_checks())


if __name__ == "__main__":
    main()
