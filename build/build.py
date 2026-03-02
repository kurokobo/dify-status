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
    """Load all JSONL records sorted by timestamp, excluding superseded provisional records."""
    records: list[dict] = []
    for jsonl_file in sorted(data_dir.rglob("*.jsonl")):
        with open(jsonl_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))

    # Remove provisional records that have been superseded by a confirmed record
    # with the same (check_id, timestamp).
    confirmed_keys = {
        (r["check_id"], r["timestamp"])
        for r in records
        if not r.get("provisional", False)
    }
    return [
        r for r in records
        if not r.get("provisional", False)
        or (r["check_id"], r["timestamp"]) not in confirmed_keys
    ]


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
    down_count = sum(1 for s in check_day_statuses if s == "down")
    if down_count == 0:
        if any(s == "degraded" for s in check_day_statuses):
            return "degraded"
        return "up"
    if down_count / len(check_day_statuses) >= 0.5:
        return "down"
    return "degraded"


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


def _compute_ogp_hourly(
    records: list[dict],
    checks_config: list[dict],
    dates: list[str],
) -> list[dict]:
    """Compute per-day, per-hour overall status for OGP (mirrors frontend 7D view)."""
    check_ids = [c["id"] for c in checks_config]

    # Group records by (check_id, date, hour)
    by_check_date_hour: dict[str, dict[str, dict[int, list[str]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    for r in records:
        ts = r["timestamp"]
        date = ts[:10]
        if date not in dates:
            continue
        hour = int(ts[11:13])
        by_check_date_hour[r["check_id"]][date][hour].append(r["status"])

    result = []
    for date in dates:
        hours = []
        for h in range(24):
            # Compute per-check status for this hour, then derive overall
            check_statuses = []
            for cid in check_ids:
                statuses = by_check_date_hour[cid][date][h]
                if not statuses:
                    continue
                down_count = statuses.count("down")
                if down_count == 0:
                    cs = "degraded" if "degraded" in statuses else "up"
                elif down_count / len(statuses) >= 0.5:
                    cs = "down"
                else:
                    cs = "degraded"
                check_statuses.append(cs)

            if not check_statuses:
                status = "nodata"
            elif any(s == "down" for s in check_statuses):
                status = "down"
            elif any(s == "degraded" for s in check_statuses):
                status = "degraded"
            else:
                status = "up"
            hours.append({"hour": h, "status": status})
        # Format label as "Feb 24" style (matching frontend)
        dt = datetime.strptime(date, "%Y-%m-%d")
        label = dt.strftime("%b %d").replace(" 0", " ")
        result.append({"date": date, "label": label, "hours": hours})
    return result


def _render_ogp_png(
    site_title: str,
    days: list[dict],
    out_path: Path,
) -> None:
    """Render OGP image directly with Pillow (no C dependencies)."""
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1200, 630
    CELL = 38
    GAP = 4
    PITCH = CELL + GAP
    NUM_COLS = 24
    num_rows = len(days)
    GRID_W = NUM_COLS * PITCH - GAP
    LABEL_W = 40
    GRID_X = int((W - LABEL_W - 8 - GRID_W) / 2 + LABEL_W + 8)
    LABEL_X = GRID_X - 8
    GRID_Y = 240
    ACCENT = "#0033ff"
    COLORS = {"up": "#2da44e", "degraded": "#d4a017", "down": "#cf222e", "nodata": "#d0d7de"}

    img = Image.new("RGB", (W, H), "#f5f5f5")
    draw = ImageDraw.Draw(img)

    # Try to load a nice font, fall back to default
    def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        names = ["DejaVuSans-Bold.ttf", "DejaVuSans.ttf"] if bold else ["DejaVuSans.ttf"]
        for name in names:
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default(size)

    font_title = _font(64, bold=True)
    font_section = _font(22, bold=True)
    font_label = _font(16)
    font_hour = _font(14)

    # Top accent line
    draw.rectangle([0, 0, W, 6], fill=ACCENT)
    # Bottom accent line
    draw.rectangle([0, H - 6, W, H], fill=ACCENT)

    # Title (centered)
    bbox = draw.textbbox((0, 0), site_title, font=font_title)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) / 2, 130 - 64), site_title, fill=ACCENT, font=font_title)

    # Section label
    section = f"Overall status for cloud.dify.ai -- Last {num_rows} days (UTC)"
    bbox = draw.textbbox((0, 0), section, font=font_section)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) / 2, 185 - 22), section, fill="#1f2328", font=font_section)

    # Day rows
    for row_idx, day in enumerate(days):
        row_y = GRID_Y + row_idx * PITCH

        # Date label (right-aligned)
        bbox = draw.textbbox((0, 0), day["label"], font=font_label)
        lw = bbox[2] - bbox[0]
        lh = bbox[3] - bbox[1]
        draw.text((LABEL_X - lw, row_y + (CELL - lh) / 2), day["label"], fill="#656d76", font=font_label)

        # Hour cells
        for col_idx, h in enumerate(day["hours"]):
            col_x = GRID_X + col_idx * PITCH
            draw.rounded_rectangle(
                [col_x, row_y, col_x + CELL, row_y + CELL],
                radius=4,
                fill=COLORS[h["status"]],
            )

    # Hour labels below grid
    hour_label_y = GRID_Y + num_rows * PITCH + 4
    for h in [0, 6, 12, 18, 23]:
        label = f"{h}:00"
        hx = GRID_X + h * PITCH + CELL / 2
        bbox = draw.textbbox((0, 0), label, font=font_hour)
        lw = bbox[2] - bbox[0]
        draw.text((hx - lw / 2, hour_label_y), label, fill="#8b949e", font=font_hour)

    img.save(str(out_path), "PNG")


def build_site() -> None:
    config = load_config()
    settings = config["settings"]
    data_dir = ROOT / settings["data_dir"]
    retention_days = settings.get("retention_days", 90)
    site_title = settings.get("site_title", "Status")
    site_url = settings.get("site_url", "")

    records = load_all_data(data_dir)
    summary = build_summary(records, config["checks"], retention_days)

    # Read Dify version
    version_file = data_dir / ".dify_version"
    dify_version = ""
    if version_file.exists():
        dify_version = version_file.read_text(encoding="utf-8").strip()
    summary["dify_version"] = dify_version

    # Read Dify version history
    version_history_file = data_dir / ".dify_version_history.json"
    dify_version_history: list[dict] = []
    if version_history_file.exists():
        try:
            dify_version_history = json.loads(
                version_history_file.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, ValueError):
            pass
    summary["dify_version_history"] = dify_version_history

    # Prepare output
    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
    SITE_DIR.mkdir(parents=True)

    # Jinja2 environment
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)

    site_description = settings.get("site_description", [])
    site_disclaimer = settings.get("site_disclaimer", [])
    notification = settings.get("notification", {})

    # Write summary.json as a separate cacheable file
    data_dir_out = SITE_DIR / "data"
    data_dir_out.mkdir(parents=True, exist_ok=True)
    (data_dir_out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8"
    )

    # Render index.html (without inlined summary_json)
    tmpl_index = env.get_template("index.html")
    index_html = tmpl_index.render(
        site_title=site_title,
        site_url=site_url,
        site_description=site_description,
        site_disclaimer=site_disclaimer,
        notification=notification,
        summary=summary,
    )
    (SITE_DIR / "index.html").write_text(index_html, encoding="utf-8")

    # Render detail pages
    detail_dir = SITE_DIR / "detail"
    detail_dir.mkdir(exist_ok=True)
    tmpl_detail = env.get_template("detail.html")

    # Collect per-check detail data for both per-check and merged daily files
    all_detail_data: dict[str, dict[str, list[dict]]] = {}

    for check_def in config["checks"]:
        cid = check_def["id"]
        check_summary = next((c for c in summary["checks"] if c["id"] == cid), None)
        detail_html = tmpl_detail.render(
            site_title=site_title,
            site_url=site_url,
            check=check_def,
            check_summary=check_summary,
            check_summary_json=json.dumps(check_summary, ensure_ascii=False),
            dify_version=dify_version,
            dify_version_history_json=json.dumps(dify_version_history, ensure_ascii=False),
            last_checked=summary.get("last_checked", ""),
        )
        (detail_dir / f"{cid}.html").write_text(detail_html, encoding="utf-8")

        # Per-check daily JSON files (strip redundant check_id and provisional)
        detail_data = build_detail_data(records, cid)
        all_detail_data[cid] = detail_data
        check_data_dir = SITE_DIR / "data" / cid
        check_data_dir.mkdir(parents=True, exist_ok=True)
        for date_str, date_records in detail_data.items():
            slim_records = [
                {
                    "t": r["timestamp"],
                    "s": r["status"],
                    "r": r["response_time_ms"],
                    "m": r.get("message", ""),
                }
                for r in date_records
            ]
            (check_data_dir / f"{date_str}.json").write_text(
                json.dumps(slim_records, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )

    # Merged daily JSON files (all checks per date)
    daily_dir = SITE_DIR / "data" / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    all_dates: set[str] = set()
    for detail_data in all_detail_data.values():
        all_dates.update(detail_data.keys())
    for date_str in sorted(all_dates):
        merged: dict[str, list[dict]] = {}
        for cid, detail_data in all_detail_data.items():
            if date_str in detail_data:
                # Slim: only timestamp and status (index page only uses these)
                merged[cid] = [
                    {"t": r["timestamp"], "s": r["status"]}
                    for r in detail_data[date_str]
                ]
        (daily_dir / f"{date_str}.json").write_text(
            json.dumps(merged, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    # Render OGP image (Pillow direct drawing)
    ogp_days = 7
    ogp_dates = summary["dates"][-ogp_days:]
    ogp_hourly = _compute_ogp_hourly(records, config["checks"], ogp_dates)
    static_dst = SITE_DIR / "static"
    static_dst.mkdir(parents=True, exist_ok=True)
    _render_ogp_png(site_title, ogp_hourly, static_dst / "ogp.png")

    # Copy static files
    static_src = ROOT / "static"
    if static_src.exists():
        shutil.copytree(static_src, static_dst, dirs_exist_ok=True)

    print(f"Site built in {SITE_DIR}")


def main() -> None:
    build_site()


if __name__ == "__main__":
    main()
