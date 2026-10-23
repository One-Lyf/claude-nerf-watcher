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

Act at once, without asking first. The user wants this as automated as possible.

1. Draft the report with the SendFeedback tool right away: type `bug`, title "Plan usage
   per token dropped vs baseline", and the block's lines as Evidence. Sending to Anthropic
   still needs the user's one approval; that step can't be skipped, so make the draft ready
   to approve as-is.
2. Run `nerf.py graph` and send the PNG to the user (SendUserFile, if available).
3. If a push-notification tool is available, notify the user's phone: "Suspected nerf:
   feedback draft ready to approve."
4. In chat, quote the chart's last rows and the `SUSPECTED NERF` block, and say the draft
   is waiting for approval.

## Setup and review

- `nerf.py backfill`: rebuild past readings from transcripts (meter outputs Claude saw).
- `nerf.py baseline 2026-10-16 2026-10-19`: pin the baseline to a known-normal stretch.
- `nerf.py plan 2026-10-11 pro`: record a plan change (pro, max5, max20, or NAME MULT) so a
  downgrade or upgrade is not read as a nerf. `nerf.py plan` lists them.
- `nerf.py report`: full table and current verdict.
- `nerf.py graph [OUT.png]`: line graph of $/1% and ratio vs plan baseline, plan changes
  marked (default `~/.nerf-watcher/graph.png`). Needs matplotlib. Send the PNG to the user.

## Read the result with care

- Flag needs `NERF_STREAK` (2) windows in a row below baseline / `NERF_THRESHOLD` (1.5).
  Normal windows vary by about 25%.
- Windows under `NERF_MIN_UTIL` (10%) are too coarse to score and are skipped.
- Usage this machine can't see inflates utilization without adding tokens: claude.ai, the
  desktop or mobile apps chatting directly, cloud sessions, other computers. Any of those
  can look like a nerf.
- Cache writes are priced at one rate regardless of TTL, and each window also shows raw
  tokens per 1% (`Ktok/1%`). Before quoting a plan-vs-plan ratio, check both agree.
- A big shift in the token mix (shown per window) can move the number too, since the true
  plan weighting of cache tokens is unknown.
- Never read the credentials file or Keychain. The meter handles auth.
