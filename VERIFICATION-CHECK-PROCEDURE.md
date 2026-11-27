# Verification check — procedure

How `verified-shows.json` gets populated, and how the tracker uses it.

## What "verified" means

A show is **verified** when its Bins worksheet's **WorkingBreaks** tab is
filled in — the verification person has checked the show and it is ready
to start packing. An empty WorkingBreaks tab means the show has NOT been
verified yet: it cannot be packed or shipped until that happens, and the
tracker waits (or flags it as pending).

- `true` — WorkingBreaks has at least one real data row (see below).
  Green V on the show's chip: verified, ready to pack.
- `false` — the worksheet was checked and WorkingBreaks is empty.
  Red V on the chip: not verified yet.
- No entry for the show — the check has not run for it yet.
  Grey `?` on the chip: not checked yet. An unchecked show must
  NEVER show red; red means "checked and empty", not "unknown".

The check is display-only. It never removes a show from the tracker and
never feeds the Slack-only shipped model — a show still comes off only
via a packed post or a Patrick-confirmed completion.

## The file

`verified-shows.json` in the repo root:

```json
{"checked_at": "2026-11-27T08:00:00-05:00", "shows": {"Whatnot Main|2026-11-26|B": true, "Fanatics Evolution|2026-11-26|A": false}}
```

- Key: `"<platform>|<YYYY-MM-DD>|<part>"` — the same
  `(platform, date, part)` triple the rebuild keys shows by.
  `part` is the session letter (`A`/`B`/`C`), or `""` when the show
  has no part (e.g. `"Fanatics Evolution|2026-11-26|"`).
- `shows`: the map of keys to `true`/`false`. Only real booleans
  count — any other value is ignored (treated as not checked).
- `checked_at`: ISO timestamp of the run that produced these
  values. Set it fresh on every check run.

## How to run the check

Run it in the cloud browser, signed in as Patrick Hawkins
(Microsoft session). The browser session is the only route that
works without the work PC and without an admin: SharePoint
downloads are blocked (the Download button never dispatches) and
the Graph API needs admin consent Patrick does not have.

For each show currently in house on the tracker:

1. Find its worksheet in the Team Site Breaking folder
   (`topshelfenterprises.sharepoint.com/sites/contentTypeHub`,
   Shared Documents > Operations > Breaking). Filename is
   `WTH-<code>_YYYYMMDD_<session>.xlsx` for the show's platform,
   date, and session. If no worksheet exists for the show, the
   show is `false` (not verified — there is nothing to verify).
2. Read its WorkingBreaks tab through the Excel Services REST
   endpoint — no download needed:
   `https://topshelfenterprises.sharepoint.com/sites/contentTypeHub/_vti_bin/ExcelRest.aspx/Shared%20Documents/Operations/Breaking/<filename>/Model/Ranges('WorkingBreaks!A1:J200')?$format=json`
   (single-word sheet names only — sheets with a space in the
   name, like `PKG STATS`, 404 on this endpoint; WorkingBreaks
   has no space, so it works.)
3. Decide `true` / `false`:
   - `true` — at least one data row below the header row has a
     real value in Break Number, Team, or Username. A filled
     WorkingBreaks tab is the verification.
   - `false` — headers only, or rows whose only content is
     formula errors (`#N/A`, `#REF!`) with no real values.
     (Verified on the Nov 26 Fanatics Evolution sheet: headers
     plus one `#N/A` / `#REF!` error row and 199 empty rows =
     `false`.)
   - Bins alone does NOT verify a show. A filled Bins tab with
     an empty WorkingBreaks tab is still `false` — the
     verification step has not happened.

Then write the results into `verified-shows.json`, set
`checked_at` to the run time, commit, and push. The next rebuild
picks them up — no page code change is needed.

## How the rebuild overlays it

`slack_rebuild.py` (the sole owner of `show-tracker-data.json` —
never `build_tracker_json.py`) loads `verified-shows.json` from
next to the data file at the start of `main()` (`load_verified_shows`)
and passes the map into `rebuild()`. At the end of `rebuild()`,
after the skipped-show flags, every kept show gets:

```python
s["verified"] = verified.get(show_verified_key(
    s["platform"], s["date"], s.get("part", "")))
```

`show_verified_key` builds `"<platform>|<date>|<part>"`. A missing
key returns `None` (grey `?` on the page). The field is preserved
like any other show field: a rebuild never drops it, and a new
check run only changes the entries it writes. The Update button
on the page rebuilds from synced data, so a fresh check lands on
the page at the next rebuild (5-minute sync or Update press).

## To update the seed later

Edit `verified-shows.json` (add/change entries, refresh
`checked_at`), commit, push. The next `slack_rebuild.py` run
overlays the new values onto the show objects in
`show-tracker-data.json` — that is the whole update path.
