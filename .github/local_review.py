#!/usr/bin/env python3
"""The local review: one read of a change before its pull request opens (agents/README.md "The local review").

    python3 .github/local_review.py --issue-file ISSUE [--note-file NOTE] [--base origin/main]
        Builds the prompt from four parts: the brief (agents/local-review.md), the issue's text (ISSUE), the author's note
        (NOTE: what the change leaves out of the issue, and why) and `git diff BASE...HEAD`. It gives the prompt on
        standard input to the command in the environment variable LOCAL_REVIEW_COMMAND, which runs in a shell at the top
        of the repository and prints the reply as JSON. The command gets the reply's schema as JSON in the environment
        variable LOCAL_REVIEW_SCHEMA. The script prints each finding as text, then the line `Local review: N findings`.

With `--metrics-run`, a Claude Code result's numbers (tokens, duration, cost) go to the run's metrics file; they are
read from its `--output-format json` result, and the prompt and the reply never enter it.

It never blocks a pull request: it exits 0 every time. With no command, no diff, a failed command, a reply that does not
fit the schema, or no reply after TIMEOUT_SECONDS, it prints the reason and the line `Local review: did not run
(REASON)`. The command and its model are a setting of the run, never named here (agents/README.md, top). A reply that
Claude Code's `--output-format json` wraps holds the object under `structured_output`: both forms are read. Standard
library only (DECISIONS.md "Tooling").

The reply may come from any harness's print mode: `parse_reply` takes the JSON object from the printed text, bare,
under `structured_output`, in a fenced block, or as the last top-level object in prose. The prompt asks for the JSON only
and holds the schema, so a harness with no schema flag gets it too. A reply with no valid object gets one more run of the
command with a short line that asks for the JSON only. A harness's own error result (`{"is_error": true, ...}`, as Claude
Code prints it for a budget or turn limit) is a failed command: the command is not run again.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

BRIEF = Path(__file__).resolve().parent.parent / "agents" / "local-review.md"
COMMAND_VARIABLE = "LOCAL_REVIEW_COMMAND"
SCHEMA_VARIABLE = "LOCAL_REVIEW_SCHEMA"
METRICS_RUN = "AGENT_METRICS_RUN"  # keep equal to .agents/metrics/report.py
METRICS_ID = re.compile(r"[A-Z][A-Z0-9]{0,9}-[0-9]{1,7}-[0-9]{8}T[0-9]{6}Z-[A-Za-z0-9.-]{1,63}")  # report.py RUN_ID
TIMEOUT_SECONDS = 600  # the issue's limit: a review that takes longer costs more than the round it can save
NO_NOTE = "The author gave no note."
RETRY_LINE = "Your last reply held no valid JSON object. Reply with the JSON object only: no text before it, none after it."
FENCE = re.compile(r"```[A-Za-z]*[ \t]*\n(.*?)```", re.DOTALL)

FINDING_KEYS = {"file": str, "line": int, "summary": str, "evidence": str, "fix": str, "confidence": str}
CONFIDENCE = ("high", "medium")
SCHEMA: dict[str, Any] = {
    "type": "object", "required": ["findings", "checked"], "additionalProperties": False,
    "properties": {
        "inventory": {"type": "array", "items": {"type": "string"}},
        "findings": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["file", "line", "summary", "evidence", "fix", "confidence"],
            "properties": {
                "file": {"type": "string"}, "line": {"type": "integer"}, "summary": {"type": "string"},
                "evidence": {"type": "string"}, "fix": {"type": "string"},
                "confidence": {"type": "string", "enum": list(CONFIDENCE)}}}},
        "checked": {"type": "array", "items": {"type": "string"}}}}


class DidNotRun(Exception):
    """The reason the review gave no findings; the script prints it and exits 0."""


def fenced(text: str) -> str:
    """The text in a fenced block whose fence is longer than any run of backticks in it, so the diff cannot close it."""
    longest = run = 0
    for char in text:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    fence = "`" * max(3, longest + 1)
    return f"{fence}diff\n{text.rstrip(chr(10))}\n{fence}"


def build_prompt(brief: str, issue: str, note: str, diff: str) -> str:
    """The brief, then the issue, the author's note and the diff, each under its heading (the replay's order), then
    the reply's form: the JSON only, with its schema in the text (a harness with no schema flag needs it)."""
    return (f"{brief.rstrip()}\n\n## The issue\n\n{issue.strip()}\n\n## What the change leaves out\n\n"
            f"{note.strip() or NO_NOTE}\n\n## The diff\n\n{fenced(diff)}\n\n## Your reply\n\n"
            "Reply with one JSON object only: no text before it, none after it. It fits this JSON schema:\n\n"
            f"```json\n{json.dumps(SCHEMA, indent=1)}\n```\n")


def git(args: Sequence[str], cwd: Path) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        message = (done.stderr.strip().splitlines() or ["no message"])[-1][:200]
        raise DidNotRun(f"git {args[0]} failed: {message}")
    return done.stdout


def problem(reply: object) -> str:
    """Why the reply does not fit SCHEMA, or "" when it fits."""
    if not isinstance(reply, dict):
        return "the reply is not an object"
    if extra := set(reply) - {"inventory", "findings", "checked"}:
        return f"unknown key {sorted(extra)[0]}"
    for key in ("findings", "checked"):
        if not isinstance(reply.get(key), list):
            return f"no list {key}"
    if not isinstance(reply.get("inventory", []), list):
        return "inventory is not a list"
    for key in ("inventory", "checked"):
        if not all(isinstance(item, str) for item in reply.get(key, [])):
            return f"{key} holds an item that is not text"
    for n, finding in enumerate(reply["findings"], 1):
        if not isinstance(finding, dict) or set(finding) != set(FINDING_KEYS):
            return f"finding {n} does not have the keys {', '.join(FINDING_KEYS)}"
        for key, kind in FINDING_KEYS.items():
            if not isinstance(finding[key], kind) or (kind is int and isinstance(finding[key], bool)):
                return f"finding {n}: {key} is not {kind.__name__}"
        if finding["confidence"] not in CONFIDENCE:
            return f"finding {n}: confidence is not high or medium"
    return ""


def unwrapped(value: object) -> object:
    """The object under `structured_output` (Claude Code's JSON output), else the value itself."""
    if isinstance(value, dict) and "structured_output" in value:
        return value["structured_output"]
    return value


def candidates(text: str) -> list[object]:
    """The JSON values of the printed text, best first: the whole text, each fenced block, then each top-level object of
    the prose; the last of a kind first, since a model gives its answer after its thoughts."""
    found: list[object] = []
    try:
        found.append(json.loads(text))
    except ValueError:
        pass
    for block in reversed(FENCE.findall(text)):
        try:
            found.append(json.loads(block))
        except ValueError:
            pass
    decoder = json.JSONDecoder()
    objects: list[object] = []
    at = text.find("{")
    while at != -1:
        try:
            value, end = decoder.raw_decode(text, at)
        except ValueError:
            at = text.find("{", at + 1)
            continue
        objects.append(value)
        at = text.find("{", end)
    return found + objects[::-1]


def harness_error(value: object) -> str:
    """The subtype of a harness's own error result, or "": Claude Code's `--output-format json` prints
    `{"is_error": true, "subtype": "error_max_budget_usd"}` when a budget or turn limit stops it. A second run meets the
    same limit, so such a reply is a failed command, not a reply to ask for again."""
    if isinstance(value, dict) and value.get("is_error") is True:
        return str(value.get("subtype") or "no subtype")
    return ""


def parse_reply(text: str) -> dict[str, Any]:
    """The reply's object, the first candidate that fits SCHEMA. A harness's own error result stops the search with its
    subtype as the reason; with no such result and no candidate that fits, DidNotRun gives the reason of the best."""
    reasons = []
    for value in candidates(text):
        if subtype := harness_error(value):
            raise DidNotRun(f"the command reported an error ({subtype})")
        value = unwrapped(value)
        reason = problem(value)
        if not reason and isinstance(value, dict):
            found: dict[str, Any] = value
            return found
        reasons.append(reason)
    if not reasons:
        raise DidNotRun("the reply is not JSON")
    raise DidNotRun(f"the reply does not fit the schema: {reasons[0]}")


def metric(value: object) -> int | float | None:
    """A number from the harness's result JSON, or None for anything else."""
    return value if type(value) in (int, float) and value >= 0 else None


def claude_review_row(out: str, started_ms: int, ended_ms: int) -> dict[str, Any] | None:
    """The review's metrics row from Claude Code's `--output-format json` result, or None for another output.

    It reads numbers and names only: the prompt and the reply never enter the row."""
    try:
        value = json.loads(out)
    except ValueError:
        return None
    if not isinstance(value, dict) or value.get("type") != "result":
        return None
    usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
    models = value.get("modelUsage") if isinstance(value.get("modelUsage"), dict) else {}
    models = {key: entry for key, entry in models.items() if isinstance(key, str) and isinstance(entry, dict)}
    model = max(models, key=lambda key: metric(models[key].get("outputTokens")) or 0) if models else ""
    # The cost and the time cover every model of `modelUsage`, so the tokens do too; `model` only names the
    # one with the most output.
    if models:
        def total(name: str) -> int | float | None:
            known = [amount for amount in (metric(entry.get(name)) for entry in models.values()) if amount is not None]
            return sum(known) if known else None
        raw, read, written, output = (total(name) for name in
                                      ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens", "outputTokens"))
    else:
        raw, read, written, output = (metric(usage.get(name)) for name in
                                      ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
                                       "output_tokens"))
    elapsed = metric(value.get("duration_api_ms")) or metric(value.get("duration_ms"))
    failed = bool(value.get("is_error"))
    cost = metric(value.get("total_cost_usd"))
    if cost is None and models:
        costs = [metric(entry.get("costUSD")) for entry in models.values()]
        known = [amount for amount in costs if amount is not None]
        cost = sum(known) if known else None
    return dict(role="review", provider="claude-code", model=model or "unknown", effort="unknown",
                started_ms=started_ms, first_ms=None, ended_ms=ended_ms, ttft_s=None, timing="invocation",
                tokens_per_s=output * 1000 / elapsed if not failed and output and elapsed else None,
                input=None if raw is None else raw + (read or 0) + (written or 0),
                cached=read, output=output, reasoning=None, cost=cost, error="api_error" if failed else None)


def record_metrics(out: str, started_ms: int, ended_ms: int, environ: Mapping[str, str]) -> None:
    """Append one row for this invocation when the run gave a metrics id; a failure here never stops the review."""
    run = environ.get(METRICS_RUN, "")
    if not METRICS_ID.fullmatch(run):
        return
    row = claude_review_row(out, started_ms, ended_ms)
    if row is None:
        return
    try:
        folder = Path.home() / ".local" / "state" / "agent-metrics"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(folder / f"{run}.jsonl", os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a") as file:
            file.write(f"{json.dumps(row)}\n")
    except OSError:
        pass


def run_command(command: str, prompt: str, cwd: Path, environ: Mapping[str, str], timeout: float) -> str:
    """The command's standard output. A command that runs past `timeout` is stopped with all its children."""
    env = {**environ, SCHEMA_VARIABLE: json.dumps(SCHEMA)}
    started_ms = int(time.time() * 1000)
    with subprocess.Popen(command, shell=True, cwd=cwd, env=env, text=True, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True) as proc:
        try:
            out, err = proc.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise DidNotRun(f"no reply after {timeout:g} seconds") from exc
        record_metrics(out, started_ms, int(time.time() * 1000), env)
    if proc.returncode != 0:
        tail = err.strip().splitlines()[-3:]
        for line in tail:
            print(f"  {line[:300]}")
        raise DidNotRun(f"the command exited with {proc.returncode}")
    return out


def report(reply: Mapping[str, Any]) -> list[str]:
    """Each finding as text, then the summary line."""
    lines: list[str] = []
    for n, f in enumerate(reply["findings"], 1):
        lines += [f"{n}. {f['file']}:{f['line']} ({f['confidence']}) {f['summary']}",
                  f"   Evidence: {f['evidence']}", f"   Fix: {f['fix']}"]
    count = len(reply["findings"])
    lines.append(f"Local review: {count} finding{'' if count == 1 else 's'}")
    return lines


def review(issue_file: Path, note_file: Path | None, base: str, cwd: Path, environ: Mapping[str, str],
           timeout: float = TIMEOUT_SECONDS) -> list[str]:
    """The lines to print. Raises DidNotRun with the reason when there are no findings to print."""
    command = environ.get(COMMAND_VARIABLE, "").strip()
    if not command:
        raise DidNotRun(f"no {COMMAND_VARIABLE} set")
    try:
        issue = issue_file.read_text()
        note = note_file.read_text() if note_file else ""
        brief = BRIEF.read_text()
    except OSError as exc:
        raise DidNotRun(f"cannot read {Path(exc.filename or '?').name}") from exc
    top = Path(git(["rev-parse", "--show-toplevel"], cwd).strip())
    diff = git(["diff", f"{base}...HEAD"], top)
    if not diff.strip():
        raise DidNotRun(f"no diff against {base}")
    prompt = build_prompt(brief, issue, note, diff)
    try:
        reply = parse_reply(run_command(command, prompt, top, environ, timeout))
    except DidNotRun as first:
        if not str(first).startswith("the reply"):
            raise  # a failed or timed-out command is no reply to ask for again
        try:
            reply = parse_reply(run_command(command, f"{prompt}\n{RETRY_LINE}\n", top, environ, timeout))
        except DidNotRun as second:
            if not str(second).startswith("the reply"):
                raise
            raise DidNotRun(f"no valid reply: {second}") from second
    return report(reply)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--issue-file", type=Path, required=True, help="the issue's text")
    parser.add_argument("--note-file", type=Path, help="what the change leaves out of the issue, and why")
    parser.add_argument("--base", default="origin/main", help="the diff is BASE...HEAD (default: origin/main)")
    parser.add_argument("--metrics-run", help="KEY-STARTTOKEN for the local review's call metrics")
    args = parser.parse_args(argv)
    env = {**os.environ, "AGENT_METRICS_RUN": args.metrics_run or ""}
    try:
        lines = review(args.issue_file, args.note_file, args.base, Path.cwd(), env)
    except DidNotRun as exc:
        lines = [f"Local review: did not run ({exc})"]
    except Exception as exc:  # the review never blocks: any other failure is a reason too
        lines = [f"Local review: did not run ({type(exc).__name__})"]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
