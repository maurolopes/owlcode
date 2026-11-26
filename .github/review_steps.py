#!/usr/bin/env python3
"""The steps of the review workflow's job `review` (claude-code-review.yml).

Each command does what one `run:` block of the job did (the blocks moved here). Since then the job's `run:`
blocks call main's copy of this file, never the branch's, and tests/test_review_steps.py checks only that wiring. The job's first step writes main's copy to
/tmp/trusted, beside main's copies of the scripts that it calls (linear.py, verdict.py, code-changed.sh),
and every later step runs `python3 -I /tmp/trusted/review_steps.py COMMAND`. The branch's files are text that the review reads, never code that runs
beside the reviewer app's token, the model's token or the Linear key. The folder of this file is the trusted
folder: the facts files (reviews.jsonl, comments.jsonl, runs.jsonl, escalated.json) go there too. The working folder is
the checkout of the head under review: review/ (pr.json, issue.json, rules/) goes there.

    trusted      main's copies of the scripts and of the rules (AGENTS.md, DECISIONS.md, agents/) that the review applies
    facts        the merge with main, code or docs, the issue and the round (outputs branch, merge, code, key,
                 round)
    label-off    a round removes the label `escalated` from the pull request
    manual       the owner's run by hand with round 1 posts the comment that starts the count again
    past-third   a round past the third gets the owner's four actions and no review
    gates        whether the gates workflow is on (output on)
    cache-guard  whether a cache of main may be restored: never when the pull request's own ref has the key KEY
                 (output restore)
    bun-check    Bun's install cache goes when the pull request's own ref has the key after the restore
    binaries     the review binaries go in the same case; else their paths, when the restore found both (outputs bun, claude)
    denied       the tool calls that the review action denied

Each command reads its values from the step's environment, as the shell block did: PR, HEAD, REVIEWER, UPDATER, OWNER,
GH_TOKEN, LINEAR_API_KEY, ROUND_INPUT, ROUND, KEY, RESTORE, EXECUTION_FILE, and GitHub's own
GITHUB_REPOSITORY, GITHUB_RUN_ID and GITHUB_OUTPUT. The product's gates workflow comes from one optional value, whose
default is this product's: GATES_WORKFLOW (the gates' workflow file, `gates.yml`). The review does not read how the gates
ended: the ruleset holds each gate as a check of its own, and gates_failed.py sends a failed head back (OWL-30). Once the
workflow calls it, a change of this logic is a pull request, never a workflow patch. The runner's `python3` is 3.12, so
the syntax stays 3.12's. Standard library only (DECISIONS.md "Tooling").
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent
OUT = subprocess.PIPE
QUIET = subprocess.DEVNULL
# the review workflow's file, as the runs API names it in `path`
WORKFLOW = ".github/workflows/claude-code-review.yml"


def env(name: str) -> str:
    """A value of the step's environment. A missing one is an error, as `set -u` made it in the shell."""
    return os.environ[name]


def gates_workflow() -> str:
    """The file name of the product's gates workflow (GATES_WORKFLOW)."""
    return os.environ.get("GATES_WORKFLOW") or "gates.yml"


def say(line: str) -> None:
    """One line of the step's log (the runner reads `::notice::`, `::warning::` and `::error::` at the line's start)."""
    print(line, flush=True)


def output(**values: str) -> None:
    """`name=value` lines for the step's outputs (the file of GITHUB_OUTPUT)."""
    with open(env("GITHUB_OUTPUT"), "a") as out:
        for name, value in values.items():
            out.write(f"{name}={value}\n")


def run(*args: str, stdout: Any = OUT, stderr: Any = None, stdin: str | None = None,
        check: bool = True) -> subprocess.CompletedProcess[str]:
    """A command. Its stdout is the result unless `stdout` says otherwise; `check` fails the step as `set -e` did."""
    return subprocess.run(list(args), stdout=stdout, stderr=stderr, input=stdin, text=True, check=check)


def rm_rf(path: Path) -> None:
    """As `rm -rf`: a symlink or a file goes itself, a folder goes with its content, a missing path is no error."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path, ignore_errors=True)


def text(done: subprocess.CompletedProcess[str]) -> str:
    """What `$(...)` gave in the shell: the command's stdout without its trailing newlines."""
    return (done.stdout or "").rstrip("\n")


def gh(*args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return run("gh", *args, **kwargs)


def main_script(name: str, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """One of main's Python scripts beside this file, in isolated mode."""
    return run(sys.executable, "-I", str(HERE / name), *args, **kwargs)


def main_verdict() -> ModuleType:
    """Main's verdict.py as a module, for its constants (MANUAL, MANUAL_RUN)."""
    sys.path.insert(0, str(HERE))
    try:
        import verdict
    finally:
        sys.path.remove(str(HERE))
    return verdict


def repo_api(path: str) -> str:
    return f"repos/{env('GITHUB_REPOSITORY')}/{path}"


def trusted() -> int:
    """Main's copies of the scripts that the review runs and of the rules that it applies. The rules merge on the
    verdict (agents/README.md, implementer.md, planner.md, DECISIONS.md), so a branch's copy must not set the rules of its
    own review. They go to review/rules/, beside the other facts that the model reads; the step empties it first, since a
    branch could hold files there. The workflow's own lines fetched main and wrote this file first."""
    def show(path: str) -> bytes:
        return subprocess.run(["git", "show", f"origin/main:{path}"], stdout=OUT, check=True).stdout

    for name in ("linear.py", "code-changed.sh", "verdict.py"):
        (HERE / name).write_bytes(show(f".github/{name}"))
    # as `rm -rf`: a branch's symlink or file there goes itself, so main's rules never go through it
    rm_rf(Path("review/rules"))
    # -r: a folder's name alone would make `git show` print its tree listing, so its rules would be missing
    listed = text(run("git", "ls-tree", "-r", "--name-only", "origin/main", "agents/")).splitlines()
    for name in ("AGENTS.md", "DECISIONS.md", *listed):
        target = Path("review/rules") / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(show(name))
    return 0


def diff_into(script: str) -> str:
    """The names that the diff against main changes, read by one of main's shell scripts (no pipefail: the script's own
    status counts, as in the shell's pipe)."""
    names = run("git", "diff", "--name-only", f"origin/main...{env('HEAD')}", check=False).stdout
    return text(run("sh", str(HERE / script), stdin=names))


def manual_runs(reviewer: str) -> None:
    """The owner's runs by hand with round 1 start the count again too: their comment counts only for a run of
    this workflow that the owner started. Each run that a MANUAL comment names is read by its id: a list page
    would lose a run once 100 newer ones exist, or when the run was deleted. A run that cannot be read is left out, and its
    error body never reaches the file: its comment then counts nothing. Only the reviewer app's comments that start with
    MANUAL's words name a run (verdict.MANUAL_RUN)."""
    runs = HERE / "runs.jsonl"
    runs.write_text("")
    try:
        pattern = main_verdict().MANUAL_RUN
        ids = set()
        for line in (HERE / "comments.jsonl").read_text().splitlines():
            comment = json.loads(line)
            found = pattern.match(str(comment.get("body") or ""))
            if found and (comment.get("user") or {}).get("login") == reviewer:
                ids.add(found.group(1))
    except (Exception, SystemExit):  # as the shell's own python3: a main's verdict.py that exits on import only warns
        say("::warning::the comments' MANUAL runs could not be read: no run by hand counts, the count is as it was")
        return
    keep = f'select(.event == "workflow_dispatch" and (.path | startswith("{WORKFLOW}")))'
    for number in sorted(ids):
        read = gh("api", repo_api(f"actions/runs/{number}"), "--jq", keep, check=False)
        if read.returncode == 0 and text(read):
            with runs.open("a") as out:
                out.write(text(read) + "\n")


def facts() -> int:
    """The facts of the run: the merge with main, code or docs, the issue and the round."""
    pr, head, reviewer = env("PR"), env("HEAD"), env("REVIEWER")
    linear_key = os.environ.get("LINEAR_API_KEY", "")
    Path("review").mkdir(exist_ok=True)
    with open("review/pr.json", "w") as out:
        gh("pr", "view", pr, "--json", "number,title,body,headRefName,headRefOid,baseRefName,isDraft,url,author", stdout=out)
    data = json.loads(Path("review/pr.json").read_text())
    branch, title = data["headRefName"], data["title"]
    # the merge with main: clean, or a conflict (a blocking finding the implementer fixes; the reviewer never resolves one)
    clean = run("git", "merge-tree", "--write-tree", "origin/main", head, stdout=QUIET, stderr=QUIET, check=False)
    merge = "clean" if clean.returncode == 0 else "conflict"
    code = diff_into("code-changed.sh")
    # the issue, from the key in the branch name or the title (Linear's own link)
    key = text(main_script("linear.py", "key-from", branch, title))
    # the issue's stays in Escalated: main's verdict.py reads the owner's answers from them and the reviews
    (HERE / "escalated.json").write_text("")
    if key and linear_key:
        with open("review/issue.json", "w") as out:
            issue = main_script("linear.py", "issue", key, stdout=out, check=False)
        if issue.returncode == 0:
            with open(HERE / "escalated.json", "w") as out:
                main_script("linear.py", "escalated", key, stdout=out, check=False)
        else:
            say(f"::warning::no readable Linear issue {key}: the review runs without one")
            Path("review/issue.json").unlink(missing_ok=True)
            key = ""
    else:
        key = ""
    # a conflict that the branch update found on an approved pull request starts the count again (the owner's
    # call). An older main's verdict.py has no command `since`: the count then starts where it did
    for name, path in (("reviews.jsonl", f"pulls/{pr}/reviews"), ("comments.jsonl", f"issues/{pr}/comments")):
        with open(HERE / name, "w") as out:
            gh("api", repo_api(f"{path}?per_page=100"), "--paginate", "--jq", ".[]", stdout=out)
    manual_runs(reviewer)
    # when the owner last answered the issue in Escalated: an exit to In Progress that a request-changes did not cause is
    # an answer too, which Linear merged with the claim after it
    answered = main_script("verdict.py", "answered", str(HERE / "escalated.json"), str(HERE / "reviews.jsonl"),
                           "--reviewer", reviewer, check=False)
    left = text(answered) if answered.returncode == 0 else ""
    counted = main_script("verdict.py", "since", str(HERE / "reviews.jsonl"), str(HERE / "comments.jsonl"),
                          "--reviewer", reviewer, "--updater", env("UPDATER"), "--since", left,
                          "--runs", str(HERE / "runs.jsonl"), "--owner", env("OWNER"), "--pr", pr, check=False)
    since = text(counted) if counted.returncode == 0 else left
    # the round: the reviewer app's earlier verdicts on this pull request since that time
    query = (f'[.[] | select(.user.login == "{reviewer}") | select(.state == "APPROVED" or .state == "CHANGES_REQUESTED")'
             f' | select(.submitted_at > "{since}")] | length')
    # `gh --paginate --jq` runs the query on each page (30 reviews) and prints one count per page: add them up
    pages = text(gh("api", repo_api(f"pulls/{pr}/reviews"), "--paginate", "--jq", query))
    earlier = str(sum(int(line) for line in pages.split() if line))
    round_ = os.environ.get("ROUND_INPUT", "") or str(int(earlier) + 1)
    say(f"head {head} on {branch}; merge with main: {merge}; code changed: {code}; "
        f"issue: {key or 'none'}; round {round_} (earlier verdicts: {earlier} since '{since or 'the start'}')")
    output(branch=branch, merge=merge, code=code, key=key, round=round_)
    return 0


def label_off() -> int:
    """A round starts after the owner's answer or after a fix round: the pull request no longer waits on the owner, so the
    label `escalated` goes first. A failure only warns, and the review goes on."""
    pr = env("PR")
    labels = gh("api", repo_api(f"issues/{pr}/labels?per_page=100"), "--jq", ".[].name", check=False)
    if labels.returncode != 0:
        say("::warning::the labels of the pull request could not be read: remove the label escalated by hand if it is there")
        return 0
    if "escalated" in text(labels).split("\n"):
        if gh("api", "-X", "DELETE", repo_api(f"issues/{pr}/labels/escalated"), stdout=QUIET, check=False).returncode:
            say("::warning::the label escalated could not be removed from the pull request: remove it by hand")
    return 0


def manual() -> int:
    """The owner's run by hand with round 1 (the owner's call, 2026-11-05) posts the comment that `verdict.py
    since` counts from, with main's words (an older main has none: no comment) and this run's id. A failure only
    warns: the owner runs round 2 by hand then."""
    try:
        words = str(main_verdict().MANUAL)
    except (Exception, SystemExit):  # as the shell's own python3: a main's verdict.py that exits on import only warns
        words = ""
    if not words:
        say("::warning::main's verdict.py has no MANUAL: no comment, so run round 2 by hand")
        return 0
    body = f"{words} (run {env('GITHUB_RUN_ID')}). The next ready mark is round 2."
    if gh("pr", "comment", env("PR"), "-R", env("GITHUB_REPOSITORY"), "--body", body, stdout=QUIET, check=False).returncode:
        say("::warning::the comment that starts the count again could not be posted: run round 2 by hand")
    return 0


def past_third() -> int:
    """A round past the third gets no review: the issue waits in Escalated, so the pull request gets the label (a
    failure only warns) and a comment with the owner's four actions, each with what to type. A move out of
    Escalated starts the count again (verdict.py answered)."""
    pr, round_, key = env("PR"), env("ROUND"), env("KEY")
    if gh("api", "-X", "POST", repo_api(f"issues/{pr}/labels"), "-f", "labels[]=escalated", stdout=QUIET,
          check=False).returncode:
        say("::warning::the label escalated could not be added to the pull request: add it by hand")
    implement = f"`/implement {key}`" if key else "`/implement` and the issue's key"
    actions = "\n".join([
        f"**Round {round_} is not reviewed: three rounds is the maximum.** The issue waits for you in Escalated. Do one of these:",
        "",
        "1. **Have an implementer fix it:** move the issue to **Todo** in Linear (that starts the count again). Until an "
        f"orchestrator runs, also start the run with {implement}: an orchestrator starts it by itself, and a second start "
        "would be a second run on one issue. Add a comment on the issue only if the implementer needs it (what to fix, or "
        "which finding is wrong).",
        "2. **You already pushed a fix:** start a new review as round 1; the ready mark after it is round 2.",
        "",
        "   ```",
        f"   gh workflow run claude-code-review.yml -f pr={pr} -f round=1",
        "   ```",
        "3. **Accept the pull request as it is:** add the label `owner-approved` on GitHub.",
        "4. **Drop it:** move the issue to **Canceled** and close the pull request.",
    ]) + "\n"
    Path("actions.md").write_text(actions)
    gh("pr", "comment", pr, "--body-file", "actions.md", stdout=None)
    # and the issue goes there, so the label and the state agree; a Linear failure only warns. A merged or closed
    # pull request moves no issue
    if not key or not os.environ.get("LINEAR_API_KEY", ""):
        return 0
    state = text(gh("pr", "view", pr, "-R", env("GITHUB_REPOSITORY"), "--json", "state", "--jq", ".state", check=False))
    if main_script("verdict.py", "ended", state, "--pr", pr, "--key", key, stdout=None, check=False).returncode == 0:
        return 0
    Path("third.md").write_text(f"PR #{pr} was marked ready for round {round_}.\n\n{actions}")
    moved = main_script("linear.py", "comment", key, "--body-file", "third.md", stdout=None, check=False).returncode == 0 \
        and main_script("linear.py", "state", key, "Escalated", stdout=None, check=False).returncode == 0
    if not moved:
        say(f"::warning::the Linear comment or the move of {key} to Escalated failed: move it by hand")
    return 0


def gates() -> int:
    """Whether the gates workflow is on, for the prompt. Only a state that says disabled counts as off: a failed request
    prints its error body on stdout, and anything else counts as on."""
    state = text(gh("api", repo_api(f"actions/workflows/{gates_workflow()}"), "--jq", ".state", stderr=QUIET, check=False))
    on = "false" if state.startswith("disabled") else "true"
    say(f"gates workflow: {state}; on: {on}")
    output(on=on)
    return 0


def pull_request_cache() -> tuple[bool, str]:
    """Whether the caches API answered, and how many caches with the key KEY it lists under the pull request's own ref
    (on a failed request: what gh printed on stdout)."""
    listed = gh("api", "-X", "GET", repo_api("actions/caches"), "-f", f"ref=refs/pull/{env('PR')}/merge",
                "-f", f"key={env('KEY')}", "--jq", ".total_count", check=False)
    return listed.returncode == 0, text(listed)


def cache_guard() -> int:
    """A cache of main may be restored only when the pull request's own ref has no entry with its key: the pull request's
    own runs can save one, and its code would run beside the app's token, the model's token and the Linear key.
    A failed request counts as an entry found: no restore is the safe answer."""
    answered, found = pull_request_cache()
    use = "true" if answered and found == "0" else "false"
    say(f"caches of the pull request with the key: {found or 'unknown'}; restore: {use}")
    output(restore=use)
    return 0


def bun_check() -> int:
    """The guard and the restore are two steps: a run of the pull request can save an entry with the key between
    them, and an entry that the restore took is still listed. The cache folder goes when the key is listed or the request
    fails."""
    answered, found = pull_request_cache()
    if answered and found == "0":
        say("no cache of the pull request with the key: the restored cache stays")
    else:
        say(f"caches of the pull request with the key: {found or 'unknown'}: the restored cache goes")
        rm_rf(Path.home() / ".bun" / "install" / "cache")
    return 0


def binaries() -> int:
    """As bun-check, for Bun and Claude Code. The paths are outputs only when the guard allowed the restore and
    both programs are there: else the action downloads them, and a cache problem never fails the review."""
    folder = Path.home() / ".cache" / "review-binaries"
    answered, found = pull_request_cache()
    if not (answered and found == "0"):
        say(f"caches of the pull request with the key: {found or 'unknown'}: the restored binaries go")
        rm_rf(folder)
    elif env("RESTORE") == "true" and all(os.access(folder / name, os.X_OK) for name in ("bun", "claude")):
        say("the restored binaries stay")
        output(bun=str(folder / "bun"), claude=str(folder / "claude"))
    else:
        say("no binaries restored: the action downloads them")
    return 0


# the last result entry of the action's execution file: one line per denied call, the tool's input cut to 300 characters
DENIALS = """[.[] | select(.type == "result")] | last | (.permission_denials // [])
  | if length == 0 then "no tool call was denied" else .[] | "denied: \\(.tool_name) \\(.tool_input | tostring | .[0:300])" end"""


def denied() -> int:
    """The tool calls that the review action denied: its log names only their count. It never fails the job:
    the file may be missing, and jq may not read it. The standalone jq comes with the runner image, but nothing here
    installs it: it is installed when it is missing."""
    if not shutil.which("jq"):
        try:
            run("sudo", "apt-get", "install", "-y", "-qq", "--no-install-recommends", "jq", stdout=QUIET, stderr=QUIET, check=False)
        except OSError:  # no sudo: the block's `|| true` goes on
            pass
    path = os.environ.get("EXECUTION_FILE", "")
    try:
        if not os.path.isfile(path):
            say(f"no execution file: {path or 'unset'}")
        elif run("jq", "-r", DENIALS, path, stdout=None, check=False).returncode:
            say("the execution file could not be read")
    except OSError:  # no jq: the block's `|| echo` goes on
        say("the execution file could not be read")
    return 0


COMMANDS = {"trusted": trusted, "facts": facts, "label-off": label_off, "manual": manual, "past-third": past_third,
            "gates": gates, "cache-guard": cache_guard, "bun-check": bun_check, "binaries": binaries, "denied": denied}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in COMMANDS:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        return COMMANDS[argv[0]]()
    except subprocess.CalledProcessError as error:  # as `set -e`: the step fails with the command's own status
        say(f"::error::{error.cmd[0] if error.cmd else 'command'} failed with status {error.returncode}")
        return error.returncode or 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
