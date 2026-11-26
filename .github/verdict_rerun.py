#!/usr/bin/env python3
"""The rerun of the gates, for the workflow verdict-rerun.yml (agents/README.md "The guards": its logic
moved here from the workflow, so a change of the rules needs no workflow patch). A rerun of the gates that passes on the head
the reviewer approved gives the check `verdict` back, with no new review and no new round.

    python3 .github/verdict_rerun.py check
        the step "The check verdict from the approve, after a rerun that passed". It finds the open pull request of this repository
        (never a fork's: agents/README.md "Fork pull requests") whose head is HEAD, asks main's verdict.py `rerun` whether the approve covers the head, and writes the check it prints. It reads
        GITHUB_REPOSITORY, GH_TOKEN (the reviewer app's: it writes the check), HEAD, BRANCH, REVIEWER, UPDATER and
        GITHUB_OUTPUT, and appends `pr=N` there when it wrote the check (`pr=` when it did not). Exit 0; gh's code when a
        read or the write of the check fails
    python3 .github/verdict_rerun.py merge
        the step "Auto-merge, or Escalated for a code owner's file; the issue back to Approved": auto-merge on (or the owner's
        files wait in Escalated), and the issue to Approved. It reads GITHUB_REPOSITORY, GH_TOKEN (the implementer app's),
        PR, HEAD, BRANCH, LINEAR_API_KEY, OWNER, RUN_CREATED (the creation of the gates run), TRIES (5) and WAIT (seconds
        between two tries, 10), and calls `gh`, verdict.py and linear.py. Exit 0; 1 when GitHub refused auto-merge on every
        try or for a reason that this job does not know

The decision is main's verdict.py `rerun`: same head, the reviewer's last verdict an approve, the last comment that starts
the rounds again the implementer app's FAILED_GATES, the gates' conclusion success. The issue waits in Escalated with the
label `escalated` (implementer.md): the rerun is the owner's answer, so `merge` takes the label off and moves the issue to
Approved. Main's code only: the workflow runs this script from main's checkout, since its steps hold the tokens that write a
check and turn on auto-merge. A code owner's file gets no auto-merge here, unless the owner's label `owner-approved` stands
(as in `report`): GitHub's issue events show that the label's last event is `labeled` by the repository
owner, and newer than the first gates run of this head (a label older than that was put on an older head). A review's or a
comment's text proves nothing: the reviewer app posts the model's words. Else the issue goes to Escalated
and the owner merges. Only a rerun (attempt 2 or later) can pass on a head whose gates failed, so a first run starts no job
(the workflow's `if`).

A write that the issue's record needs (a comment, a move, the label) warns (`::warning::`) and goes on, as the shell did.
Standard library only (DECISIONS.md "Tooling").
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path

HERE = Path(__file__).resolve().parent
# gh answers these with a failure that a new try can fix: GitHub's "Base branch was modified" and a 5xx
TRANSIENT = ("Base branch was modified", "status code: 502", "status code: 503", "status code: 504", "HTTP 502", "HTTP 503",
             "HTTP 504")
# the states in which gh merges at once (a workflow file then fails for the app's missing permission): CLEAN,
# UNSTABLE (a check that is not required fails or waits; measured) and HAS_HOOKS. UNKNOWN follows a change and does not
IMMEDIATE = {"CLEAN", "UNSTABLE", "HAS_HOOKS"}
WORKFLOWS = ".github/workflows/"


def run(cmd: list[str], stdin: str | None = None, merge_err: bool = False) -> subprocess.CompletedProcess[str]:
    """One call, with no stdin unless given. `merge_err` puts stderr into stdout, as the shell's `2>&1` does."""
    return subprocess.run(cmd, input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT if merge_err else subprocess.PIPE, text=True, check=False)


def gh(*args: str, merge_err: bool = False) -> subprocess.CompletedProcess[str]:
    return run(["gh", *args], merge_err=merge_err)


def script(name: str, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """One call of main's verdict.py or linear.py, which sit beside this script, from the working folder (verdict.py `owned`
    reads AGENTS.md there)."""
    return run([sys.executable, "-I", str(HERE / name), *args], stdin=stdin)


def say(text: str) -> None:
    print(text, flush=True)


def fail(done: subprocess.CompletedProcess[str]) -> int:
    """The code of a failed read, with gh's message on stderr."""
    print(done.stderr or "", file=sys.stderr, end="")
    return done.returncode


def files_of(text: str) -> list[dict[str, object]]:
    """The files API's objects, one a line (`--jq '.[]'`)."""
    return [item for item in (json.loads(line) for line in text.splitlines() if line.strip()) if isinstance(item, dict)]


def changes_a_workflow(files: list[dict[str, object]]) -> bool:
    """Whether a file of the pull request, or the name that it had before, is under .github/workflows/."""
    return any(str(file.get(key) or "").startswith(WORKFLOWS) for file in files for key in ("filename", "previous_filename"))


def check(env: Mapping[str, str]) -> int:
    """The check `verdict` from the approve, when the rerun passed on the head that the approve covers."""
    repo, head, branch = env["GITHUB_REPOSITORY"], env["HEAD"], env["BRANCH"]
    output = Path(env["GITHUB_OUTPUT"])
    with output.open("a") as out:
        out.write("pr=\n")
    found = gh("pr", "list", "-R", repo, "--head", branch, "--state", "open", "--json", "number,headRefOid,isCrossRepository",
               "--jq", f'.[] | select(.headRefOid == "{head}" and (.isCrossRepository | not)) | .number')  # never a fork's
    if found.returncode:
        return fail(found)
    pr = found.stdout.strip()
    if not pr:
        say(f"no open pull request has the head {head}: nothing to do")
        return 0
    reviews = gh("api", f"repos/{repo}/pulls/{pr}/reviews?per_page=100", "--paginate", "--jq", ".[]")
    if reviews.returncode:
        return fail(reviews)
    comments = gh("api", f"repos/{repo}/issues/{pr}/comments?per_page=100", "--paginate", "--jq", ".[]")
    if comments.returncode:
        return fail(comments)
    with tempfile.TemporaryDirectory() as folder:
        reviews_file, comments_file = Path(folder, "reviews.jsonl"), Path(folder, "comments.jsonl")
        reviews_file.write_text(reviews.stdout)
        comments_file.write_text(comments.stdout)
        rerun = script("verdict.py", "rerun", str(reviews_file), str(comments_file), "--reviewer", env["REVIEWER"],
                       "--updater", env["UPDATER"], "--head", head, "--gates", "success")
    print(rerun.stderr, file=sys.stderr, end="")
    if rerun.returncode:
        return rerun.returncode
    if not rerun.stdout.strip():
        say("no passing check from this rerun")
        return 0
    print(rerun.stdout, end="")
    written = run(["gh", "api", f"repos/{repo}/check-runs", "--input", "-"], stdin=rerun.stdout)
    if written.returncode:
        return fail(written)
    with output.open("a") as out:
        out.write(f"pr={pr}\n")
    return 0


class Issue:
    """The issue of the pull request, which `say` comments on and moves. A merged or closed pull request moves no issue: its
    Linear state is Done, or the owner's close."""

    def __init__(self, env: Mapping[str, str], title: str) -> None:
        self.env, self.pr = env, env["PR"]
        found = script("linear.py", "key-from", env["BRANCH"], title)
        self.key = found.stdout.strip() if not found.returncode else ""

    def state(self) -> str:
        done = gh("pr", "view", self.pr, "-R", self.env["GITHUB_REPOSITORY"], "--json", "state", "--jq", ".state")
        return done.stdout.strip() if not done.returncode else ""

    def say(self, text: str, state: str) -> None:
        if not self.key or not self.env.get("LINEAR_API_KEY"):
            return
        ended = script("verdict.py", "ended", self.state(), "--pr", self.pr, "--key", self.key)
        if not ended.returncode:
            print(ended.stdout, end="")
            return
        with tempfile.TemporaryDirectory() as folder:
            body = Path(folder, "say.md")
            body.write_text(text + "\n")
            moved = script("linear.py", "comment", self.key, "--body-file", str(body)).returncode or \
                script("linear.py", "state", self.key, state).returncode
        if moved:
            say(f"::warning::the Linear comment or the move of {self.key} to {state} failed: move it by hand")


def label_escalated(repo: str, pr: str) -> None:
    if gh("api", "-X", "POST", f"repos/{repo}/issues/{pr}/labels", "-f", "labels[]=escalated").returncode:
        say("::warning::the label escalated could not be added: add it by hand")


def owners_wait(env: Mapping[str, str], issue: Issue, owned: list[str]) -> int:
    """A pull request that changes a code owner's file gets no auto-merge: the label escalated, one comment, and the issue
    in Escalated. Auto-merge goes off first, when it is on."""
    repo, pr, head = env["GITHUB_REPOSITORY"], env["PR"], env["HEAD"]
    if issue.state() == "MERGED":
        say("::notice::the pull request changes a code owner's file and merged already: the issue keeps Linear's Done")
        return 0
    auto = gh("pr", "view", pr, "-R", repo, "--json", "autoMergeRequest", "--jq", ".autoMergeRequest != null")
    if auto.stdout.strip() == "true":
        off = gh("pr", "merge", pr, "-R", repo, "--disable-auto")
        if off.returncode:
            return fail(off)
    label_escalated(repo, pr)
    files = "\n".join(f"- {name}" for name in owned)
    issue.say(f"The gates passed on a rerun at {head[:7]}, so the check verdict passes again with the same approve. PR #{pr} "
              f"changes a file of CODEOWNERS, so it gets no auto-merge: the owner merges it on GitHub. Files:\n{files}", "Escalated")
    return 0


def turn_on_auto_merge(env: Mapping[str, str], issue: Issue, tries: int, wait: float) -> int:
    """Up to `tries` tries: "Base branch was modified" and a 5xx answer try again, after a read of the
    pull request: auto-merge on, or a merge, ends the job with success (a 5xx answer can come after GitHub did the work)."""
    repo, pr, head = env["GITHUB_REPOSITORY"], env["PR"], env["HEAD"]
    attempt = 1
    while True:
        answer = gh("pr", "merge", pr, "-R", repo, "--auto", "--merge", merge_err=True)
        if not answer.returncode:
            return 0
        text = answer.stdout.rstrip("\n")
        print(text)
        if not any(part in text for part in TRANSIENT):
            return 1
        read = gh("pr", "view", pr, "-R", repo, "--json", "state,autoMergeRequest", "--jq",
                  'if .state == "MERGED" or .autoMergeRequest != null then "yes" else "no" end')
        if read.stdout.strip() == "yes" and not read.returncode:
            say("::notice::GitHub answered with an error, but the pull request has auto-merge on or merged already")
            return 0
        if attempt >= tries:
            reason = (text.splitlines() or [""])[-1][:200]
            say(f"::error::GitHub refused auto-merge {tries} times ({reason}): the pull request waits on the owner")
            label_escalated(repo, pr)
            issue.say(f"The gates passed on a rerun at {head[:7]} and the check verdict passes again, but GitHub refused to turn "
                      f'on auto-merge {tries} times. Its last answer: "{reason}". Without auto-merge, nothing updates or merges '
                      f"the pull request. Run the failed job of verdict-rerun again (run {env.get('GITHUB_RUN_ID') or '?'}), "
                      "turn on auto-merge on GitHub, or merge the pull request on GitHub: the issue then goes to Done.", "Escalated")
            return 1
        attempt += 1
        time.sleep(wait)


def merge(env: Mapping[str, str]) -> int:
    """Auto-merge, or Escalated for a code owner's file; the issue back to Approved."""
    repo, pr, head = env["GITHUB_REPOSITORY"], env["PR"], env["HEAD"]
    if not env.get("GH_TOKEN"):
        say("::warning::no implementer app token: turn on auto-merge by hand")
        return 0
    tries, wait = int(env.get("TRIES") or 5), float(env.get("WAIT") or 10)
    title = gh("pr", "view", pr, "-R", repo, "--json", "title", "--jq", ".title")
    issue = Issue(env, title.stdout.strip() if not title.returncode else "")
    listed = gh("api", f"repos/{repo}/pulls/{pr}/files?per_page=100", "--paginate", "--jq", ".[]")
    if listed.returncode:
        return fail(listed)
    files = files_of(listed.stdout)
    owners = script("verdict.py", "owned", str(HERE / "CODEOWNERS"), stdin=listed.stdout)
    if owners.returncode:
        return fail(owners)
    owned = owners.stdout.split()
    # The owner's label lifts the guard on GitHub's facts only: see the docstring
    lift = False
    if owned:
        events = gh("api", f"repos/{repo}/issues/{pr}/events?per_page=100", "--paginate", "--jq", ".[]")
        if events.returncode:
            return fail(events)
        with tempfile.TemporaryDirectory() as folder:
            events_file = Path(folder, "events.jsonl")
            events_file.write_text(events.stdout)
            labeled = script("verdict.py", "labeled", str(events_file), "--owner", env["OWNER"], "--since", env["RUN_CREATED"])
        print(labeled.stderr, file=sys.stderr, end="")
        lift = not labeled.returncode and labeled.stdout.strip() == "true"
    if owned and lift:
        say(f"::notice::the owner's label owner-approved: auto-merge also for the owner's files ({','.join(owned)})")
    if owned and not lift:
        return owners_wait(env, issue, owned)
    # The issue goes to Approved before auto-merge, as `report` does: a pull request that can merge at once merges
    # within seconds, and Linear's Done must come after the move, never before it. A merged pull request keeps Done.
    if issue.state() != "MERGED":
        issue.say(f"The gates passed on a rerun at {head[:7]}, so the check verdict passes again with the same approve (no new "
                  "review). Auto-merge goes on next.", "Approved")
    # A pull request that changes a workflow file and is clean already: gh would merge it at once as the app, and GitHub refuses
    # that without the permission `workflows`. One comment says the owner merges it. With a check still pending,
    # auto-merge goes on as always: GitHub's own later merge passes.
    if changes_a_workflow(files):
        status = gh("pr", "view", pr, "-R", repo, "--json", "mergeStateStatus", "--jq", ".mergeStateStatus")
        if not status.returncode and status.stdout.strip() in IMMEDIATE:
            note = gh("pr", "comment", pr, "-R", repo, "--body",
                      "Ready to merge. It changes a workflow file, which the app cannot merge: the owner merges it on GitHub.")
            if note.returncode:
                return fail(note)
            gh("api", "-X", "DELETE", f"repos/{repo}/issues/{pr}/labels/escalated")
            return 0
    code = turn_on_auto_merge(env, issue, tries, wait)
    if code:
        return code
    # auto-merge is on (or the pull request merged): the owner's rerun answered the wait, so the label escalated goes off
    gh("api", "-X", "DELETE", f"repos/{repo}/issues/{pr}/labels/escalated")
    return 0


def main(argv: list[str]) -> int:
    if argv == ["check"]:
        return check(os.environ)
    if argv == ["merge"]:
        return merge(os.environ)
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
