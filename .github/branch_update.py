#!/usr/bin/env python3
"""The branch update, for the workflow branch-update.yml (agents/README.md "The loop" step 5): after each merge,
the next approved pull request that conflicts with main is found, and goes back to a person.

    python3 .github/branch_update.py query
        the GraphQL query that reads the open pull requests into main (for `gh api graphql -f query=...`)
    python3 .github/branch_update.py choose PULLS [--skip N ...]
        what to do, from the query's reply in the file PULLS: `update N SHA` (ask GitHub to merge main into pull request N,
        whose head is SHA, which GitHub refuses with a conflict: the workflow then reports it), `update N SHA escalated`
        when it waits for the owner's merge (the same request, and the word says that there is no auto-merge to turn off;
        the refusal takes the same path as any other), `pending N ...` (no pull request to update, but GitHub has
        not computed `mergeable` yet for the pull requests N of the queue: the workflow reads them again after a wait), or
        nothing
    python3 .github/branch_update.py refusal < REPLY
        what GitHub's refusal of an update says: `conflict` (main does not merge cleanly), `moved` (the head is no longer
        SHA: a push came first, and its own checks decide), or `other`
    python3 .github/branch_update.py update
        the job `update` (its logic moved here from the workflow, so a change of the rules needs no workflow
        patch): read the pull requests, pick one, ask GitHub to merge main into it and report a conflict. It reads
        GITHUB_REPOSITORY, GH_TOKEN, LINEAR_API_KEY, READS (reads of the pull requests at most, 6) and WAIT (seconds
        between two reads, 10), and calls `gh` and linear.py. Exit 0; gh's code when the read of the pull requests
        fails; 1 when GitHub refuses the update for a reason that this job does not know

Since the owner's call of 2026-11-02 the ruleset `main` does not need a branch that is up to date: pull requests
merge in parallel, and a pull request that is only behind main is left alone (an update would run the gates again and make
the merges serial). What still needs a person is a merge conflict: GitHub's auto-merge waits for ever on a conflicting
branch and nobody sees it. A pull request in the queue has its head in this repository (a fork's is left out: agents/README.md "Fork pull requests"),
is ready for review (not a draft), has auto-merge on (an approve that
is not a code owner's file: the review workflow's `report`), has no failed check (a failed check is the implementer's fix
round first), and its head has a successful check `verdict`. It is picked only when GitHub says its `mergeable`
is CONFLICTING. GitHub computes `mergeable` lazily after main moves, so a read a few seconds after a merge gives UNKNOWN:
with nothing to update, a queue pull request that reads UNKNOWN makes the choice `pending`, and the workflow reads the pull
requests again after a wait, up to a fixed number of reads (a run 4 s after a merge once read UNKNOWN as no conflict,
and the next run came only at the next merge, so a conflict waited with no sign). The oldest auto-merge goes first. After it, an approved pull request
that waits for the owner's merge (the label `escalated`, no auto-merge, a successful `verdict`, since it changes a
code owner's file) is checked the same way, the oldest first. The pick is a request for GitHub's merge of main, which GitHub
refuses with a conflict: the workflow reports that refusal. A conflict takes the same path on either kind of pull request
(the owner's call, 2026-11-23): back to its session. The pick's word `escalated` is what tells the workflow that
the pull request has no auto-merge, so it removes the label instead. Standard
library only (DECISIONS.md "Tooling").

The workflow runs `update` from main's checkout, since the step holds the implementer app's token. The job warns
(`::warning::`) and goes on when a write fails, as it did as shell: a failed comment changes nothing else, so the next run
meets the conflict again and writes it. Failed gates on an approved head are no part of this workflow: gates_failed.py
reports them (OWL-30).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any, NamedTuple

QUERY = """query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, baseRefName: "main", first: 100) {
      nodes {
        number
        isDraft
        isCrossRepository
        headRefOid
        mergeable
        labels(first: 20) { nodes { name } }
        autoMergeRequest { enabledAt }
        headRef { name }
        commits(last: 1) {
          nodes {
            commit {
              statusCheckRollup {
                contexts(first: 100) {
                  nodes {
                    ... on CheckRun { databaseId name status conclusion checkSuite { app { slug } } }
                    ... on StatusContext { context state }
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

# what the head's checks say (checks): one still runs, or one failed
RUNNING = {"PENDING", "EXPECTED"}
FAILED = {"FAILURE", "ERROR"}
# the conclusions of a completed check run (GraphQL CheckConclusionState) that GitHub's rollup counts as a failure
FAILED_RUN = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
# the conclusions of an older run of a check that a newer skipped run replaces (newest): they gave no result
REPLACEABLE = {"SKIPPED", "CANCELLED"}


def conflicting(pull: dict[str, Any]) -> bool:
    """Whether GitHub says that main does not merge cleanly into the pull request's branch (UNKNOWN: not computed yet)."""
    return pull.get("mergeable") == "CONFLICTING"


def contexts(pull: dict[str, Any]) -> list[dict[str, Any]]:
    """The head's check runs and commit statuses, as the query reads them."""
    nodes = (pull.get("commits") or {}).get("nodes") or []
    rollup = (nodes[-1].get("commit") or {}).get("statusCheckRollup") if nodes else None
    return [c for c in ((rollup or {}).get("contexts") or {}).get("nodes") or [] if c]


def app(run: dict[str, Any]) -> str | None:
    return ((run.get("checkSuite") or {}).get("app") or {}).get("slug")


def newest(pull: dict[str, Any]) -> dict[tuple[str | None, str], dict[str, Any]]:
    """The newest run of each check of the head, by its app and its name. A workflow whose concurrency group
    cancels an older run leaves that run on the head beside the newer one: only the newer one counts. The newest run is the
    one with the highest id, since GitHub gives each new run (a rerun too) a higher id; a queued run has no start time. A
    skipped run never replaces a run that ran: a workflow that runs on several events skips a job on the events it does not
    act on (the review workflow's `review` on a review event, measured), and that must not hide a failure. It
    replaces a cancelled run only: a cancelled run gave no result, and the gates' `python` and `web` on a docs-only
    pull request are a cancelled run, then a skipped one, when a reopen cancels the first run in their concurrency group."""
    runs: dict[tuple[str | None, str], dict[str, Any]] = {}
    for run in sorted((c for c in contexts(pull) if "name" in c), key=lambda c: int(c.get("databaseId") or 0)):
        key = (app(run), run["name"])
        if key not in runs or run.get("conclusion") != "SKIPPED" or runs[key].get("conclusion") in REPLACEABLE:
            runs[key] = run
    return runs


def checks(pull: dict[str, Any]) -> str | None:
    """What the head's checks say: "FAILURE" when the newest run of a check failed or a commit status failed, "PENDING"
    when none failed and one still runs, "SUCCESS", or None when no check reported yet. Not the rollup's `state`: it counts
    every run, so a cancelled run that a newer one replaced made it FAILURE and held an approved pull request."""
    states = [s["state"] for s in contexts(pull) if "context" in s and s.get("state")]
    for run in newest(pull).values():
        if run.get("status") != "COMPLETED":
            states.append("PENDING")
        elif run.get("conclusion") in FAILED_RUN:
            states.append("FAILURE")
        else:
            states.append("SUCCESS")
    if not states:
        return None
    if any(s in FAILED for s in states):
        return "FAILURE"
    if any(s in RUNNING for s in states):
        return "PENDING"
    return "SUCCESS"


def approved(pull: dict[str, Any]) -> bool:
    """Whether the newest check `verdict` of the head passed. GraphQL hides a private app's name (the reviewer app's check
    reads `app: null`, measured 2026-10-28), so only GitHub Actions' checks are left out: a workflow job named `verdict`
    would write one, and the ruleset would not count it."""
    return any(name == "verdict" and slug != "github-actions" and run.get("conclusion") == "SUCCESS"
               for (slug, name), run in newest(pull).items())


def escalated(pull: dict[str, Any]) -> bool:
    """Whether the pull request carries the label `escalated`: it waits for the owner's merge, so it has no
    auto-merge. A conflict on it goes back to its session as on any other pull request."""
    return any(label.get("name") == "escalated" for label in ((pull.get("labels") or {}).get("nodes") or []))


def unknown(pull: dict[str, Any]) -> bool:
    """Whether GitHub has not computed `mergeable` yet (it does so lazily after the base moves)."""
    return pull.get("mergeable") == "UNKNOWN"


def choose(reply: dict[str, Any], skip: Collection[int] = ()) -> str:
    """`update N SHA`, `update N SHA escalated`, `pending N ...` or "" for the query's reply; `skip` holds the pull
    requests this run tried already. The word `escalated` says that there is no auto-merge to turn off."""
    pulls = [p for p in reply["data"]["repository"]["pullRequests"]["nodes"] if not p.get("isCrossRepository")]  # never a fork's

    def ready(p: dict[str, Any]) -> bool:
        return not p["isDraft"] and bool(p.get("headRef")) and checks(p) not in FAILED and approved(p) and p["number"] not in skip

    queue = [p for p in pulls if ready(p) and p.get("autoMergeRequest")]
    # the owner's merges: a needs-decision or a third round carries `escalated` too, but no successful `verdict`
    owners = [p for p in pulls if ready(p) and not p.get("autoMergeRequest") and escalated(p)]
    stale = [p for p in queue if conflicting(p)]
    if stale:
        first = min(stale, key=lambda p: (p["autoMergeRequest"]["enabledAt"], p["number"]))
        return f"update {first['number']} {first['headRefOid']}"
    if mine := [p for p in owners if conflicting(p)]:
        first = min(mine, key=lambda p: p["number"])
        return f"update {first['number']} {first['headRefOid']} escalated"
    if waiting := sorted(p["number"] for p in queue + owners if unknown(p)):
        return "pending " + " ".join(map(str, waiting))
    return ""


def refusal(reply: str) -> str:
    """The kind of a refused update, from gh's output (the error body and gh's own line). The texts measured 2026-10-28:
    "merge conflict between base and head" and "expected head sha didn’t match current head ref." (both HTTP 422)."""
    text = reply.lower()
    if "merge conflict" in text:
        return "conflict"
    if "expected head sha" in text:
        return "moved"
    return "other"


class Result(NamedTuple):
    code: int
    out: str
    err: str


def run(command: list[str]) -> Result:
    """Run a command and keep its stdout. Its stderr goes to the job's log at once, as it did in the shell (a failed write
    shows gh's or linear.py's own reason there); `Result.err` keeps a copy for the one reader that parses it (`refusal`)."""
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    if done.stderr:
        print(done.stderr, file=sys.stderr, end="", flush=True)
    return Result(done.returncode, done.stdout, done.stderr)


def gh(*args: str) -> Result:
    """One call of the GitHub CLI, with the token of the environment."""
    return run(["gh", *args])


def linear(*args: str) -> Result:
    """One call of linear.py, which sits beside this script."""
    return run([sys.executable, "-I", str(Path(__file__).with_name("linear.py")), *args])


def say(text: str) -> None:
    print(text, flush=True)


def move_to_in_progress(branch: str, title: str, env: Mapping[str, str]) -> None:
    """The pull request's issue goes to In Progress, so an implementer takes it. A failed move only warns."""
    key = linear("key-from", branch, title).out.strip()
    if key and env.get("LINEAR_API_KEY"):
        if linear("state", key, "In Progress").code:
            say(f"::warning::the move of {key} to In Progress failed: move it by hand")


def view(repo: str, pr: str, field: str) -> str:
    """A field of a pull request, or "" when gh fails."""
    done = gh("pr", "view", pr, "-R", repo, "--json", field, "--jq", f".{field}")
    return done.out.strip() if not done.code else ""


def conflict(repo: str, pr: str, head: str, owners: bool, env: Mapping[str, str]) -> bool:
    """A conflict goes back to the pull request's session, also when it waited for the owner's merge. The first
    words are verdict.py's CONFLICT, which the orchestrator reads as the conflict that sends the issue back to its session:
    the review counts its rounds again from them. One comment per head, matched on these words: the earlier
    comment of a pull request that waited for the owner's merge names the head too, and it must not hold the words that the
    orchestrator reads. A failed comment changes nothing else: the pull request keeps its label,
    or its auto-merge, and the issue its state, so the next run meets the conflict again and writes the comment. Returns
    whether the pull request has the comment: when it has not, the caller leaves it for the next run."""
    words = f"The branch update found a merge conflict with main. Its head is {head}."
    # such a pull request has no auto-merge to turn off
    note = "" if owners else " Auto-merge is off until the next approve."
    comments = gh("api", f"repos/{repo}/issues/{pr}/comments?per_page=100", "--paginate", "--jq", ".[].body")
    if not comments.code and words in comments.out:
        say(f"::notice::PR #{pr}: the conflict on {head} has its comment already")
    else:
        body = (f"{words}{note} An implementer merges main into the branch, resolves the conflict, and marks the pull request "
                'ready again: the review runs again as round 1 (agents/implementer.md "When a review asks for changes").')
        if gh("pr", "comment", pr, "-R", repo, "--body", body).code:
            say(f"::warning::PR #{pr}: the comment on the conflict failed")
            say(f"::notice::PR #{pr}: left as it is, for the next run to write the comment")
            return False
    # a failure from here on only warns: the next pull request still gets its update
    if owners:
        # the orchestrator reads the label as a wait on the owner, and the pull request waits on its session now
        if gh("api", "-X", "DELETE", f"repos/{repo}/issues/{pr}/labels/escalated").code:
            say(f"::warning::PR #{pr}: the label escalated could not be removed: remove it by hand")
    elif gh("pr", "merge", pr, "-R", repo, "--disable-auto").code:
        say(f"::warning::PR #{pr}: auto-merge could not be turned off")
    move_to_in_progress(view(repo, pr, "headRefName"), view(repo, pr, "title"), env)
    return True


def update(env: Mapping[str, str]) -> int:
    """Pick the next pull request that conflicts with main, one per run, and report the conflict. GitHub computes
    `mergeable` lazily after main moves (UNKNOWN until then): a pick `pending` reads the pull requests again, WAIT seconds
    apart, READS times at most."""
    repo = env["GITHUB_REPOSITORY"]
    owner, name = repo.split("/", 1)
    limit, wait = int(env.get("READS") or 6), float(env.get("WAIT") or 10)
    skip: list[int] = []
    reads = 1
    while True:
        read = gh("api", "graphql", "-F", f"owner={owner}", "-F", f"name={name}", "-f", f"query={QUERY}")
        if read.code:
            return read.code
        pick = choose(json.loads(read.out), skip)
        if pick.startswith("pending"):
            waiting = pick.removeprefix("pending ")
            if reads >= limit:
                say(f"::warning::GitHub has not computed mergeable after {reads} reads (pull requests {waiting}): the next run reads it again")
                return 0
            say(f"GitHub has not computed mergeable yet (pull requests {waiting}): read again in {env.get('WAIT') or 10} s")
            reads += 1
            sleep(wait)
            continue
        say(pick or "no pull request conflicts with main")
        if not pick:
            return 0
        _, pr, head, *owners = pick.split(" ")  # `escalated`: it waits for the owner's merge, so it has no auto-merge
        done = gh("api", "-X", "PUT", f"repos/{repo}/pulls/{pr}/update-branch", "-f", f"expected_head_sha={head}")
        if not done.code:
            say(f"::notice::PR #{pr}: GitHub merges main into the branch")
            return 0
        reply = done.out + done.err
        print(done.out, end="")
        kind = refusal(reply)
        if kind == "conflict":
            if not conflict(repo, pr, head, bool(owners), env):
                skip.append(int(pr))  # nothing else changed, so the pull request stays in the queue for the next run
                continue
        elif kind == "moved":
            say(f"::notice::PR #{pr}: a push came first; its own checks decide")
        else:
            say(f"::error::PR #{pr}: GitHub refused the branch update for a reason this job does not know (above)")
            return 1
        skip.append(int(pr))


def sleep(seconds: float) -> None:
    time.sleep(seconds)


def main(argv: list[str]) -> int:
    if argv[:1] == ["query"]:
        print(QUERY)
        return 0
    if argv == ["update"]:
        return update(os.environ)
    if argv[:1] == ["refusal"]:
        print(refusal(sys.stdin.read()))
        return 0
    if len(argv) >= 2 and argv[0] == "choose":
        rest = argv[2:]
        if len(rest) % 2 or any(flag != "--skip" for flag in rest[::2]):
            print("usage: branch_update.py choose PULLS [--skip N ...]", file=sys.stderr)
            return 2
        print(choose(json.loads(Path(argv[1]).read_text()), {int(n) for n in rest[1::2]}))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
