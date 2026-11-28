#!/usr/bin/env python3
"""The owner's label applies a workflow patch, for the workflow apply-workflow-patch.yml (agents/README.md "The guards").

    python3 .github/apply_workflow_patch.py ready RESULT
        Runs after a push, with the implementer app's token in GH_TOKEN (not GitHub Actions' token: an event of that token
        starts no workflow, so its ready mark would start no review). When the file RESULT.applied exists (the apply
        wrote it), marks the pull request PR of the repository GITHUB_REPOSITORY ready for review when it is a draft, and
        rewrites RESULT with what happened. It first waits (60 s at most) for a run of the pushed head, then marks, then
        checks (100 s at most) that a run of "Claude Code Review" with a job `start` that did not skip began after the mark;
        with none it marks once more (`--undo`, then ready). The text says "the review starts" only when the check found the
        run, else it gives the owner's `gh workflow run` step. Environment too: BRANCH, and READY_POLL_SECONDS,
        READY_PUSH_WAIT_SECONDS and READY_CHECK_SECONDS (the waits). A refused patch leaves no RESULT.applied, so nothing is marked. It exits 0.
    python3 .github/apply_workflow_patch.py COMMENTS RESULT
        COMMENTS is the reply of `gh api repos/OWNER/REPO/issues/N/comments --paginate --slurp`, flattened or not. Run it in a
        checkout of the pull request's branch. It applies the newest good patch, commits it and pushes the branch, then
        writes one line to the file RESULT: what it pushed (and says that the pull request is not marked ready yet, in case
        the `ready` step does not run), or why it refused. After a push it writes RESULT.applied too: "FILES at SHORT". It exits 0 for a refusal (the workflow
        comments RESULT on the pull request) and 1 when git fails.
        Environment: SENDER (the label's sender), OWNER (the repository owner), BEFORE (the time of the label: a comment
        that was written or edited after it is not what the owner read), BRANCH. IMPLEMENTER_LOGIN, when set, is the
        implementer app's login (default OWNER-implementer[bot]: each repository may have its own app pair).

The implementer app has no `workflows` permission, so a run that needs a change under `.github/workflows/` writes it as a
patch in one comment (agents/implementer.md step 6). The owner reads the patch and adds the label `apply-workflow-patch`:
that label is the owner's review, as `owner-approved` is. The workflow runs main's code only and pushes with a
token that has the `workflows` permission. So this script is the whole guard:

- The patch comes from the newest comment of the implementer app that holds a fenced block of `diff` or `patch`. A comment
  of any other author counts for nothing: anybody can comment on a pull request, and the owner's label would then apply
  a stranger's text.
- After `git apply`, the staged diff (git's own result, not the patch's text) must hold only files under
  `.github/workflows/`, no link and no submodule: else the patch is undone, with no commit and no push (a review finding:
  a legacy `rename old` header and a patch with no `a/` prefix passed the check of the text).
- Every path that the patch names is a file under `.github/workflows/`, with no `..`, and no link. A rename or a copy
  counts at both its ends. A patch that touches a path outside refuses whole: a part would hide the rest from the owner.
- The newest patch must be older than the label, and unedited since: the owner read that text. A newer or edited patch
  refuses (the owner reads it and adds the label again).
- The label's sender is the repository owner (`SENDER` and `OWNER` in the environment). A GitHub App that adds the
  label is no owner: "No agent adds or removes the label".

Standard library only (DECISIONS.md "Tooling").
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, UTC
from pathlib import Path
from collections.abc import Callable, Mapping
from typing import Any

WORKFLOWS = ".github/workflows/"
FENCE = re.compile(r"^(`{3,}|~{3,})[ \t]*(diff|patch)[ \t]*\n(.*?)^\1[ \t]*$", re.S | re.M)
LINK_MODE = re.compile(r"^(new|old|deleted file|new file) mode 120000$", re.M)
PATH_LINES = re.compile(
    r"^(?:diff --git (?P<git>.+)|(?:---|\+\+\+) (?P<side>.+?)(?:\t.*)?|(?:rename|copy) (?:from|to) (?P<move>.+))$", re.M
)


def implementer(owner: str) -> str:
    """The implementer app's login: IMPLEMENTER_LOGIN (the workflow sets it from the variable IMPLEMENTER_APP_SLUG), else
    OWNER-implementer[bot], the name of the owner's shared pair. (UPDATER is no login: some review steps put the app's
    token under that name.)"""
    return os.environ.get("IMPLEMENTER_LOGIN") or f"{owner}-implementer[bot]"


def patch_of(comment: str) -> str:
    """The text of the comment's fenced diff blocks, in order, each ending in a newline."""
    return "".join(block if block.endswith("\n") else block + "\n" for _, _, block in FENCE.findall(comment))


def newest_patch(comments: list[dict[str, Any]], app: str, before: str = "") -> tuple[str, str] | None:
    """(the comment's id, its patch) for the newest comment of the implementer app (its login APP) that holds a patch, else None.
    With BEFORE (an ISO time, which compares as text), that newest comment must be unchanged since then: a patch that
    the implementer wrote or edited after the label replaces the one the owner read, so the answer is None, never an older patch."""
    for comment in sorted(comments, key=lambda c: c.get("created_at", ""), reverse=True):
        if (comment.get("user") or {}).get("login") != app:
            continue
        patch = patch_of(comment.get("body") or "")
        if patch.strip():
            changed = max(comment.get("created_at", ""), comment.get("updated_at", ""))
            return None if before and changed > before else (str(comment.get("id")), patch)
    return None


def paths(patch: str) -> list[str]:
    """Each path that the patch names, as the text of its header lines gives it (`a/` and `b/` taken off)."""
    found: list[str] = []
    for match in PATH_LINES.finditer(patch):
        if match["git"]:
            names = [n for n in re.split(r" (?=[ab]/)", match["git"]) if n]
            found += [re.sub(r"^[ab]/", "", n.strip('"')) for n in names] if len(names) == 2 else ["?" + match["git"]]
        elif match["side"] and match["side"] != "/dev/null":
            found.append(re.sub(r"^[ab]/", "", match["side"].strip('"')))
        elif match["move"]:
            found.append(match["move"].strip('"'))
    return found


def refusal(patch: str) -> str | None:
    """None when every path is a file under .github/workflows/ and nothing is a link, else the reason."""
    named = paths(patch)
    if not named:
        return "the patch names no file (a patch is a `git diff`)"
    for path in named:
        if path.startswith("?") or ".." in path.split("/") or not path.startswith(WORKFLOWS) or path == WORKFLOWS:
            return f"the patch touches {path.lstrip('?')}, which is no file under {WORKFLOWS}"
    if LINK_MODE.search(patch):
        return "the patch makes or changes a symbolic link"
    return None


def decide(comments: list[dict[str, Any]], sender: str, owner: str, before: str = "") -> tuple[str, str]:
    """(`apply`, the patch) or (`refuse`, the reason)."""
    if not owner or sender != owner:
        return "refuse", f"the label came from {sender or 'nobody'}, and only the repository owner's label applies a patch"
    app = implementer(owner)
    found = newest_patch(comments, app, before)
    if found is None:
        return "refuse", f"no comment of {app} holds a patch (a fenced block of diff)"
    why = refusal(found[1])
    return ("refuse", why) if why else ("apply", found[1])


def flat(reply: object) -> list[dict[str, Any]]:
    """`gh api --paginate --slurp` gives a list of pages; a plain reply gives one list."""
    if not isinstance(reply, list):
        return []
    return [c for page in reply for c in (page if isinstance(page, list) else [page]) if isinstance(c, dict)]


def git(*args: str, stdin: str | None = None) -> str:
    """One git command with no hook of the branch: a hook is code of the branch, and this job holds a token."""
    done = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=apply-workflow-patch",
         "-c", "user.email=apply-workflow-patch@users.noreply.github.com", *args],
        input=stdin, text=True, capture_output=True, check=False)
    if done.returncode:
        raise SystemExit(f"git {args[0]} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def written_outside() -> str | None:
    """After `git apply --index`: the first path git changed that is no file under .github/workflows/, or a link or a
    submodule, else None. The staged diff is git's own result, so it holds what the patch's text can hide (a legacy
    `rename old` header, a patch with no `a/` prefix that git applies one directory deeper). No renames: both ends show."""
    raw = git("diff", "--cached", "--no-renames", "--raw", "-z").split("\0")
    for meta, path in zip(raw[0::2], raw[1::2], strict=False):
        modes = meta.lstrip(":").split()[:2]
        if not path.startswith(WORKFLOWS) or ".." in path.split("/") or {"120000", "160000"} & set(modes):
            return path
    return None


def gh(*args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["gh", *args], text=True, capture_output=True, check=False)
    except OSError as error:  # no gh on PATH: the owner marks the pull request ready
        return subprocess.CompletedProcess(args, 127, "", str(error))


def seconds(name: str, default: float) -> float:
    """A wait of this script, in seconds: the environment may shorten it (the tests do)."""
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def runs(repo: str, *args: str) -> list[dict[str, Any]]:
    """The newest runs (`gh run list --json ...`) of the repository, or an empty list when gh cannot read them."""
    done = gh("run", "list", "-R", repo, "--limit", "50", *args)
    try:
        found = json.loads(done.stdout) if not done.returncode else []
    except ValueError:
        return []
    return found if isinstance(found, list) else []


def until(found: Callable[[], bool], limit: float) -> bool:
    """True when FOUND turns true within LIMIT seconds (polled every READY_POLL_SECONDS)."""
    stop = time.monotonic() + limit
    while True:
        if found():
            return True
        if time.monotonic() >= stop:
            return False
        time.sleep(seconds("READY_POLL_SECONDS", 5))


def review_started(repo: str, branch: str, since: str) -> bool:
    """True when a run of "Claude Code Review" of BRANCH, created at or after SINCE (an ISO time, which compares as
    text), has a job `start` that did not skip (agents/implementer.md "When you are done" step 6)."""
    for run in runs(repo, "--workflow", "claude-code-review.yml", "--branch", branch, "--json", "databaseId,createdAt"):
        if str(run.get("createdAt", "")) < since:
            continue
        view = gh("run", "view", str(run.get("databaseId")), "-R", repo, "--json", "jobs")
        try:
            jobs = json.loads(view.stdout).get("jobs", []) if not view.returncode else []
        except (ValueError, AttributeError):
            continue
        if any(j.get("name") == "start" and j.get("conclusion") != "skipped" for j in jobs if isinstance(j, dict)):
            return True
    return False


def mark_ready(applied: str, env: Mapping[str, str]) -> str:
    """The text for the pull request after the push. A draft is marked ready, and the text says that the review starts only
    after a run of it exists (agents/implementer.md step 6): a mark a few seconds after a push can start no workflow, and
    the review watchdog skips a pull request with the label `escalated`, so a missed review would wait with nobody told."""
    pr, repo, branch = env.get("PR", ""), env.get("GITHUB_REPOSITORY", ""), env.get("BRANCH", "")
    ask = f"The label applied the patch to {applied}. The pull request is not marked ready"
    view = gh("pr", "view", pr, "-R", repo, "--json", "isDraft", "--jq", ".isDraft")
    if view.returncode or view.stdout.strip() not in ("true", "false"):
        return f"{ask} (it could not be read: {view.stderr.strip()[:200]}): mark it ready to start the review.\n"
    if view.stdout.strip() == "false":
        return f"{ask}, because it is not a draft: mark it ready again (`gh pr ready {pr} --undo`, then `gh pr ready {pr}`) to start the review.\n"
    sha = applied.rsplit(" at ", 1)[-1]
    until(lambda: any(str(r.get("headSha", "")).startswith(sha) for r in runs(repo, "--branch", branch, "--json", "headSha")),
          seconds("READY_PUSH_WAIT_SECONDS", 60))
    for mark in (1, 2):
        if mark == 2:
            undo = gh("pr", "ready", pr, "-R", repo, "--undo")
            if undo.returncode:
                break
        since = (datetime.now(UTC) - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        done = gh("pr", "ready", pr, "-R", repo)
        if done.returncode:
            return f"{ask} (the ready mark failed: {done.stderr.strip()[:200]}): mark it ready to start the review.\n"
        if until(lambda since=since: review_started(repo, branch, since), seconds("READY_CHECK_SECONDS", 100)):
            return f"The label applied the patch to {applied}, and marked the pull request ready: the review starts.\n"
    return (f"The label applied the patch to {applied} and marked the pull request ready twice, but no review started. "
            f"Start it by hand: `gh workflow run claude-code-review.yml -R {repo} -f pr={pr}` (leave the round empty).\n")


def main(argv: list[str]) -> int:
    env = os.environ
    if argv[1] == "ready":
        marker = Path(argv[2] + ".applied")
        if marker.exists():
            Path(argv[2]).write_text(mark_ready(marker.read_text().strip(), env))
        return 0
    comments = flat(json.loads(Path(argv[1]).read_text()))
    verdict, text = decide(comments, env.get("SENDER", ""), env.get("OWNER", ""), env.get("BEFORE", ""))
    if verdict == "refuse":
        Path(argv[2]).write_text(f"The label did not apply a patch: {text}.\n")
        return 0
    git("apply", "--index", "--whitespace=nowarn", "-", stdin=text)
    outside = written_outside()
    if outside:
        git("reset", "--hard", "HEAD")
        Path(argv[2]).write_text(f"The label did not apply a patch: git would change {outside}, which is no file under {WORKFLOWS} (or it is a link).\n")
        return 0
    git("commit", "-m", f"Apply the workflow patch of the implementer's comment, on the owner's label ({env.get('OWNER')})")
    git("push", "origin", f"HEAD:refs/heads/{env['BRANCH']}")
    applied = f"{', '.join(sorted(set(paths(text))))} at {git('rev-parse', '--short', 'HEAD')}"
    Path(argv[2] + ".applied").write_text(applied + "\n")
    Path(argv[2]).write_text(f"The label applied the patch to {applied}. The pull request is not marked ready: mark it ready to start the review.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
