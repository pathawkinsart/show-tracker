#!/usr/bin/env python3
"""Rebuild show-tracker-data.json from Slack ONLY (general version).

The event-driven counterpart to the Nov 24 manual rebuild
(rebuild_from_slack.py, which had the packed data hardcoded).
This script parses the two channels' fetched history and applies
the same in-house math:

  * a show that ended (has a #breaking-ops pic post) AND has been
    fully packed (Slack packed total >= its total) comes off;
  * a partially packed show stays with pkgs reduced by shipped;
  * a newly ended show (ops post, not already in the tracker) is
    added with pkgs = null (unknown total — Slack has no totals);
  * a show with no packed post stays at its current count.

Sources (fetched by slack_fetch.py into --data-dir):
  ops.json    — #breaking-ops messages: post timestamp = show end.
  packed.json — #breaking-shipping-updates messages: SHOW/DATE/Pkgs blocks.

Parsing reuses the existing slack_packed.py parser (from the shared
repo: forge-claude-shared/shipping-report/slack_packed.py) for the
packed posts — its SHOW/Pkgs regexes, code normalizer and carrier
totals. This script only adds the DATE-line extraction that the
in-house math needs (slack_packed itself aggregates per channel,
not per show). The #breaking-ops title parser is heuristic
(free-text titles); unparseable posts are logged and skipped,
never crash the build.

shippedPerDay / slaRules / premier / version and any other
top-level keys are preserved verbatim — the event rebuild only
touches the in-house show list (channelEnds, today, generatedAt).

Validation: the output must parse, have 1-200 shows, unique ids,
and every show must have date/end/platform and pkgs (int >= 0 or
null). On any validation failure the script exits non-zero and
writes nothing.

Usage:
  python3 slack_rebuild.py --data-dir slack-data [--in show-tracker-data.json]
      [--out show-tracker-data.json] [--slack-parser-dir DIR]
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

EST = ZoneInfo("America/New_York")

# Tracker platform names (must match build_tracker_json.py / the page).
PLATFORMS = [
    "Whatnot Footy", "Whatnot Main", "Whatnot Galaxy",
    "Whatnot Card Shop", "Whatnot Ice",
    "Fanatics Baseball", "Fanatics Jamz", "Fanatics Blitz",
    "Fanatics Evolution", "Fanatics Repack",
    "eBay Live", "TikTok Card Shop",
]

# Template column (slack_packed) -> tracker platform (build_tracker_json
# SLACK_COL_TO_CHANNEL, plus the Whatnot columns which the report builder
# takes from snapshot deltas but the Slack-only rebuild takes from Slack).
COLUMN_TO_PLATFORM = {
    "Footy": "Whatnot Footy",
    "WN OG": "Whatnot Main",
    "ICE": "Whatnot Ice",
    "Galaxy": "Whatnot Galaxy",
    "Card Shop": "Whatnot Card Shop",
    "Tik Tok": "TikTok Card Shop",
    "E-Bay live": "eBay Live",
    "FNBA": "Fanatics Jamz",
    "FNFLB": "Fanatics Blitz",
    "FMLB": "Fanatics Baseball",
    "Fpoke": "Fanatics Evolution",
    "FNFLR": "Fanatics Repack",
}

# #breaking-ops title -> tracker platform. Titles are free text; match on
# the normalized title (uppercased, spaces/hyphens removed). Order matters:
# check the specific codes before the general ones (e.g. TT CARDSHOP
# before CARDSHOP, FL-FB before WTH2).
OPS_CODE_MAP = [
    ("FLPK", "Fanatics Evolution"),      # WTH-FL-PK
    ("POKE", "Fanatics Evolution"),
    ("WNET", "Whatnot Galaxy"),          # WTH WN ET
    ("GALAXY", "Whatnot Galaxy"),
    ("FLBK", "Fanatics Jamz"),           # WTH FL-BK
    ("FLFB", "Fanatics Blitz"),          # WTH FL FB / FL-FB02
    ("FB02", "Fanatics Blitz"),
    ("REPACK", "Fanatics Repack"),
    ("WTH4", "Fanatics Repack"),
    ("EBAY", "eBay Live"),
    ("WTH1", "Whatnot Main"),
    ("WTH2", "Fanatics Baseball"),       # WTH2 / Wth 2 — after FLFB so "WTH FL FB 2B" is Blitz
    ("FLWTH2", "Fanatics Baseball"),
    ("FLMLB", "Fanatics Baseball"),
    ("ICE", "Whatnot Ice"),
    ("WNHK", "Whatnot Ice"),
    ("TTCARDSHOP", "TikTok Card Shop"),
    ("TIKTOK", "TikTok Card Shop"),
    ("FOOTY", "Whatnot Footy"),
    ("WNSC", "Whatnot Footy"),
    ("CARDSHOP", "Whatnot Card Shop"),
]

_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?")
# DATE line inside a packed post: "DATE: 11/20", "DATE: 11/21 B",
# "DATE: 11/17/2026 A".
_PACKED_DATE_RE = re.compile(
    r"^DATE\s*:\s*(\d{1,2})/(\d{1,2})(?:/(\d{4}))?(?:\s+([ABC]))?\s*$",
    re.IGNORECASE | re.MULTILINE)


def load_slack_packed_parser(parser_dir):
    """Import slack_packed.py (shared repo) and return the module.
    Searches --slack-parser-dir, then the Action checkout layout
    (shared-repo/shipping-report), then the local workspace."""
    candidates = []
    if parser_dir:
        candidates.append(parser_dir)
    candidates += [
        os.path.join(os.getcwd(), "shared-repo", "shipping-report"),
        os.path.expanduser("~/workspace/forge-claude-shared-repo/shipping-report"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "forge-claude-shared-repo", "shipping-report"),
    ]
    for d in candidates:
        path = os.path.join(d, "slack_packed.py")
        if os.path.isfile(path):
            sys.path.insert(0, d)
            import slack_packed
            return slack_packed
    raise RuntimeError(
        "slack_packed.py not found (tried: %s). The Action checks out "
        "forge-claude-shared into shared-repo/; pass --slack-parser-dir "
        "to point at shipping-report/." % ", ".join(candidates))


def parse_packed_posts(packed_messages, sp):
    """Per (platform, show_date, session) -> shipped pkgs (max across
    duplicate posts for the same show+session).

    Reuses slack_packed's SHOW/Pkgs regexes, code normalizer and
    carrier totals; adds the DATE line (show date + session letter)
    that slack_packed itself does not return."""
    packed = {}
    unknown = []
    for m in packed_messages:
        text = m.get("text", "")
        for sm in sp._SHOW_RE.finditer(text):
            code_raw = sm.group(1)
            code = sp._norm_code(code_raw)
            rest = text[sm.end():]
            pm = sp._PKGS_LINE_RE.search(rest)
            if not pm:
                continue
            next_block = re.search(r"^(SHOW|E-?COMMERCE)\s*:",
                                   rest, re.IGNORECASE | re.MULTILINE)
            if next_block and pm.start() > next_block.start():
                continue
            total = sp._pkgs_total(pm.group(1))
            if not total:
                continue
            # DATE line for this SHOW block (between SHOW and the next block).
            block = rest[:next_block.start()] if next_block else rest
            dm = _PACKED_DATE_RE.search(block)
            if not dm:
                unknown.append(("no DATE line", code_raw, total))
                continue
            month, day = int(dm.group(1)), int(dm.group(2))
            year = int(dm.group(3)) if dm.group(3) else None
            session = dm.group(4) or ""
            # Session can also ride on the SHOW code ("WTH-1 B", "WN-FOOTY 11/21 B"
            # is the DATE line; "SHOW: FL-MLB B" is the code). _norm_code strips
            # a trailing letter; recover it from the raw code.
            if not session:
                raw_norm = re.sub(r"[\s-]+", "", (code_raw or "").upper())
                if len(raw_norm) > 1 and raw_norm[-1] in "ABC":
                    bare = raw_norm[:-1]
                    if bare in sp.CODE_TO_COLUMN:
                        session = raw_norm[-1]
            if code is None:
                unknown.append(("unknown code", code_raw, total))
                continue
            column = sp.CODE_TO_COLUMN[code]
            platform = COLUMN_TO_PLATFORM.get(column)
            if platform is None:
                unknown.append(("unmapped column", column, total))
                continue
            # Show year: from the DATE line, else the post year.
            post_year = None
            try:
                post_year = datetime.fromtimestamp(
                    float(m.get("ts", 0)), EST).year
            except (TypeError, ValueError):
                pass
            show_year = year or post_year or datetime.now(EST).year
            try:
                show_date = datetime(show_year, month, day).date().isoformat()
            except ValueError:
                unknown.append(("bad DATE", dm.group(0), total))
                continue
            key = (platform, show_date, session)
            # Duplicates (same show re-posted) report the same packages
            # twice — take the max, never the sum.
            packed[key] = max(packed.get(key, 0), total)
    return packed, unknown


def parse_ops_posts(ops_messages):
    """Parse #breaking-ops pic posts into
    [(platform, show_date, session, end_iso)].
    end = the post timestamp (the pic is taken when the break ends).
    Returns (parsed, skipped) where skipped is [(reason, text)]."""
    parsed = []
    skipped = []
    for m in ops_messages:
        text = (m.get("text") or "").strip()
        ts = m.get("ts")
        if not text or not ts:
            continue
        # Skip obvious non-show chatter (no date in the text at all).
        dm = _DATE_RE.search(text)
        if not dm:
            skipped.append(("no date in text", text[:80]))
            continue
        try:
            post_dt = datetime.fromtimestamp(float(ts), EST)
        except (TypeError, ValueError):
            skipped.append(("bad timestamp", text[:80]))
            continue
        month, day = int(dm.group(1)), int(dm.group(2))
        year = int(dm.group(3)) if dm.group(3) else post_dt.year
        try:
            show_date = datetime(year, month, day).date()
        except ValueError:
            skipped.append(("bad date", text[:80]))
            continue
        # Night shows are posted after midnight with the prior day's date
        # in the text; if the text date is in the future vs the post date,
        # it is last year's show (Dec/Jan boundary) — nudge back a year.
        if (show_date - post_dt.date()).days > 1:
            try:
                show_date = show_date.replace(year=year - 1)
            except ValueError:
                skipped.append(("bad date", text[:80]))
                continue
        # Session: NIGHT = B; else a standalone A/B/C token; else a letter
        # attached to the date ("11/23B"); else a letter attached to the
        # code token ("WTH1A", "Wth 2a", "WTH2B").
        upper = text.upper()
        if "NIGHT" in upper:
            session = "B"
        else:
            tokens = re.findall(r"\b([ABC])\b", upper)
            if tokens:
                session = tokens[0]
            else:
                m2 = re.search(r"\d{1,2}/\d{1,2}([ABC])\b", upper)
                if m2:
                    session = m2.group(1)
                else:
                    m3 = re.search(r"\b(?:WTH|FL|WN)[\s\-]*[A-Z0-9]*([ABC])\b", upper)
                    session = m3.group(1) if m3 else ""
        # Platform from the normalized title.
        norm = re.sub(r"[\s\-]+", "", upper)
        platform = None
        for code, plat in OPS_CODE_MAP:
            if code in norm:
                platform = plat
                break
        if platform is None:
            skipped.append(("unmapped title", text[:80]))
            continue
        parsed.append((platform, show_date.isoformat(), session,
                       post_dt.strftime("%Y-%m-%dT%H:%M:%S")))
    return parsed, skipped


def load_snapshot_totals(paths):
    """Original per-show totals from the SLA snapshot CSVs (shared
    repo's sla/snapshots/*.csv). Each row: (channel, show_start,
    show_end, shipments). Loads every file given, newest first,
    newest row winning per (channel, show_start) — mirroring
    build_tracker_json.load_all_snapshot_ends, because a channel
    missing from the newest snapshot (Footy, not re-read in the
    Nov 23 5 PM run; TikTok, never in a snapshot) keeps its last
    read instead of breaking the build.
    Returns {(channel, show_start): (show_end, shipments)} and
    {(channel, show_date): [(show_start, shipments), ...]}."""
    import csv
    import glob
    by_start = {}
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += glob.glob(os.path.join(p, "*.csv"))
        elif os.path.isfile(p):
            files.append(p)
    # Newest first by the date in the filename.
    def key(fn):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(fn))
        return m.group(1) if m else ""
    files.sort(key=key, reverse=True)
    for path in files:
        try:
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    ch = (row.get("channel") or "").strip()
                    start = (row.get("show_start") or "").strip()
                    end = (row.get("show_end") or "").strip()
                    try:
                        shipments = int(row.get("shipments") or 0)
                    except ValueError:
                        continue
                    try:
                        start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M")
                    except ValueError:
                        continue
                    if not ch or shipments <= 0:
                        continue
                    # Newest wins: only set when not already present.
                    by_start.setdefault((ch, start_dt), (end, shipments))
        except (OSError, ValueError):
            continue
    return by_start


def snapshot_total_for(by_start, platform, date, end, group):
    """Original total for one tracker show from the snapshot rows.
    Tries, in order:
      1. exact match on (platform, show_end) — the tracker's end
         came from this snapshot row's show_end;
      2. (platform, show_date) + index within the group (the Nth
         show of the day = the Nth snapshot row, both time-ordered).
    Returns None when no snapshot row matches (e.g. TikTok, which
    is never in a snapshot; or a show that ended after the last
    snapshot)."""
    if end:
        for (ch, start_dt), (row_end, shipments) in by_start.items():
            if ch != platform:
                continue
            try:
                row_end_dt = datetime.strptime(row_end, "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            if row_end_dt.strftime("%Y-%m-%dT%H:%M:%S") == end:
                return shipments
    # Fall back to (platform, date) + index within the group.
    rows = sorted(
        [(s, v[1]) for (c, s), v in by_start.items()
         if c == platform and s.date().isoformat() == date])
    if rows and len(rows) == len(group):
        idx = group.index(next(s for s in group if s["end"] == end))
        if idx < len(rows):
            return rows[idx][1]
    return None


def rebuild(current, packed, ops_shows, now, snapshot_totals=None):
    """Apply the in-house math. Returns (new_data, report_lines).

    IDEMPOTENCY: each show carries a stored `total` (its original
    package count) and `shipped` (cumulative shipped applied).
    Every run recomputes shipped from the full packed-post history
    and sets pkgs = total - shipped, so re-running with the same
    posts changes nothing. The stored `total` is the source of
    truth; it is derived only once, when a show first lacks it:
      1. the SLA snapshot (authoritative original total), then
      2. pkgs + shipped (correct when the file is a Slack-rebuild
         output whose pkgs is already reduced — the state the
         Action first sees after the Nov 24 manual rebuild).
    A show with total=None (unknown total) is never reduced.
    Date-level packed totals are a pool shared by every show of
    that (platform, date): allocated to the earliest-ending show
    first, capped by each show's total."""
    snapshot_totals = snapshot_totals or {}
    report = []
    old_shows = current["shows"]

    # Group tracker shows by (platform, date) for snapshot matching
    # and pool allocation (both need the group's shows in end order).
    from collections import defaultdict
    groups = defaultdict(list)
    for s in sorted(old_shows,
                    key=lambda x: (x["platform"], x["date"], x["end"])):
        groups[(s["platform"], s["date"])].append(s)

    # Date-level packed pools: (platform, date) -> cumulative pkgs.
    pool = {}
    for (p, d, s), v in packed.items():
        if s == "":
            pool[(p, d)] = max(pool.get((p, d), 0), v)

    # Per-show shipped (cumulative) from the packed data. Session-level
    # posts first; otherwise the date-level pool, allocated earliest-first.
    # The pool allocation needs each show's total as the cap, so resolve
    # totals first (below), then allocate.
    def resolve_total(show, shipped_cum):
        """Original total for one show. Prefers the stored `total`,
        then the snapshot (authoritative), then pkgs + shipped
        (correct only when the file is a Slack-rebuild output whose
        pkgs is already reduced — the state the Action first sees
        after the Nov 24 manual rebuild; see the module docstring
        and README for the report-path caveat)."""
        if show.get("total") is not None:
            return show["total"]
        group = groups[(show["platform"], show["date"])]
        snap = snapshot_total_for(
            snapshot_totals, show["platform"], show["date"],
            show["end"], group)
        if snap is not None:
            return snap
        # Slack-baseline derivation: pkgs is already reduced by shipped.
        if show.get("pkgs") is None:
            return None
        return show["pkgs"] + shipped_cum

    # First pass: session-level shipped per show (no pool involved).
    session_shipped = {}
    for s in old_shows:
        platform, date = s["platform"], s["date"]
        sess = [v for (p, d, ss), v in packed.items()
                if p == platform and d == date and ss != ""]
        if sess:
            session_shipped[id(s)] = (
                max(sess) if platform == "Whatnot Footy" else sum(sess))

    # Second pass: pool allocation per (platform, date) group, using
    # each show's total as the cap. Shows with session posts don't
    # draw from the pool.
    pool_alloc = {}
    for (platform, date), group in groups.items():
        remaining_pool = pool.get((platform, date), 0)
        if not remaining_pool:
            continue
        for s in group:
            if id(s) in session_shipped:
                continue
            if s.get("pkgs") is None and s.get("total") is None:
                continue  # unknown total — cannot draw from the pool
            # Total for capping: stored, snapshot, or pkgs + pool share.
            # For the derivation, the show's share is min(pool, total);
            # total itself is pkgs + share, so solve: if the pool fits
            # entirely in this show (pool <= total), share = pool and
            # total = pkgs + pool. That holds whenever the show is still
            # present with pkgs > 0 (it was not fully shipped).
            total = resolve_total(s, remaining_pool)
            if total is None:
                continue
            share = min(remaining_pool, total)
            pool_alloc[id(s)] = share
            remaining_pool -= share
            if remaining_pool <= 0:
                break

    kept, removed, changed = [], [], []
    for s in sorted(old_shows,
                    key=lambda x: (x["platform"], x["date"], x["end"])):
        platform, date = s["platform"], s["date"]
        if s.get("pkgs") is None and s.get("total") is None:
            # Unknown total — Slack cannot prove it shipped. Keep as-is.
            kept.append(dict(s))
            continue
        if id(s) in session_shipped:
            shipped = session_shipped[id(s)]
        elif id(s) in pool_alloc:
            shipped = pool_alloc[id(s)]
        else:
            shipped = 0
        total = resolve_total(s, shipped)
        if total is None:
            kept.append(dict(s))
            continue
        remaining = total - shipped
        ns = dict(s)
        ns["total"] = total
        ns["shipped"] = shipped
        if remaining <= 0:
            removed.append((platform, date, total, shipped))
            continue
        ns["pkgs"] = remaining
        if shipped and ns["pkgs"] != s.get("pkgs"):
            changed.append((platform, date, total, shipped, remaining))
        kept.append(ns)

    for r in removed:
        report.append(f"OFF: {r[0]} {r[1]} total={r[2]} shipped={r[3]}")
    for c in changed:
        report.append(
            f"REDUCED: {c[0]} {c[1]} {c[2]} -> {c[4]} (shipped {c[3]})")

    # New / updated shows from #breaking-ops.
    existing_keys = {(s["platform"], s["date"], s.get("part", ""))
                     for s in kept}
    next_id = max((s["id"] for s in kept), default=0) + 1
    for platform, date, part, end in ops_shows:
        key = (platform, date, part)
        match = None
        for s in kept:
            if (s["platform"], s["date"], s.get("part", "")) == key:
                match = s
                break
        if match is not None:
            # Already in the tracker (e.g. from the snapshot): the pic
            # post time is the true end — use it when it is earlier
            # than the current end (the 5 AM fallback is later).
            if end < match["end"]:
                match["end"] = end
                report.append(f"END UPDATED: {platform} {date} "
                              f"part={part or '-'} -> {end}")
            continue
        if key in existing_keys:
            continue
        entry = {
            "date": date,
            "end": end,
            "id": next_id,
            "note": "",
            "pkgs": None,
            "platform": platform,
        }
        if part:
            entry["part"] = part
        kept.append(entry)
        existing_keys.add(key)
        next_id += 1
        report.append(f"ADD: {platform} {date} part={part or '-'} "
                      f"end={end} pkgs=null")

    kept.sort(key=lambda x: (x["platform"], x["date"], x["end"]))
    for i, s in enumerate(kept, start=1):
        s["id"] = i

    channel_ends = {}
    for s in kept:
        ch = s["platform"]
        if ch not in channel_ends or s["end"] < channel_ends[ch]:
            channel_ends[ch] = s["end"]
    channel_ends = {k: channel_ends[k] for k in sorted(channel_ends)}

    data = dict(current)
    data["shows"] = kept
    data["channelEnds"] = channel_ends
    data["today"] = now.date().isoformat()
    data["generatedAt"] = now.strftime("%Y-%m-%dT%H:%M:%S-05:00")
    return data, report


def validate(data):
    """Sanity checks before anything is written. Raises on failure."""
    shows = data.get("shows")
    if not isinstance(shows, list) or not (1 <= len(shows) <= 200):
        raise ValueError(f"show count {len(shows) if isinstance(shows, list) else shows!r} out of range 1-200")
    ids = set()
    for s in shows:
        if not s.get("date") or not s.get("end") or not s.get("platform"):
            raise ValueError(f"show missing date/end/platform: {s!r}")
        pkgs = s.get("pkgs")
        if pkgs is not None and (not isinstance(pkgs, int) or pkgs < 0):
            raise ValueError(f"bad pkgs {pkgs!r} on {s!r}")
        if s.get("id") in ids:
            raise ValueError(f"duplicate show id {s.get('id')}")
        ids.add(s.get("id"))
    if not data.get("slaRules"):
        raise ValueError("slaRules missing — refusing to write")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="slack-data")
    ap.add_argument("--in", dest="in_path", default="show-tracker-data.json")
    ap.add_argument("--out", dest="out_path", default=None,
                    help="default: overwrite --in")
    ap.add_argument("--slack-parser-dir", default=None)
    ap.add_argument("--snapshot", default=None,
                    help="SLA snapshot CSV file or snapshots directory "
                         "(shared repo) — source of original per-show "
                         "totals when the tracker file has no stored "
                         "`total`. May be given more than once.")
    ap.add_argument("--snapshot-dir", default=None,
                    help="same as --snapshot, but a directory")
    args = ap.parse_args()

    with open(args.in_path, encoding="utf-8") as f:
        current = json.load(f)

    def load(name):
        path = os.path.join(args.data_dir, name)
        if not os.path.exists(path):
            # Token-less mode: the VM browser-read crons sync slack-data/
            # into the repo; a channel whose read failed simply has no
            # file yet. Treat it as "no messages" — never crash the
            # rebuild and never invent posts.
            print(f"WARN: {path} missing — treating {name} as empty")
            return []
        with open(path, encoding="utf-8") as f:
            return json.load(f)["messages"]

    ops_messages = load("ops.json")
    packed_messages = load("packed.json")
    print(f"ops: {len(ops_messages)} messages, "
          f"packed: {len(packed_messages)} messages")

    sp = load_slack_packed_parser(args.slack_parser_dir)
    packed, unknown = parse_packed_posts(packed_messages, sp)
    print(f"packed posts parsed: {len(packed)} show totals")
    for u in unknown:
        print(f"  WARN packed skipped: {u[0]} {u[1]!r} pkgs={u[2]}")

    ops_shows, skipped = parse_ops_posts(ops_messages)
    print(f"ops posts parsed: {len(ops_shows)} shows")
    for reason, text in skipped:
        print(f"  WARN ops skipped: {reason}: {text!r}")

    now = datetime.now(EST)
    snap_paths = []
    if args.snapshot:
        snap_paths.append(args.snapshot)
    if args.snapshot_dir:
        snap_paths.append(args.snapshot_dir)
    snapshot_totals = load_snapshot_totals(snap_paths) if snap_paths else {}
    if snap_paths:
        print(f"snapshot totals: {len(snapshot_totals)} show rows")
    data, report = rebuild(current, packed, ops_shows, now, snapshot_totals)
    for line in report:
        print("  " + line)
    known = sum(s["pkgs"] for s in data["shows"] if s["pkgs"] is not None)
    nulls = sum(1 for s in data["shows"] if s["pkgs"] is None)
    print(f"NEW: {len(data['shows'])} shows, {known} known pkgs, "
          f"{nulls} shows with pkgs=null")

    validate(data)
    out_path = args.out_path or args.in_path
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"wrote {out_path} (generatedAt {data['generatedAt']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
