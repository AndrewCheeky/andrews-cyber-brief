# Andrew's Cyber Brief

A GitHub Actions workflow reads the briefing Google Sheet, takes the newest complete `PUBLISHED` row, and publishes it on GitHub Pages as a one-item Alexa Flash Briefing text feed (JSON). Alexa reads a static file, so it never waits for Apps Script.

## How it works

| Piece | Role |
|---|---|
| Google Sheet | Source of truth. Columns used: `Published (Jamaica)`, `Report title`, `Alexa summary`, `Status`. `Full report` is not published. |
| `scripts/build_feed.py` | Downloads the sheet as CSV, picks the newest row with status `PUBLISHED`, a valid Jamaica date/time, a title and a summary of at least 200 characters. Writes `docs/briefing.json`. |
| `.github/workflows/publish.yml` | Runs tests, builds the feed, commits it if it changed, deploys to Pages. |
| `apps-script/refresh-trigger.gs` | Optional: lets the sheet start a refresh immediately after it changes. |

**Feed item:** `uid` and `updateDate` come from the row's `Published (Jamaica)` time (converted to UTC). Rebuilding the same briefing produces identical output, so old content is never re-dated.

**Failure handling:** If the sheet can't be downloaded, has missing columns, has no complete row, or the newest briefing is more than 6 days old, nothing is replaced and the workflow fails (GitHub emails you). The last valid feed stays live. Between 6:45 and 9:00 a.m. Jamaica, a run also fails if today's briefing hasn't appeared, while Alexa keeps reading the previous one.

## Triggers

- **Manual:** Actions → Refresh and publish briefing → Run workflow (tick *force deploy* to redeploy unchanged content).
- **Sheet change:** `apps-script/refresh-trigger.gs` (recommended; GitHub's scheduler can be delayed).
- **Backup schedule:** every 10 minutes 5:00–6:50 a.m. Jamaica, and every 3 hours otherwise.

## Timing limits

Briefing ready by 6:30 a.m. depends on the chain: sheet row written → workflow (~1–2 min) → GitHub Pages cache (up to 10 min) → Alexa fetches about every 15 min (Amazon says new content can take up to 30 min). For reliable 6:30 playback, the row should be in the sheet by about 5:50 a.m. A later row may mean Alexa reads the previous day's briefing.

## Feed URLs

- Production: `https://andrewcheeky.github.io/andrews-cyber-brief/briefing.json`
- Diagnostic (fresh timestamp each deploy, for testing only): `.../test.json`

These assume the repository is named `andrews-cyber-brief` (no trailing dot).

## Local test

```
python -m unittest discover -s tests -v
python scripts/build_feed.py --site-url https://andrewcheeky.github.io/andrews-cyber-brief/ --csv-file sheet.csv --out /tmp/briefing.json
```

## Privacy

The sheet is shared as "anyone with the link can view", and its ID is in `config.json`. Anyone with the ID can read the whole sheet, including full reports. To keep the ID out of this public repo, add a `SHEET_CSV_URL` repository secret (`https://docs.google.com/spreadsheets/d/<ID>/export?format=csv`) and blank `sheet_id` in `config.json` (the ID also remains in git history). The published feed contains only the title and Alexa summary.

## Rollback

The old Apps Script feed URL is in git history (`source-url.txt`, before this change).
