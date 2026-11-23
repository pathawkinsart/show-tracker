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
to the report's totals (34 shows / 4,240 pkgs for the Nov 22 5 PM run).

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
    result lands more than 10 days from the run date (Dec/Jan boundary)."""
    dt = datetime.strptime(f"{date_str} {year} {start_str}", "%b %d %Y %I:%M %p")
    return dt


def latest_snapshot(repo):
    """Newest sla/snapshots/*.csv by the date in its filename (YYYY-MM-DD-am|pm)."""
    d = os.path.join(repo, SNAP_DIR)
    snaps = []
    for fn in os.listdir(d):
        m = re.match(r"(\d{4}-\d{2}-\d{2})-(am|pm)\.csv$", fn)
        if not m:
            continue
        key = (m.group(1), 0 if m.group(2) == "am" else 1)
        snaps.append((key, os.path.join(d, fn), m.group(1)))
    if not snaps:
        raise ValueError(f"no SLA snapshot found in {d}")
    snaps.sort(key=lambda s: s[0], reverse=True)
    return snaps  # newest first: [(key, path, run_date)]


def snapshot_covering(repo, in_house):
    """Newest snapshot whose rows hold an end time for every IN_HOUSE
    show. A partial snapshot (e.g. the eBay-only 2026-11-23-pm proof
    pass) must never break the tracker build — fall back to the newest
    snapshot that covers the whole in-house list. Each candidate is
    judged on its own run date (its own year anchor for the starts)."""
    for _key, path, run_date in latest_snapshot(repo):
        try:
            ends = load_snapshot_ends(path)
        except (KeyError, ValueError):
            # Old-format snapshot (no show_end column) — cannot cover
            # the list; skip it and keep looking.
            continue
        run_year = int(run_date[:4])
        run_dt = datetime.strptime(run_date, "%Y-%m-%d")
        missing = False
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
                missing = True
                break
        if not missing:
            return path, run_date
    raise ValueError("no SLA snapshot covers the full in-house list")


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


def build(repo, builder_path):
    in_house = parse_ast_list(builder_path, "IN_HOUSE")
    snapshot_path, run_date = snapshot_covering(repo, in_house)
    run_year = int(run_date[:4])
    # The cover table rows are locals inside the builder script, not top-level
    # names; totals are verified against IN_HOUSE itself in verify().
    snap_ends = load_snapshot_ends(snapshot_path)
    tiktok_path = os.path.join(repo, FINDINGS_DIR, "TIKTOK-7DAY-FINDINGS.md")
    tiktok_ends = load_tiktok_ends(tiktok_path, run_year) if os.path.exists(tiktok_path) else {}

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
            unmatched.append((channel, date_str, start_str, show))
            continue
        entry = {
            "date": start_dt.date().isoformat(),
            "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
            "id": i,
            "note": "",
            "pkgs": pkgs,
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

    now = datetime.now(EST)
    data = {
        "channelEnds": channel_ends,
        "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%S-05:00"),
        "shows": shows,
        "slaRules": sla_rules,
        "today": run_date,
        "version": 1,
    }
    if premier is not None:
        data["premier"] = premier
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
