# slack-data/ — Slack channel history for the token-less rebuild

These files are the input to `slack_rebuild.py` (same format as
`slack_fetch.py` writes). They are kept fresh by the VM browser-read
cron (`tracker-update-requests`, every 15 min), which reads both
channels in the cloud browser with Patrick's own signed-in Slack
session — no Slack app, no bot token, no admin approval.

- `packed.json` — #breaking-shipping-updates (G0C1YNLJXBJ): packed posts.
- `ops.json` — #breaking-ops (C0C19E8SQ58): break-over pic posts (post
  time = show end).

Each file: {"channel", "fetched_at", "messages": [{"ts", "post_date",
"text", "subtype", "user"}]}. Messages cover a rolling 8-day window,
deduplicated by ts; older messages are pruned.

INVARIANT — read before touching these files: the window only ever
grows FORWARD. Never add older posts after the fact. The rebuild
derives each show's original total as pkgs + shipped-from-this-data,
which is only correct while the window grows forward. If older posts
are ever backfilled, delete the stored total/shipped fields from
show-tracker-data.json first (or let the next folder rebuild
regenerate the file) so totals are re-derived from complete data.

Seed note (Nov 24): packed.json was seeded from the per-day packed
file for 2026-11-23 (real posts, real post dates; ts = noon EST on
the post date because those files record the date, not the time —
only the year is used from a packed post's ts). ops.json starts
empty; the cron fills it from #breaking-ops reads.
