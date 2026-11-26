#!/usr/bin/env python3
"""Gates that did not pass on an approved head, for the workflow gates-failed.yml (agents/README.md "The guards").

    python3 .github/gates_failed.py check
        the job `failed`: each open pull request that is ready for review, whose head has the check `verdict` passed (the
        review approved it) and a required gate that ended with no pass (failed, cancelled, timed out, or skipped), gets
        one comment and its issue goes to In Progress, since nobody watches a pull request after its approve. It reads
        GITHUB_REPOSITORY, GH_TOKEN (the implementer app's: it comments on pull requests and reads their checks),
        LINEAR_API_KEY and GATES_CHECKS (the gates' checks, separated by spaces, default `python web`), and calls `gh` and
        linear.py. Exit 0; gh's code when the read of the pull requests fails

The check `verdict` holds only the review (the owner's call, 2026-11-25: OWL-30). Each gate is a required check of the
ruleset `main` on its own, so GitHub holds a red gate. GitHub counts a skipped required check as passed, so a product's gates
workflow never skips a gate job as a whole (agents/README.md "Setting up a product"): a gate that is skipped here is a
gate that did not run, and counts as a failure. The job reads every open pull request of this repository (a pull request whose
head is in a fork is left out: agents/README.md "Fork pull requests") and not only the one of the event
that started it: the approve and the failed gate come in any order, and the workflow starts on the end of the gates and on
the end of a review. A gate that still runs decides nothing: its end starts the workflow again. The workflow does not start on a cancelled
run of the gates: a reopen or a new push cancels the run of the same head's group, and its newer run is not on the head yet.

One comment for each head: its first words are verdict.py's FAILED_GATES and then the head's full hash, and a head with
that comment is left as it is (a rerun of the gates that fails again on the same head says nothing new). The review counts
its rounds again from the comment. The workflow has one concurrency group, so its runs follow each other and a
later run finds the comment of an earlier one. A comment that fails changes nothing else: the next run writes it. A pull request that merged or
closed since the read keeps its issue (once, a review that ended after the merge moved a Done issue back). A pull request
with the label `escalated` (it changes a code owner's file and waited for the owner's merge) waits on its session now, so
the label goes off, as for a conflict (.github/branch_update.py). The job runs main's code only: it holds the implementer
app's token. Standard library only (DECISIONS.md "Tooling"); the runner's python3 is 3.12.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple

HERE = Path(__file__).resolve().parent
FAILED_GATES = "The gates failed on this approved head"  # verdict.py's FAILED_GATES: the review counts its rounds from it
VERDICT = "verdict"
# the conclusions of a completed check run that count as a pass (GitHub's required checks pass on these)
PASSED = {"SUCCESS", "NEUTRAL"}
QUERY = """query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, baseRefName: "main", first: 100) {
      nodes {
        number
        title
        isDraft
        isCrossRepository
        headRefOid
        headRef { name }
        labels(first: 20) { nodes { name } }
        commits(last: 1) {
          nodes {
            commit {
              statusCheckRollup {
                contexts(first: 100) {
                  nodes {
                    ... on CheckRun { databaseId name status conclusion detailsUrl }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}"""


class Result(NamedTuple):
    code: int
    out: str


def say(line: str) -> None:
    print(line, flush=True)


def run(cmd: list[str]) -> Result:
    done = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=None, text=True, check=False)
    return Result(done.returncode, done.stdout or "")


def gh(*args: str) -> Result:
    return run(["gh", *args])


def linear(*args: str) -> Result:
    return run([sys.executable, "-I", str(HERE / "linear.py"), *args])


def newest(pull: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """The newest run of each check of the head, by its name: GitHub gives each new run (a rerun too) a higher id, and a
    run that a newer push cancelled stays on the head beside the newer one."""
    nodes = (pull.get("commits") or {}).get("nodes") or []
    rollup = ((nodes[-1].get("commit") or {}).get("statusCheckRollup") or {}) if nodes else {}
    runs = [c for c in (rollup.get("contexts") or {}).get("nodes") or [] if c and "name" in c]
    return {run_["name"]: run_ for run_ in sorted(runs, key=lambda c: int(c.get("databaseId") or 0))}


def approved(runs: Mapping[str, Mapping[str, Any]]) -> bool:
    """Whether the review approved the head: the newest check `verdict` ended with success."""
    verdict = runs.get(VERDICT) or {}
    return verdict.get("status") == "COMPLETED" and verdict.get("conclusion") == "SUCCESS"


def not_passed(runs: Mapping[str, Mapping[str, Any]], gates: list[str]) -> list[Mapping[str, Any]]:
    """The required gates whose newest run ended with no pass. A gate with no run yet (a head that conflicts with main gets
    no gates run: the branch update reports it) or one that still runs decides nothing."""
    return [runs[name] for name in gates if name in runs and runs[name].get("status") == "COMPLETED"
            and str(runs[name].get("conclusion") or "").upper() not in PASSED]


def to_send(reply: Mapping[str, Any], gates: list[str]) -> list[tuple[Mapping[str, Any], list[Mapping[str, Any]]]]:
    """The pull requests that need the comment, each with its gates that did not pass: ready for review, approved, and a
    required gate that ended with no pass."""
    sent = []
    for pull in (((reply.get("data") or {}).get("repository") or {}).get("pullRequests") or {}).get("nodes") or []:
        runs = newest(pull)
        bad = not_passed(runs, gates)
        if not pull.get("isDraft") and not pull.get("isCrossRepository") and approved(runs) and bad:
            sent.append((pull, bad))
    return sent


def words(head: str) -> str:
    """The first words of the comment for a head: verdict.py's FAILED_GATES, then the head's full hash."""
    return f"{FAILED_GATES} {head}:"


def comment(pull: Mapping[str, Any], bad: list[Mapping[str, Any]]) -> str:
    what = ", ".join(f"the check {run_['name']} ended with {str(run_.get('conclusion') or 'no result').lower()}" for run_ in bad)
    link = next((str(run_["detailsUrl"]) for run_ in bad if run_.get("detailsUrl")), "")
    return (f"{words(pull['headRefOid'])} {what}{f' ({link})' if link else ''}. A gate that is skipped did not run. An implementer "
            "fixes what made it fail and marks the pull request ready again: the review runs again as round 1 "
            '(agents/implementer.md "When a review asks for changes").')


def move_to_in_progress(pull: Mapping[str, Any], env: Mapping[str, str]) -> None:
    """The pull request's issue goes to In Progress, so an implementer takes it. A failed move only warns."""
    key = linear("key-from", str((pull.get("headRef") or {}).get("name") or ""), str(pull.get("title") or "")).out.strip()
    if key and env.get("LINEAR_API_KEY"):
        if linear("state", key, "In Progress").code:
            say(f"::warning::the move of {key} to In Progress failed: move it by hand")


def send_back(repo: str, pull: Mapping[str, Any], bad: list[Mapping[str, Any]], env: Mapping[str, str]) -> None:
    """The comment, once for the head, and then the move. Without the comment nothing else changes, so the next run writes it."""
    number, head = str(pull["number"]), str(pull["headRefOid"])
    state = gh("pr", "view", number, "-R", repo, "--json", "state", "--jq", ".state")
    if not state.code and state.out.strip() in ("MERGED", "CLOSED"):  # a state that gh could not read counts as open
        say(f"::notice::PR #{number} is {state.out.strip().lower()}: its issue stays as it is")
        return
    said = gh("api", f"repos/{repo}/issues/{number}/comments?per_page=100", "--paginate", "--jq", ".[].body")
    if said.code:
        say(f"::warning::PR #{number}: the comments could not be read, so the head {head} waits for the next run")
        return
    if words(head) in said.out:
        say(f"::notice::PR #{number}: the head {head} has its comment already")
        return
    if gh("pr", "comment", number, "-R", repo, "--body", comment(pull, bad)).code:
        say(f"::warning::PR #{number}: the comment on the gates failed")
        return
    say(f"::notice::PR #{number}: gates did not pass on the approved head {head}: {', '.join(str(r['name']) for r in bad)}")
    if "escalated" in [str(n.get("name")) for n in (pull.get("labels") or {}).get("nodes") or []]:
        # the pull request waited for the owner's merge, and now it waits on its session
        if gh("api", "-X", "DELETE", f"repos/{repo}/issues/{number}/labels/escalated").code:
            say(f"::warning::PR #{number}: the label escalated could not be removed: remove it by hand")
    move_to_in_progress(pull, env)


def check(env: Mapping[str, str]) -> int:
    repo = env["GITHUB_REPOSITORY"]
    owner, name = repo.split("/", 1)
    read = gh("api", "graphql", "-F", f"owner={owner}", "-F", f"name={name}", "-f", f"query={QUERY}")
    if read.code:
        return read.code
    gates = (env.get("GATES_CHECKS") or "python web").split()
    pulls = to_send(json.loads(read.out), gates)
    if not pulls:
        say(f"no approved pull request has a gate of {' '.join(gates)} that did not pass")
    for pull, bad in pulls:
        send_back(repo, pull, bad, env)
    return 0


def main(argv: list[str]) -> int:
    if argv != ["check"]:
        print("usage: gates_failed.py check", file=sys.stderr)
        return 2
    return check(os.environ)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
