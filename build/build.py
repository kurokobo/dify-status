"""Build the static site from JSONL data files."""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
SITE_DIR = ROOT / "site"


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_all_data(data_dir: Path) -> list[dict]:
    """Load all JSONL records sorted by timestamp."""
    records: list[dict] = []
    for jsonl_file in sorted(data_dir.rglob("*.jsonl")):
        with open(jsonl_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records


def compute_day_status(statuses: list[str]) -> str:
    """Determine day status for a single check from individual check results."""
    if not statuses:
        return "nodata"
    down_count = statuses.count("down")
    if down_count == 0:
        return "up"
    if down_count / len(statuses) >= 0.5:
        return "down"
    return "degraded"


def compute_overall_day_status(check_day_statuses: list[str]) -> str:
    """Determine overall status for a day from per-check day statuses."""
    if not check_day_statuses:
        return "nodata"
    if any(s == "down" for s in check_day_statuses):
        return "down"
    if any(s == "degraded" for s in check_day_statuses):
        return "degraded"
    return "up"


def build_summary(
    records: list[dict],
    checks_config: list[dict],
    retention_days: int,
) -> dict:
    """Build a summary structure for the last N days."""
    today = datetime.now(timezone.utc).date()
    dates = [
        (today - timedelta(days=i)).isoformat() for i in range(retention_days - 1, -1, -1)
    ]

    # Group records by (check_id, date)
    by_check_date: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in records:
        ts = r["timestamp"][:10]  # YYYY-MM-DD
        by_check_date[r["check_id"]][ts].append(r)

    # Find the latest individual record per check
    latest_by_check: dict[str, dict] = {}
    for r in records:
        cid = r["check_id"]
        if cid not in latest_by_check or r["timestamp"] > latest_by_check[cid]["timestamp"]:
            latest_by_check[cid] = r

    check_ids = [c["id"] for c in checks_config]
    check_names = {c["id"]: c["name"] for c in checks_config}
    check_descriptions = {c["id"]: c.get("description", "") for c in checks_config}
    check_notes = {c["id"]: c.get("note", "") for c in checks_config}

    checks_summary: list[dict] = []
    overall_days: dict[str, list[str]] = defaultdict(list)

    for cid in check_ids:
        days = []
        for d in dates:
            day_records = by_check_date[cid].get(d, [])
            statuses = [r["status"] for r in day_records]
            day_status = compute_day_status(statuses)

            resp_times = [r["response_time_ms"] for r in day_records if r["response_time_ms"] >= 0]
            avg_resp = int(sum(resp_times) / len(resp_times)) if resp_times else -1
            total = len(statuses)
            up_count = statuses.count("up")
            uptime_pct = round(up_count / total * 100, 1) if total else None

            days.append({
                "date": d,
                "status": day_status,
                "avg_response_ms": avg_resp,
                "uptime_pct": uptime_pct,
                "total_checks": total,
            })

            if day_status != "nodata":
                overall_days[d].append(day_status)

        # Latest individual check result for this check
        latest_rec = latest_by_check.get(cid)
        latest_status = latest_rec["status"] if latest_rec else "nodata"
        latest_timestamp = latest_rec["timestamp"] if latest_rec else None
        latest_response_ms = latest_rec["response_time_ms"] if latest_rec else -1
        latest_message = latest_rec.get("message", "") if latest_rec else ""

        checks_summary.append({
            "id": cid,
            "name": check_names[cid],
            "description": check_descriptions[cid],
            "note": check_notes[cid],
            "days": days,
            "current_status": latest_status,
            "latest_timestamp": latest_timestamp,
            "latest_response_ms": latest_response_ms,
            "latest_message": latest_message,
        })

    # Overall row
    overall_day_list = []
    for d in dates:
        if d in overall_days:
            overall_day_list.append({
                "date": d,
                "status": compute_overall_day_status(overall_days[d]),
            })
        else:
            overall_day_list.append({"date": d, "status": "nodata"})

    # Current overall status
    current_statuses = [c["current_status"] for c in checks_summary]
    if all(s == "up" for s in current_statuses):
        current_overall = "All Components Operational"
        current_overall_status = "up"
    elif any(s == "down" for s in current_statuses):
        current_overall = "Partial Outage"
        current_overall_status = "down"
    elif any(s == "degraded" for s in current_statuses):
        current_overall = "Degraded Performance"
        current_overall_status = "degraded"
    else:
        current_overall = "No Data"
        current_overall_status = "nodata"

    # Last checked timestamp
    last_checked = max((r["timestamp"] for r in records), default=None)

    return {
        "current_overall": current_overall,
        "current_overall_status": current_overall_status,
        "last_checked": last_checked,
        "dates": dates,
        "overall_days": overall_day_list,
        "checks": checks_summary,
    }


def build_detail_data(
    records: list[dict], check_id: str
) -> dict[str, list[dict]]:
    """Group records by date for a single check."""
    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if r["check_id"] == check_id:
            d = r["timestamp"][:10]
            by_date[d].append(r)
    return dict(by_date)


def build_site() -> None:
    config = load_config()
    settings = config["settings"]
    data_dir = ROOT / settings["data_dir"]
    retention_days = settings.get("retention_days", 90)
    site_title = settings.get("site_title", "Status")
    site_url = settings.get("site_url", "")

    records = load_all_data(data_dir)
    summary = build_summary(records, config["checks"], retention_days)

    # Prepare output
    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
    SITE_DIR.mkdir(parents=True)

    # Jinja2 environment
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)

    site_description = settings.get("site_description", [])
    site_disclaimer = settings.get("site_disclaimer", [])
    notification = settings.get("notification", {})

    # Render index.html
    tmpl_index = env.get_template("index.html")
    index_html = tmpl_index.render(
        site_title=site_title,
        site_url=site_url,
        site_description=site_description,
        site_disclaimer=site_disclaimer,
        notification=notification,
        summary=summary,
        summary_json=json.dumps(summary, ensure_ascii=False),
    )
    (SITE_DIR / "index.html").write_text(index_html, encoding="utf-8")

    # Render detail pages
    detail_dir = SITE_DIR / "detail"
    detail_dir.mkdir(exist_ok=True)
    tmpl_detail = env.get_template("detail.html")

    for check_def in config["checks"]:
        cid = check_def["id"]
        check_summary = next((c for c in summary["checks"] if c["id"] == cid), None)
        detail_html = tmpl_detail.render(
            site_title=site_title,
            site_url=site_url,
            check=check_def,
            check_summary=check_summary,
            summary_json=json.dumps(summary, ensure_ascii=False),
        )
        (detail_dir / f"{cid}.html").write_text(detail_html, encoding="utf-8")

        # Per-check daily JSON files
        detail_data = build_detail_data(records, cid)
        check_data_dir = SITE_DIR / "data" / cid
        check_data_dir.mkdir(parents=True, exist_ok=True)
        for date_str, date_records in detail_data.items():
            (check_data_dir / f"{date_str}.json").write_text(
                json.dumps(date_records, ensure_ascii=False), encoding="utf-8"
            )

    # Copy static files
    static_src = ROOT / "static"
    static_dst = SITE_DIR / "static"
    if static_src.exists():
        shutil.copytree(static_src, static_dst)

    print(f"Site built in {SITE_DIR}")


def main() -> None:
    build_site()


if __name__ == "__main__":
    main()
