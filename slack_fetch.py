#!/usr/bin/env python3
"""Fetch the two tracker Slack channels via conversations.history.

Uses the Slack Web API directly (stdlib urllib only — no requests needed).
Auth: bot token from SLACK_BOT_TOKEN env (xoxb-..., from the installed app).
The bot must be a member of each channel (invited with /invite) or the
API returns an error.

Channels:
  C0C19E8SQ58  #breaking-ops (public) — break-over pic posts
  G0C1YNLJXBJ  #breaking-shipping-updates (private) — packed posts

Output (one JSON file per channel, --out-dir):
  {"channel": "<id>", "fetched_at": "<ISO EST>", "messages": [
      {"ts": "<unix>", "post_date": "YYYY-MM-DD", "text": "...",
       "subtype": "<subtype or null>", "user": "<user id>"}]}
Only top-level messages are returned (conversations.history does not
include thread replies). message_changed edits are unwrapped to the new
text; message_deleted / bot_message entries are skipped.

Usage:
  SLACK_BOT_TOKEN=xoxb-... python3 slack_fetch.py --out-dir out [--days 8]
Exit non-zero on any API error — the Action must fail loudly, never
rebuild from a partial fetch.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

EST = ZoneInfo("America/New_York")
API = "https://slack.com/api/conversations.history"

CHANNELS = {
    "C0C19E8SQ58": "ops",
    "G0C1YNLJXBJ": "packed",
}


def slack_get(token, params):
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "User-Agent": "show-tracker-slack-fetch",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"Slack API HTTP {e.code}: {body}") from e


def fetch_channel(token, channel_id, oldest_ts):
    """All top-level messages in the channel since oldest_ts (Unix)."""
    out = []
    cursor = None
    pages = 0
    while True:
        params = {
            "channel": channel_id,
            "oldest": str(oldest_ts),
            "limit": "200",
        }
        if cursor:
            params["cursor"] = cursor
        data = slack_get(token, params)
        if not data.get("ok"):
            raise RuntimeError(
                f"Slack conversations.history failed for {channel_id}: "
                f"{data.get('error', 'unknown error')} "
                f"(is the bot invited to the channel?)")
        for m in data.get("messages", []):
            subtype = m.get("subtype")
            if subtype == "message_deleted":
                continue
            if subtype == "message_changed":
                # Edited post: the new text lives under message.
                inner = m.get("message") or {}
                text = inner.get("text", "")
                ts = inner.get("ts") or m.get("ts")
            elif subtype == "bot_message":
                continue
            else:
                text = m.get("text", "")
                ts = m.get("ts")
            if not text or not ts:
                continue
            try:
                post_dt = datetime.fromtimestamp(float(ts), EST)
            except (TypeError, ValueError):
                continue
            out.append({
                "ts": ts,
                "post_date": post_dt.date().isoformat(),
                "text": text,
                "subtype": subtype,
                "user": m.get("user", ""),
            })
        pages += 1
        meta = data.get("response_metadata") or {}
        cursor = meta.get("next_cursor") or None
        if not data.get("has_more") or not cursor or pages >= 10:
            break
        time.sleep(0.5)  # be gentle with the API
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="slack-data")
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--token", default=None,
                    help="Slack bot token (default: SLACK_BOT_TOKEN env)")
    args = ap.parse_args()
    token = args.token or os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        print("SLACK_BOT_TOKEN is not set (env or --token)", file=sys.stderr)
        return 2
    now = datetime.now(EST)
    oldest = int((now - timedelta(days=args.days)).timestamp())
    os.makedirs(args.out_dir, exist_ok=True)
    for channel_id, name in CHANNELS.items():
        msgs = fetch_channel(token, channel_id, oldest)
        path = os.path.join(args.out_dir, name + ".json")
        payload = {
            "channel": channel_id,
            "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%S-05:00"),
            "messages": msgs,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1)
        print(f"{name}: {len(msgs)} messages -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
