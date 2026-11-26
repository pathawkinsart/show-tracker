#!/usr/bin/env python3
"""Rebuild show-tracker-data.json from Slack ONLY — pure-Slack model.

Clean rewrite (Nov 24, 2026, Patrick's start-over rule). The previous
slack_rebuild.py was patched five times around a data model that
contradicted the rule below, and it kept telling Patrick shows still
had "packages left" — a number that came from the Excel/Bins side,
not Slack. This file replaces it. Do not re-add the removed model.

THE RULE (Patrick, stated repeatedly):
  The tracker goes by SLACK NUMBERS ONLY. If Slack says a show
  shipped, it shipped. The tracker must NOT know "packages left"
  from any other source — no Bins, no Excel/snapshots, no worksheet
  counts. Slack's packed posts are the only source of shipped.
  Slack's ops pics are the only source of shows. That is the whole
  model.

THE MODEL:
  * Shows enter the tracker ONLY from #breaking-ops pic posts
    (channel, date, session, end = post time). A show with no
    pic-derived end auto-ends at 5 AM the morning after its date
    (Patrick, Nov 23).
  * A show is DONE and comes OFF the tracker when a packed post
    from #breaking-shipping-updates names it. Any packed post
    naming the show = shipped = off. No total, no "left", no
    packed>=total test, no cross-check against any other source.
    Patrick-confirmed completes (completed-shows.json) also come
    off, and are never resurrected by a later ops pic.
  * Show objects carry ONLY: platform, date, end, id, note — plus
    `part` (the A/B/C session letter, from the ops pic; the page
    needs it to label session chips) and `psa` (display flag for
    the "Free PSA Grading" badge; carried from the existing show,
    or set when an ops pic title mentions PSA). NO pkgs, NO total,
    NO shipped on show objects. Shipped counts live ONLY in the
    shipped-per-day table, which this script preserves verbatim
    and never recomputes.
  * The in-house show list persists in show-tracker-data.json
    across runs. Ops posts only ADD new shows and update end
    times; packed posts and completed-shows.json only REMOVE
    them. A show whose ops pic has aged out of the 8-day Slack
    window is NOT dropped — it stays until a packed post names
    it or Patrick confirms it complete.

Sources (fetched by slack_fetch.py / the VM browser-read cron
into --data-dir):
  ops.json    — #breaking-ops messages: post timestamp = show end.
  packed.json — #breaking-shipping-updates messages: SHOW/DATE/Pkgs blocks.

Parsing reuses the vendored slack_packed.py parser (vendor/) for
the packed posts — its SHOW/Pkgs regexes, code normalizer and
carrier totals — plus the DATE-line extraction that maps a packed
block to a show date and session. The #breaking-ops title parser
is heuristic (free-text titles); unparseable posts are logged and
skipped, never crash the build.

shippedPerDay / slaRules / premier / version and any other
top-level keys are preserved verbatim — the rebuild only touches
the in-house show list (shows, channelEnds, today, generatedAt).

Validation: the output must parse, have 1-200 shows, unique ids,
and every show must have date/end/platform. On any validation
failure the script exits non-zero and writes nothing.

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

# Tracker platform names (must match the page's ALL_CHANNELS).
PLATFORMS = [
    "Whatnot Footy", "Whatnot Main", "Whatnot Galaxy",
    "Whatnot Card Shop", "Whatnot Ice",
    "Fanatics Baseball", "Fanatics Jamz", "Fanatics Blitz",
    "Fanatics Evolution", "Fanatics Repack",
    "eBay Live", "TikTok Card Shop",
]

# Template column (slack_packed) -> tracker platform.
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
    ("PKFL", "Fanatics Evolution"),      # WTH-PK-FL (same show, code reversed)
    ("POKE", "Fanatics Evolution"),
    ("WNENT", "Whatnot Galaxy"),         # WTH WN ENT (Galaxy day show)
    ("WNET", "Whatnot Galaxy"),          # WTH WN ET (same show, no N)
    ("GALAXY", "Whatnot Galaxy"),
    ("FLBK", "Fanatics Jamz"),           # WTH FL-BK
    ("JAMZ", "Fanatics Jamz"),           # "FL JAMZ" in title/body
    ("FLFB", "Fanatics Blitz"),          # WTH FL FB / FL-FB02
    ("FB02", "Fanatics Blitz"),
    ("FLBL", "Fanatics Blitz"),           # WTH FL BL (same show, BL for Blitz)
    ("REPACK", "Fanatics Repack"),
    ("FLRP", "Fanatics Repack"),          # WTH FL RP
    ("WTH4", "Fanatics Repack"),
    ("EBAY", "eBay Live"),
    ("WTH1", "Whatnot Main"),
    ("WTH2", "Fanatics Baseball"),       # WTH2 / Wth 2 — after FLFB so "WTH FL FB 2B" is Blitz
    ("FLWTH2", "Fanatics Baseball"),
    ("FLMLB", "Fanatics Baseball"),
    # CARDSHOP above ICE: ICE is a bare substring and fires inside body
    # words ("notice", "price") — a Card Shop post with notes mis-mapped
    # to Whatnot Ice on 2026-11-26. Specific codes before general ones.
    ("CARDSHOP", "Whatnot Card Shop"),
    ("ICE", "Whatnot Ice"),
    ("WNHK", "Whatnot Ice"),
    ("TTCARDSHOP", "TikTok Card Shop"),
    ("TTCS", "TikTok Card Shop"),         # WTH TT CS
    ("TIKTOK", "TikTok Card Shop"),
    ("WTHTT", "TikTok Card Shop"),        # WTH TT / WTH TT PP / WTH TT Studio — bare TT, WTH-anchored so it cannot fire inside body words
    ("FOOTY", "Whatnot Footy"),
    ("WNSC", "Whatnot Footy"),
]

_MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
           "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
# Every date shape a breaker might type in an ops title: ISO
# (2026-11-24), month names (Nov 24, 24 Nov), numeric with / or -
# (11/24, 11-24-26), dotted with a year (11.24.26). Day+month only
# takes the post year. Dotted without a year is NOT a date — a bare
# "1.5" is a decimal. Each pattern yields (year, month, day) with
# year None when it is not given.
_MONTH_WORD = (r"(JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|MAY|"
               r"JUN(?:E)?|JUL(?:Y)?|AUG(?:UST)?|SEP(?:T(?:EMBER)?)?|"
               r"OCT(?:OBER)?|NOV(?:EMBER)?|DEC(?:EMBER)?)")
_DATE_PATTERNS = [
    ("iso", re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})")),
    ("month_first", re.compile(
        r"\b" + _MONTH_WORD + r"\.?\s+(\d{1,2})(?:\s*,?\s*(\d{4}|\d{2}))?",
        re.IGNORECASE)),
    ("day_first", re.compile(
        r"\b(\d{1,2})\s+" + _MONTH_WORD + r"\.?(?:\s*,?\s*(\d{4}|\d{2}))?",
        re.IGNORECASE)),
    # No word boundary on numeric: dates ride attached to a code
    # ("WTH 1B11/24", "WTH-FL-PK-A11/23"). A token that cannot be
    # a date (month 19 in #1119-11) is skipped by the caller.
    ("numeric", re.compile(r"(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{4}|\d{2}))?")),
    ("dotted", re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})")),
]


def _norm_year(s, post_dt):
    """Year from a regex group: None -> post year, 2-digit -> 20YY."""
    if s is None:
        return post_dt.year
    y = int(s)
    return 2000 + y if y < 100 else y


def extract_show_date(text, post_dt):
    """First date-like token in the text that forms a real date.

    Candidates are tried in text order; a token that cannot be a
    date (month 19 in a break number like #1119-11) is skipped in
    favour of a later token. Returns a datetime.date or None."""
    cands = []
    for kind, pat in _DATE_PATTERNS:
        for m in pat.finditer(text):
            if kind == "iso":
                y, mo, d = (int(m.group(1)), int(m.group(2)),
                            int(m.group(3)))
            elif kind == "month_first":
                mo = _MONTHS[m.group(1).upper()[:3]]
                d = int(m.group(2))
                y = _norm_year(m.group(3), post_dt)
            elif kind == "day_first":
                d = int(m.group(1))
                mo = _MONTHS[m.group(2).upper()[:3]]
                y = _norm_year(m.group(3), post_dt)
            else:  # numeric / dotted
                mo, d = int(m.group(1)), int(m.group(2))
                y = _norm_year(m.group(3), post_dt)
            cands.append((m.start(), y, mo, d))
    cands.sort(key=lambda c: c[0])
    for _pos, y, mo, d in cands:
        try:
            return datetime(y, mo, d).date()
        except ValueError:
            continue
    return None


# DATE line inside a packed post: "DATE: 11/20", "DATE: 11/21 B",
# "DATE: 11/17/2026 A", "Date: 11/21/26" (2-digit year — Caleb
# Jordan's posts use MM/DD/YY; normalized to 20YY in the parser).
# Separators may be slashes or dashes ("DATE: 11-21-26").
_PACKED_DATE_RE = re.compile(
    r"^DATE\s*:\s*(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{4}|\d{2}))?(?:[\s/\-]+([ABC]))?\s*$",
    re.IGNORECASE | re.MULTILINE)


def end_5am(date_iso):
    """Patrick's rule (Nov 23): when the break is over the breaker
    takes a pic; if there is no pic, the show auto-ends at 5 AM
    the morning after the show date. Returns "YYYY-MM-DDTHH:MM:SS"."""
    d = datetime.strptime(date_iso, "%Y-%m-%d").date()
    return datetime.combine(
        d + timedelta(days=1), datetime.min.time()).replace(
            hour=5).strftime("%Y-%m-%dT%H:%M:%S")


def load_slack_packed_parser(parser_dir):
    """Import slack_packed.py and return the module.
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
    that slack_packed itself does not return.

    The pure-Slack model only needs the KEYS (which shows a packed
    post names); the counts are returned for logging and are never
    used to compute a total, a remainder, or a removal test."""
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
            if year is not None and year < 100:
                year += 2000  # 2-digit year ("11/21/26") means 2026
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
        try:
            post_dt = datetime.fromtimestamp(float(ts), EST)
        except (TypeError, ValueError):
            skipped.append(("bad timestamp", text[:80]))
            continue
        # Skip obvious non-show chatter (no date in the text at all).
        # extract_show_date tries every date shape (ISO, month names,
        # numeric / or -, dotted with a year, day+month only) and
        # skips tokens that cannot be a date (break numbers).
        show_date = extract_show_date(text, post_dt)
        if show_date is None:
            # No date in the text. If the post still names a known
            # channel it is a show pic — a show happened. The show
            # started the day before when posted after midnight
            # (a 2 AM post is the prior day's show); otherwise it
            # is the post day's show. Anything that names no known
            # channel is chatter and stays skipped.
            norm0 = re.sub(r"[\s\-]+", "", text.upper())
            if not any(code in norm0 for code, _ in OPS_CODE_MAP):
                skipped.append(("no date in text", text[:80]))
                continue
            if post_dt.hour < 6:
                show_date = post_dt.date() - timedelta(days=1)
            else:
                show_date = post_dt.date()
        # Night shows are posted after midnight with the prior day's date
        # in the text; if the text date is in the future vs the post date,
        # it is last year's show (Dec/Jan boundary) — nudge back a year.
        if (show_date - post_dt.date()).days > 1:
            try:
                show_date = show_date.replace(year=show_date.year - 1)
            except ValueError:
                skipped.append(("bad date", text[:80]))
                continue
        # Session: NIGHT = B; else a standalone A/B/C token; else a letter
        # attached before the date ("A11/23" — the caption jams the
        # session letter between the code and the date, e.g.
        # "WTH-FL-PK-A11/23" or "WTH1B11/22"); else a letter on a
        # number right before the date ("2A-11/22", "2B 11/22") —
        # this must beat the code-token fallback, which would
        # otherwise read the B in "FL FB"; else a letter attached
        # to the date ("11/23B"); else a letter attached to the code
        # token ("WTH1A", "Wth 2a", "WTH2B").
        upper = text.upper()
        if "NIGHT" in upper:
            session = "B"
        else:
            tokens = re.findall(r"\b([ABC])\b", upper)
            if tokens:
                session = tokens[0]
            else:
                m1b = re.search(r"([ABC])\d{1,2}[/\-]\d{1,2}", upper)
                if m1b:
                    session = m1b.group(1)
                else:
                    m2 = re.search(r"\d{1,2}[/\-]\d{1,2}([ABC])\b", upper)
                    if m2:
                        session = m2.group(1)
                    else:
                        m2b = re.search(r"\d([ABC])[\s\-]*\d{1,2}[/\-]\d{1,2}", upper)
                        if m2b:
                            session = m2b.group(1)
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


def is_named_by_packed(packed_keys, platform, date, part):
    """True when a packed post names this show.

    A packed post names a show when:
      * its (platform, date, session) matches the show's session
        exactly, or
      * it is a date-level post (session "") for the show's date —
        it covers every show of that date, or
      * the show is date-level (part "") and any packed post for
        its (platform, date) exists — the session posts together
        cover the whole date's break.
    No count is compared. Any post naming the show = shipped."""
    if (platform, date, part) in packed_keys:
        return True
    if (platform, date, "") in packed_keys:
        return True
    if part == "":
        return any(p == platform and d == date
                   for (p, d, _s) in packed_keys)
    return False


def is_skipped(platform, date, part, packed_keys, completed):
    """True when a show looks skipped: the team packs in order, so if
    a LATER show on the same channel is already shipped (packed post
    or Patrick-confirmed complete), this earlier show must have been
    packed too — the team just never posted for it. A show is NOT
    flagged when no later show on its channel has shipped yet (it may
    simply not be packed yet). Display-only; never removes a show."""
    for (p, d, s) in packed_keys:
        if p != platform:
            continue
        if d > date:
            return True
        # Same date, later session: if B is packed but A is not, A
        # was skipped (the team packs A before B).
        if d == date and s and part and s > part:
            return True
    for (p, d) in completed:
        if p == platform and d > date:
            return True
    return False


REGULAR_DAYS = 4  # a channel that ran >=4 of the last 7 days "usually" runs


def missing_yesterday_flags(ops_shows, now):
    """Channels that usually run but have no show for yesterday.

    Returns [{platform, date}] — display only, never a show and
    never counted. A show that happened and already shipped still
    counts as having happened (ops pics are the record). The flag
    clears on its own at the next rebuild once the pic post lands
    (or the Update button is pressed after it is posted)."""
    yesterday = (now - timedelta(days=1)).date()
    week_start = yesterday - timedelta(days=7)
    days_with_show = {}
    yesterday_platforms = set()
    for platform, date, part, end in ops_shows:
        if platform not in PLATFORMS:
            continue
        try:
            d = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d == yesterday:
            yesterday_platforms.add(platform)
        elif week_start <= d < yesterday:
            days_with_show.setdefault(platform, set()).add(d)
    flags = []
    for platform in PLATFORMS:
        if len(days_with_show.get(platform, ())) >= REGULAR_DAYS \
                and platform not in yesterday_platforms:
            flags.append({"platform": platform,
                          "date": yesterday.isoformat()})
    return flags


def rebuild(current, packed_keys, ops_shows, ops_psa, now, completed):
    """Apply the pure-Slack model. Returns (new_data, report_lines).

    packed_keys: set of (platform, date, session) named by packed posts.
    ops_shows:   list of (platform, date, part, end) from ops pics.
    ops_psa:     set of (platform, date, part) whose ops pic title
                 mentions PSA (display flag for the page's PSA badge).
    completed:   set of (platform, date) Patrick confirmed complete.

    A show comes off when a packed post names it, or when it is in
    completed-shows.json. Nothing else removes a show. New shows
    enter only from ops pics. Show objects carry no package counts.
    """
    old_shows = current["shows"]
    report = []
    kept, removed = [], []

    for s in sorted(old_shows,
                    key=lambda x: (x["platform"], x["date"], x["end"])):
        platform, date = s["platform"], s["date"]
        part = s.get("part", "")
        if (platform, date) in completed:
            removed.append((platform, date, "confirmed complete"))
            continue
        if is_named_by_packed(packed_keys, platform, date, part):
            removed.append((platform, date, "packed post"))
            continue
        # Keep, stripped to the Slack-only fields. No pkgs, no total,
        # no shipped — the tracker does not know package counts.
        entry = {
            "date": date,
            "end": s.get("end") or end_5am(date),
            "id": s["id"],
            "note": s.get("note", ""),
            "platform": platform,
        }
        if part:
            entry["part"] = part
        if s.get("psa"):
            entry["psa"] = True
        kept.append(entry)

    for r in removed:
        report.append(f"OFF: {r[0]} {r[1]} ({r[2]})")

    # New shows from #breaking-ops.
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
            # Already in the tracker: the pic post time is the true
            # end — use it when it is earlier than the current end
            # (the 5 AM fallback is later).
            if end < match["end"]:
                match["end"] = end
                report.append(f"END UPDATED: {platform} {date} "
                              f"part={part or '-'} -> {end}")
            continue
        if key in existing_keys:
            continue
        if (platform, date) in completed:
            # Patrick-confirmed complete — do not resurrect it.
            report.append(f"SKIP (confirmed complete): {platform} {date} "
                          f"part={part or '-'}")
            continue
        if is_named_by_packed(packed_keys, platform, date, part):
            # Already packed (a packed post exists) — it shipped, so
            # do not add it as an in-house show.
            report.append(f"SKIP (packed): {platform} {date} "
                          f"part={part or '-'}")
            continue
        try:
            end_dt = datetime.strptime(
                end, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=EST)
        except ValueError:
            end_dt = None
        if end_dt is not None and \
                (now - end_dt).total_seconds() > 48 * 3600:
            # Ended more than 48h ago and still not in the tracker:
            # it shipped. Do not add a stale entry.
            report.append(f"SKIP (ended >48h ago): {platform} {date} "
                          f"part={part or '-'} end={end}")
            continue
        entry = {
            "date": date,
            "end": end,
            "id": next_id,
            "note": "",
            "platform": platform,
        }
        if part:
            entry["part"] = part
        if key in ops_psa:
            entry["psa"] = True
        kept.append(entry)
        existing_keys.add(key)
        next_id += 1
        report.append(f"ADD: {platform} {date} part={part or '-'} "
                      f"end={end}")

    # Same-end dedup: one pic post can only end one show, so two
    # entries for a channel+date ending the same second are one show
    # seen twice — e.g. a session letter the old parser read ("A")
    # and the new one missed (""), which the page then letters "C".
    # Keep the entry that carries the session letter; drop the
    # part-less twin(s). Groups where both carry letters are left
    # alone (distinct sessions that share an end second).
    grouped = {}
    for s in kept:
        grouped.setdefault((s["platform"], s["date"], s["end"]), []).append(s)
    deduped = []
    for key, group in grouped.items():
        parted = [s for s in group if s.get("part")]
        if len(group) > 1 and len(parted) == 1:
            for s in group:
                if not s.get("part"):
                    report.append(
                        f"DEDUP: {key[0]} {key[1]} end={key[2]} "
                        f"dropped part-less twin of part={parted[0]['part']}")
            deduped.append(parted[0])
        else:
            deduped.extend(group)
    kept = deduped

    kept.sort(key=lambda x: (x["platform"], x["date"], x["end"]))
    for i, s in enumerate(kept, start=1):
        s["id"] = i

    # Skipped-show flag: the team packs in order, so a show whose
    # LATER sibling on the same channel is already shipped, but
    # which has no packed post of its own, was almost certainly
    # packed without being posted. Flag it for Patrick; the flag is
    # display-only and never removes a show.
    for s in kept:
        if is_skipped(s["platform"], s["date"], s.get("part", ""),
                      packed_keys, completed):
            s["skipped"] = True
            report.append(f"FLAG skipped: {s['platform']} {s['date']} "
                          f"part={s.get('part') or '-'}")

    channel_ends = {}
    for s in kept:
        ch = s["platform"]
        if ch not in channel_ends or s["end"] < channel_ends[ch]:
            channel_ends[ch] = s["end"]
    channel_ends = {k: channel_ends[k] for k in sorted(channel_ends)}

    data = dict(current)
    data["shows"] = kept
    data["channelEnds"] = channel_ends
    data["noPost"] = missing_yesterday_flags(ops_shows, now)
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
        if "pkgs" in s or "total" in s or "shipped" in s:
            raise ValueError(f"show carries a package count: {s!r}")
        if s.get("id") in ids:
            raise ValueError(f"duplicate show id {s.get('id')}")
        ids.add(s.get("id"))
    if not data.get("slaRules"):
        raise ValueError("slaRules missing — refusing to write")
    for f in data.get("noPost", []):
        if not f.get("platform") or not f.get("date"):
            raise ValueError(f"bad noPost flag: {f!r}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="slack-data")
    ap.add_argument("--in", dest="in_path", default="show-tracker-data.json")
    ap.add_argument("--out", dest="out_path", default=None,
                    help="default: overwrite --in")
    ap.add_argument("--slack-parser-dir", default=None)
    ap.add_argument("--snapshot", default=None,
                    help="accepted and ignored: the pure-Slack model "
                         "takes no totals from SLA snapshots.")
    ap.add_argument("--snapshot-dir", default=None,
                    help="accepted and ignored: see --snapshot.")
    args = ap.parse_args()

    if args.snapshot or args.snapshot_dir:
        print("NOTE: --snapshot/--snapshot-dir ignored — the tracker "
              "is Slack-only and takes no totals from snapshots.")

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
    print(f"packed posts parsed: {len(packed)} show keys")
    for u in unknown:
        print(f"  WARN packed skipped: {u[0]} {u[1]!r} pkgs={u[2]}")
    packed_keys = set(packed.keys())

    ops_shows, skipped = parse_ops_posts(ops_messages)
    print(f"ops posts parsed: {len(ops_shows)} shows")
    for reason, text in skipped:
        print(f"  WARN ops skipped: {reason}: {text!r}")
    # PSA flag: an ops pic title mentioning PSA (display flag only).
    # parse_ops_posts drops the text, so re-parse just the messages
    # that mention PSA to recover their (platform, date, part).
    ops_psa = set()
    for m in ops_messages:
        text = (m.get("text") or "")
        if "PSA" not in text.upper():
            continue
        # Reuse the parsed show for this message by matching its
        # timestamp; simpler: mark by (platform, date, part) via a
        # second parse of this one message.
        one, _ = parse_ops_posts([m])
        for (platform, date, part, _end) in one:
            ops_psa.add((platform, date, part))
    if ops_psa:
        print(f"ops PSA shows: {sorted(ops_psa)}")

    now = datetime.now(EST)

    # Patrick-confirmed completions (completed-shows.json, next to the
    # --in file or in the working directory). A listed (platform,
    # date) comes off and is never resurrected by a later ops pic.
    completed = set()
    for cand in (os.path.join(
                     os.path.dirname(os.path.abspath(args.in_path)),
                     "completed-shows.json"),
                 "completed-shows.json"):
        if os.path.exists(cand):
            with open(cand, encoding="utf-8") as f:
                completed = {(e["platform"], e["date"])
                             for e in json.load(f)}
            print(f"confirmed completions: {len(completed)} from {cand}")
            break

    data, report = rebuild(current, packed_keys, ops_shows, ops_psa,
                           now, completed)
    for line in report:
        print("  " + line)
    print(f"NEW: {len(data['shows'])} shows")

    # Premier medallions: premier.json (next to the --in file) is the
    # source of record for the On-Time Scan % per channel. The rebuild
    # used to preserve the premier block embedded in the data file,
    # so a re-read committed to premier.json never reached the page
    # (Footy stayed 97.3 after the Nov 25 re-read to 97.5). Load it
    # fresh here; if it is missing or unreadable, keep the preserved
    # block and warn — never fail the build over it.
    premier = None
    for cand in (os.path.join(
                     os.path.dirname(os.path.abspath(args.in_path)),
                     "premier.json"),
                 "premier.json"):
        if os.path.exists(cand):
            try:
                with open(cand, encoding="utf-8") as f:
                    premier = json.load(f)
                print(f"premier: loaded from {cand}")
            except Exception as e:
                print(f"WARN: premier.json unreadable ({cand}): {e} — "
                      "keeping preserved premier block")
            break
    if premier is not None:
        data["premier"] = premier

    validate(data)
    out_path = args.out_path or args.in_path
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"wrote {out_path} (generatedAt {data['generatedAt']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
