---
name: nerf-watcher
description: Detect whether each 1% of the Claude plan's 5-hour limit is buying less work than it used to (a quiet nerf or throttle). Use in place of the usage-pacer meter check during long work, when the user asks whether usage is being nerfed, or to set and review the baseline.
---

# Nerf Watcher

Tracks one number per 5-hour window: **API-equivalent dollars per 1% of the limit**.
Tokens come from Claude Code transcripts on this machine, weighted by public API prices
(input, output, cache read, cache write). Utilization comes from the usage-pacer meter.
If a window buys well under baseline, the plan got stingier for the same work.

## Check usage (replaces the usage-pacer call)

```
python3 ~/.claude/skills/nerf-watcher/nerf.py tick
```

It prints the usual pacer lines and adds one reading to a log. It is silent otherwise.
When a 5-hour window has closed since the last tick, it first prints a chart of recent
windows against the baseline and a verdict. That is the only time anything is charted, so
routine checks cost no extra tokens.

## On a SUSPECTED NERF verdict

1. Tell the user, quoting the chart's last rows and the `SUSPECTED NERF` block.
2. Draft a report with the SendFeedback tool: type `bug`, title "Plan usage per token
   dropped vs baseline", and the block's lines as Evidence. The user approves sending.

## Setup and review

- `nerf.py backfill`: rebuild past readings from transcripts (meter outputs Claude saw).
- `nerf.py baseline 2026-10-16 2026-10-19`: pin the baseline to a known-normal stretch.
- `nerf.py report`: full table and current verdict.

## Read the result with care

- Flag needs `NERF_STREAK` (2) windows in a row below baseline / `NERF_THRESHOLD` (1.5).
  Normal windows vary by about 25%.
- Windows under `NERF_MIN_UTIL` (10%) are too coarse to score and are skipped.
- Usage this machine can't see inflates utilization without adding tokens: claude.ai, the
  desktop or mobile apps chatting directly, cloud sessions, other computers. Any of those
  can look like a nerf.
- A big shift in the token mix (shown per window) can move the number too, since the true
  plan weighting of cache tokens is unknown.
- Never read the credentials file or Keychain. The meter handles auth.
