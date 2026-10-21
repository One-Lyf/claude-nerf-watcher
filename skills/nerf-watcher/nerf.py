#!/usr/bin/env python3
"""Nerf watcher: is each 1% of your Claude plan buying less work than it used to?

Stdlib only. Usage:
  python3 nerf.py tick        log one usage reading; report any 5h window that just closed
  python3 nerf.py backfill    rebuild past readings from Claude Code transcripts
  python3 nerf.py report      table + chart of every scored window
  python3 nerf.py baseline FROM TO   pin the baseline to windows ending in [FROM, TO] (YYYY-MM-DD)

Metric: API-equivalent dollars of tokens used in a 5-hour window, divided by the window's
utilization percent ("$ per 1%"). Tokens come from the transcripts Claude Code writes under
~/.claude/projects, weighted by public API prices so input, output and cache tokens count
at their real relative cost. If $ per 1% falls well below baseline, each percent of the
plan is buying less work: a possible nerf.
"""
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timedelta, timezone

HOME = os.path.expanduser(os.environ.get("NERF_HOME", "~/.nerf-watcher"))
PROJECTS = os.path.join(os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")), "projects")
USAGE_CMD = os.environ.get("NERF_USAGE_CMD", "python3 ~/.claude/skills/usage-pacer/usage.py --json")
THRESHOLD = float(os.environ.get("NERF_THRESHOLD", 1.5))  # flag when $/1% is below baseline / this
STREAK = int(os.environ.get("NERF_STREAK", 2))  # consecutive low windows needed to flag
MIN_UTIL = float(os.environ.get("NERF_MIN_UTIL", 10))  # windows below this % are too coarse to score
KEEP_DAYS = int(os.environ.get("NERF_KEEP_DAYS", 45))
WINDOW = timedelta(hours=5)

# USD per million tokens: input, output, cache read, cache write 5m, cache write 1h.
# Only the ratios matter for nerf detection. Override with NERF_PRICES='{"opus":[...]}'.
PRICES = {
    "opus": [5.0, 25.0, 0.5, 6.25, 10.0],
    "sonnet": [3.0, 15.0, 0.3, 3.75, 6.0],
    "haiku": [1.0, 5.0, 0.1, 1.25, 2.0],
}
PRICES.update(json.loads(os.environ.get("NERF_PRICES", "{}")))
KINDS = ["input", "output", "cache_read", "cache_write"]

READINGS = os.path.join(HOME, "readings.jsonl")
INDEX = os.path.join(HOME, "token-index.json")
STATE = os.path.join(HOME, "state.json")


def die(msg):
    print("nerf-watcher: " + msg, file=sys.stderr)
    sys.exit(1)


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def round10(dt):
    """Reset times sit on 10-minute marks; round so jittered readings share one key."""
    epoch = int(dt.timestamp())
    return datetime.fromtimestamp(round(epoch / 600) * 600, timezone.utc)


def local(dt):
    return dt.astimezone().strftime("%m-%d %I:%M %p").replace(" 0", " ")


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


# ---------- usage readings ----------

def extract_reading(data):
    """Accept usage-pacer --json output or the raw usage endpoint shape."""
    if "windows" in data:
        w = data["windows"].get("5h") or {}
        used, reset = w.get("used"), w.get("resets_at")
    else:
        w = data.get("five_hour") or {}
        used, reset = w.get("utilization"), w.get("resets_at")
    if used is None or not reset:
        return None
    return float(used), round10(parse_time(reset))


def read_readings():
    out = []
    try:
        with open(READINGS) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    out.append((parse_time(r["ts"]), float(r["used"]), parse_time(r["resets_at"]), r.get("src", "live")))
                except (ValueError, KeyError):
                    continue
    except OSError:
        pass
    return sorted(out)


def append_readings(rows):
    with open(READINGS, "a") as f:
        for ts, used, reset, src in rows:
            f.write(json.dumps({"ts": ts.isoformat(), "used": used, "resets_at": reset.isoformat(), "src": src}) + "\n")


# ---------- transcript token index ----------

def family(model):
    m = (model or "").lower()
    for fam in PRICES:
        if fam in m:
            return fam
    return "opus"  # unknown models priced as the top tier; reported in the mix line


def usage_tuple(u):
    cw = u.get("cache_creation") or {}
    cw1 = int(cw.get("ephemeral_1h_input_tokens") or 0)
    cw_total = int(u.get("cache_creation_input_tokens") or 0)
    return [int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0),
            int(u.get("cache_read_input_tokens") or 0), max(cw_total - cw1, 0), cw1]


def update_index():
    idx = load_json(INDEX, {"files": {}, "msgs": {}})
    files, msgs = idx["files"], idx["msgs"]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).timestamp()
    for root, _, names in os.walk(PROJECTS):
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if st.st_mtime < cutoff:
                continue
            seen = files.get(path, {})
            offset = seen.get("offset", 0)
            if st.st_size < offset:  # rewritten: rescan
                offset = 0
            if st.st_size == offset:
                continue
            with open(path, "rb") as f:
                f.seek(offset)
                chunk = f.read()
            end = chunk.rfind(b"\n") + 1  # leave any half-written last line for next time
            for raw in chunk[:end].splitlines():
                if b'"usage"' not in raw or b'"assistant"' not in raw:
                    continue
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                m = d.get("message") or {}
                u, mid = m.get("usage"), m.get("id")
                if d.get("type") != "assistant" or not u or not mid or m.get("model") == "<synthetic>":
                    continue
                ts = parse_time(d["timestamp"]).timestamp()
                row = [ts, family(m.get("model")), m.get("model")] + usage_tuple(u)
                old = msgs.get(mid)
                if old:  # streamed messages repeat; keep the largest count of each field
                    row = [old[0], old[1], old[2]] + [max(a, b) for a, b in zip(old[3:], row[3:])]
                msgs[mid] = row
            files[path] = {"offset": offset + end}
    idx["msgs"] = {k: v for k, v in msgs.items() if v[0] >= cutoff}
    idx["files"] = {p: v for p, v in files.items() if os.path.exists(p)}
    save_json(INDEX, idx)
    return list(idx["msgs"].values())


def tally(msgs, start, end):
    """Weighted dollars, per-kind dollars and per-kind tokens for messages in [start, end]."""
    s, e = start.timestamp(), end.timestamp()
    dollars = dict.fromkeys(KINDS, 0.0)
    tokens = dict.fromkeys(KINDS, 0)
    models = {}
    for ts, fam, model, i, o, cr, cw5, cw1 in msgs:
        if not s <= ts <= e:
            continue
        p = PRICES[fam]
        parts = {"input": (i, i * p[0]), "output": (o, o * p[1]), "cache_read": (cr, cr * p[2]),
                 "cache_write": (cw5 + cw1, cw5 * p[3] + cw1 * p[4])}
        for k, (n, cost) in parts.items():
            tokens[k] += n
            dollars[k] += cost / 1e6
        models[model] = models.get(model, 0) + 1
    return sum(dollars.values()), dollars, tokens, models


# ---------- windows ----------

def score_windows(readings, msgs, now):
    """One score per 5h window: tokens from window start to the last reading, over its utilization.
    Utilization is 0 at window start by definition, so a single late reading is enough."""
    by_reset = {}
    for ts, used, reset, src in readings:
        if reset - WINDOW <= ts <= reset:
            best = by_reset.get(reset)
            if not best or used >= best[1]:
                by_reset[reset] = (ts, used, src)
    out = []
    for reset in sorted(by_reset):
        ts, used, src = by_reset[reset]
        if used < MIN_UTIL:
            continue
        total, dollars, tokens, models = tally(msgs, reset - WINDOW, ts)
        if total <= 0:
            continue
        out.append({"reset": reset, "closed": reset <= now, "used": used, "dollars": total,
                    "per_pct": total / used, "mix": dollars, "tokens": tokens, "models": models, "src": src})
    return out


def baseline(windows, state):
    b = state.get("baseline")
    if not b:
        return None
    lo, hi = parse_time(b["from"]), parse_time(b["to"])
    vals = sorted(w["per_pct"] for w in windows if w["closed"] and lo <= w["reset"] <= hi)
    if not vals:
        return None
    mid = len(vals) // 2
    med = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
    mix = {}
    picked = [w for w in windows if w["closed"] and lo <= w["reset"] <= hi]
    tot = sum(w["dollars"] for w in picked)
    for k in KINDS:
        mix[k] = sum(w["mix"][k] for w in picked) / tot
    return {"value": med, "n": len(vals), "mix": mix, "from": b.get("label") or "%s to %s" % (b["from"][:10], (parse_time(b["to"]) - timedelta(days=1)).date())}


def mix_line(mix_dollars):
    tot = sum(mix_dollars.values()) or 1
    return "  ".join("%s %d%%" % (k, round(100 * v / tot)) for k, v in mix_dollars.items())


def chart(windows, base, n=12):
    lines = ["window ends      used   $/1%  vs base"]
    for w in windows[-n:]:
        ratio = w["per_pct"] / base["value"] if base else None
        bar = ""
        if ratio is not None:
            width = min(int(round(ratio * 20)), 40)
            bar = ("#" * width).ljust(20)[:20] + "|" + "#" * max(width - 20, 0)
        lines.append("%-15s %4d%%  %6.2f  %s %s" % (
            local(w["reset"]), w["used"], w["per_pct"],
            ("%.2fx" % ratio) if ratio is not None else "  -  ", bar))
    if base:
        lines.append("'|' = baseline %.2f $/1%% (median of %d windows, %s)" % (
            base["value"], base["n"], base["from"]))
    return "\n".join(lines)


def verdict(windows, base):
    closed = [w for w in windows if w["closed"]]
    if not base or len(closed) < STREAK:
        return None
    recent = closed[-STREAK:]
    if all(w["per_pct"] < base["value"] / THRESHOLD for w in recent):
        return recent
    return None


def feedback_block(recent, base):
    lines = ["SUSPECTED NERF: last %d windows bought less than 1/%.1f of baseline usage per 1%% of the 5h limit." % (len(recent), THRESHOLD)]
    lines.append("baseline: %.2f API-equivalent $ per 1%% (median of %d windows, %s), mix: %s" % (
        base["value"], base["n"], base["from"],
        "  ".join("%s %d%%" % (k, round(100 * v)) for k, v in base["mix"].items())))
    for w in recent:
        lines.append("window ending %s: %.0f%% used, %.2f $/1%% (%.2fx baseline), tokens %s, mix: %s, models %s" % (
            w["reset"].isoformat(), w["used"], w["per_pct"], w["per_pct"] / base["value"],
            json.dumps(w["tokens"]), mix_line(w["mix"]), ", ".join(sorted(w["models"]))))
    lines.append("method: tokens from local Claude Code transcripts weighted by public API prices, "
                 "divided by 5h utilization from the usage endpoint.")
    return "\n".join(lines)


# ---------- commands ----------

def fetch_usage(argv):
    if "--input" in argv:
        with open(argv[argv.index("--input") + 1]) as f:
            return json.load(f)
    try:
        out = subprocess.run([os.path.expanduser(p) for p in shlex.split(USAGE_CMD)],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        die("usage command failed: %s" % e)
    if out.returncode:
        die("usage command failed: %s" % (out.stderr.strip() or out.stdout.strip()))
    try:
        return json.loads(out.stdout)
    except ValueError:
        die("usage command did not return JSON. Set NERF_USAGE_CMD to a command that does.")


def pacer_lines(data):
    if "windows" not in data:
        return []
    lines = []
    for label in ("5h", "7d"):
        w = data["windows"].get(label) or {}
        if "used" in w:
            lines.append("%s  used %3d%%  target %3d%%  %-8s resets %s" % (
                label, w["used"], w.get("target", 0), w.get("status", ""),
                local(parse_time(w["resets_at"])) if w.get("resets_at") else "-"))
    if data.get("advice"):
        lines.append("advice: " + data["advice"])
    return lines


def print_verdict(windows, base):
    hit = verdict(windows, base)
    if hit:
        print(feedback_block(hit, base))
    elif base:
        print("verdict: normal (no %d-window streak below %.2fx baseline)" % (STREAK, 1 / THRESHOLD))
    else:
        print("verdict: no baseline yet. Run backfill or `baseline FROM TO`.")


def cmd_tick(argv):
    data = fetch_usage(argv)
    now = datetime.now(timezone.utc)
    r = extract_reading(data)
    if r:
        append_readings([(now, r[0], r[1], "live")])
    state = load_json(STATE, {})
    reported = set(state.get("reported", []))
    windows = score_windows(read_readings(), update_index(), now)
    fresh = [w for w in windows if w["closed"] and w["reset"].isoformat() not in reported]
    if fresh:  # a 5h window closed since the last report: the only time we chart
        base = baseline(windows, state)
        print("== 5h window closed ==")
        print(chart(windows, base))
        print("mix (last window, by $): " + mix_line(fresh[-1]["mix"]))
        print_verdict(windows, base)
        state["reported"] = sorted(reported | {w["reset"].isoformat() for w in fresh})
        save_json(STATE, state)
    for line in pacer_lines(data):
        print(line)


PACER_5H = re.compile(r"^5h\s+used\s+(\d+(?:\.\d+)?)%.*?resets in (\d+)h(\d+)m", re.M)


def transcript_readings():
    """Usage-pacer outputs captured in past transcripts. Only Bash results whose command ran
    the meter count; doc examples (SKILL.md, README) are skipped."""
    rows = []
    for root, _, names in os.walk(PROJECTS):
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(root, name)
            try:
                f = open(path, "rb")
            except OSError:
                continue
            cmds = {}
            with f:
                for raw in f:
                    if b"usage" not in raw:
                        continue
                    try:
                        d = json.loads(raw)
                    except ValueError:
                        continue
                    content = (d.get("message") or {}).get("content")
                    if not isinstance(content, list):
                        continue
                    for c in content:
                        if c.get("type") == "tool_use" and c.get("name") == "Bash":
                            cmd = (c.get("input") or {}).get("command", "")
                            # A real meter run, not a test with --input fixtures or a doc read.
                            if (re.search(r"python3?\s+\S*usage\.py|usage-now", cmd)
                                    and not re.search(r"--input|python3? -|\bcat\b|SKILL\.md|README", cmd)):
                                cmds[c["id"]] = cmd
                        elif c.get("type") == "tool_result" and c.get("tool_use_id") in cmds:
                            text = c.get("content")
                            if isinstance(text, list):
                                text = "\n".join(x.get("text", "") for x in text if isinstance(x, dict))
                            if not isinstance(text, str):
                                continue
                            ts = parse_time(d["timestamp"])
                            for m in PACER_5H.finditer(text):
                                reset = round10(ts + timedelta(hours=int(m.group(2)), minutes=int(m.group(3))))
                                rows.append((ts, float(m.group(1)), reset, "backfill"))
                            for m in re.finditer(r"\{.*\}", text):
                                try:
                                    r = extract_reading(json.loads(m.group(0)))
                                except (ValueError, AttributeError, TypeError):
                                    r = None
                                if r:
                                    rows.append((ts, r[0], r[1], "backfill"))
    return rows


def cmd_backfill(argv):
    have = {(r[0].isoformat(), r[1]) for r in read_readings()}
    rows = [r for r in transcript_readings() if (r[0].isoformat(), r[1]) not in have]
    append_readings(sorted(rows))
    now = datetime.now(timezone.utc)
    windows = score_windows(read_readings(), update_index(), now)
    state = load_json(STATE, {})
    state["reported"] = sorted(set(state.get("reported", [])) | {w["reset"].isoformat() for w in windows if w["closed"]})
    save_json(STATE, state)
    print("backfill: %d new readings, %d scored windows" % (len(rows), len(windows)))
    if not state.get("baseline"):
        print("no baseline set. Pick a known-normal stretch: nerf.py baseline YYYY-MM-DD YYYY-MM-DD")
    print(chart(windows, baseline(windows, state), n=40))


def cmd_report(argv):
    now = datetime.now(timezone.utc)
    windows = score_windows(read_readings(), update_index(), now)
    state = load_json(STATE, {})
    base = baseline(windows, state)
    print(chart(windows, base, n=40))
    print_verdict(windows, base)


def cmd_baseline(argv):
    if len(argv) < 2:
        die("usage: nerf.py baseline YYYY-MM-DD YYYY-MM-DD")
    lo = datetime.fromisoformat(argv[0]).astimezone()
    hi = datetime.fromisoformat(argv[1]).astimezone() + timedelta(days=1)
    state = load_json(STATE, {})
    state["baseline"] = {"from": lo.isoformat(), "to": hi.isoformat(), "label": "%s to %s" % (argv[0], argv[1])}
    save_json(STATE, state)
    cmd_report([])


def main(argv):
    os.makedirs(HOME, exist_ok=True)
    cmds = {"tick": cmd_tick, "backfill": cmd_backfill, "report": cmd_report, "baseline": cmd_baseline}
    if not argv or argv[0] not in cmds:
        die("usage: nerf.py tick|backfill|report|baseline FROM TO")
    cmds[argv[0]](argv[1:])


if __name__ == "__main__":
    main(sys.argv[1:])
