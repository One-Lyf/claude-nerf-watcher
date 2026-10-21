# claude-nerf-watcher

Is your Claude plan quietly giving you less? This Claude Code skill measures how much work
each 1% of your 5-hour limit actually buys, compares it to your own baseline, and flags
when it drops.

```
window ends      used   $/1%  vs base
10-18 12:50 AM    92%    0.28  0.98x ####################|
10-18 1:50 PM     30%    0.31  1.09x ####################|##
10-19 11:50 AM    86%    0.21  0.72x ##############      |
10-20 8:00 PM     93%    0.26  0.92x ##################  |
'|' = baseline 0.29 $/1% (median of 8 windows, 2026-10-16 to 2026-10-19)
verdict: normal (no 2-window streak below 0.67x baseline)
```

## How it works

- **Tokens:** read from the transcripts Claude Code already writes under
  `~/.claude/projects`, subagents included. Each message is counted once.
- **Weighting:** tokens are priced at public API rates (input, output, cache read, cache
  write) so a cache read doesn't count the same as an output token. The result is
  "API-equivalent dollars". Only the ratios matter.
- **Utilization:** the 5-hour percent from
  [claude-usage-pacer](https://github.com/One-Lyf/claude-usage-pacer).
- **Score:** each window starts at 0%, so `$ per 1% = tokens since window start / percent used`.
  One reading late in a window is enough.
- **Cheap by design:** `tick` appends one line to a log. A chart prints only when a 5-hour
  window has closed.

## Install

Requires claude-usage-pacer.

```
git clone https://github.com/One-Lyf/claude-usage-pacer
git clone https://github.com/One-Lyf/claude-nerf-watcher
mkdir -p ~/.claude/skills
cp -R claude-usage-pacer/skills/usage-pacer claude-nerf-watcher/skills/nerf-watcher ~/.claude/skills/
python3 ~/.claude/skills/nerf-watcher/nerf.py backfill
python3 ~/.claude/skills/nerf-watcher/nerf.py baseline 2026-10-01 2026-10-07
```

Backfill finds meter readings Claude saw in past sessions. Pick baseline dates you trust
were normal. After that, ask Claude to "check usage", or run `nerf.py tick` yourself.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `NERF_THRESHOLD` | 1.5 | Flag when $/1% falls below baseline / this |
| `NERF_STREAK` | 2 | Consecutive low windows needed to flag |
| `NERF_MIN_UTIL` | 10 | Skip windows under this % (too coarse) |
| `NERF_HOME` | `~/.nerf-watcher` | Log and state directory |
| `NERF_USAGE_CMD` | usage-pacer `--json` | Any command printing usage JSON |
| `NERF_PRICES` | built-in | JSON of per-family `[in, out, cache read, write 5m, write 1h]` $/MTok |

## Caveats

- **It only sees this machine.** Usage from claude.ai, the apps, cloud sessions or another
  computer raises utilization with no matching tokens. That looks like a nerf.
- **Mix shifts matter.** The plan's real weighting of cache tokens is unknown, so a big
  change in token mix can move the score. Each window prints its mix.
- **Readings are whole percents**, so low-usage windows are noisy and skipped.
- The usage endpoint is undocumented and may change.
- Not affiliated with or endorsed by Anthropic.

## License

MIT
