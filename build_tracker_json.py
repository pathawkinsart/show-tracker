#!/usr/bin/env python3
"""Regenerate show-tracker-data.json from the latest Shipping Report data.

Reads the IN_HOUSE list out of the latest build_report.py (AST-parsed, never
executed) and the show end times out of the latest SLA snapshot CSV in the
working repo (TikTok sessions come from TIKTOK-7DAY-FINDINGS.md, since TikTok
is not in the snapshot). Emits the tracker JSON in exactly the schema the
Pages site reads (same keys, same platform names, same slaRules, version 1).

Rules are NOT re-derived here: the in-house list is the report builder's
IN_HOUSE (already settled-rule filtered by the report build). This script
only converts that list into the tracker schema and verifies it reconciles
to the report's totals.

Usage:
    python3 build_tracker_json.py [--repo DIR] [--builder PATH] [--out PATH]
Defaults: repo = ~/workspace/forge-claude-shared-repo,
          builder = ~/workspace/shipping-report/build_report.py,
          out = stdout.
Exit non-zero if any show's end time can't be found or the totals don't
reconcile -- never emit a wrong file.
"""
import argparse
import ast
import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

EST = ZoneInfo("America/New_York")
WORKSPACE = os.path.expanduser("~/workspace")
DEFAULT_REPO = os.path.join(WORKSPACE, "forge-claude-shared-repo")
DEFAULT_BUILDER = os.path.join(WORKSPACE, "shipping-report", "build_report.py")
SNAP_DIR = "reports/7-day-rolling/sla/snapshots"
FINDINGS_DIR = "reports/7-day-rolling"
TRACKER_JSON = "reports/7-day-rolling/show-tracker/show-tracker-data.json"


def parse_ast_list(path, name):
    """Extract a top-level list literal (e.g. IN_HOUSE) from a .py file by AST."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise ValueError(f"{name} not found in {path}")


def parse_report_dt(date_str, start_str, year):
    """'Nov 21' + '8:02 PM' -> datetime. Year from the run; nudged if the
    result lands more than 10 days from the run date (Dec/Jan boundary).
    'evening' (TikTok sessions whose times were not read) parses as 6:00 PM
    for date purposes only - the tracker never shows a start time, and the
    end still falls back to the 5 AM rule."""
    if start_str.strip().lower() == "evening":
        start_str = "6:00 PM"
    dt = datetime.strptime(f"{date_str} {year} {start_str}", "%b %d %Y %I:%M %p")
    return dt


def latest_snapshot(repo):
    """Newest sla/snapshots/*.csv by the date in its filename (YYYY-MM-DD-am|pm[N]).
    The suffix number breaks same-day ties: pm2 is newer than pm."""
    d = os.path.join(repo, SNAP_DIR)
    snaps = []
    for fn in os.listdir(d):
        m = re.match(r"(\d{4}-\d{2}-\d{2})-(am|pm)(\d*)\.csv$", fn)
        if not m:
            continue
        key = (m.group(1), 0 if m.group(2) == "am" else 1, int(m.group(3) or 0))
        snaps.append((key, os.path.join(d, fn), m.group(1)))
    if not snaps:
        raise ValueError(f"no SLA snapshot found in {d}")
    snaps.sort(key=lambda s: s[0], reverse=True)
    return snaps  # newest first: [(key, path, run_date)]


def load_all_snapshot_ends(repo):
    """Merged (channel, show_start) -> show_end across all snapshots,
    newest first (a re-read wins). A channel missing from the newest
    snapshot (e.g. Footy, not re-read this run) keeps its last read ends
    instead of breaking the build."""
    ends = {}
    for _key, path, _run_date in latest_snapshot(repo):
        try:
            for k, v in load_snapshot_ends(path).items():
                ends.setdefault(k, v)
        except (KeyError, ValueError):
            # Old-format snapshot (no show_end column) - skip it.
            continue
    return ends


def snapshot_covering(repo, in_house):
    """Newest snapshot date, verified against the MERGED ends across all
    snapshots: a channel missing from the newest snapshot (e.g. Footy,
    not re-read) is covered by its last read instead of rejecting the
    whole build. A partial snapshot (e.g. the eBay-only 2026-11-23-pm
    proof pass) must never break the tracker build. TikTok end times
    come from TIKTOK-7DAY-FINDINGS.md, not the snapshot - never let a
    TikTok show reject a snapshot."""
    snaps = latest_snapshot(repo)
    newest_path, run_date = snaps[0][1], snaps[0][2]
    ends = load_all_snapshot_ends(repo)
    run_year = int(run_date[:4])
    run_dt = datetime.strptime(run_date, "%Y-%m-%d")
    for (channel, date_str, _letter, _show, start_str, _pkgs) in in_house:
        # TikTok end times come from TIKTOK-7DAY-FINDINGS.md, not the
        # snapshot — never let a TikTok show reject a snapshot.
        if channel == "TikTok Card Shop":
            continue
        start_dt = parse_report_dt(date_str, start_str, run_year)
        if (run_dt - start_dt).days > 8:
            start_dt = start_dt.replace(year=run_year + 1)
        if (run_dt - start_dt).days < -1:
            start_dt = start_dt.replace(year=run_year - 1)
        if (channel, start_dt) not in ends:
            raise ValueError(
                f"no SLA snapshot covers {channel} show starting {start_dt}")
    return newest_path, run_date


def load_snapshot_ends(path):
    """(channel, show_start datetime) -> show_end datetime, from the snapshot."""
    ends = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ch = row["channel"]
            start = datetime.strptime(row["show_start"], "%Y-%m-%d %H:%M")
            end = datetime.strptime(row["show_end"], "%Y-%m-%d %H:%M")
            ends[(ch, start)] = end
    return ends


def load_tiktok_ends(path, year):
    """Parse the 'LIVE sessions (start/end EST)' table in TIKTOK findings.
    Returns {(channel, start datetime): end datetime} and {start date: end}."""
    text = open(path, encoding="utf-8").read()
    ends = {}
    for m in re.finditer(
        r"\|\s*([A-Z][a-z]{2} \d{1,2}, \d{1,2}:\d{2} [AP]M)\s*\|\s*([A-Z][a-z]{2} \d{1,2}, \d{1,2}:\d{2} [AP]M)\s*\|",
        text,
    ):
        start = datetime.strptime(f"{m.group(1)} {year}", "%b %d, %I:%M %p %Y")
        end = datetime.strptime(f"{m.group(2)} {year}", "%b %d, %I:%M %p %Y")
        # Sessions can end after midnight; if end <= start, it rolled to next day.
        if end <= start:
            end += timedelta(days=1)
        ends[("TikTok Card Shop", start)] = end
    return ends


def load_tiktok_ends_fallback(path, year):
    """Fallback TikTok LIVE session ends from SHOW-END-TIMES-*.md.
    The findings file's LIVE sessions table is only as fresh as
    the last LIVE-page read — when a run rewrites it without the
    table (Nov 23: Chrome closed mid-run), the ends are still in
    the end-times file. Parses the TikTok section's table rows:
    | star | Pacific start | duration | EST start | EST end |
    Session | ... Returns {(channel, start datetime): end datetime}.
    """
    ends = {}
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return ends
    # Only the TikTok section.
    start = text.find("## TikTok (LIVE sessions")
    if start < 0:
        return ends
    section = text[start:]
    nxt = section.find("\n## ", 1)
    if nxt > 0:
        section = section[:nxt]
    for line in section.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        try:
            est_start = datetime.strptime(
                f"{cells[3]} {year}", "%b %d, %I:%M %p %Y")
            est_end = datetime.strptime(
                f"{cells[4]} {year}", "%b %d, %I:%M %p %Y")
        except (ValueError, IndexError):
            continue
        if est_end <= est_start:
            est_end += timedelta(days=1)
        ends[("TikTok Card Shop", est_start)] = est_end
    return ends


def load_folder_shows(repo):
    """The Breaking folder read (breaking-folder-shows.json):
    shows pulled the Team Break way, each with a Bins package
    count (pkgs) when the sheet was in the 7-day window and
    readable. Returns {(channel, date_iso, session): pkgs} and
    {date_iso: [show dicts]}. Empty when the folder read has not
    run/published yet — the tracker then falls back to the
    snapshot counts alone."""
    path = os.path.join(
        repo, FINDINGS_DIR, "breaking-folder-shows.json")
    if not os.path.exists(path):
        return {}, {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}, {}
    by_key = {}
    by_date = {}
    for s in data.get("sheets", []) or []:
        key = (s.get("channel") or "", s.get("date_iso") or "",
               s.get("session") or "")
        if s.get("pkgs") is not None:
            by_key[key] = s["pkgs"]
        by_date.setdefault(s.get("date_iso") or "", []).append(s)
    return by_key, by_date


def folder_end_5am(date_iso):
    """Patrick's rule (Nov 23): when the break is over the breaker
    takes a pic; if there is no pic, the show auto-ends at 5 AM
    the morning after the show date. Returns a datetime."""
    d = datetime.strptime(date_iso, "%Y-%m-%d").date()
    return datetime.combine(
        d + timedelta(days=1), datetime.min.time()).replace(hour=5)


# ---------- SHIPPED PACKAGES PER CHANNEL PER DAY ----------
# Patrick (Nov 24): a Mon-Sun table of shipped packages per
# channel per day, visible at the bottom of the tracker page
# and exportable as Excel. Same computation as the sibling
# section in the daily report (build_report.py) — Whatnot =
# delta of cumulative shipped (Shipping + Delivered) per show
# between daily SLA snapshots (the weekly-template method);
# non-Whatnot = Slack packed posts by post date
# (slack_packed.py + Jr.'s per-day slack-packed JSON files).
# A day with no usable snapshot / no Slack file is blank
# (null in the JSON), never invented. Days after the run date
# are blank — they have not happened yet.
SHIPPED_CHANNELS = [
    "Whatnot Footy", "Whatnot Main", "Whatnot Galaxy",
    "Whatnot Card Shop", "Whatnot Ice",
    "Fanatics Baseball", "Fanatics Jamz", "Fanatics Blitz",
    "Fanatics Evolution", "Fanatics Repack",
    "eBay Live", "TikTok Card Shop",
]
# Slack template column -> tracker channel (non-Whatnot only;
# Whatnot columns come from the snapshot delta, never Slack,
# so they cannot double-count).
SLACK_COL_TO_CHANNEL = {
    "FMLB": "Fanatics Baseball",
    "FNBA": "Fanatics Jamz",
    "FNFLB": "Fanatics Blitz",
    "Fpoke": "Fanatics Evolution",
    "FNFLR": "Fanatics Repack",
    "E-Bay live": "eBay Live",
    "Tik Tok": "TikTok Card Shop",
}


def _read_snapshot_shipped(path):
    """Per-show shipped (shipping + delivered) keyed by
    channel|show_start|title."""
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            channel = (row.get("channel") or "").strip()
            if not channel.startswith("Whatnot"):
                continue
            shipped = int(row.get("shipping") or 0) + int(row.get("delivered") or 0)
            key = "%s|%s|%s" % (channel, (row.get("show_start") or "").strip(),
                                (row.get("show_title") or "").strip())
            out[key] = shipped
    return out


def _snapshots_with_coverage(repo):
    """Snapshot CSVs with Whatnot coverage, as (date, name, path)
    sorted by date then name. A partial snapshot (no Whatnot
    rows) is skipped, never used as a delta baseline."""
    snap_dir = os.path.join(repo, SNAP_DIR)
    out = []
    try:
        names = sorted(os.listdir(snap_dir))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".csv"):
            continue
        m = re.match(r"(\d{4}-\d{2}-\d{2})", name)
        if not m:
            continue
        path = os.path.join(snap_dir, name)
        try:
            with open(path, newline="", encoding="utf-8") as fh:
                has = any((r.get("channel") or "").startswith("Whatnot")
                          for r in csv.DictReader(fh))
        except Exception:
            has = False
        if has:
            out.append((m.group(1), name, path))
    out.sort()
    return out


def _whatnot_daily_deltas(repo, week_days):
    """Per-day Whatnot shipped deltas. For each day, the delta
    is the latest snapshot on that day minus the latest
    snapshot before that day (both with Whatnot coverage).
    Returns ({day: {channel: pkgs}}, {day: reason},
    {day: (prev_date, cur_date)}). A channel present in the
    day's snapshot with no increase reads 0 (a real zero); a
    channel absent from the snapshot is not in the dict at all
    (no data)."""
    snaps = _snapshots_with_coverage(repo)
    by_date = {}
    for dstr, name, path in snaps:
        by_date[dstr] = path
    ordered = sorted(by_date.items())
    deltas = {}
    blank = {}
    intervals = {}
    for day in week_days:
        dstr = day.isoformat()
        if dstr not in by_date:
            blank[day] = "no snapshot for %s" % day.strftime("%a %b %d")
            continue
        prev = None
        for pd, pp in ordered:
            if pd < dstr:
                prev = (pd, pp)
            else:
                break
        if prev is None:
            blank[day] = "no earlier snapshot to delta against"
            continue
        cur = _read_snapshot_shipped(by_date[dstr])
        prv = _read_snapshot_shipped(prev[1])
        present = set(k.split("|", 1)[0] for k in cur)
        day_delta = {ch: 0 for ch in present}
        for key, shipped in cur.items():
            d = shipped - prv.get(key, 0)
            if d < 0:
                d = 0
            if d:
                ch = key.split("|", 1)[0]
                day_delta[ch] = day_delta.get(ch, 0) + d
        deltas[day] = day_delta
        intervals[day] = (prev[0], dstr)
    return deltas, blank, intervals


def _slack_daily_totals(repo, week_days):
    """Per-day non-Whatnot shipped from Jr.'s slack-packed JSON
    files. Returns ({day: {channel: pkgs}}, {day: reason})."""
    totals = {}
    blank = {}
    slack_dir = os.path.join(repo, FINDINGS_DIR, "slack-packed")
    try:
        sys.path.insert(0, os.path.join(repo, "shipping-report"))
        import slack_packed
    except Exception:
        for day in week_days:
            blank[day] = "Slack parser unavailable"
        return totals, blank
    for day in week_days:
        path = os.path.join(slack_dir, day.isoformat() + ".json")
        if not os.path.exists(path):
            blank[day] = "no Slack packed file for %s" % day.strftime("%a %b %d")
            continue
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            blank[day] = "Slack file for %s unreadable" % day.strftime("%a %b %d")
            continue
        messages = [(day, m.get("text", "")) for m in data.get("messages", [])]
        parsed = slack_packed.daily_totals(messages)
        day_tot = {}
        for col, pkgs in parsed.get(day, {}).items():
            if col.startswith("UNKNOWN"):
                continue
            ch = SLACK_COL_TO_CHANNEL.get(col)
            if ch:
                day_tot[ch] = day_tot.get(ch, 0) + pkgs
        totals[day] = day_tot
    return totals, blank


def shipped_per_day(repo, run_date):
    """Build the Mon-Sun shipped-per-day table for the week
    containing run_date (a datetime.date). Returns a JSON-ready
    dict: weekStart/weekEnd, days (["Mon 11/23", ...]),
    channels ({channel: [Mon..Sun] with ints or null}),
    totals ([Mon..Sun] ints or null), notes ([str]).
    Days after run_date are null — they have not happened yet."""
    monday = run_date - timedelta(days=run_date.weekday())
    week_days = [monday + timedelta(days=i) for i in range(7)]
    wn, wn_blank, wn_intervals = _whatnot_daily_deltas(repo, week_days)
    sl, sl_blank = _slack_daily_totals(repo, week_days)
    notes = []
    for day in week_days:
        if day > run_date:
            continue
        if day in wn_intervals:
            prev_d, cur_d = wn_intervals[day]
            notes.append(
                "%s Whatnot: delta between the %s snapshot and the "
                "%s snapshot."
                % (day.strftime("%a %b %d"),
                   datetime.strptime(prev_d, "%Y-%m-%d").strftime("%a %b %d"),
                   datetime.strptime(cur_d, "%Y-%m-%d").strftime("%a %b %d")))
    for day in week_days:
        if day > run_date:
            continue
        if day in wn:
            present = set(wn[day].keys())
            missing = [c for c in SHIPPED_CHANNELS
                       if c.startswith("Whatnot") and c not in present]
            if missing:
                notes.append(
                    "%s: %s not re-read in the snapshot, so no figure "
                    "for %s (blank, not zero)."
                    % (day.strftime("%a %b %d"), ", ".join(missing),
                       ", ".join(missing)))
    for day in week_days:
        if day > run_date:
            continue
        if day in sl_blank:
            notes.append(
                "%s: %s, so Fanatics / eBay / TikTok shipped per day "
                "is blank (never invented)."
                % (day.strftime("%a %b %d"), sl_blank[day]))
    channels = {}
    for ch in SHIPPED_CHANNELS:
        vals = []
        for day in week_days:
            if day > run_date:
                vals.append(None)
                continue
            if ch.startswith("Whatnot"):
                vals.append(wn.get(day, {}).get(ch))
            else:
                vals.append(sl.get(day, {}).get(ch))
        channels[ch] = vals
    totals = []
    for i, day in enumerate(week_days):
        if day > run_date:
            totals.append(None)
            continue
        s = 0
        any_val = False
        for ch in SHIPPED_CHANNELS:
            v = channels[ch][i]
            if v is not None:
                s += v
                any_val = True
        totals.append(s if any_val else None)
    return {
        "weekStart": week_days[0].isoformat(),
        "weekEnd": week_days[6].isoformat(),
        "days": [d.strftime("%a %m/%d") for d in week_days],
        "channels": channels,
        "totals": totals,
        "notes": notes,
    }


def build(repo, builder_path):
    in_house = parse_ast_list(builder_path, "IN_HOUSE")
    folder_pkgs, _folder_by_date = load_folder_shows(repo)
    snapshot_path, run_date = snapshot_covering(repo, in_house)
    run_year = int(run_date[:4])
    # The cover table rows are locals inside the builder script, not top-level
    # names; totals are verified against IN_HOUSE itself in verify().
    snap_ends = load_all_snapshot_ends(repo)
    tiktok_path = os.path.join(repo, FINDINGS_DIR, "TIKTOK-7DAY-FINDINGS.md")
    tiktok_ends = load_tiktok_ends(tiktok_path, run_year) if os.path.exists(tiktok_path) else {}
    # Fallback: when the findings file's LIVE table is missing
    # (a run rewrote it without a LIVE read), the ends still live
    # in SHOW-END-TIMES-2026-11-20.md. Findings win on overlap.
    end_times_path = os.path.join(
        repo, FINDINGS_DIR, "SHOW-END-TIMES-2026-11-20.md")
    if os.path.exists(end_times_path):
        for key, end in load_tiktok_ends_fallback(
                end_times_path, run_year).items():
            tiktok_ends.setdefault(key, end)

    # slaRules: copy verbatim from the existing tracker JSON so it can never drift.
    existing_path = os.path.join(repo, TRACKER_JSON)
    with open(existing_path, encoding="utf-8") as f:
        existing = json.load(f)
    sla_rules = existing["slaRules"]

    # Premier Shop status (Whatnot channels only): read from premier.json
    # next to this builder. It holds Whatnot's own per-account
    # On-Time Scan % vs the 95% Premier bar (period, defect-free,
    # earned / not qualifying), from Claude's Premier Shop page reads.
    # The builder carries it into the tracker JSON verbatim so the
    # auto-update crons keep it live; update premier.json (not the
    # tracker JSON) when the numbers are re-read.
    premier = None
    premier_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "premier.json")
    if os.path.exists(premier_path):
        with open(premier_path, encoding="utf-8") as f:
            premier = json.load(f)

    shows = []
    unmatched = []
    for i, (channel, date_str, letter, show, start_str, pkgs) in enumerate(in_house, start=1):
        start_dt = parse_report_dt(date_str, start_str, run_year)
        # Nudge across a year boundary if the parsed start is implausible
        # relative to the run date (show within the last 8 days).
        run_dt = datetime.strptime(run_date, "%Y-%m-%d")
        if (run_dt - start_dt).days > 8:
            start_dt = start_dt.replace(year=run_year + 1)
        if (run_dt - start_dt).days < -1:
            start_dt = start_dt.replace(year=run_year - 1)
        end = snap_ends.get((channel, start_dt))
        if end is None and channel == "TikTok Card Shop":
            end = tiktok_ends.get((channel, start_dt))
            if end is None:
                # TikTok findings start times can differ by a minute from the
                # report; match by channel + start date, nearest start.
                same_day = [
                    (s, e) for (c, s), e in tiktok_ends.items()
                    if c == channel and s.date() == start_dt.date()
                ]
                if same_day:
                    same_day.sort(key=lambda se: abs((se[0] - start_dt).total_seconds()))
                    end = same_day[0][1]
        if end is None:
            # Patrick's rule (Nov 23): no pic, the show auto-ends
            # at 5 AM the morning after the show date. This always
            # gives an end, so no show is dropped for a missing end.
            end = folder_end_5am(start_dt.date().isoformat())
        if end is None:
            unmatched.append((channel, date_str, start_str, show))
            continue
        # Package count: the Bins tab count from the Breaking
        # folder wins when the folder read has it (one bin = one
        # package); the snapshot count is the fallback.
        folder_key = (channel, start_dt.date().isoformat(), letter or "")
        bins_pkgs = folder_pkgs.get(folder_key)
        entry = {
            "date": start_dt.date().isoformat(),
            "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
            "id": i,
            "note": "",
            "pkgs": bins_pkgs if bins_pkgs is not None else pkgs,
            "platform": channel,
        }
        if letter:
            entry["part"] = letter
        if "[PSA]" in show:
            entry["psa"] = True
        shows.append(entry)

    if unmatched:
        raise ValueError(
            "no end time found for %d show(s): %s"
            % (len(unmatched), unmatched)
        )

    # channelEnds = end of the oldest (earliest-ending) in-house show per channel.
    channel_ends = {}
    for s in shows:
        ch = s["platform"]
        if ch not in channel_ends or s["end"] < channel_ends[ch]:
            channel_ends[ch] = s["end"]
    channel_ends = {k: channel_ends[k] for k in sorted(channel_ends)}

    # The Breaking folder (Patrick, Nov 23): the Show Tracker
    # takes its show count from the Breaking folder, the way Team
    # Break does. The shop app writes breaking-folder-shows.json
    # each pass (the folder is located once in the app settings
    # and remembered in shop.db). It updates each day with
    # yesterday's shows: the JSON carries yesterday's sheets
    # explicitly. The folder is the show inventory — which shows
    # happened, with dates. Shipped still comes from Slack
    # (Patrick): the folder never says shipped, so the builder
    # does NOT cross-check folder sheets against the in-house
    # list here — a folder show missing from in-house may simply
    # be shipped (Slack-confirmed), not missed. The in-house
    # list itself still comes from the report builder.
    breaking = None
    folder_path = os.path.join(
        repo, FINDINGS_DIR, "breaking-folder-shows.json")
    if os.path.exists(folder_path):
        with open(folder_path, encoding="utf-8") as f:
            folder_data = json.load(f)
        run_d = datetime.strptime(run_date, "%Y-%m-%d").date()
        window_start = run_d - timedelta(days=6)
        folder_in_window = [
            s for s in (folder_data.get("sheets") or [])
            if window_start.isoformat() <= (s.get("date_iso") or "")
            <= run_date]
        breaking = {
            "folder": folder_data.get("folder", ""),
            "exists": bool(folder_data.get("exists")),
            "read_at": folder_data.get("read_at", ""),
            "sheet_count": folder_data.get("sheet_count", 0),
            "yesterday": folder_data.get("yesterday", ""),
            "yesterday_count": folder_data.get("yesterday_count", 0),
            "yesterday_sheets": folder_data.get("yesterday_sheets", []),
            "window_count": len(folder_in_window),
        }

    now = datetime.now(EST)
    run_d = datetime.strptime(run_date, "%Y-%m-%d").date()
    data = {
        "channelEnds": channel_ends,
        "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%S-05:00"),
        "shows": shows,
        "shippedPerDay": shipped_per_day(repo, run_d),
        "slaRules": sla_rules,
        "today": run_date,
        "version": 1,
    }
    if premier is not None:
        data["premier"] = premier
    if breaking is not None:
        data["breakingFolder"] = breaking
    return data, snapshot_path, run_date


def verify(data, builder_path):
    """Reconcile the generated JSON against the report builder's own totals."""
    in_house = parse_ast_list(builder_path, "IN_HOUSE")
    expect_shows = len(in_house)
    expect_pkgs = sum(row[5] for row in in_house)
    got_shows = len(data["shows"])
    got_pkgs = sum(s["pkgs"] for s in data["shows"])
    problems = []
    if got_shows != expect_shows:
        problems.append(f"show count {got_shows} != report {expect_shows}")
    if got_pkgs != expect_pkgs:
        problems.append(f"pkgs {got_pkgs} != report {expect_pkgs}")
    # Per-channel sums must match the report's in-house sums.
    from collections import defaultdict
    exp_ch, got_ch = defaultdict(int), defaultdict(int)
    for row in in_house:
        exp_ch[row[0]] += row[5]
    for s in data["shows"]:
        got_ch[s["platform"]] += s["pkgs"]
    for ch in sorted(set(exp_ch) | set(got_ch)):
        if exp_ch[ch] != got_ch[ch]:
            problems.append(f"{ch}: {got_ch[ch]} != report {exp_ch[ch]}")
    if problems:
        raise ValueError("RECONCILE FAILED: " + "; ".join(problems))
    return expect_shows, expect_pkgs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--builder", default=DEFAULT_BUILDER)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    data, snapshot_path, run_date = build(args.repo, args.builder)
    n_shows, n_pkgs = verify(data, args.builder)
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {args.out} (snapshot {snapshot_path}, run {run_date})")
    else:
        sys.stdout.write(text)
    print(f"VERIFIED: {n_shows} shows / {n_pkgs} pkgs reconcile to the report",
          file=sys.stderr)


if __name__ == "__main__":
    main()
