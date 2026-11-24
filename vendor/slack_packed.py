#!/usr/bin/env python3
"""Slack #breaking-shipping-updates — packed-post parser.

Turns the supervisors' daily packed posts into per-channel
packages-shipped totals for the Weekly Report Shipping Totals
template.

SOURCE
------
Channel #breaking-shipping-updates (G0C1YNLJXBJ). Supervisors
post each show once it is packed:

    SHOW: <code>
    DATE: <show date> [A/B/C]
    Pkgs: <n> USPS, <n> UPS, <n> FedEx

Nick Angeline posts the eCommerce counts daily in the same
channel:

    E-Commerce: Walmart / Best Buy / eBay
    Date: <date>
    Pkgs: <n> pkgs

THE RULE (validated Nov 23 against the template's filled week
1114-1120, 8/8 exact matches on eBay / Best Buy / Walmart):
for template day D, sum USPS+UPS+FedEx (+local, as posted)
across all posts MADE on day D for that channel's codes. The
DATE inside the post is the SHOW date; the POST date is the
SHIP date, and the template is by ship date.

CODE -> TEMPLATE COLUMN
-----------------------
From SLACK-PACKED-CHECK.md (repo reports/7-day-rolling/):
  WN-SC, WN-FOOTY      -> Footy
  WTH-1, WN-WTH-1      -> WN OG (Whatnot Main)
  WN-HK                -> ICE
  WN-GALAXY            -> Galaxy
  WN-CARDSHOP          -> Card Shop
  WN-TIKTOK, TIKTOK    -> Tik Tok
  EBAY LIVE            -> E-Bay live
  FL-BK                -> FNBA (Fanatics Jamz)
  FL-FB, FL-FB02       -> FNFLB (Fanatics Blitz)
  FL-MLB, FL-WTH-2, WTH2 -> FMLB (Fanatics Baseball)
  FL-POKE              -> Fpoke (Fanatics Evolution)
  WTH4                 -> FNFLR (Fanatics Repack)
  E-Commerce Walmart + eBay (store) -> Walmart/Ebay (added)
  E-Commerce Best Buy  -> Best Buy
FNFLB = Blitz and FNFLR = Repack, confirmed by Patrick
Nov 23 ("blitz ends in B and repack ends in R").

INPUT
-----
parse_messages(messages) takes a list of (post_date, text)
tuples — post_date is a datetime.date (the day the message
was posted, EST), text is the raw message text as Slack
delivers it. It returns {date: {template_column: pkgs}}.

This module does NOT fetch from Slack. The fetch is a
separate step (Slack Web API conversations.history, or the
Muse Slack connector, or a browser read) — the parser takes
whatever that step returns. It is tested against the
verbatim snapshot in SLACK-PACKED-POSTS.md.
"""
import re
from datetime import date

# Slack code (normalized: uppercase, no spaces, no hyphens) ->
# template column. Keys are written in their posted form and
# normalized at load, so "FL-WTH 2" (space) and "FL-WTH-2"
# (hyphen) are the same code. "WN-ICE" is an alternate code
# for Whatnot Ice (posted Nov 14, 63 pkgs) alongside WN-HK.
_CODE_MAP_RAW = {
    "WN-SC": "Footy",
    "WN-FOOTY": "Footy",
    "WTH-1": "WN OG",
    "WN-WTH-1": "WN OG",
    "WN-HK": "ICE",
    "WN-ICE": "ICE",
    "WN-GALAXY": "Galaxy",
    "WN-CARDSHOP": "Card Shop",
    "WN-TIKTOK": "Tik Tok",
    "TIKTOK": "Tik Tok",
    "EBAY LIVE": "E-Bay live",
    "FL-BK": "FNBA",
    "FL-FB": "FNFLB",
    "FL-FB02": "FNFLB",
    "FL-MLB": "FMLB",
    "FL-WTH-2": "FMLB",
    "WTH2": "FMLB",
    "FL-POKE": "Fpoke",
    "WTH4": "FNFLR",
}
CODE_TO_COLUMN = {
    re.sub(r"[\s-]+", "", k).upper(): v
    for k, v in _CODE_MAP_RAW.items()
}

# E-Commerce block name (lowercased) -> template column.
ECOMM_TO_COLUMN = {
    "walmart": "Walmart/Ebay",
    "ebay": "Walmart/Ebay",
    "best buy": "Best Buy",
}

# A carrier word (or "pkgs" / "local") after a number.
_CARRIER = r"(?:USPS|UPS|FEDEX|FEDEX|LOCAL|PKGS?)"
# "43 USPS", "43USPS", "119USPS,1UPS", "158 USPS".
_PKGS_RE = re.compile(r"(\d+)\s*" + _CARRIER, re.IGNORECASE)
# "SHOW: FL-MLB", "Show: EBAY LIVE", "SHOW:EBAY LIVE", "SHOW:FL-BK".
_SHOW_RE = re.compile(r"^SHOW\s*:\s*(.+?)\s*$",
                      re.IGNORECASE | re.MULTILINE)
# "E-Commerce: Walmart", "E-Commerce: Best Buy", "E-Commerce: eBay".
_ECOMM_RE = re.compile(r"^E-?COMMERCE\s*:\s*(.+?)\s*$",
                       re.IGNORECASE | re.MULTILINE)
# "Pkgs: 79 USPS (1 international shipment on hold)" — a Pkgs
# line, up to the end of the line.
_PKGS_LINE_RE = re.compile(r"^PKGS?\s*:\s*(.+?)\s*$",
                           re.IGNORECASE | re.MULTILINE)


def _norm_code(raw):
    """Normalize a SHOW code to a CODE_TO_COLUMN key, or None.

    Uppercases, collapses spaces and hyphens, strips a trailing
    show letter (A/B/C) when the bare code is a known code —
    e.g. "WTH-1B" and "FL-MLB B" are the B show of WTH-1 /
    FL-MLB, not new codes. "FL-FB02" keeps its 02 (second
    Blitz show that day).
    """
    code = re.sub(r"[\s-]+", "", (raw or "").upper())
    if code in CODE_TO_COLUMN:
        return code
    # Trailing show letter: FL-MLBB -> FL-MLB, WTH-1B -> WTH-1.
    if len(code) > 1 and code[-1] in "ABC" and code[:-1] in CODE_TO_COLUMN:
        return code[:-1]
    return None


def _pkgs_total(pkgs_line):
    """Sum the carrier counts on one Pkgs line.

    Parentheticals are stripped first ("(1 international
    shipment on hold)" is not a shipped package). Counts both
    "43 USPS" and "43USPS", and "119USPS,1UPS".
    """
    line = re.sub(r"\([^)]*\)", " ", pkgs_line or "")
    return sum(int(n) for n in _PKGS_RE.findall(line))


def parse_message(text):
    """Parse one Slack message's text into [(column, pkgs)].

    A message usually covers several shows (and sometimes an
    E-Commerce block too). Each SHOW block contributes its
    Pkgs total to its code's column; each E-Commerce block
    contributes its Pkgs total to its store's column.
    Unknown codes are returned under ("UNKNOWN:<code>", pkgs)
    so a new code is visible, never silently dropped.
    """
    out = []
    text = text or ""
    # SHOW blocks: a SHOW line, then the next PKGS line.
    for m in _SHOW_RE.finditer(text):
        code = _norm_code(m.group(1))
        # The Pkgs line belonging to this SHOW is the first
        # PKGS line after the SHOW line.
        rest = text[m.end():]
        pm = _PKGS_LINE_RE.search(rest)
        if not pm:
            continue
        # Stop at the next SHOW/E-Commerce block: only take a
        # PKGS line that comes before the next block header.
        next_block = re.search(r"^(SHOW|E-?COMMERCE)\s*:",
                               rest, re.IGNORECASE | re.MULTILINE)
        if next_block and pm.start() > next_block.start():
            continue
        total = _pkgs_total(pm.group(1))
        if not total:
            continue
        if code is None:
            out.append(("UNKNOWN:%s" % m.group(1).strip(), total))
        else:
            out.append((CODE_TO_COLUMN[code], total))
    # E-Commerce blocks: an E-Commerce line, then its Pkgs line.
    for m in _ECOMM_RE.finditer(text):
        name = re.sub(r"\s+", " ", m.group(1).strip().lower())
        column = ECOMM_TO_COLUMN.get(name)
        rest = text[m.end():]
        pm = _PKGS_LINE_RE.search(rest)
        if not pm:
            continue
        next_block = re.search(r"^(SHOW|E-?COMMERCE)\s*:",
                               rest, re.IGNORECASE | re.MULTILINE)
        if next_block and pm.start() > next_block.start():
            continue
        total = _pkgs_total(pm.group(1))
        if not total:
            continue
        if column is None:
            out.append(("UNKNOWN-ECOMM:%s" % name, total))
        else:
            out.append((column, total))
    return out


def daily_totals(messages):
    """Aggregate [(post_date, text)] into {date: {column: pkgs}}.

    post_date is the day the message was posted (EST) — that
    is the ship date the template wants. Multiple posts on the
    same day for the same column add up.
    """
    totals = {}
    for post_date, text in messages:
        if not isinstance(post_date, date):
            raise TypeError("post_date must be a datetime.date, "
                            "got %r" % (post_date,))
        day = totals.setdefault(post_date, {})
        for column, pkgs in parse_message(text):
            day[column] = day.get(column, 0) + pkgs
    return totals


if __name__ == "__main__":
    import sys
    print("slack_packed parser — run the test with "
          "python3 test_slack_packed.py")
    sys.exit(0)
