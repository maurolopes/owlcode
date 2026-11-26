#!/usr/bin/env python3
"""The rerun of failed gates, for the workflow gates-retry.yml (agents/README.md "The guards").

    python3 .github/gates_retry.py JOBS [--pulls PULLS --head SHA] [--suite RUN]
        `rerun` when the run's failed jobs failed only in a setup step, or the suite's job in the browser suite with no test
        failed after its retry (RUN is the suite's counts, the file run.json of its reporter), `rerun all` when the run has
        no job at all (GitHub failed it at startup), else `keep: REASON` (each on one line, exit 0).
        JOBS is the file that holds the reply of `gh api repos/OWNER/REPO/actions/runs/ID/jobs`. For a `pull_request` run,
        PULLS holds `gh pr list --head BRANCH --state open --json headRefOid,isCrossRepository` and SHA is the run's head: `keep`
        unless an open pull request of this repository (never a fork's) still has that head.

A setup step fetches something from outside (an action, the mise binary, a package from apt, a cache): when GitHub or a
mirror fails there, the branch is not at fault, and a second try mostly passes (three runs failed on 2026-11-08 at the step
of `jdx/mise-action`). A failed gate is never rerun, but for one case: a red test stays red until a person looks, a failed
step that this list does not name keeps its job red, and so does a failed job with no failed step (a cancelled or lost
runner). `uv sync` and `npm ci` are not setup steps here: a lock that does not match fails them for good. The workflow
reruns once (attempt 1 only), so a second failure is the owner's, as before. A run with no job is GitHub's own failure
at startup ("This run likely failed because of a workflow file issue", conclusion `failure`, zero jobs: four runs at
one second on 2026-11-09, and a rerun started normally): it has no failed job for `--failed` to rerun, so the whole run runs
again.

The one case (the owner's call, 2026-11-16, which reverses "a failed gate is never rerun" of 2026-11-08 for it):
the suite's job (SUITE_JOB) failed in the step of the browser suite (SUITE_STEP), and the suite's counts (RUN, which
the suite's reporter writes) say that no test failed after its retry. That is: the suite ran tests, none
failed twice (`unexpected`) and none was interrupted, the run did not time out, the checks reporter named no NEW check and
each MISSING check is in the source of a test that did not run (`inTestsNotRun`: a test that did not run takes
all its checks with it, so a count of tests against a count of names kept such a job red), and the run failed for a
flaky test, a test that did not run or an error outside any test. On 2026-11-16 three pull requests waited for the owner only for that click (one for 6.5
hours). The flake still gets its issue: the workflow `flakes` reads attempt 1's file. With no RUN file (the suite did not
end, or the download failed) the job stays red.

The product's gates come from two optional values of the environment, with this product's defaults: GATES_SUITE_JOB,
the job that runs the browser suite (default `web`), and GATES_SUITE_STEP, the start of its step's name in the gates
workflow (default `Unit tests and the browser suite`). Standard library only (DECISIONS.md "Tooling").
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# The names of steps in the job's API reply: a step with `uses` and no `name` is "Run OWNER/ACTION@SHA".
SETUP_STEPS = re.compile(
    r"^(Set up job|Set up runner|Run actions/checkout@|Run jdx/mise-action@|Run actions/cache@|ghostscript and pdftotext)"
)


SUITE_JOB = os.environ.get("GATES_SUITE_JOB") or "web"
SUITE_STEP = os.environ.get("GATES_SUITE_STEP") or "Unit tests and the browser suite"


def flaky_only(run: object) -> str | None:
    """None when the suite's counts say that no test failed after its retry (the module's docstring), else why not."""
    if not isinstance(run, dict):
        return "no counts of the browser suite"
    unexpected, not_run = run.get("unexpected"), run.get("didNotRun")
    checks = run.get("checks")
    if run.get("status") not in ("passed", "failed"):
        return f"the browser suite ended {run.get('status')!r}"
    if not isinstance(unexpected, list) or not isinstance(not_run, list) or not isinstance(checks, dict):
        return "the counts of the browser suite are incomplete"
    if not int(run.get("total") or 0) or len(not_run) >= int(run.get("total") or 0):
        return "the browser suite ran no test"
    if unexpected:
        return f"{len(unexpected)} test(s) failed after their retry: {unexpected[0].get('location')}"
    if int(run.get("interrupted") or 0):
        return "a test was interrupted"
    if checks.get("new"):
        return f"a check is not in the list of checks: {checks['new'][0]}"
    held = set(checks.get("inTestsNotRun") or [])
    if stopped := [name for name in checks.get("missing") or [] if name not in held]:
        return f"a check stopped running that no test that did not run holds: {stopped[0]}"
    if not (int(run.get("flaky") or 0) or not_run or run.get("errors")):
        return "the browser suite failed with no flaky test, no test that did not run and no error"
    return None


def decide(reply: dict[str, Any], run: object = None) -> str:
    """`rerun`, `rerun all`, or `keep: REASON`, for one run's jobs and, for the browser suite, its counts."""
    if "jobs" in reply and not reply["jobs"]:
        return "rerun all"
    failed = [job for job in reply.get("jobs", []) if job.get("conclusion") == "failure"]
    if not failed:
        return "keep: no failed job"
    for job in failed:
        steps = [str(s.get("name", "")) for s in job.get("steps", []) if s.get("conclusion") == "failure"]
        if not steps:
            return f"keep: job {job.get('name')} failed with no failed step"
        for name in steps:
            if job.get("name") == SUITE_JOB and name.startswith(SUITE_STEP):
                if (why := flaky_only(run)) is not None:
                    return f"keep: job {SUITE_JOB} failed in the browser suite: {why}"
            elif not SETUP_STEPS.match(name):
                return f"keep: job {job.get('name')} failed in the step {name!r}, which is not a setup step"
    return "rerun"


def current(pulls: list[dict[str, Any]], head: str) -> bool:
    """True when an open pull request of this repository still has the run's head. A pull request whose head is in a fork
    does not count: `gh pr list --head BRANCH` lists it by its branch's name, and the run is the fork's (agents/README.md
    "Fork pull requests").

    A rerun keeps the run's concurrency group (`gates-refs/pull/N/merge`, cancel-in-progress), so a rerun of an outdated
    head would cancel the gates of the pull request's new head."""
    return any(pull.get("headRefOid") == head and not pull.get("isCrossRepository") for pull in pulls)


def read_run(path: Path | None) -> object:
    """The suite's counts, or None when the file is missing or not JSON."""
    if path is None or not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError:
        return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("jobs", type=Path)
    parser.add_argument("--pulls", type=Path)
    parser.add_argument("--head")
    parser.add_argument("--suite", type=Path)
    args = parser.parse_args(argv[1:])
    if (args.pulls is None) != (args.head is None):
        print("--pulls and --head go together", file=sys.stderr)
        return 2
    answer = decide(json.loads(args.jobs.read_text()), read_run(args.suite))
    if args.pulls is not None and answer.startswith("rerun") and not current(json.loads(args.pulls.read_text()), args.head):
        answer = f"keep: no open pull request has the head {args.head} any more"
    print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
