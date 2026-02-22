"""Post GitHub Issue comments on incident start/recovery."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "data" / ".incident_state.json"


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_state() -> dict[str, str]:
    """Load previous check states. Returns {check_id: "up"|"degraded"|"down"}."""
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict[str, str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_latest_results(data_dir: Path) -> dict[str, dict]:
    """Get the most recent result for each check from today's JSONL."""
    now = datetime.now(timezone.utc)
    day_file = data_dir / now.strftime("%Y/%m/%Y-%m-%d.jsonl")
    if not day_file.exists():
        return {}

    latest: dict[str, dict] = {}
    with open(day_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            cid = record["check_id"]
            # Keep the latest by timestamp
            if cid not in latest or record["timestamp"] > latest[cid]["timestamp"]:
                latest[cid] = record
    return latest


def post_issue_comment(repo: str, issue_number: int, body: str) -> bool:
    """Post a comment to a GitHub Issue using gh CLI."""
    try:
        subprocess.run(
            ["gh", "issue", "comment", str(issue_number),
             "--repo", repo, "--body", body],
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Failed to post issue comment: {e}", file=sys.stderr)
        return False


def is_healthy(status: str) -> bool:
    return status == "up"


def run_notify() -> None:
    config = load_config()
    settings = config["settings"]
    data_dir = ROOT / settings["data_dir"]

    notification = settings.get("notification", {})
    repo = notification.get("github_repo", "")
    issue_number = notification.get("issue_number", 0)
    if not repo or not issue_number:
        print("Notification not configured (missing github_repo or issue_number in settings.notification)")
        return

    check_names = {c["id"]: c["name"] for c in config["checks"]}
    prev_state = load_state()
    latest = get_latest_results(data_dir)

    if not latest:
        print("No results found for today")
        return

    incidents: list[str] = []
    recoveries: list[str] = []

    new_state = dict(prev_state)
    for cid, record in latest.items():
        current_status = record["status"]
        prev_status = prev_state.get(cid)
        new_state[cid] = current_status

        if prev_status is None:
            # First time seeing this check, no transition
            continue

        was_healthy = is_healthy(prev_status)
        now_healthy = is_healthy(current_status)

        if was_healthy and not now_healthy:
            name = check_names.get(cid, cid)
            incidents.append(f"- **{name}** — {current_status} ({record.get('message', '')})")
        elif not was_healthy and now_healthy:
            name = check_names.get(cid, cid)
            recoveries.append(f"- **{name}**")

    save_state(new_state)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if incidents:
        body = f"**Incident detected** — {now_str}\n\n" + "\n".join(incidents)
        print(f"Posting incident comment:\n{body}")
        post_issue_comment(repo, issue_number, body)

    if recoveries:
        body = f"**Recovered** — {now_str}\n\n" + "\n".join(recoveries)
        print(f"Posting recovery comment:\n{body}")
        post_issue_comment(repo, issue_number, body)

    if not incidents and not recoveries:
        print("No status transitions detected")


def main() -> None:
    run_notify()


if __name__ == "__main__":
    main()
