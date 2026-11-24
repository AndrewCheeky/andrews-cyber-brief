#!/usr/bin/env python3
"""Build the Alexa Flash Briefing text feed from the briefing spreadsheet.

Reads the sheet as CSV, picks the newest complete PUBLISHED row and writes a
one-item JSON feed (Amazon's preferred Flash Briefing format).

The feed file is only replaced when a valid briefing is found, so a failed or
incomplete fetch leaves the last good feed in place.

Exit codes:
  0  feed is valid (written or unchanged)
  1  fetch or validation failed; existing feed left untouched
  3  feed is valid, but today's briefing is missing after the alert time
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MAX_SPOKEN_CHARS = 4500
DATE_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
)


class FeedError(Exception):
    """Raised when no valid briefing can be produced."""


# ---------- fetching ----------

def sheet_csv_url(config: dict) -> str:
    override = os.environ.get("SHEET_CSV_URL", "").strip()
    if override:
        return override
    url = f"https://docs.google.com/spreadsheets/d/{config['sheet_id']}/export?format=csv"
    if config.get("sheet_gid"):
        url += f"&gid={config['sheet_gid']}"
    return url


def fetch_csv(url: str, timeout: int = 60) -> str:
    request = Request(url, headers={"User-Agent": "andrews-cyber-brief-feed/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            body = response.read().decode("utf-8-sig")
    except Exception as exc:  # network, HTTP 4xx/5xx, timeout
        raise FeedError(f"Could not download the spreadsheet: {exc}") from exc
    if "text/html" in content_type or body.lstrip().lower().startswith("<!doctype html"):
        raise FeedError("Spreadsheet returned a web page instead of CSV; it may no longer be shared for link viewing.")
    return body


# ---------- parsing and selection ----------

def parse_local_time(value: str, tz: ZoneInfo) -> datetime | None:
    value = (value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def clean_spoken_text(text: str) -> str:
    """Plain text only: no markup, single spaces, a full stop between paragraphs."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n|\r\n\s*\r\n", text)]
    sentences = []
    for p in paragraphs:
        if not p:
            continue
        if p[-1] not in ".!?:;":
            p += "."
        sentences.append(p)
    text = " ".join(sentences)
    if len(text) > MAX_SPOKEN_CHARS:
        cut = text[:MAX_SPOKEN_CHARS]
        end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        text = cut[: end + 1] if end > 0 else cut
    return text


def select_latest(csv_text: str, config: dict, now: datetime) -> dict:
    tz = ZoneInfo(config["timezone"])
    cols = config["columns"]
    reader = csv.DictReader(io.StringIO(csv_text))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    missing = [name for name in cols.values() if name not in headers]
    if missing:
        raise FeedError(f"Spreadsheet is missing column(s): {', '.join(missing)}. Found: {', '.join(headers) or 'none'}")

    candidates, skipped = [], 0
    for raw in reader:
        row = {(k or "").strip(): (v or "") for k, v in raw.items()}
        published = parse_local_time(row[cols["published"]], tz)
        title = row[cols["title"]].strip()
        summary = clean_spoken_text(row[cols["summary"]])
        status = row[cols["status"]].strip().upper()
        if not any(row.values()):
            continue
        if (published is None or not title or status != config["published_status"].upper()
                or len(summary) < config["min_summary_chars"]
                or published > now + timedelta(minutes=10)):
            skipped += 1
            continue
        candidates.append({"published": published, "title": title, "summary": summary})

    if not candidates:
        raise FeedError(f"No complete {config['published_status']} briefing found ({skipped} incomplete row(s) skipped).")
    latest = max(candidates, key=lambda c: c["published"])
    age = now - latest["published"]
    if age > timedelta(days=config["max_age_days"]):
        raise FeedError(f"Newest complete briefing is from {latest['published']:%Y-%m-%d %H:%M} Jamaica, "
                        f"older than {config['max_age_days']} days; Alexa would ignore it.")
    return latest


# ---------- feed ----------

def build_item(briefing: dict, site_url: str) -> dict:
    published_utc = briefing["published"].astimezone(timezone.utc)
    stamp = published_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    uid = uuid.uuid5(uuid.NAMESPACE_URL, f"andrews-cyber-brief:{stamp}:{briefing['title']}")
    return {
        "uid": f"urn:uuid:{uid}",
        "updateDate": stamp,
        "titleText": briefing["title"],
        "mainText": briefing["summary"],
        "redirectionUrl": site_url,
    }


def write_if_changed(path: Path, feed: list) -> bool:
    text = json.dumps(feed, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    return True


def todays_briefing_missing(briefing: dict, config: dict, now: datetime) -> bool:
    tz = ZoneInfo(config["timezone"])
    local_now = now.astimezone(tz)
    start, end = (time.fromisoformat(t) for t in config["stale_alert_window_local"])
    return start <= local_now.time() <= end and briefing["published"].astimezone(tz).date() < local_now.date()


def report(message: str) -> None:
    print(message)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(message + "\n\n")


def set_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site-url", required=True, help="HTTPS page for the Alexa app's Read More link")
    parser.add_argument("--csv-file", help="Read this CSV instead of downloading the sheet (testing)")
    parser.add_argument("--out", default=str(ROOT / "docs" / "briefing.json"))
    parser.add_argument("--test-feed", help="Also write a short diagnostic feed to this path")
    parser.add_argument("--now", help="Override current time, ISO 8601 with offset (testing)")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    args = parser.parse_args(argv)

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if not args.site_url.startswith("https://"):
        report("::error::--site-url must use HTTPS")
        return 1

    try:
        csv_text = Path(args.csv_file).read_text(encoding="utf-8-sig") if args.csv_file else fetch_csv(sheet_csv_url(config))
        briefing = select_latest(csv_text, config, now)
    except FeedError as exc:
        report(f"::error::Feed not updated. {exc} The last published feed stays live.")
        set_output("changed", "false")
        return 1

    item = build_item(briefing, args.site_url)
    changed = write_if_changed(Path(args.out), [item])
    set_output("changed", "true" if changed else "false")
    report(f"{'Updated' if changed else 'Unchanged'}: {item['titleText']} "
           f"(published {briefing['published']:%Y-%m-%d %H:%M} Jamaica, {item['updateDate']}, "
           f"{len(item['mainText'])} characters).")

    if args.test_feed:
        stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        test = [{"uid": f"andrew-diagnostic-{stamp}", "updateDate": stamp,
                 "titleText": "Andrew's Cyber Brief Test",
                 "mainText": "Hello Andrew. This is the diagnostic feed for Andrew's Cyber Brief. "
                             "If you can hear this, Alexa can reach your GitHub hosted feed.",
                 "redirectionUrl": args.site_url}]
        Path(args.test_feed).parent.mkdir(parents=True, exist_ok=True)
        Path(args.test_feed).write_text(json.dumps(test, indent=2) + "\n", encoding="utf-8")

    if todays_briefing_missing(briefing, config, now):
        report(f"::warning::No briefing for today yet. Alexa will read the "
               f"{briefing['published']:%Y-%m-%d} briefing until the sheet is updated.")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
