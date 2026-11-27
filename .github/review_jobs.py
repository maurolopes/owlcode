#!/usr/bin/env python3
"""The steps of the review workflow's jobs `start`, `report` and `carry` (claude-code-review.yml).

    python3 -I .github/review_jobs.py access
        `start`: whether the reviewer app's Client ID and private key are set, and whether the key is a PEM file's contents
        (env APP_ID, APP_KEY, GITHUB_OUTPUT); it writes `ready=true` or `ready=false`, and exits 1 for a key of the wrong shape.
        A pull request whose head is in another repository (a fork: the event's payload, GITHUB_EVENT_PATH) gets `ready=false`
        and a notice first: no review of a fork's code (agents/README.md "Fork pull requests")
    python3 -I .github/review_jobs.py owner
        `start`: the owner's Approved. It runs main's `verdict.py owner` and writes `ok=true` only when the label was the
        repository owner's (env LABEL, SENDER, ACTOR, OWNER, DRAFT, PR, LABELED_HEAD, GITHUB_OUTPUT); the approve goes to the
        file owner-review.json, verdict.py's reason to owner-why.txt
    python3 -I .github/review_jobs.py open
        `start`: the head to review and the check `verdict` in progress on it (env PR, LABELED_HEAD, REVIEWER, GITHUB_REPOSITORY,
        GITHUB_OUTPUT, and GH_TOKEN for gh); it writes `head`, `started` and `check`. A pull request with no issue key
        gets a failed check and no `check` output, except a pure bump of the loop's files: a branch `loop-bump-SHORT` whose diff
        is only the pin and the files that the loop's list names gets its review with no issue (such a branch that changes another
        file gets the failed check "A bump that changes a product file needs its issue"). A pull request whose head is in another repository (a fork) gets no check, no `check`
        output and a notice, also on a run by hand (the owner's label never reaches a fork: `access` refuses it). A pull request that is in draft now, or a head whose review completed after the mark that started this run
        (env GITHUB_EVENT_NAME and GITHUB_EVENT_PATH: a mark's time is in its payload), or a head whose review the reviewer app posted after that mark, gets no check and no `check`
        output, and a notice in the log: one review for one head
    python3 -I .github/review_jobs.py posted
        `report`, before the step that writes the verdict: whether a second review of the head was posted since this
        run started (env PR, HEAD, REVIEWER, STARTED, CHECK, GITHUB_REPOSITORY, GH_TOKEN); it writes `second=true` or `second=false`, and
        on `true` it ends this run's check `verdict` as neutral
    python3 -I .github/review_jobs.py carry
        `carry`: the approve's check on a branch update, a failed one on a dismissal (env COMMAND, PR, HEAD, REVIEWER,
        GITHUB_REPOSITORY, GH_TOKEN); it writes reviews.jsonl and check.json
    python3 -I .github/review_jobs.py again
        `carry`: a real commit after the approve gets a new review as round 1 (env PR, HEAD, REVIEWER, MAIN,
        DISPATCH, GITHUB_REPOSITORY, GH_TOKEN; it reads reviews.jsonl, which `carry` wrote)

The logic moved here from the workflow's `run:` blocks with no change of behaviour, so that a change of it is a pull request
that the owner reviews and merges, never a patch of a workflow file that the owner applies by hand. The commands
that hold a secret or a write token run main's copy of this file: the job checks out the default branch first, and the
review reads main's copies of its rules, never the branch's. The helpers it calls, `verdict.py` and `linear.py`,
are main's copies beside it, and they run in isolated mode. The runner's `python3` is 3.12, so the syntax stays 3.12's
. Standard library only (DECISIONS.md "Tooling").
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
VERDICT = HERE / "verdict.py"
LINEAR = HERE / "linear.py"
PEM_BEGIN = "-----BEGIN "
PEM_END = "-----END "
BUMP_BRANCH = re.compile(r"^loop-bump-[0-9a-f]{12}$")
PUSHED_TEXT = "A commit was pushed after the approved one, so the approve does not cover the new head."


def env(name: str) -> str:
    """The value of an environment variable of the step. A missing one is an error, as `set -u` made it in the shell."""
    return os.environ[name]


def say(line: str) -> None:
    """One line of the step's log (the runner reads `::notice::`, `::warning::` and `::error::` at the line's start)."""
    print(line, flush=True)


def output(**values: str) -> None:
    """Append `name=value` lines to the file of GITHUB_OUTPUT: the step's outputs."""
    with open(env("GITHUB_OUTPUT"), "a") as out:
        for name, value in values.items():
            out.write(f"{name}={value}\n")


def python(script: Path, *args: str, stdout: Any = subprocess.PIPE, stderr: Any = None,
           check: bool = True) -> subprocess.CompletedProcess[str]:
    """One of main's scripts in isolated mode, with this interpreter."""
    return subprocess.run([sys.executable, "-I", str(script), *args], stdout=stdout, stderr=stderr, text=True, check=check)


def gh(*args: str, stdout: Any = subprocess.PIPE, token: str | None = None,
       check: bool = True) -> subprocess.CompletedProcess[str]:
    """`gh` with the environment's token, or with `token` instead. Its stdout is the result, unless `stdout` says another."""
    run_env = {**os.environ, "GH_TOKEN": token} if token is not None else None
    return subprocess.run(["gh", *args], stdout=stdout, text=True, check=check, env=run_env)


FORK_NOTICE = ("::notice::the head of this pull request is in another repository (a fork), so no review: a fork's code never "
               "runs beside the repository's secrets or on its runner, and the owner reads and merges it by hand "
               "(agents/README.md 'Fork pull requests')")


def event_from_fork() -> bool:
    """Whether the event that started this run is a pull request whose head is in another repository. The payload of
    `pull_request` events says so (`head.repo` is null for a fork that was deleted). An event with no payload, or with no pull
    request in it, is no fork's: the step `open` asks GitHub about the pull request of such a run."""
    path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not path or not Path(path).is_file():
        return False
    pull = json.loads(Path(path).read_text()).get("pull_request")
    if not isinstance(pull, dict):
        return False
    head = (pull.get("head") or {}).get("repo")
    return not isinstance(head, dict) or head.get("full_name") != os.environ.get("GITHUB_REPOSITORY")


def access() -> int:
    """The app's Client ID and key. The key's shape is checked, never its value: a PEM file's contents are many lines
    between a BEGIN line and an END line. A paste into the prompt of `gh secret set` keeps one line, which OpenSSL reports as
    "DECODER routines::unsupported" (2026-10-25): a wrong shape fails loud here, with the fix in the message. A fork's pull
    request is refused first, with its own notice: GitHub gives such an event no secret, so the key would look unset."""
    if event_from_fork():
        say(FORK_NOTICE)
        output(ready="false")
        return 0
    app_id, key = env("APP_ID"), env("APP_KEY")
    if not app_id or not key:
        say("::notice::the variable REVIEWER_APP_CLIENT_ID or the secret REVIEWER_APP_PRIVATE_KEY is not set, so no review "
            "(agents/README.md 'Setting up a product')")
        output(ready="false")
        return 0
    lines = len(f"{key}\n".splitlines())
    if not (key.startswith(PEM_BEGIN) and PEM_END in key[len(PEM_BEGIN):] and lines >= 3):
        say(f"::error::the secret REVIEWER_APP_PRIVATE_KEY is not a PEM file's contents ({lines} line(s)): set it from the "
            "file, gh secret set REVIEWER_APP_PRIVATE_KEY -R OWNER/REPO < FILE, never pasted into the prompt")
        return 1
    output(ready="true")
    return 0


def owner() -> int:
    """The owner's Approved: only the repository owner's own label counts: the account that added it and
    the account that started the run (a re-run by another account replays the owner's event). Anyone else's label, an app's
    included, gets a notice in the log and nothing more. An older main's verdict.py has no command `owner`: nothing
    then."""
    label, who = env("LABEL"), env("OWNER")
    head = env("LABELED_HEAD")
    pairs = (("--label", label), ("--sender", env("SENDER")), ("--actor", env("ACTOR")), ("--owner", who),
             ("--draft", env("DRAFT")), ("--pr", env("PR")), ("--head", head))
    with open("owner-review.json", "w") as review, open("owner-why.txt", "w") as why:
        run = python(VERDICT, "owner", *(part for pair in pairs for part in pair), stdout=review, stderr=why, check=False)
    reason = Path("owner-why.txt").read_text().splitlines()
    ok = False
    if run.returncode != 0:
        say(f"::warning::main's verdict.py could not read the label {label}: no approve ({reason[-1] if reason else ''})")
    elif Path("owner-review.json").stat().st_size:
        ok = True
        say(f"the owner {who} approved {head} with the label {label}")
    else:
        say(f"::notice::the label {label} is ignored: {Path('owner-why.txt').read_text().rstrip(chr(10))}")
    output(ok="true" if ok else "false")
    return 0


def checks_api() -> str:
    return f"repos/{env('GITHUB_REPOSITORY')}/check-runs"


def mark_time() -> datetime | None:
    """The time of the event that started this run: `pull_request.updated_at` of the payload, which GitHub wrote when it made
    the event (a late delivery keeps it). A run by hand has none: its payload has no time, and its run's time needs the
    permission `actions: read`, which the start's token does not hold (so a run by hand is left out: OWL-35)."""
    path = os.environ.get("GITHUB_EVENT_PATH", "")
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request" or not path or not Path(path).is_file():
        return None
    stamp = json.loads(Path(path).read_text()).get("pull_request", {}).get("updated_at")
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None


def reviewed_since(check_runs: list[dict[str, Any]], since: datetime) -> bool:
    """Whether a check `verdict` of the head completed after `since`, the time of the mark that started this run: another
    run of the group `review-N` reviewed the head after this mark, so this run is a second review of one mark. The runs of
    the group go one after the other, so a check in progress never shows here: only a completed one does. A check that
    completed before the mark is an earlier review, and a mark again after it gets a review."""
    for run in check_runs:
        if run.get("name") == "verdict" and run.get("status") == "completed" and run.get("completed_at"):
            if datetime.fromisoformat(run["completed_at"].replace("Z", "+00:00")) > since:
                return True
    return False


VERDICT_STATES = ("APPROVED", "CHANGES_REQUESTED")  # a review that holds a verdict; COMMENTED holds inline comments


def reviews_since(reviews: list[dict[str, Any]], reviewer: str, head: str, since: datetime) -> int:
    """How many verdicts on `head` by `reviewer` were posted after `since`. The model posts its review inside the review step,
    before the check `verdict` completes, so the reviews show a second run's review earlier than the checks do (OWL-35). A
    verdict is an approve or a request for changes; a run's inline comments come as one more review in the state COMMENTED,
    which does not count (OWL-49: it made each run with an inline comment its own second review)."""
    count = 0
    for review in reviews:
        if (review.get("user") or {}).get("login") == reviewer and review.get("commit_id") == head and review.get("submitted_at") \
                and review.get("state") in VERDICT_STATES:
            if datetime.fromisoformat(review["submitted_at"].replace("Z", "+00:00")) > since:
                count += 1
    return count


def pull_reviews(repo: str, pr: str) -> list[dict[str, Any]]:
    """The reviews of the pull request, all pages."""
    listing = gh("api", f"repos/{repo}/pulls/{pr}/reviews?per_page=100", "--paginate", "--jq", ".[]").stdout
    return [json.loads(line) for line in listing.splitlines() if line.strip()]


def skip_start(repo: str, pr: str, head: str, draft: bool, since: datetime | None) -> str:
    """Why this run starts no review, or an empty string. A ready mark that GitHub delivers late, a second mark and a run that
    waited for a runner (2026-11-25, PR #6) gave two reviews of one mark, and the second counted as a round: the pull
    request is in draft now (its payload said it was ready), or a review of the head completed after this run's mark."""
    if draft:
        return "the pull request is in draft now"
    if since is None:
        return ""
    listing = gh("api", f"repos/{repo}/commits/{head}/check-runs?check_name=verdict&per_page=100", "--jq",
                 "[.check_runs[] | {name, status, completed_at}]").stdout
    if reviewed_since(json.loads(listing or "[]"), since):
        return f"a review of {head} completed after the mark that started this run"
    if reviews_since(pull_reviews(repo, pr), env("REVIEWER"), head, since):
        return f"a review of {head} was posted after the mark that started this run"
    return ""


def bump_extras(repo: str, pr: str, head: str) -> list[str]:
    """The files of a `loop-bump-SHORT` pull request that make it more than a pure bump (`loop_sync.py pure`: the pin, and the
    files that the loop's list names at the head). A read that fails counts as a file that is not a bump's: the check fails, and
    a new ready mark runs the step again. Main's copy of `loop_sync.py` decides."""
    spec = importlib.util.spec_from_file_location("loop_sync", HERE.parent / ".workflow" / "loop_sync.py")
    if spec is None or spec.loader is None or not (HERE.parent / ".workflow" / "loop_sync.py").is_file():
        return ["(this repository has no .workflow/loop_sync.py)"]
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    files = gh("api", f"repos/{repo}/pulls/{pr}/files?per_page=100", "--paginate", "--jq", ".[].filename", check=False)
    listing = gh("api", f"repos/{repo}/contents/.workflow/loop-files.txt?ref={head}", "-H", "Accept: application/vnd.github.raw", check=False)
    if files.returncode != 0 or listing.returncode != 0:
        return ["(the files or the list could not be read)"]
    return list(module.pure(files.stdout.splitlines(), listing.stdout))


def open_check() -> int:
    """The head to review and the check `verdict` in progress on it. A pull request with no issue key in its branch or its
    title gets a failed check and no review: its verdict would move no issue. The key is what main's
    `linear.py key-from` finds, as the review's facts read it. The owner's Approved needs no key: the owner looked at the
    head."""
    pr, repo, labeled = env("PR"), env("GITHUB_REPOSITORY"), env("LABELED_HEAD")
    if not labeled:
        view = gh("pr", "view", pr, "-R", repo, "--json", "headRefOid,headRefName,title,isDraft,isCrossRepository").stdout
        Path("pr.json").write_text(view)
        data = json.loads(view)
        head, branch, title = data["headRefOid"], data["headRefName"], data["title"]
        if data.get("isCrossRepository"):  # a fork's head: a run by hand or the watchdog's dispatch has no payload for `access`
            say(FORK_NOTICE)
            output(head=head)
            return 0
        key = python(LINEAR, "key-from", branch, title).stdout.rstrip("\n")
        if not key and BUMP_BRANCH.fullmatch(branch):
            extra = bump_extras(repo, pr, head)
            if not extra:
                say(f"::notice::{branch} is a pure bump of the loop's files (the pin and the listed files): it needs no issue")
                key = branch  # no issue key, and the review runs; the report moves no issue
            else:
                shown = ", ".join(extra[:5]) + (f" and {len(extra) - 5} more" if len(extra) > 5 else "")
                gh("api", checks_api(), "-f", "name=verdict", "-f", f"head_sha={head}", "-f", "status=completed",
                   "-f", "conclusion=failure", "-f", "output[title]=A bump that changes a product file needs its issue",
                   "-f", f"output[summary]=The branch {branch} is a pure bump only when it changes the pin and the loop's "
                   f"files. It changes more: {shown}. File the issue first, move the product change to its own pull request "
                   "or rename the branch with the issue key, and mark the pull request ready again.", stdout=subprocess.DEVNULL)
                say(f"::error::{branch} changes more than a pure bump ({shown}): no review, and the check verdict fails")
                output(head=head)
                return 0
        if not key:
            gh("api", checks_api(), "-f", "name=verdict", "-f", f"head_sha={head}", "-f", "status=completed",
               "-f", "conclusion=failure", "-f", "output[title]=No issue: put its key in the title",
               "-f", "output[summary]=No issue key (such as ABC-123) is in the branch name or the title, so no review ran: "
               "its verdict would move no issue. File the issue first, put its key at the start of the title, and mark the "
               "pull request ready again.", stdout=subprocess.DEVNULL)
            say(f"::error::no issue key in the branch '{branch}' or the title: no review, and the check verdict fails")
            output(head=head)
            return 0
        why = skip_start(repo, pr, head, bool(data.get("isDraft")), mark_time())
        if why:
            say(f"::notice::no review from this run: {why}")
            output(head=head)
            return 0
    if labeled:
        head = labeled
        # the owner's Approved as the reviewer app's approve, with a verdict JSON: `check`, `carry`, `rereview`,
        # `dismissed` and `since` read it as any approve. The report reads the verdicts from its own time on: GitHub's
        # time, never the runner's clock
        started = gh("api", f"repos/{repo}/pulls/{pr}/reviews", "--input", "owner-review.json", "--jq",
                     ".submitted_at").stdout.rstrip("\n")
    else:
        started = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")  # the report reads only the verdicts posted from here on
    check = gh("api", checks_api(), "-f", "name=verdict", "-f", f"head_sha={head}", "-f", "status=in_progress",
               "-f", "output[title]=Under review", "-f", "output[summary]=The reviewer reviews this commit.",
               "--jq", ".id").stdout.rstrip("\n")
    say(f"head {head}, check {check}")
    output(head=head, started=started, check=check)
    return 0


def posted() -> int:
    """A second review of the head, posted since this run started: the model's step posted its own review, so one is the run's
    own and two or more mean that another run reviewed the head too. The step before the verdict reads `second`, and a
    second review gets no verdict from this run (one review, one verdict: OWL-35)."""
    since = datetime.fromisoformat(env("STARTED").replace("Z", "+00:00"))
    count = reviews_since(pull_reviews(env("GITHUB_REPOSITORY"), env("PR")), env("REVIEWER"), env("HEAD"), since)
    say(f"{count} review(s) of {env('HEAD')} by {env('REVIEWER')} since {env('STARTED')}")
    second = count > 1
    if second and os.environ.get("CHECK"):  # this run writes no verdict, so its check ends here, never in progress for ever
        gh("api", "-X", "PATCH", f"{checks_api()}/{env('CHECK')}", "-f", "status=completed", "-f", "conclusion=neutral",
           "-f", "output[title]=A second review of this head", "-f", "output[summary]=Another review of this commit was posted "
           "since this run started, so this run writes no verdict.", stdout=subprocess.DEVNULL)
    output(second="true" if second else "false")  # after the PATCH: a failed one leaves no `second`, so the verdict is written
    return 0


def carry() -> int:
    """The approve's check on a branch update, a failed one on a dismissal: verdict.py's `carry` or `dismissed` reads the
    reviews and says what check to write, or nothing."""
    pr, repo, head = env("PR"), env("GITHUB_REPOSITORY"), env("HEAD")
    if not VERDICT.is_file():
        say("main has no verdict.py yet")
        return 0
    subprocess.run(["git", "fetch", "-q", "origin", head], check=True)
    with open("reviews.jsonl", "w") as reviews:
        gh("api", f"repos/{repo}/pulls/{pr}/reviews?per_page=100", "--paginate", "--jq", ".[]", stdout=reviews)
    with open("check.json", "w") as check:
        python(VERDICT, env("COMMAND"), "reviews.jsonl", "--reviewer", env("REVIEWER"), "--head", head, stdout=check)
    if Path("check.json").stat().st_size:
        say(Path("check.json").read_text().rstrip("\n"))
        gh("api", checks_api(), "--input", "check.json", stdout=subprocess.DEVNULL)
    return 0


def again() -> int:
    """A real commit after the approve leaves the head with no check `verdict`, and no implementer has anything to
    fix: before this step the pull request waited with nobody told. So the review runs again, as round 1 (the owner's call,
    2026-10-28): first a comment whose first words are verdict.py's PUSHED, from which the review counts its rounds again,
    then a review started by hand. GitHub Actions' token may start a workflow_dispatch run. An older main's verdict.py
    has no command `rereview`: nothing then."""
    pr, repo = env("PR"), env("GITHUB_REPOSITORY")
    run = python(VERDICT, "rereview", "reviews.jsonl", "--reviewer", env("REVIEWER"), "--head", env("HEAD"), check=False)
    word = run.stdout.rstrip("\n") if run.returncode == 0 else "false"
    if word != "pushed":
        return 0
    comment = gh("pr", "comment", pr, "-R", repo, "--body",
                 f'{PUSHED_TEXT} The review runs again as round 1 (agents/reviewer.md "Rounds").',
                 stdout=subprocess.DEVNULL, check=False)
    if comment.returncode != 0:
        say(f"::warning::PR #{pr}: the comment failed, so the review may count an extra round: answer on the issue")
    gh("workflow", "run", "claude-code-review.yml", "-R", repo, "--ref", env("MAIN"), "-f", f"pr={pr}", token=env("DISPATCH"))
    say(f"::notice::PR #{pr}: the approve does not cover the new head, so a new review runs")
    return 0


COMMANDS = {"access": access, "owner": owner, "open": open_check, "posted": posted, "carry": carry, "again": again}


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
