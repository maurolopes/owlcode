#!/usr/bin/env python3
"""The review watchdog, for the workflow review-watchdog.yml (agents/README.md "The guards"): a pull request that is
ready for review, but whose head got no check `verdict`, waits for ever, since only the review workflow writes that check
and the ruleset `main` requires it. Every 15 minutes the watchdog finds such a pull request and starts its review again, or
says what it waits for.

    python3 .github/review_watchdog.py query
        the GraphQL query that reads the open pull requests into main (for `gh api graphql -f query=...`)
    python3 .github/review_watchdog.py failed-reviews PULLS RUNS
        the id of each review run whose jobs `choose` reads, one a line: the latest run of each open pull request, when it
        failed in attempt 1 (see "A report that failed" below)
    python3 .github/review_watchdog.py choose PULLS RUNS --now TIME --owner LOGIN --bot LOGIN [--jobs JOBS]
        what to do, one JSON object a line, from the query's reply in the file PULLS and the review workflow's latest runs
        in RUNS (the runs API's reply): {"pr": N, "head": SHA, "action": ACTION, "marker": TEXT, "run": ID}, ACTION one of
        `approve-note`, `dispatch`, `second-miss`, `rerun-report`, `report-miss` (`run`: the review run, for
        `rerun-report` only). JOBS maps the id of each run that `failed-reviews` printed to the jobs API's reply for it.
        MARKER goes into the comment, so the next run repeats nothing
    python3 .github/review_watchdog.py stalled-gates PULLS GATES --now TIME
        the id of each `gates` run to check for a failure at startup, one a line, from the query's reply in PULLS and the
        gates workflow's latest `pull_request` runs in GATES (the runs API's reply): see "Gates that never started" below
    python3 .github/review_watchdog.py watch
        the step "Start a lost review again, or say what the pull request waits for" of the workflow (its logic
        moved here from the workflow, so a change of the rules needs no workflow patch): read the pull requests and the
        review runs, `choose`, then comment and start each review or rerun each report. It reads GITHUB_REPOSITORY,
        GH_TOKEN (the reviewer app's: the comments and the pull requests), DISPATCH (GitHub Actions' token: the runs, the
        jobs, the dispatch and the rerun), OWNER, MAIN (the default branch) and REVIEWER, and writes pulls.json in the
        working folder for `rerun-gates`. Exit 0; gh's code when a read of the pull requests or of the runs fails
    python3 .github/review_watchdog.py rerun-gates
        the step "Rerun the gates that GitHub failed at startup": `stalled-gates` on pulls.json, then the rerun of each
        run that has no job. It reads GITHUB_REPOSITORY, GH_TOKEN (GitHub Actions' token) and GATES_WORKFLOW (optional: the
        gates' workflow file, default `gates.yml`). Exit 0; gh's code when a
        read of the runs fails

A pull request is a candidate when its head is in this repository (a fork's pull request is never read: agents/README.md
"Fork pull requests"), it is not a draft, its head has no check `verdict` (the review's `start` writes one,
"Under review", as soon as it runs), no run of "Claude Code Review" for it is queued or running, and its last ready mark,
or the push of its head, is 15 minutes old or more (WAIT). Then, in this order:

1. The repository owner approved the head on GitHub, and the label `owner-approved` is not there: GitHub's Approve is not
   the owner's Approved here (the owner's answer, 2026-11-04: the label stays the one signal). One
   comment, once per head (`approve-note`). The watchdog never adds the label.
2. The label `escalated` and no such approve: nothing. The pull request waits on the owner, as it should.
3. Else the review is started again by `workflow_dispatch` with `pr`, as the review workflow's `carry` does, at most once
   per head (`dispatch`). When that run left no check either, WAIT after its comment, one comment for the owner
   (`second-miss`), once per head, and no more dispatches.

A report that failed. The review can post its approve or its request for changes and then fail in its job
`report`, which completes the check: on 2026-11-16 that job of a review failed in its checkout,
the head kept the check "Under review" in progress, and the pull request waited 4.7 hours with nobody told. So before the
steps above: when the reviewer app (BOT) approved or requested changes on the head REPORT_WAIT or more ago, the head has
no completed check `verdict`, and no review run for the pull request is queued or running, the watchdog reads the
pull request's latest review run. When that run failed in attempt 1 and its job `report` failed, the failed jobs of that
run are run again (`rerun-report`), once per head, only when no other job failed: `gh run rerun --failed` reruns every
failed job, and a second job `review` would be a second review and a new round. Otherwise (attempt 2 or more, no such run
in RUNS, a run in which another job failed too, or a rerun that still did not start REPORT_WAIT after its comment), one
comment for the owner (`report-miss`), once per head. Without the jobs of such a run (their read failed), the watchdog waits for the next pass.

Each head's state is in the comments the watchdog wrote (BOT's: the reviewer app's, with a MARKER for the head): no other
store. A run of the
review workflow that names no pull request (a run by hand before the workflow had a run-name) holds every dispatch of this run:
it may be one of these pull requests' reviews, and a second review would count a round.

Gates that never started. GitHub can fail a run at startup: conclusion `failure`, zero jobs. The workflow
gates-retry reruns such a run, but on 2026-11-09 it failed at startup in the same second, and the pull request waited two
hours. So each pass also takes the latest `gates` run of each open pull request's head: when it completed as a failure in
attempt 1, RETRY_WAIT or more ago (gates-retry had its turn), its id is printed. `rerun-gates` reads that run's jobs and
reruns the whole run only when it has none: a run with a failed gate keeps its jobs and stays red. A rerun is attempt 2,
so it happens once. Only a current head counts: the rerun of an outdated head would cancel the gates of the new one, which
share its concurrency group. Standard library only (DECISIONS.md "Tooling").
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

QUERY = """query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, baseRefName: "main", first: 100) {
      nodes {
        number
        isDraft
        isCrossRepository
        createdAt
        headRefOid
        labels(first: 20) { nodes { name } }
        reviews(last: 50) { nodes { author { login } state submittedAt commit { oid } } }
        comments(last: 100) { nodes { author { login } body createdAt } }
        timelineItems(last: 20, itemTypes: [READY_FOR_REVIEW_EVENT]) { nodes { ... on ReadyForReviewEvent { createdAt } } }
        headRef {
          target {
            ... on Commit {
              oid
              committedDate
              checkSuites(first: 50) {
                nodes { createdAt checkRuns(first: 10, filterBy: {checkName: "verdict"}) { nodes { name status } } }
              }
            }
          }
        }
      }
    }
  }
}"""

WAIT = timedelta(minutes=15)
REPORT_WAIT = timedelta(minutes=10)  # the job `report` ends within a few minutes of the review's verdict
RETRY_WAIT = timedelta(minutes=5)  # gates-retry starts within a minute of the failure
FAILED = {"failure", "startup_failure"}
PASSED = {"success", "skipped"}  # the other jobs of a review run whose failed `report` alone may run again
OWNER_LABEL = "owner-approved"
ESCALATED = "escalated"
VERDICTS = {"APPROVED", "CHANGES_REQUESTED"}  # the reviewer app's reviews that carry a verdict (it also comments)
DONE = "completed"
# a run by hand names its pull request in its title (the review workflow's run-name)
TITLE_PR = re.compile(r"\bPR #(\d+),")
MARKER = "<!-- review-watchdog:{action}:{head} -->"


def when(text: object) -> datetime | None:
    """An ISO 8601 time of the API, or None."""
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def nodes(value: object) -> list[dict[str, Any]]:
    """The `nodes` of a GraphQL connection, or []."""
    found = value.get("nodes") if isinstance(value, Mapping) else None
    return [n for n in found if isinstance(n, dict)] if isinstance(found, list) else []


def head_commit(pull: Mapping[str, Any]) -> dict[str, Any]:
    """The head branch's commit, read from the branch itself (a review finding: the last of a pull request's commits by
    date need not be its head). {} when the branch is gone or has moved past `headRefOid` since: the next run reads it."""
    ref = pull.get("headRef")
    commit = ref.get("target") if isinstance(ref, Mapping) else None
    if not isinstance(commit, dict) or commit.get("oid") != pull.get("headRefOid"):
        return {}
    return commit


def has_verdict(pull: Mapping[str, Any]) -> bool:
    """Whether the head has a check `verdict`, whatever its state: a review ran, or runs."""
    return any(nodes(suite.get("checkRuns")) for suite in nodes(head_commit(pull).get("checkSuites")))


def verdict_done(pull: Mapping[str, Any]) -> bool:
    """Whether the head has a completed check `verdict`: the job `report` wrote it (an "Under review" stays in progress)."""
    return any(str(run.get("status")).upper() == "COMPLETED"
               for suite in nodes(head_commit(pull).get("checkSuites")) for run in nodes(suite.get("checkRuns")))


def started(pull: Mapping[str, Any]) -> datetime | None:
    """The latest of the pull request's last ready mark (its opening when it opened ready) and the push of its head (the
    first check suite of the head, else the commit's own date)."""
    commit = head_commit(pull)
    pushed = [t for suite in nodes(commit.get("checkSuites")) if (t := when(suite.get("createdAt")))]
    marks = [t for item in nodes(pull.get("timelineItems")) if (t := when(item.get("createdAt")))]
    times = [min(pushed) if pushed else when(commit.get("committedDate")), max(marks) if marks else when(pull.get("createdAt"))]
    known = [t for t in times if t is not None]
    return max(known) if known else None


def run_numbers(run: Mapping[str, Any]) -> set[int]:
    """The pull requests that a review run is for: GitHub's list, or the number in the run's title (a run by hand)."""
    found = {int(p["number"]) for p in run.get("pull_requests") or [] if isinstance(p, Mapping) and "number" in p}
    if (title := TITLE_PR.search(str(run.get("display_title") or ""))) is not None:
        found.add(int(title.group(1)))
    return found


def latest_run(number: int, runs: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The newest review run for the pull request, or None."""
    mine = [run for run in runs if number in run_numbers(run)]
    return max(mine, key=lambda run: int(run.get("id") or 0)) if mine else None


def failed_first(run: Mapping[str, Any] | None) -> bool:
    """Whether a review run completed as a failure in its first attempt: its failed jobs may run again once."""
    return (run is not None and run.get("status") == DONE and run.get("conclusion") in FAILED
            and int(run.get("run_attempt") or 0) == 1)


def reviewing(runs: Iterable[Mapping[str, Any]]) -> tuple[set[int], bool]:
    """The pull requests that a queued or running review is for, and whether a running one names none."""
    numbers: set[int] = set()
    unknown = False
    for run in runs:
        if run.get("status") == DONE:
            continue
        found = run_numbers(run)
        numbers |= found
        unknown = unknown or not found
    return numbers, unknown


def bare(login: object) -> str:
    """A login without the `[bot]` that REST adds to an app's login and GraphQL leaves out."""
    return str(login or "").removesuffix("[bot]")


def marked(pull: Mapping[str, Any], bot: str, action: str, head: str) -> datetime | None:
    """The time of the bot's comment with the marker of `action` for `head`, or None."""
    marker = MARKER.format(action=action, head=head)
    for comment in nodes(pull.get("comments")):
        if bare((comment.get("author") or {}).get("login")) == bare(bot) and marker in str(comment.get("body") or ""):
            return when(comment.get("createdAt")) or datetime.min.replace(tzinfo=UTC)
    return None


def report_action(pull: Mapping[str, Any], run: Mapping[str, Any] | None, jobs: Mapping[str, Any], now: datetime,
                  bot: str) -> str | None:
    """`rerun-report`, `report-miss` or None for a head whose review gave its verdict but whose job `report` did not write
    the check ("A report that failed" in the module's docstring). The caller checked that no review run is busy."""
    head = str(pull.get("headRefOid") or "")
    reviewed = [t for r in nodes(pull.get("reviews"))
                if bare((r.get("author") or {}).get("login")) == bare(bot) and r.get("state") in VERDICTS
                and (r.get("commit") or {}).get("oid") == head and (t := when(r.get("submittedAt")))]
    if not reviewed or verdict_done(pull) or now - max(reviewed) < REPORT_WAIT:
        return None
    if run is not None and failed_first(run):
        found = jobs.get(str(run.get("id")))
        if not isinstance(found, Mapping):
            return None  # its jobs were not read: the next pass decides
        ended = {str(job.get("name")): job.get("conclusion") for job in found.get("jobs") or [] if isinstance(job, Mapping)}
        # only `report` failed: `gh run rerun --failed` reruns every failed job, and a second `review` is a second review
        if ended.get("report") in FAILED and all(c in PASSED for name, c in ended.items() if name != "report"):
            rerun = marked(pull, bot, "rerun-report", head)
            if rerun is None:
                return "rerun-report"
            if now - rerun < REPORT_WAIT:
                return None  # the rerun may still start
            # the rerun never started (its call failed): the run is still a failed attempt 1, so the owner is told
    return "report-miss" if marked(pull, bot, "report-miss", head) is None else None


def action_for(pull: Mapping[str, Any], busy: set[int], unknown: bool, now: datetime, owner: str, bot: str,
               runs: Iterable[Mapping[str, Any]] = (), jobs: Mapping[str, Any] | None = None) -> str | None:
    """What the watchdog does for one pull request, or None (the module's docstring)."""
    number, head = int(pull.get("number") or 0), str(pull.get("headRefOid") or "")
    if not pull.get("isDraft") and head and head_commit(pull) and number not in busy:
        report = report_action(pull, latest_run(number, runs), jobs or {}, now, bot)
        if report is not None:
            return report
    begun = started(pull)
    if (pull.get("isDraft") or not head or not head_commit(pull) or has_verdict(pull) or number in busy or begun is None
            or now - begun < WAIT):
        return None
    labels = {str(n.get("name")) for n in nodes(pull.get("labels"))}
    approved = any(bare((r.get("author") or {}).get("login")) == bare(owner) and r.get("state") == "APPROVED"
                   and (r.get("commit") or {}).get("oid") == head for r in nodes(pull.get("reviews")))
    if approved and OWNER_LABEL not in labels:
        return "approve-note" if marked(pull, bot, "approve-note", head) is None else None
    if ESCALATED in labels:
        return None
    dispatched = marked(pull, bot, "dispatch", head)
    if dispatched is None:
        return None if unknown else "dispatch"  # a review that names no pull request may be this one's
    if now - dispatched >= WAIT and marked(pull, bot, "second-miss", head) is None:
        return "second-miss"
    return None


def choose(pulls: Iterable[Mapping[str, Any]], runs: Iterable[Mapping[str, Any]], now: datetime, owner: str,
           bot: str, jobs: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """What the watchdog does, for each pull request that needs it."""
    runs = list(runs)
    busy, unknown = reviewing(runs)
    actions: list[dict[str, Any]] = []
    for pull in pulls:
        action = action_for(pull, busy, unknown, now, owner, bot, runs, jobs)
        if action is not None:
            number, head = int(pull.get("number") or 0), str(pull.get("headRefOid"))
            found = latest_run(number, runs) if action == "rerun-report" else None
            actions.append({"pr": number, "head": head, "action": action,
                            "marker": MARKER.format(action=action, head=head),
                            "run": int(found.get("id") or 0) if found else 0})
    return actions


def failed_reviews(pulls: Iterable[Mapping[str, Any]], runs: Iterable[Mapping[str, Any]]) -> list[int]:
    """The ids of the review runs whose jobs `choose` reads: each open, ready pull request's latest run, when it failed in
    attempt 1."""
    runs = list(runs)
    found: set[int] = set()
    for pull in pulls:
        run = None if pull.get("isDraft") else latest_run(int(pull.get("number") or 0), runs)
        if run is not None and failed_first(run):
            found.add(int(run.get("id") or 0))
    return sorted(found)


def stalled_gates(pulls: Iterable[Mapping[str, Any]], runs: Iterable[Mapping[str, Any]], now: datetime) -> list[int]:
    """The ids of the `gates` runs to check for a failure at startup ("Gates that never started" in the docstring)."""
    heads = {str(p.get("headRefOid")) for p in pulls if p.get("headRefOid")}
    latest: dict[str, Mapping[str, Any]] = {}
    for run in runs:
        head = str(run.get("head_sha") or "")
        if head in heads and run.get("event") == "pull_request" and int(run.get("id") or 0) > int(latest.get(head, {}).get("id") or 0):
            latest[head] = run
    found = []
    for run in latest.values():
        ended = when(run.get("updated_at"))
        if (run.get("status") == DONE and run.get("conclusion") in FAILED and run.get("run_attempt") == 1
                and ended is not None and now - ended >= RETRY_WAIT):
            found.append(int(run["id"]))
    return sorted(found)


def pull_requests(reply: object) -> list[dict[str, Any]]:
    """The open pull requests of this repository. A pull request whose head is in a fork is left out: a review that the
    watchdog starts again would run a fork's code beside the secrets and on the paid runner (agents/README.md "Fork pull
    requests"). The owner reads and merges such a pull request by hand."""
    data = reply.get("data") if isinstance(reply, Mapping) else None
    repo = data.get("repository") if isinstance(data, Mapping) else None
    found = nodes(repo.get("pullRequests")) if isinstance(repo, Mapping) else []
    return [pull for pull in found if not pull.get("isCrossRepository")]


def workflow_runs(reply: object) -> list[dict[str, Any]]:
    found = reply.get("workflow_runs") if isinstance(reply, Mapping) else None
    return [r for r in found if isinstance(r, dict)] if isinstance(found, list) else []


def gh(*args: str, token: str | None = None) -> subprocess.CompletedProcess[str]:
    """One call of the GitHub CLI with the environment's token, or `token`. It reads no stdin: a loop that reads a list must
    not lose it to gh."""
    env = {**os.environ, "GH_TOKEN": token} if token is not None else None
    done = subprocess.run(["gh", *args], capture_output=True, text=True, check=False, env=env, stdin=subprocess.DEVNULL)
    if done.returncode and done.stderr:  # the step's log keeps gh's error text, as it did when the shell ran gh
        print(done.stderr, file=sys.stderr, end="", flush=True)
    return done


def say(text: str) -> None:
    print(text, flush=True)


# the comment of each action, with the head's short sha (and the review run, for `rerun-report`); the marker follows it
TEXTS = {
    "approve-note": "GitHub's Approve does not count here: add the label owner-approved to merge (the owner's Approved, "
                    'agents/README.md "The guards"). This head ({short}) has no check verdict.',
    "dispatch": "No review ran for this head ({short}) within 15 minutes of its ready mark or push, so the review watchdog "
                "started one.",
    "second-miss": "The review that the watchdog started for this head ({short}) left no check verdict either, and the "
                   "watchdog starts no more. Mark the pull request ready again, or run the workflow 'Claude Code Review' by hand.",
    "rerun-report": "The review of this head ({short}) gave its verdict, but its job report failed and wrote no check verdict, "
                    "so the review watchdog runs the failed jobs of run {run} again. The review itself does not run again.",
    "report-miss": "The review of this head ({short}) gave its verdict, but no check verdict was written, and the watchdog "
                   "reruns nothing more. Rerun the failed jobs of the latest 'Claude Code Review' run of this pull request by hand.",
}


def read_jobs(repo: str, ids: Iterable[int], dispatch: str) -> dict[str, Any]:
    """The jobs of each review run that `failed_reviews` named, by run id. The runs need Actions read, which the app's token
    has not: GitHub Actions' token reads them. A read that fails leaves the run out: the next pass decides."""
    jobs: dict[str, Any] = {}
    for run_id in ids:
        done = gh("api", f"repos/{repo}/actions/runs/{run_id}/jobs", token=dispatch)
        try:
            if done.returncode:
                raise ValueError(done.stderr)
            jobs[str(run_id)] = json.loads(done.stdout)
        except ValueError:
            say(f"::warning::review run {run_id}: its jobs were not read; the next pass decides")
    return jobs


def act(env: Mapping[str, str], action: Mapping[str, Any]) -> None:
    """One action of `choose`: the comment first (it is the head's state, so a failed dispatch is never repeated every 15
    minutes), then the review or the rerun that goes with it."""
    repo, pr, head, name = env["GITHUB_REPOSITORY"], str(action["pr"]), str(action["head"]), str(action["action"])
    if name not in TEXTS:
        say(f"::warning::unknown action {name}")
        return
    short = head[:7]
    text = TEXTS[name].format(short=short, run=action.get("run", 0))
    if gh("pr", "comment", pr, "-R", repo, "--body", f"{text} {action['marker']}").returncode:
        say(f"::warning::PR #{pr}: the watchdog's comment failed, so it does nothing more this run")
        return
    dispatch = env.get("DISPATCH", "")
    if name == "dispatch" and gh("workflow", "run", "claude-code-review.yml", "-R", repo, "--ref", env["MAIN"], "-f", f"pr={pr}",
                                 token=dispatch).returncode:
        say(f"::warning::PR #{pr}: the review did not start: start it by hand")
    if name == "rerun-report" and gh("run", "rerun", str(action["run"]), "--failed", "-R", repo, token=dispatch).returncode:
        say(f"::warning::PR #{pr}: the rerun of review run {action['run']} failed: rerun its failed jobs by hand")
    say(f"::notice::PR #{pr} ({short}): {name}")


def watch(env: Mapping[str, str], now: datetime, folder: Path = Path()) -> int:
    """The step "Start a lost review again, or say what the pull request waits for"."""
    repo, dispatch = env["GITHUB_REPOSITORY"], env.get("DISPATCH", "")
    owner, name = repo.split("/", 1)
    read = gh("api", "graphql", "-f", f"owner={owner}", "-f", f"name={name}", "-f", f"query={QUERY}")
    if read.returncode:
        return read.returncode
    (folder / "pulls.json").write_text(read.stdout)
    listed = gh("api", f"repos/{repo}/actions/workflows/claude-code-review.yml/runs?per_page=50", token=dispatch)
    if listed.returncode:
        return listed.returncode
    pulls, runs = pull_requests(json.loads(read.stdout)), workflow_runs(json.loads(listed.stdout))
    jobs = read_jobs(repo, failed_reviews(pulls, runs), dispatch)
    actions = choose(pulls, runs, now, env["OWNER"], env["REVIEWER"], jobs)
    say(f"{len(actions)} actions")
    for action in actions:
        act(env, action)
    return 0


def rerun_gates(env: Mapping[str, str], folder: Path = Path()) -> int:
    """The step "Rerun the gates that GitHub failed at startup": a run with a failed gate keeps its jobs and stays red."""
    repo = env["GITHUB_REPOSITORY"]
    pulls_file = folder / "pulls.json"
    if not pulls_file.is_file() or not pulls_file.stat().st_size:
        say("no pull requests read")
        return 0
    listed = gh("api", f"repos/{repo}/actions/workflows/{env.get('GATES_WORKFLOW') or 'gates.yml'}/runs?event=pull_request&per_page=100")
    if listed.returncode:
        return listed.returncode
    now = datetime.now(UTC)
    for run_id in stalled_gates(pull_requests(json.loads(pulls_file.read_text())), workflow_runs(json.loads(listed.stdout)), now):
        count = gh("api", f"repos/{repo}/actions/runs/{run_id}/jobs", "--jq", ".total_count")
        if count.returncode:
            return count.returncode
        if count.stdout.strip() != "0":
            continue  # it ran its jobs: a red gate stays red
        if gh("run", "rerun", str(run_id), "-R", repo).returncode:
            say(f"::warning::gates run {run_id} had no job, and its rerun failed: rerun it by hand")
        else:
            say(f"::notice::gates run {run_id} had no job (a failure at startup): rerun once")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("query")
    sub.add_parser("watch")
    sub.add_parser("rerun-gates")
    one = sub.add_parser("choose")
    one.add_argument("pulls", type=Path)
    one.add_argument("runs", type=Path)
    for flag in ("--now", "--owner", "--bot"):
        one.add_argument(flag, required=True)
    one.add_argument("--jobs", type=Path)
    failed = sub.add_parser("failed-reviews")
    failed.add_argument("pulls", type=Path)
    failed.add_argument("runs", type=Path)
    gates = sub.add_parser("stalled-gates")
    gates.add_argument("pulls", type=Path)
    gates.add_argument("gates", type=Path)
    gates.add_argument("--now", required=True)
    args = parser.parse_args()
    if args.command == "query":
        print(QUERY)
        return 0
    if args.command == "watch":
        return watch(os.environ, datetime.now(UTC))
    if args.command == "rerun-gates":
        return rerun_gates(os.environ)
    if args.command == "failed-reviews":
        pulls = pull_requests(json.loads(args.pulls.read_text()))
        for run_id in failed_reviews(pulls, workflow_runs(json.loads(args.runs.read_text()))):
            print(run_id)
        return 0
    now = when(args.now)
    if now is None:
        print(f"--now {args.now!r}: not an ISO 8601 time", file=sys.stderr)
        return 2
    pulls = pull_requests(json.loads(args.pulls.read_text()))
    if args.command == "stalled-gates":
        for run_id in stalled_gates(pulls, workflow_runs(json.loads(args.gates.read_text())), now):
            print(run_id)
        return 0
    runs = workflow_runs(json.loads(args.runs.read_text()))
    jobs = json.loads(args.jobs.read_text()) if args.jobs else {}
    for action in choose(pulls, runs, now, args.owner, args.bot, jobs if isinstance(jobs, dict) else {}):
        print(json.dumps(action))
    return 0


if __name__ == "__main__":
    sys.exit(main())
