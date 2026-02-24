"""Post GitHub Issue comments on incident start/recovery."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "data" / ".incident_state.json"

DEFAULT_FAILURE_THRESHOLD = 2


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_state() -> dict[str, dict]:
    """Load previous check states.

    Returns {check_id: {"consecutive_failures": int, "incident_reported": bool,
    "last_timestamp": str}}.
    Migrates old formats automatically.
    """
    if not STATE_FILE.exists():
        return {}
    with open(STATE_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    migrated = {}
    for cid, val in raw.items():
        if isinstance(val, str):
            # Old format: val is a status string
            migrated[cid] = {
                "consecutive_failures": 0 if val == "up" else 1,
                "incident_reported": val != "up",
                "last_timestamp": "",
            }
        else:
            if "last_timestamp" not in val:
                val["last_timestamp"] = ""
            migrated[cid] = val
    return migrated


def save_state(state: dict[str, dict]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_latest_results(data_dir: Path) -> dict[str, dict]:
    """Get the most recent result for each check, trying today's file then yesterday's."""
    now = datetime.now(timezone.utc)
    day_file = None
    for days_ago in range(2):
        candidate = data_dir / (now - timedelta(days=days_ago)).strftime("%Y/%m/%Y-%m-%d.jsonl")
        if candidate.exists():
            day_file = candidate
            break
    if day_file is None:
        return {}

    latest: dict[str, dict] = {}
    with open(day_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("provisional", False):
                continue
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

    failure_threshold = notification.get("failure_threshold", DEFAULT_FAILURE_THRESHOLD)
    check_names = {c["id"]: c["name"] for c in config["checks"]}
    prev_state = load_state()
    latest = get_latest_results(data_dir)

    if not latest:
        print("No results found for today or yesterday")
        return

    incidents: list[str] = []
    recoveries: list[str] = []
    incident_cids: set[str] = set()

    new_state: dict[str, dict] = {}
    for cid, record in latest.items():
        current_status = record["status"]
        current_ts = record["timestamp"]
        prev = prev_state.get(cid, {"consecutive_failures": 0, "incident_reported": False, "last_timestamp": ""})
        consecutive_failures = prev["consecutive_failures"]
        incident_reported = prev["incident_reported"]
        last_timestamp = prev.get("last_timestamp", "")

        # Skip if we already processed this exact record
        if current_ts == last_timestamp:
            new_state[cid] = {
                "consecutive_failures": consecutive_failures,
                "incident_reported": incident_reported,
                "last_timestamp": last_timestamp,
            }
            continue

        if not is_healthy(current_status):
            consecutive_failures += 1
            if consecutive_failures >= failure_threshold and not incident_reported:
                name = check_names.get(cid, cid)
                incidents.append(f"- **{name}** — {current_status} ({record.get('message', '')})")
                incident_cids.add(cid)
                incident_reported = True
        else:
            if incident_reported:
                name = check_names.get(cid, cid)
                recoveries.append(f"- **{name}**")
            consecutive_failures = 0
            incident_reported = False

        new_state[cid] = {
            "consecutive_failures": consecutive_failures,
            "incident_reported": incident_reported,
            "last_timestamp": current_ts,
        }

    # Preserve state for checks not present in latest results
    for cid, val in prev_state.items():
        if cid not in new_state:
            new_state[cid] = val

    save_state(new_state)

    # Checks with an ongoing (already-reported) incident, excluding newly reported ones
    ongoing = [
        check_names.get(cid, cid)
        for cid, s in new_state.items()
        if s["incident_reported"] and cid not in incident_cids
    ]

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if incidents and recoveries:
        lines = [f"📊 **Status Update** — {now_str}", "", "🔴 New incidents:"] + incidents + ["", "🟢 Recovered:"] + recoveries
        if ongoing:
            lines += ["", "🟡 Ongoing issues:"] + [f"- **{name}**" for name in ongoing]
        body = "\n".join(lines)
        print(f"Posting status update comment:\n{body}")
        post_issue_comment(repo, issue_number, body)
    elif incidents:
        body = f"🔴 **Incident detected** — {now_str}\n\n" + "\n".join(incidents)
        if ongoing:
            body += "\n\n🟡 Ongoing issues:\n" + "\n".join(f"- **{name}**" for name in ongoing)
        print(f"Posting incident comment:\n{body}")
        post_issue_comment(repo, issue_number, body)
    elif recoveries:
        if ongoing:
            lines = [f"🟡 **Partially Recovered** — {now_str}", "", "🟢 Recovered:"] + recoveries + ["", "🟡 Ongoing issues:"] + [f"- **{name}**" for name in ongoing]
            body = "\n".join(lines)
        else:
            body = f"🟢 **Recovered** — {now_str}\n\n" + "\n".join(recoveries)
        print(f"Posting recovery comment:\n{body}")
        post_issue_comment(repo, issue_number, body)
    else:
        print("No status transitions detected")


def main() -> None:
    run_notify()


if __name__ == "__main__":
    main()
