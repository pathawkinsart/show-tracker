# vendor/

Vendored copies of code the Slack rebuild workflow needs, so the
show-tracker repo is self-contained and the workflow never has to
check out another (private) repo.

- `slack_packed.py` — the #breaking-shipping-updates packed-post
  parser. Source of truth:
  `forge-claude-shared/shipping-report/slack_packed.py`.
  Copied verbatim; stdlib only (`re`, `datetime`), no local imports.
  If the shared copy changes, re-copy it here and note the sync in
  the commit message.
