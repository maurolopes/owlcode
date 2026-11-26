"""The loop's numbers (agents/README.md "What to measure"), read from GitHub with `gh` and from a dump of the Linear issues.

    python3 .agents/skills/measure/measure.py --since 2026-10-29 [--until 2026-11-09] [--issues issues.json]
        --team KEY=OWNER/NAME [--team ...] [--dump prs.json | --from-dump prs.json]

The pull requests are the ones created in [since, until) and merged (the 2026-11-08 analysis's window). Each
number's definition is in the comment above the line that computes it. `--issues` is a JSON list of issues in the shape
of `.github/linear.py list` (identifier, state, labels, created, comments): the Linear numbers and the table by model
and effort need it, and are left out without it. `--dump` writes the pull requests read from GitHub to a file, and
`--from-dump` reads them again from it, so a second report needs no request. Each `--team KEY=OWNER/NAME` is a Linear team
and its repository (AGENTS.md "The teams"): the report has one part per team, and a total when there are
several. With more than one team, each team's dump is its own file, the team's key before the file's suffix
(`prs.ABC.json` for the team ABC). Standard library only; no key in a file.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REVIEWER = "-reviewer"  # the end of the reviewer app's login in GraphQL (no "[bot]"): any login that ends with this counts
GAP_MINUTES = 30  # a gap between two events of a pull request longer than this is idle time
BANDS = ((100, "100 or fewer"), (400, "101 to 400"), (None, "over 400"))  # changed lines
FENCED = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
# A run line: `Run: MODEL, effort EFFORT`, then the local review's model and effort level when the
# orchestrator wrote them (`; review MODEL, effort EFFORT`: the orchestrator writes it on every line; an older line has none), then the profile that ran
# the cell (`; profile NAME`: an older line has none), then a start token or the words of a fix round in a bracket.
RUN_LINE = re.compile(r"^Run:\s*(?P<model>[^,\n]+?)\s*,\s*effort\s+(?P<effort>[^\s;(]+)"
                      r"(?:;\s*review\s+(?P<review>[^,\n]+?)\s*,\s*effort\s+(?P<review_effort>[^\s;(]+))?"
                      r"(?:;\s*profile\s+(?P<profile>[^\s;(]+))?", re.MULTILINE)
FINDING = re.compile(r"^\s*- \[[ xX]\] ", re.MULTILINE)  # a follow-up's finding: one checklist line (.github/linear.py)
# The line the local review leaves in the pull request's text (implementer.md "When you are done", step 2), as the
# implementer writes it (`Local review: N findings, F fixed, L left out`) or as the script prints it when it did not run.
# A bullet, a quote mark or code backticks before it are allowed; "1 finding" is singular
LOCAL_REVIEW = re.compile(r"^[\s>*-]*`?Local review: (?:(?P<n>\d+) findings?, (?P<f>\d+) fixed, (?P<l>\d+) left out|did not run\b)",
                          re.MULTILINE)

# The last 30 commits, 10 check suites and 10 check runs each: 3 points of GraphQL's hourly 5000 per pull request. The
# 2026-11-08 analysis read 100 commits and 20 of each, 21 points, and ran out of the limit on 270 pull requests
QUERY = """query($owner: String!, $name: String!, $n: Int!) { repository(owner: $owner, name: $name) { pullRequest(number: $n) {
  number title body createdAt mergedAt state headRefName mergedBy { login } additions deletions
  labels(first: 20) { nodes { name } }
  files(first: 100) { nodes { path } }
  timelineItems(first: 100, itemTypes: [READY_FOR_REVIEW_EVENT, CONVERT_TO_DRAFT_EVENT, LABELED_EVENT, UNLABELED_EVENT]) {
    nodes { __typename
      ... on ReadyForReviewEvent { createdAt }
      ... on ConvertToDraftEvent { createdAt }
      ... on LabeledEvent { createdAt label { name } }
      ... on UnlabeledEvent { createdAt label { name } } } }
  reviews(first: 50) { nodes { author { login } state submittedAt body } }
  comments(first: 100) { nodes { createdAt } }
  commits(last: 30) { nodes { commit { committedDate
    checkSuites(first: 10) { nodes { checkRuns(first: 10) { nodes { name conclusion startedAt completedAt } } } } } } }
} } }"""


# ---- pure parts (tests/test_measure.py) ----

def when(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


def minutes(start: str | None, end: str | None) -> float | None:
    a, b = when(start), when(end)
    return (b - a).total_seconds() / 60 if a and b else None


def median_p90(values: Iterable[float | None]) -> tuple[float, float, int] | None:
    """The median, the 90th percentile (the value at rank 0.9 of the sorted list, as the 2026-11-08 analysis read it) and
    the count; None for no value."""
    xs = sorted(x for x in values if x is not None)
    if not xs:
        return None
    return statistics.median(xs), xs[min(len(xs) - 1, int(len(xs) * 0.9))], len(xs)


def verdict_of(body: str | None) -> dict[str, Any] | None:
    """The verdict JSON of a review's body (agents/verdict.schema.json): the last fenced json block, or None."""
    blocks = FENCED.findall(body or "")
    if not blocks:
        return None
    try:
        found = json.loads(blocks[-1])
    except ValueError:
        return None
    return found if isinstance(found, dict) and "verdict" in found else None


def verdicts(pr: Mapping[str, Any], reviewer: str = REVIEWER) -> list[dict[str, Any]]:
    """The reviewer's verdicts on the pull request, in order. A review with no verdict JSON (an inline comment) is none."""
    found = []
    for review in pr["reviews"]["nodes"]:
        if ((review.get("author") or {}).get("login") or "").endswith(reviewer) and (v := verdict_of(review.get("body"))) is not None:
            found.append({**v, "submitted": review.get("submittedAt")})
    return found


def blocking(verdict: Mapping[str, Any]) -> bool:
    return any(f.get("blocking") for f in verdict.get("findings") or [])


def fix_now_only(verdict: Mapping[str, Any]) -> bool:
    findings = verdict.get("findings") or []
    return not blocking(verdict) and any(f.get("fix_now") for f in findings)


def second_rounds(found: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The verdicts whose JSON says round 2. The position is no round: the count starts again on an approved pull request
    (failed gates, a conflict, a commit after the approve, the owner's answer), so a second verdict can be a new round 1."""
    return [v for v in found if v.get("round") == 2]


def open_blocking(verdict: Mapping[str, Any]) -> bool:
    """A blocking finding is open: a new one in `findings`, or one of the round before that `previous` lists as not fixed
    (round 3 records the blocking findings that round 2 left open there, and often writes no new finding)."""
    return blocking(verdict) or any(p.get("fixed") is False and not p.get("fix_now") for p in verdict.get("previous") or [])


def escalated_after_the_last(found: Sequence[Mapping[str, Any]]) -> bool:
    """A pull request still blocked after its last round went to the owner: a `needs-decision` verdict in round 2 or later with a blocking
    finding open (a new one, or one of the round before still open). A needs-decision with none asks for the owner's step before the merge: the pull request was not blocked.
    Two rounds at most until 2026-11-16, three since: a round 2 that asks for changes is no longer the last."""
    return any(v.get("verdict") == "needs-decision" and int(v.get("round") or 0) >= 2 and open_blocking(v) for v in found)


def first_ready(pr: Mapping[str, Any]) -> str:
    """The first ready mark, or the creation for a pull request opened ready."""
    marks = [e["createdAt"] for e in pr["timelineItems"]["nodes"] if e["__typename"] == "ReadyForReviewEvent"]
    return min(marks) if marks else pr["createdAt"]


def events(pr: Mapping[str, Any]) -> list[str]:
    """Every dated event of the pull request: its creation and merge, timeline events, reviews, comments, commits."""
    found = [pr["createdAt"], pr["mergedAt"]]
    found += [e.get("createdAt") for e in pr["timelineItems"]["nodes"]]
    found += [r.get("submittedAt") for r in pr["reviews"]["nodes"]]
    found += [c.get("createdAt") for c in pr["comments"]["nodes"]]
    found += [c["commit"].get("committedDate") for c in pr["commits"]["nodes"]]
    return [e for e in found if e]


def active_share(stamps: Iterable[str], start: str, end: str, gap: float = GAP_MINUTES) -> tuple[float, float]:
    """(active minutes, total minutes) between start and end: the gaps between two events that follow each other, each one
    counted active when it is `gap` minutes or less. An event outside [start, end] does not count."""
    a, b = when(start), when(end)
    assert a and b
    points = sorted({t for s in stamps if (t := when(s)) and a <= t <= b} | {a, b})
    active = total = 0.0
    for x, y in itertools.pairwise(points):
        length = (y - x).total_seconds() / 60
        total += length
        active += length if length <= gap else 0
    return active, total


def size_band(changed: int) -> str:
    for limit, name in BANDS:
        if limit is None or changed <= limit:
            return name
    return BANDS[-1][1]


def escalated_minutes(pr: Mapping[str, Any]) -> float:
    """Minutes that the label `escalated` was on: each labeled event to the next unlabeled one, or to the merge."""
    on: str | None = None
    total = 0.0
    for e in sorted(pr["timelineItems"]["nodes"], key=lambda e: e.get("createdAt") or ""):
        if (e.get("label") or {}).get("name") != "escalated":
            continue
        if e["__typename"] == "LabeledEvent" and on is None:
            on = e["createdAt"]
        elif e["__typename"] == "UnlabeledEvent" and on is not None:
            total += minutes(on, e["createdAt"]) or 0
            on = None
    if on is not None:
        total += minutes(on, pr["mergedAt"]) or 0
    return total


def check_runs(pr: Mapping[str, Any], name: str) -> list[dict[str, Any]]:
    return [run for c in pr["commits"]["nodes"] for suite in c["commit"]["checkSuites"]["nodes"]
            for run in suite["checkRuns"]["nodes"] if run.get("name") == name]


def first_run(comments: Iterable[Mapping[str, Any]]) -> tuple[str, str, str] | None:
    """(model, effort, profile) of the issue's first run line `Run: MODEL, effort EFFORT`, by the comment's date.
    The profile is the one the line names (`; profile NAME`), "none" for a line that names none (an older one)."""
    for found in run_lines(comments):
        return found["model"].strip(), found["effort"].strip().rstrip(".,"), found["profile"] or "none"
    return None


def review_name(run: str, review: str) -> str:
    """The local review's model as one name across old and new pi run lines. A pi run model is
    `PROVIDER/MODEL`, so its first part is the provider. An older run line wrote the review's catalog ID
    without that prefix (`zai-org/GLM-5.3-Flash`); a newer one writes the full name (`pi-lithosai/zai-org/GLM-5.3-Flash`).
    The provider prefixes an older name, so both group together. A name that already holds the prefix stays. A run model
    that names no provider (a Claude Code run) keeps the review's name as written. The review's own first part is never
    a provider: a catalog ID can hold a slash."""
    name = review.strip()
    provider, slash, _ = run.strip().partition("/")
    if not slash or name == provider or name.startswith(f"{provider}/"):
        return name
    return f"{provider}/{name}"


def first_review(comments: Iterable[Mapping[str, Any]]) -> tuple[str, str] | None:
    """(model, effort) of the local review in the issue's first run line that names one: `Run: MODEL, effort
    EFFORT; review MODEL, effort EFFORT`. A line that names none (an older issue's) is none, so the two tables never group an
    unknown model as one. The review's model gets the provider of the run's model."""
    for found in run_lines(comments):
        if found["review"]:
            return (review_name(found["model"], found["review"]),
                    (found["review_effort"] or "none").strip().rstrip(".,"))
    return None


def run_lines(comments: Iterable[Mapping[str, Any]]) -> list[re.Match[str]]:
    """The issue's run lines, by the comment's date."""
    return [found for comment in sorted(comments, key=lambda c: str(c.get("created") or ""))
            if (found := RUN_LINE.search(comment.get("body") or "")) is not None]


def issue_key(pr: Mapping[str, Any], teams: Sequence[str]) -> str | None:
    """The issue's key in the pull request's branch, else in its title, for a key of one of `teams` (one Linear team
    per repository), in upper case."""
    key = re.compile(rf"\b({'|'.join(map(re.escape, teams))})-(\d+)\b", re.IGNORECASE)
    found = key.search(pr.get("headRefName") or "") or key.search(pr.get("title") or "")
    return f"{found.group(1).upper()}-{found.group(2)}" if found else None


def team_of(identifier: object) -> str:
    """The team of an issue's key: `ABC-12` -> `ABC`."""
    return str(identifier).split("-", 1)[0].upper()


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}% ({part} of {whole})" if whole else "none"


def fmt(stats: tuple[float, float, int] | None, unit: str = "min") -> str:
    return f"{stats[0]:.1f} {unit} median, p90 {stats[1]:.1f} (n {stats[2]})" if stats else "none"


def local_review(pr: Mapping[str, Any]) -> tuple[int, int, int] | None:
    """(N, F, L) of the pull request's line `Local review: N findings, F fixed, L left out`; None when its text says `did not
    run`, or has no such line (an older dump has no text: no line). The last such line counts."""
    found = list(LOCAL_REVIEW.finditer(str(pr.get("body") or "")))
    if not found or found[-1].group("n") is None:
        return None
    last = found[-1]
    return int(last.group("n")), int(last.group("f")), int(last.group("l"))


def local_review_numbers(prs: Sequence[Mapping[str, Any]], reviewer: str = REVIEWER) -> list[tuple[str, str]]:
    """The local review (kept on 2026-11-18): the pull requests that ran it beside the others."""
    rows: list[tuple[str, str]] = []
    ran = [(pr, counts) for pr in prs if (counts := local_review(pr)) is not None]
    numbers = {pr["number"] for pr, _ in ran}
    rest = [pr for pr in prs if pr["number"] not in numbers]
    # ran it: the text has the line `Local review: N findings, ...`; did not: the line `did not run (...)`, or no line
    rows.append(("local review ran", str(len(ran))))
    rows.append(("local review did not run, or no line", str(len(rest))))
    # the round-1 approve share of each group: its pull requests with a verdict, whose first verdict is an approve
    for name, group in (("ran it", [pr for pr, _ in ran]), ("did not", rest)):
        firsts = [v[0] for pr in group if (v := verdicts(pr, reviewer))]
        rows.append((f"round 1 approve, local review {name}", pct(sum(v["verdict"] == "approve" for v in firsts), len(firsts))))
    # the findings of the group that ran it: the totals of N, F and L, and N for each pull request
    n, f, left = (sum(c[i] for _, c in ran) for i in range(3))
    rows.append(("local review findings", f"{n} in all, {f} fixed, {left} left out" if ran else "none"))
    rows.append(("local review findings per pull request",
                 f"{n / len(ran):.2f} (" + ", ".join(f"#{pr['number']} {c[0]}" for pr, c in ran) + ")" if ran else "none"))
    return rows


def loop_numbers(prs: Sequence[Mapping[str, Any]], owner: str, reviewer: str = REVIEWER) -> list[tuple[str, str]]:
    """The first table: every number of agents/README.md "What to measure" that the pull requests give, in that order."""
    rows: list[tuple[str, str]] = []
    vs = {pr["number"]: verdicts(pr, reviewer) for pr in prs}
    ready_to_merge = {pr["number"]: minutes(first_ready(pr), pr["mergedAt"]) or 0.0 for pr in prs}
    rows.append(("pull requests", str(len(prs))))
    # lead time: from the first ready mark (or the creation, for a pull request opened ready) to the merge
    rows.append(("lead time, ready mark to merge", fmt(median_p90(ready_to_merge.values()))))
    # active share: the share of that time in gaps of GAP_MINUTES or less between two events of the pull request
    active = total = 0.0
    for pr in prs:
        a, t = active_share(events(pr), first_ready(pr), pr["mergedAt"])
        active, total = active + a, total + t
    rows.append((f"active share (gaps of {GAP_MINUTES} min or less)", f"{100 * active / total:.0f}%" if total else "none"))
    # the first verdict after the first ready mark
    rows.append(("ready mark to first verdict", fmt(median_p90(minutes(first_ready(pr), v[0]["submitted"])
                                                               for pr in prs if (v := vs[pr["number"]])))))
    # verdicts per pull request: the reviewer's reviews with a verdict JSON, over every pull request
    rows.append(("verdicts per pull request", f"{sum(len(v) for v in vs.values()) / len(prs):.2f}" if prs else "none"))
    # round 1: the first verdict's kind; "fix_now only": a request-changes with no blocking finding
    firsts = [v[0] for v in vs.values() if v]
    kinds = Counter(v["verdict"] for v in firsts)
    for kind in ("approve", "request-changes", "needs-decision"):
        rows.append((f"round 1 {kind}", pct(kinds[kind], len(firsts))))
    asked = [v for v in firsts if v["verdict"] == "request-changes"]
    rows.append(("round 1 request-changes with fix_now findings only", pct(sum(fix_now_only(v) for v in asked), len(asked))))
    # blocking rounds: verdicts with at least one blocking finding, per pull request
    rows.append(("blocking rounds per pull request", f"{sum(blocking(x) for v in vs.values() for x in v) / len(prs):.2f}" if prs else "none"))
    # round 2 (it follows a round-1 request-changes): the approves among the verdicts whose JSON says round 2
    seconds = [x for v in vs.values() for x in second_rounds(v)]
    rows.append(("round 2 approve after a round-1 request-changes", pct(sum(v["verdict"] == "approve" for v in seconds), len(seconds))))
    # still blocked after round 2: a pull request with a round-2 verdict that is not an approve and has a blocking finding open
    # (a clean needs-decision for the owner's step before the merge is not blocked)
    rows.append(("still blocked after round 2", str(sum(any(x["verdict"] != "approve" and open_blocking(x) for x in second_rounds(v))
                                                        for v in vs.values()))))
    # still blocked after round 3: the same test for a round-3 verdict
    rows.append(("still blocked after round 3", str(sum(any(x.get("round") == 3 and x["verdict"] != "approve" and open_blocking(x) for x in v)
                                                        for v in vs.values()))))
    # merges by the owner: the merge's actor is the repository owner (a code owner's file, or the owner's own merge)
    by_owner = [pr for pr in prs if (pr.get("mergedBy") or {}).get("login") == owner]
    rows.append(("merges by the owner", pct(len(by_owner), len(prs))))
    # the owner's wait: from the reviewer's last APPROVED review to the merge, on the pull requests the owner merged. The
    # owner's own approve, given just before a merge, is not the start
    waits = [minutes(approved[-1]["submittedAt"], pr["mergedAt"]) for pr in by_owner
             if (approved := [r for r in pr["reviews"]["nodes"]
                              if r.get("state") == "APPROVED" and ((r.get("author") or {}).get("login") or "").endswith(reviewer)])]
    whole = sum(ready_to_merge.values())
    rows.append(("the owner's wait after the last approve",
                 f"{sum(w or 0 for w in waits) / 60:.0f} h in all, {100 * sum(w or 0 for w in waits) / whole:.0f}% of the ready-to-merge time; "
                 + fmt(median_p90(waits)) if whole else "none"))
    # escalated dwell: the hours the label `escalated` was on, summed over the pull requests
    rows.append(("escalated dwell", f"{sum(escalated_minutes(pr) for pr in prs) / 60:.0f} h in all"))
    # the gates' and the review's durations: each successful check run on the pull request's commits, start to end
    for name in ("python", "web", "review"):
        rows.append((f"check {name}", fmt(median_p90(minutes(r["startedAt"], r["completedAt"]) for pr in prs
                                                     for r in check_runs(pr, name) if r.get("conclusion") == "SUCCESS"))))
    # the last green gate to the merge: on each pull request whose gates ran, the end of its last successful python or web
    ends = [(pr, max(ok)) for pr in prs if (ok := [r["completedAt"] for n in ("python", "web") for r in check_runs(pr, n)
                                                   if r.get("conclusion") == "SUCCESS" and r.get("completedAt")])]
    rows.append(("last green gate to merge", fmt(median_p90(minutes(end, pr["mergedAt"]) for pr, end in ends))))
    # changed lines: additions plus deletions
    rows.append(("changed lines per pull request", fmt(median_p90(pr["additions"] + pr["deletions"] for pr in prs), "lines")))
    # round-1 approve by size band of changed lines
    for _, band in BANDS:
        inside = [pr for pr in prs if size_band(pr["additions"] + pr["deletions"]) == band and vs[pr["number"]]]
        rows.append((f"round 1 approve, {band} lines", pct(sum(vs[pr["number"]][0]["verdict"] == "approve" for pr in inside), len(inside))))
    # fix_now findings in a document: the round-1 fix_now findings whose file ends in .md
    fixes = [f for v in firsts for f in v.get("findings") or [] if f.get("fix_now")]
    rows.append(("round 1 fix_now findings in a document", pct(sum(str(f.get("file", "")).endswith(".md") for f in fixes), len(fixes))))
    return rows


# The events of a `gates` run on main: once each night and on demand since 2026-11-12, on each push before it.
# The push runs stay here, so a window before that day still reads.
MAIN_EVENTS = ("push", "schedule", "workflow_dispatch")


def main_runs(repo: str, since: str, until: str, gh: Callable[..., str]) -> list[dict[str, Any]]:
    """The `gates` runs on main created in the window, of each event in MAIN_EVENTS (`gh run list` takes one event)."""
    runs: list[dict[str, Any]] = []
    for event in MAIN_EVENTS:
        try:
            runs += json.loads(gh("run", "list", "-R", repo, "--workflow", "gates.yml", "--branch", "main", "--event", event,
                                  "--created", f"{since}..{until}", "--limit", "2000", "--json", "conclusion,createdAt"))
        except subprocess.CalledProcessError as failed:
            if "not found" not in (failed.stderr or ""):
                raise
            return []  # a team with no `gates` workflow has no red-main row to count: red_main prints "none"
    return [r for r in runs if r["createdAt"][:10] < until]


def red_main(runs: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    """Red runs on main: the `gates` runs on main (main_runs) that ended in failure, over those that ended. A run on demand
    that found its commit passed already runs no gate and counts as passed."""
    ended = [r for r in runs if r.get("conclusion") in ("success", "failure")]
    return ("red gates runs on main", pct(sum(r["conclusion"] == "failure" for r in ended), len(ended)))


def issue_numbers(issues: Sequence[Mapping[str, Any]], since: str, until: str) -> list[tuple[str, str]]:
    """Follow-up and fast-lane issues created in the window, with how many are Done and how many Canceled. A follow-up issue
    collects the findings of several reviews of its Area: it counts once, and the row adds its findings in all
    (its checklist lines)."""
    rows = []
    for label in ("follow-up", "fast-lane"):
        filed = [i for i in issues if label in i.get("labels", []) and since <= str(i.get("created", ""))[:10] < until]
        states = Counter(i.get("state") for i in filed)
        found = sum(len(FINDING.findall(str(i.get("description") or ""))) for i in filed)
        rows.append((f"{label} issues filed", f"{len(filed)}, Done {states['Done']}, Canceled {states['Canceled']}"
                     + (f", findings {found}" if label == "follow-up" else "")))
    return rows


def by_run(prs: Sequence[Mapping[str, Any]], issues: Sequence[Mapping[str, Any]], reviewer: str = REVIEWER,
           key: Callable[[Iterable[Mapping[str, Any]]], tuple[str, ...] | None] = first_run) -> dict[tuple[str, ...], dict[str, int]]:
    """The second table: the issues grouped by their first run line, with their pull requests, review rounds,
    blocking rounds, and issues sent to Escalated after the last round (`escalated_after_the_last`). A run with
    `unknown` keeps its group as written: the reader counts only where the value is known. `key` reads the group out of an
    issue's comments: `first_run`, or `first_review` for the local review's model."""
    runs = {i["identifier"]: run for i in issues if (run := key(i.get("comments") or []))}
    teams = sorted({team_of(i["identifier"]) for i in issues})
    table: dict[tuple[str, ...], dict[str, int]] = defaultdict(lambda: Counter({"issues": 0, "pull requests": 0, "review rounds": 0,
                                                                                 "blocking rounds": 0, "escalated after the last round": 0}))
    seen: set[str] = set()
    for pr in prs:
        key = issue_key(pr, teams)
        if key is None or key not in runs:
            continue
        row = table[runs[key]]
        if key not in seen:
            seen.add(key)
            row["issues"] += 1
        v = verdicts(pr, reviewer)
        row["pull requests"] += 1
        row["review rounds"] += len(v)
        row["blocking rounds"] += sum(blocking(x) for x in v)
        row["escalated after the last round"] += int(escalated_after_the_last(v))
    return dict(table)


# ---- GitHub ----

def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def read_prs(repo: str, since: str, until: str, dump: Path | None) -> list[dict[str, Any]]:
    """The merged pull requests of the window, one GraphQL request each. With `dump`, the file is written after each one,
    and the pull requests already in it are not read again: a run that stopped (GitHub's rate limit) goes on from there."""
    owner, name = repo.split("/")
    listed = json.loads(gh("pr", "list", "-R", repo, "--state", "merged", "--limit", "2000", "--json", "number",
                           "--search", f"created:{since}..{until}"))
    kept: dict[int, dict[str, Any]] = {}
    if dump and dump.is_file():
        kept = {pr["number"]: pr for pr in json.loads(dump.read_text())}
    for number in sorted(p["number"] for p in listed):
        if number in kept:
            continue
        try:
            data = json.loads(gh("api", "graphql", "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"n={number}", "-f", f"query={QUERY}"))
        except subprocess.CalledProcessError as failed:
            sys.exit(f"#{number} could not be read: {failed.stderr.strip()[:300]}. Run the same command again later"
                     + (": it goes on from the dump." if dump else " with --dump FILE, which keeps what was read."))
        kept[number] = data["data"]["repository"]["pullRequest"]
        if dump:
            dump.write_text(json.dumps(list(kept.values())))
        print(f"read #{number}", file=sys.stderr)
    return [pr for pr in kept.values() if pr and pr.get("mergedAt") and since <= pr["createdAt"][:10] < until]


def report(title: str, prs: Sequence[Mapping[str, Any]], owner: str, runs: Sequence[Mapping[str, Any]],
           issues: Sequence[Mapping[str, Any]], since: str, until: str) -> None:
    """Print one part of the report: the loop's numbers, then the table by model and effort when there are issues."""
    rows = loop_numbers(prs, owner)
    rows += local_review_numbers(prs)
    rows.append(red_main(runs))
    if issues:
        rows += issue_numbers(issues, since, until)
    print(f"{title}, pull requests created {since} to {until} (the last day not included) and merged\n")
    width = max(len(name) for name, _ in rows)
    for name, value in rows:
        print(f"{name:<{width}}  {value}")
    if not issues:
        return
    columns = ("issues", "pull requests", "review rounds", "blocking rounds", "escalated after the last round")
    print(f"\n{title}, by the first run line of each issue (Run: MODEL, effort EFFORT; profile NAME)\n")
    print("model, effort, profile | " + " | ".join(columns))
    for (model, effort, profile), counts in sorted(by_run(prs, issues).items()):
        print(f"{model}, {effort}, {profile} | " + " | ".join(str(counts[c]) for c in columns))
    reviews = by_run(prs, issues, key=first_review)
    if reviews:
        print(f"\n{title}, by the local review of each issue's first run line that names one "
              "(review MODEL, effort EFFORT)\n")
        print("review model, effort | " + " | ".join(columns))
        for (model, effort), counts in sorted(reviews.items()):
            print(f"{model}, {effort} | " + " | ".join(str(counts[c]) for c in columns))


def with_repo(prs: Sequence[Mapping[str, Any]], repo: str) -> list[dict[str, Any]]:
    """The pull requests with the repository in their number (`owner/repo#12`): two repositories each have a
    #12, and the total keys its numbers by pull request."""
    return [{**pr, "number": f"{repo}#{pr['number']}"} for pr in prs]


def team_arg(text: str) -> tuple[str, str]:
    """`abc=owner/name` -> ("ABC", "owner/name")."""
    key, sep, repo = text.partition("=")
    if not sep or not key or repo.count("/") != 1:
        raise argparse.ArgumentTypeError(f"{text!r} is not KEY=OWNER/NAME")
    return key.upper(), repo


def team_dump(path: Path | None, key: str, several: bool) -> Path | None:
    """With several teams, each team's dump is its own file: `prs.json` -> `prs.ABC.json`."""
    return path.with_name(f"{path.stem}.{key}{path.suffix}") if path and several else path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", required=True, help="the first day, YYYY-MM-DD (UTC)")
    parser.add_argument("--until", default=datetime.now(UTC).strftime("%Y-%m-%d"), help="the day after the last (default: today)")
    parser.add_argument("--team", action="append", type=team_arg, metavar="KEY=OWNER/NAME", required=True,
                        help="a Linear team and its repository, once per team (AGENTS.md \"The teams\")")
    parser.add_argument("--issues", type=Path, help="a JSON list of issues in the shape of .github/linear.py list, of every team")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--dump", type=Path, help="write the pull requests read to this file, and go on from it when it exists")
    source.add_argument("--from-dump", type=Path, help="read the pull requests from this file, not from GitHub")
    args = parser.parse_args(argv)
    teams: list[tuple[str, str]] = args.team
    several = len(teams) > 1
    issues: list[dict[str, Any]] = json.loads(args.issues.read_text()) if args.issues else []
    every_pr: list[Mapping[str, Any]] = []
    every_run: list[Mapping[str, Any]] = []
    for key, repo in teams:
        # the `--until` day is not in the window; gh's created:A..B takes both days, and the filter drops the last
        if args.from_dump:
            dumped = team_dump(args.from_dump, key, several)
            assert dumped is not None
            prs = [pr for pr in json.loads(dumped.read_text())
                   if pr and pr.get("mergedAt") and args.since <= pr["createdAt"][:10] < args.until]
        else:
            prs = read_prs(repo, args.since, args.until, team_dump(args.dump, key, several))
        runs = main_runs(repo, args.since, args.until, gh)
        report(f"{key} ({repo})", prs, repo.split("/")[0], runs, [i for i in issues if team_of(i.get("identifier")) == key],
               args.since, args.until)
        print()
        every_pr += with_repo(prs, repo)
        every_run += runs
    if several:
        # the total: every team's pull requests, runs and issues together; the owner is the first team's owner
        report("All teams", every_pr, teams[0][1].split("/")[0], every_run, issues, args.since, args.until)
    if not issues:
        print("\nNo --issues file: the Linear numbers and the table by model and effort are left out.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
