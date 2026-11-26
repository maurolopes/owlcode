"""Print speed lines for a run. The output contains no prompt, reply or error text.

A Claude Code run reads its own session transcript: `CLAUDE_CODE_SESSION_ID` names the file under the harness's
`projects` folder, and no other session is opened. `--claude-transcript` overrides that path.
"""

import argparse
from collections import defaultdict
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
from statistics import median
import sys

RUN_ID = re.compile(r"([A-Z][A-Z0-9]{0,9}-[0-9]{1,7})-([0-9]{8}T[0-9]{6}Z-[A-Za-z0-9.-]{1,63})")
NAME = re.compile(r"[A-Za-z0-9_./:@+-]{1,200}")
SESSION_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
APPROXIMATE = ("transcript", "invocation")  # timing values whose throughput is approximate
STATE = Path.home() / ".local/state/agent-metrics"


def entries(path):
    with path.open() as file:
        for line in file:
            try:
                row = json.loads(line)
            except ValueError:
                continue  # The last line can be incomplete when a session writes it.
            if isinstance(row, dict):
                yield row


def number(value):
    return value if type(value) in (int, float) and 0 <= value < math.inf else None


def name(value):
    return value if isinstance(value, str) and NAME.fullmatch(value) else "unknown"


def values(calls, field):
    return sorted(n for row in calls if (n := number(row.get(field))) is not None)


def summary(rows):
    groups = defaultdict(list)
    for row in rows:
        role = row.get("role")
        if role in ("run", "review"):
            groups[role, name(row.get("provider")), name(row.get("model"))].append(row)
    for (role, provider, model), calls in sorted(groups.items(), key=lambda item: (item[0][0] != "run", item[0])):
        ttft = values(calls, "ttft_s")
        tps = values(calls, "tokens_per_s")
        timing = f"{median(ttft):.2f} s (p90 {ttft[math.ceil(.9 * len(ttft)) - 1]:.2f} s)" if ttft else "n/a (p90 n/a)"
        speed = f"{median(tps):.1f} tok/s" if tps else "n/a"
        if tps and any(row.get("timing") in APPROXIMATE for row in calls):
            speed = f"~{speed}"
        tokens = "/".join(str(sum(v)) if (v := values(calls, field)) else "n/a" for field in ("input", "cached", "output"))
        cost = f"${sum(found):.6f}" if (found := values(calls, "cost")) else "n/a"
        prefix = "Speed" if role == "run" else "Local review speed"
        yield (f"{prefix}: {provider}/{model}, {len(calls)} calls, TTFT median {timing}, "
               f"throughput median {speed}, tokens {tokens} in/cached/out, cost {cost}")


def session_transcript(environ):
    """The transcript of the Claude Code session that runs this, from its id, or None. It opens only that session."""
    session = environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    if not SESSION_ID.fullmatch(session):
        return None
    config = Path(environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    name = f"{session}.jsonl"
    direct = config / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd())) / name
    if direct.is_file():
        return direct
    found = [path for path in (config / "projects").glob(f"*/{name}") if path.is_file()]
    return max(found, key=lambda path: path.stat().st_mtime) if found else None


def claude_rows(rows, prompt, provider):
    """Use Claude Code's reply times. Several transcript entries can share one request."""
    active = False
    calls = {}
    start = None
    for row in rows:
        if row.get("isSidechain") or row.get("type") not in ("user", "assistant"):
            continue
        message = row.get("message") or {}
        if not active:
            content = message.get("content")
            texts = [block.get("text") for block in content if isinstance(block, dict)] if isinstance(content, list) else [content]
            command = "<command-name>/implement</command-name>"
            arguments = f"<command-args>{prompt.removeprefix('/implement ')}</command-args>"
            active = row["type"] == "user" and any(isinstance(text, str) and (
                text.strip() == prompt or (text.strip().startswith(command) and arguments in text)) for text in texts)
            if not active:
                continue
        try:
            end = int(datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).timestamp() * 1000)
        except (KeyError, AttributeError, ValueError):
            continue
        if row["type"] == "user":
            start = end
            continue
        failed = row.get("isApiErrorMessage") or message.get("stop_reason") == "error"
        request = row.get("requestId") or message.get("id") or row.get("uuid")
        usage = message.get("usage")
        if not request or (not failed and not isinstance(usage, dict)):
            continue
        usage = usage if isinstance(usage, dict) else {}
        previous = calls.get(request)
        began = previous["started_ms"] if previous else start
        if previous and end < previous["ended_ms"]:
            continue
        read = number(usage.get("cache_read_input_tokens"))
        written = number(usage.get("cache_creation_input_tokens"))
        raw = number(usage.get("input_tokens"))
        output = number(usage.get("output_tokens"))
        details = usage.get("output_tokens_details") or {}
        calls[request] = dict(role="run", provider=name(provider), model=name(row.get("requestedModel") or message.get("model")),
                              effort=name(row.get("effort")), started_ms=began, first_ms=None, ended_ms=end,
                              ttft_s=None, timing="transcript",
                              tokens_per_s=output * 1000 / (end - began) if not failed and output and began and end > began else None,
                              input=None if raw is None else raw + (read or 0) + (written or 0),
                              cached=read, output=output, reasoning=number(details.get("reasoning_tokens")), cost=None,
                              error="api_error" if failed else None)
        start = end
    return list(calls.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", help="KEY-STARTTOKEN")
    parser.add_argument("--claude-transcript", type=Path, help="the current Claude Code session's transcript")
    parser.add_argument("--claude-provider", default="unknown")
    args = parser.parse_args()
    match = RUN_ID.fullmatch(args.run)
    if not match:
        parser.error("expected KEY-STARTTOKEN")
    path = STATE / f"{args.run}.jsonl"
    rows = list(entries(path)) if path.exists() else []
    transcript = args.claude_transcript or session_transcript(os.environ)
    if transcript and not any(row.get("role") == "run" for row in rows):
        try:
            added = claude_rows(entries(transcript), f"/implement {match[1]} start {match[2]}", args.claude_provider)
        except OSError:
            if args.claude_transcript:  # an automatic path that vanished is no transcript, not an error
                print("Cannot read the Claude Code transcript.", file=sys.stderr)
            added = []
        if added:
            STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a") as file:
                file.writelines(f"{json.dumps(row)}\n" for row in added)
            rows.extend(added)
    print("\n".join(summary(rows)) or "Speed: unavailable (no call records)")


if __name__ == "__main__":
    main()
