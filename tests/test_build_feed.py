import csv
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_feed  # noqa: E402

CONFIG = json.loads((ROOT / "config.json").read_text())
HEADER = ["Published (Jamaica)", "Report title", "Full report", "Alexa summary", "Status"]
SITE = "https://example.github.io/andrews-cyber-brief/"
SUMMARY = ("Andrew, today's priority is a sample vulnerability.\n\nWhat changed: a vendor shipped a fix. "
           "Why it matters: Caribbean and LATAM enterprise customers run this product widely. "
           "Your three actions are: first, patch. Second, hunt. Third, brief account teams. ") * 2
NOW = datetime(2026, 11, 23, 6, 10, tzinfo=ZoneInfo("America/Jamaica"))


def make_csv(rows, header=HEADER):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def row(date, title=None, summary=SUMMARY, status="PUBLISHED"):
    return [date, title or f"Cyber Trend Watcher {date[:10]}", "Full report text", summary, status]


class SelectionTests(unittest.TestCase):
    def test_picks_newest_published_row_regardless_of_order(self):
        text = make_csv([row("2026-11-22 06:13"), row("2026-11-23 05:55"), row("2026-11-21 05:41")])
        self.assertEqual(build_feed.select_latest(text, CONFIG, NOW)["title"], "Cyber Trend Watcher 2026-11-23")

    def test_skips_incomplete_draft_and_future_rows(self):
        text = make_csv([
            row("2026-11-22 06:13"),
            row("2026-11-23 05:55", status="DRAFT"),
            row("2026-11-23 05:56", summary="Too short."),
            row("2026-11-23 05:57", summary=""),
            row("not a date"),
            row("2026-11-24 05:55"),  # future
        ])
        self.assertEqual(build_feed.select_latest(text, CONFIG, NOW)["title"], "Cyber Trend Watcher 2026-11-22")

    def test_empty_sheet_fails(self):
        with self.assertRaises(build_feed.FeedError):
            build_feed.select_latest(make_csv([]), CONFIG, NOW)

    def test_missing_column_fails_with_names(self):
        with self.assertRaisesRegex(build_feed.FeedError, "Alexa summary"):
            build_feed.select_latest(make_csv([row("2026-11-23 05:55")[:3]], HEADER[:3]), CONFIG, NOW)

    def test_stale_briefing_fails(self):
        with self.assertRaisesRegex(build_feed.FeedError, "older than"):
            build_feed.select_latest(make_csv([row("2026-11-10 06:00")]), CONFIG, NOW)


class ItemTests(unittest.TestCase):
    def test_item_fields_and_jamaica_time(self):
        briefing = build_feed.select_latest(make_csv([row("2026-11-23 05:55")]), CONFIG, NOW)
        item = build_feed.build_item(briefing, SITE)
        self.assertEqual(item["updateDate"], "2026-11-23T10:55:00Z")  # Jamaica is UTC-5, no DST
        self.assertTrue(item["uid"].startswith("urn:uuid:"))
        self.assertEqual(set(item), {"uid", "updateDate", "titleText", "mainText", "redirectionUrl"})
        self.assertNotIn("\n", item["mainText"])

    def test_identity_is_stable_across_builds(self):
        text = make_csv([row("2026-11-23 05:55")])
        a = build_feed.build_item(build_feed.select_latest(text, CONFIG, NOW), SITE)
        later = datetime(2026, 11, 23, 14, 0, tzinfo=ZoneInfo("America/Jamaica"))
        b = build_feed.build_item(build_feed.select_latest(text, CONFIG, later), SITE)
        self.assertEqual(a, b)

    def test_markup_removed_and_long_text_cut_at_sentence(self):
        cleaned = build_feed.clean_spoken_text("<b>Bold</b> start.\n\n" + "A full sentence here. " * 400)
        self.assertNotIn("<", cleaned)
        self.assertLessEqual(len(cleaned), 4500)
        self.assertTrue(cleaned.endswith("."))


class FetchTests(unittest.TestCase):
    def test_login_page_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            page = Path(d) / "login.html"
            page.write_text("<!DOCTYPE html><html><body>Sign in</body></html>")
            with self.assertRaisesRegex(build_feed.FeedError, "web page"):
                build_feed.fetch_csv(page.as_uri())

    def test_unreachable_sheet_is_reported(self):
        with self.assertRaisesRegex(build_feed.FeedError, "Could not download"):
            build_feed.fetch_csv("file:///nonexistent/sheet.csv")


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        for name in ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY"):
            os.environ.pop(name, None)

    def run_main(self, csv_text, out, now=NOW):
        src = Path(out).parent / "sheet.csv"
        src.write_text(csv_text, encoding="utf-8")
        return build_feed.main(["--site-url", SITE, "--csv-file", str(src), "--out", out, "--now", now.isoformat()])

    def test_writes_feed_then_reports_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            out = str(Path(d) / "briefing.json")
            self.assertEqual(self.run_main(make_csv([row("2026-11-23 05:55")]), out), 0)
            feed = json.loads(Path(out).read_text())
            self.assertEqual(len(feed), 1)
            before = Path(out).stat().st_mtime_ns
            self.assertEqual(self.run_main(make_csv([row("2026-11-23 05:55")]), out), 0)
            self.assertEqual(Path(out).stat().st_mtime_ns, before)

    def test_bad_input_keeps_last_valid_feed(self):
        with tempfile.TemporaryDirectory() as d:
            out = str(Path(d) / "briefing.json")
            self.run_main(make_csv([row("2026-11-23 05:55")]), out)
            good = Path(out).read_text()
            self.assertEqual(self.run_main(make_csv([]), out), 1)
            self.assertEqual(self.run_main("not,a,briefing\n1,2,3\n", out), 1)
            self.assertEqual(Path(out).read_text(), good)

    def test_missing_todays_briefing_is_flagged_but_feed_kept(self):
        with tempfile.TemporaryDirectory() as d:
            out = str(Path(d) / "briefing.json")
            late = datetime(2026, 11, 23, 7, 0, tzinfo=ZoneInfo("America/Jamaica"))
            self.assertEqual(self.run_main(make_csv([row("2026-11-22 06:13")]), out, late), 3)
            self.assertIn("2026-11-22", Path(out).read_text())


if __name__ == "__main__":
    unittest.main()
