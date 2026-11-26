#!/usr/bin/env python3
"""The job `report` of the review workflow (.github/workflows/claude-code-review.yml): after the review, the check
`verdict`, the Linear moves and the auto-merge (the logic moved here from the job's `run:` blocks).

    python3 .github/review_report.py early
        before the check: turn off an auto-merge that an earlier approve turned on, when the pull request now changes a
        code owner's file and the owner's label does not stand for the head
    python3 .github/review_report.py check
        the check `verdict` from the verdict the review posted (main's verdict.py `check`), and the approve's check carried
        to a head pushed while the review ran. It writes the step's output `conclusion`. The check holds only the review:
        the gates are required checks of their own, and gates_failed.py sends a failed head back (OWL-30)
    python3 .github/review_report.py pushed
        a real commit pushed while the review ran: the reviewer app's comment, then a new review of main's workflow
    python3 .github/review_report.py escalated
        a needs-decision: the label `escalated` on the pull request
    python3 .github/review_report.py owner-approved
        the owner's label `owner-approved` gave the check: the issue to Approved, and the follow-up lines of the last review
        that ended in a question for the owner into the Area's collecting issue (`linear.py follow-up`)
    python3 .github/review_report.py merge
        an approve: GitHub's auto-merge with the implementer app's token, or, for a code owner's file without the
        owner's label, no auto-merge, the label `escalated` and the issue to Escalated

Each command reads the job's environment: PR, HEAD (the head the review read), SINCE (the verdicts from that time on),
CHECK (the check run's id), REVIEWER (the reviewer app's login), GH_TOKEN, GITHUB_REPOSITORY, and its step's own variables
(the workflow's `env:`). The texts name the product's limits file (OWNER_LIMITS_FILE, default `limits.toml`), the optional
value that verdict.py reads. It runs `gh`, and main's verdict.py and linear.py from the current folder, which is a checkout of
main: the job holds the implementer app's token and the Linear key, so it never runs a file of the branch. The
file reviews.jsonl that `check` writes is read by the later commands `pushed` and `escalated`; the other files that a
command writes (files.txt, owned.txt, check.json, carry.json, the comments' bodies) are its own, as the shell's were. A
failed call to gh stops the command with gh's status where the shell step stopped (`set -e`), and only warns where the
shell warned. The reasons for each rule are in the workflow's comments and in DECISIONS.md "Delivery". Standard library
only (DECISIONS.md "Tooling"); the runner's python3 is 3.12 (agents/README.md).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

GITHUB = Path(".github")
WORKFLOW_FILE = re.compile(r'(^|"(previous_)?filename": *")\.github/workflows/', re.MULTILINE)
RETRY = re.compile(r"Base branch was modified|status code: 50[234]|HTTP 50[234]")
FENCED = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
FROM_LINE = re.compile(r"^From:\s*PR #\d+, round \d+, \S+, depth (\d+)\s*$", re.MULTILINE)
FOLLOW_UP_LABEL = "follow-up"
FOLLOW_UP_MAX = 5  # the schema's cap of `follow_up: true` findings in one verdict
MERGES_AT_ONCE = ("CLEAN", "UNSTABLE", "HAS_HOOKS")  # the states in which gh merges at once


def env(name: str) -> str:
    return os.environ.get(name, "")


def say(line: str) -> None:
    print(line, flush=True)


def gh(*args: str, token: str = "", stdin: str | None = None, both: bool = False) -> subprocess.CompletedProcess[str]:
    """One gh call. `token` replaces GH_TOKEN for this call only; `both` reads stderr into stdout (`2>&1`)."""
    run_env = dict(os.environ, GH_TOKEN=token) if token else None
    return subprocess.run(["gh", *args], env=run_env, input=stdin, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT if both else None, check=False)


def script(name: str, *args: str, stdin: str | None = None, out: bool = True) -> subprocess.CompletedProcess[str]:
    """Main's .github/NAME, Python isolated. `out` False lets its output through to the log."""
    return subprocess.run([sys.executable, "-I", str(GITHUB / name), *args], input=stdin, text=True,
                          stdout=subprocess.PIPE if out else None, check=False)


def must(done: subprocess.CompletedProcess[str]) -> str:
    """The call's output; a failed call stops the command with its status, as `set -e` stopped the shell step."""
    if done.returncode != 0:
        raise SystemExit(done.returncode)
    return done.stdout or ""


def word(done: subprocess.CompletedProcess[str], default: str = "") -> str:
    """The output of `$(...)` with a fallback: the last newline cut, `default` for a failed call."""
    return (done.stdout or "").rstrip("\n") if done.returncode == 0 else default


def output(**values: str) -> None:
    path = env("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as out:
            out.writelines(f"{name}={value}\n" for name, value in values.items())


def view(field: str, default: str | None = None) -> str:
    """`gh pr view PR --json FIELD --jq .FIELD`; a failed read stops the command, or gives `default` when there is one."""
    done = gh("pr", "view", env("PR"), "-R", env("GITHUB_REPOSITORY"), "--json", field, "--jq", f".{field}")
    return must(done).rstrip("\n") if default is None else word(done, default)


def add_escalated_label() -> None:
    done = gh("api", "-X", "POST", f"repos/{env('GITHUB_REPOSITORY')}/issues/{env('PR')}/labels", "-f", "labels[]=escalated")
    if done.returncode != 0:
        say("::warning::the label escalated could not be added to the pull request: add it by hand")


def auto_merge_on() -> str:
    return must(gh("pr", "view", env("PR"), "-R", env("GITHUB_REPOSITORY"), "--json", "autoMergeRequest", "--jq",
                   ".autoMergeRequest != null")).strip()


def disable_auto_merge() -> None:
    must(gh("pr", "merge", env("PR"), "-R", env("GITHUB_REPOSITORY"), "--disable-auto"))


def issue_key(branch: str, title: str) -> str:
    return word(script("linear.py", "key-from", branch, title))


def ended(state: str, key: str) -> bool:
    """Main's verdict.py `ended`: true (and its notice line) when the pull request merged or closed, so its issue keeps its
    state. A state that gh could not read counts as open."""
    return script("verdict.py", "ended", state, "--pr", env("PR"), "--key", key, out=False).returncode == 0


def linear_move(key: str, state: str, body: str = "", body_file: str = "") -> None:
    """The issue's comment (when there is a body) and its move; a Linear failure only warns."""
    if body:
        Path(body_file).write_text(body)
        moved = script("linear.py", "comment", key, "--body-file", body_file, out=False).returncode == 0 \
            and script("linear.py", "state", key, state, out=False).returncode == 0
        what = f"the Linear comment or the move of {key} to {state}"
    else:
        moved = script("linear.py", "state", key, state, out=False).returncode == 0
        what = f"the move of {key} to {state}"
    if not moved:
        say(f"::warning::{what} failed: move it by hand")


def owned_files() -> list[str]:
    """The pull request's files (the files API's objects, files.txt) that only the owner merges, by main's verdict.py
    (owned.txt). A failed read stops the command, before any merge."""
    files = must(gh("api", f"repos/{env('GITHUB_REPOSITORY')}/pulls/{env('PR')}/files?per_page=100", "--paginate", "--jq", ".[]"))
    Path("files.txt").write_text(files)
    owned = must(script("verdict.py", "owned", str(GITHUB / "CODEOWNERS"), stdin=files))
    Path("owned.txt").write_text(owned)
    return owned.splitlines()


def early() -> int:
    """The owner's label lifts the guard only while the head is the one the owner labeled; a failed read
    lifts nothing."""
    owned = owned_files()
    auto = auto_merge_on()
    lift = env("OWNER_APPROVED") == "true" and view("headRefOid", "") == env("HEAD")
    if owned and auto == "true" and not lift:
        disable_auto_merge()
    return 0


def check() -> int:
    """The check `verdict`, from the verdict the review posted and nothing else: the gates are required checks of their
    own, so the ruleset holds a red gate, and gates_failed.py sends an approved head with a red gate back to an
    implementer. A review that ended with no verdict gives a failed check that names the case (`MODEL`)."""
    repo, head = env("GITHUB_REPOSITORY"), env("HEAD")
    reviews = must(gh("api", f"repos/{repo}/pulls/{env('PR')}/reviews?per_page=100", "--paginate", "--jq", ".[]"))
    Path("reviews.jsonl").write_text(reviews)
    body = json.loads(must(script("verdict.py", "check", "reviews.jsonl", "--reviewer", env("REVIEWER"), "--head", head,
                                  "--since", env("SINCE"), "--review", env("MODEL"))))
    text = json.dumps(body)
    Path("check.json").write_text(text + "\n")
    say(text)
    output(conclusion=str(body["conclusion"]))
    update = {k: v for k, v in body.items() if k != "head_sha"}  # an update takes no head_sha: the run keeps its commit
    must(gh("api", "-X", "PATCH", f"repos/{repo}/check-runs/{env('CHECK')}", "--input", "-", stdin=json.dumps(update)))
    # a push while the review ran: its carry run read the verdicts before this one, so the carry runs again here
    now = view("headRefOid")
    if now != head:
        must(subprocess.run(["git", "fetch", "-q", "origin", now], text=True, check=False))
        carry = must(script("verdict.py", "carry", "reviews.jsonl", "--reviewer", env("REVIEWER"), "--head", now))
        Path("carry.json").write_text(carry)
        if carry:
            say(carry.rstrip("\n"))
            must(gh("api", f"repos/{repo}/check-runs", "--input", "carry.json"))
    return 0


def pushed() -> int:
    """A real commit pushed while the review ran: the comment first (its first words are verdict.py's PUSHED: the review
    counts its rounds again from it), then a review of main's workflow with GitHub Actions' token (DISPATCH)."""
    pr, repo = env("PR"), env("GITHUB_REPOSITORY")
    now = view("headRefOid")
    if now == env("HEAD"):
        return 0
    subprocess.run(["git", "fetch", "-q", "origin", now], check=False)  # without the commit, rereview answers false
    if word(script("verdict.py", "rereview", "reviews.jsonl", "--reviewer", env("REVIEWER"), "--head", now), "false") != "pushed":
        return 0
    text = ("A commit was pushed after the approved one, so the approve does not cover the new head. The review runs again as "
            'round 1 (agents/reviewer.md "Rounds").')
    if gh("pr", "comment", pr, "-R", repo, "--body", text).returncode != 0:
        say(f"::warning::PR #{pr}: the comment failed, so the review may count an extra round: answer on the issue")
    if gh("workflow", "run", "claude-code-review.yml", "-R", repo, "--ref", env("MAIN"), "-f", f"pr={pr}",
          token=env("DISPATCH")).returncode != 0:
        say(f"::warning::PR #{pr}: the new review did not start: start it by hand")
    say(f"::notice::PR #{pr}: a commit was pushed while the review ran, so a new review runs")
    return 0


def escalated() -> int:
    """The verdict this run wrote the check from is a needs-decision: the label. A failure only warns."""
    done = script("verdict.py", "escalates", "reviews.jsonl", "--reviewer", env("REVIEWER"), "--since", env("SINCE"))
    if done.returncode != 0:
        say("::warning::main's verdict.py could not read the verdict: on a needs-decision, add the label escalated by hand")
        return 0
    if word(done) == "true":
        add_escalated_label()
    return 0


def owner_approved() -> int:
    """The owner's label gave the check: the issue to Approved, with a comment that names the head."""
    key = issue_key(view("headRefName", ""), view("title", ""))
    if not key or not env("LINEAR_API_KEY"):
        say("::notice::no Linear issue or no Linear key: the issue keeps its state")
        return 0
    if ended(view("state", ""), key):
        return 0
    text = (f"The owner approved PR #{env('PR')} on GitHub with the label owner-approved, at {env('HEAD')[:7]}: the check "
            "verdict passed on that commit.\n")
    linear_move(key, "Approved", text, "approved.md")
    owner_follow_ups(key)
    return 0


def verdict_json(body: str) -> dict[str, Any]:
    """The last fenced `json` block of a review's body that holds a verdict, or {}."""
    for block in reversed(FENCED.findall(body)):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and "verdict" in data:
            return data
    return {}


def review_verdicts() -> list[dict[str, Any]]:
    """The verdict JSON of each review of the reviewer app that is its own, not the owner's approve (`approved_by`), oldest
    first. A failed read of the reviews is [] with a warning."""
    done = gh("api", f"repos/{env('GITHUB_REPOSITORY')}/pulls/{env('PR')}/reviews?per_page=100", "--paginate", "--jq", ".[]")
    if done.returncode != 0:
        say("::warning::the reviews could not be read: the follow-up lines of the last review are not filed")
        return []
    found = []
    for line in (done.stdout or "").splitlines():
        try:
            review = json.loads(line)
        except ValueError:
            continue
        if not isinstance(review, dict) or (review.get("user") or {}).get("login") != env("REVIEWER"):
            continue
        data = verdict_json(str(review.get("body") or ""))
        if data and not data.get("approved_by"):
            found.append((str(review.get("submitted_at") or ""), data))
    return [data for _, data in sorted(found, key=lambda pair: pair[0])]


def finding_line(finding: dict[str, Any]) -> str:
    """One checklist line for a finding: its id, its place and its summary."""
    where = f"{finding.get('file', '')}:{finding['line']}" if finding.get("line") else str(finding.get("file", ""))
    return f"- [ ] {finding.get('id', '?')} {where}: {finding.get('summary', '')}".rstrip()


def follow_up_lines(data: dict[str, Any], earlier: Sequence[dict[str, Any]] = ()) -> list[str]:
    """One checklist line for each finding of the verdict with `follow_up` true, as the reviewer writes them, then one for
    each `fix_now` finding of round 1 that the verdict's `previous` leaves open (agents/README.md "Follow-ups"), taken with
    its round-1 id from the `earlier` verdicts (newest first wins)."""
    lines = []
    for finding in data.get("findings") or []:
        if isinstance(finding, dict) and finding.get("follow_up") is True and not finding.get("blocking"):
            lines.append(finding_line(finding))
    lines = lines[:FOLLOW_UP_MAX]  # the schema's cap holds for `follow_up` lines; the open round-1 lines come after it
    open_ids: dict[str, bool] = {}
    for verdict in [*earlier, data]:  # round 3's `previous` lists blocking findings only: round 2's open fix_now ones stay open
        for entry in verdict.get("previous") or []:
            if isinstance(entry, dict) and entry.get("fix_now") is True and isinstance(entry.get("id"), str):
                open_ids[entry["id"]] = entry.get("fixed") is False
    for entry in data.get("previous") or []:
        if isinstance(entry, dict) and entry.get("id") in open_ids and entry.get("fixed") is True:
            open_ids[entry["id"]] = False
    for found, still_open in open_ids.items():
        if not still_open:
            continue
        for old in reversed(earlier):
            hit = next((f for f in old.get("findings") or [] if isinstance(f, dict) and f.get("id") == found), None)
            if hit:
                lines.append(finding_line(hit))
                break
    return lines


def owner_follow_ups(key: str) -> None:
    """The owner's approve runs no model, so nothing files the follow-up lines of a review that ended in a question for the
    owner (`needs-decision`) or in a request for changes. This reads the reviewer app's last review of its own, takes the
    findings with `follow_up` true, and the round-1 `fix_now` findings that its `previous` leaves open, and files them as the reviewer does: `linear.py follow-up`, into the Area's collecting
    issue, priority low, in Triage (the fast lane's test is the reviewer's). An approve of the reviewer filed its own lines
    already, so it files nothing. A review with no such line files nothing. A finding that needs the owner's choice has
    `follow_up` true too (agents/reviewer.md "Follow-ups"): the verdict does not mark it, so it goes in as a line, and the
    owner moves it by hand. Nothing here stops the approve: a failure to
    file writes one comment on the pull request with the lines, so the owner sees them (agents/README.md "Follow-ups")."""
    verdicts = review_verdicts()
    # only the current round count: from the newest round-1 verdict on (an earlier count's open findings are fixed or filed)
    verdicts = verdicts[max((i for i, v in enumerate(verdicts) if v.get("round") == 1), default=0):]
    data = verdicts[-1] if verdicts else {}
    if data.get("verdict") not in ("needs-decision", "request-changes"):
        return
    lines = follow_up_lines(data, verdicts[:-1])
    if not lines:
        return
    why = ""
    depth = 1
    shown = script("linear.py", "list", key.split("-")[0], "--key", key)
    try:
        issue = json.loads(shown.stdout or "[]")[0] if shown.returncode == 0 else {}
    except (ValueError, IndexError):
        issue = {}
    labels = list(issue.get("labels") or [])
    area = next(iter(issue.get("areas") or []), "")
    if FOLLOW_UP_LABEL in labels:
        depth = max([int(n) for n in FROM_LINE.findall(str(issue.get("description") or ""))] or [1]) + 1
    if not area:
        why = f"{key} has no Area label, or its text could not be read"
    else:
        origin = f"PR #{env('PR')}, round {data.get('round') or 1}, {str(data.get('commit') or env('HEAD'))[:7]}, depth {depth}"
        Path(f"{key}-follow-up.md").write_text("\n".join(lines) + "\n")
        done = script("linear.py", "follow-up", key.split("-")[0], "--area", area, "--from", origin, "--body-file",
                      f"{key}-follow-up.md", "--priority", "low")
        if done.returncode == 0:
            say(f"the follow-up lines of the last review went to: {(done.stdout or '').strip()}")
            return
        why = "`linear.py follow-up` failed"
    say(f"::warning::the follow-up lines of the last review were not filed ({why}): they are in a comment on the pull request")
    text = (f"The owner approved this pull request after a review whose follow-up lines were not filed ({why}). File them "
            "by hand:\n\n" + "\n".join(lines) + "\n")
    if gh("pr", "comment", env("PR"), "-R", env("GITHUB_REPOSITORY"), "--body", text).returncode != 0:
        say("::warning::the comment with the follow-up lines failed too")


def lifted() -> bool:
    """Whether the owner's label lifts the guard: it stands for the head the review read. A failed read stops the command
    before any merge."""
    if env("OWNER_APPROVED") != "true":
        return False
    now, head = view("headRefOid"), env("HEAD")
    if now == head:
        return True
    say(f"::notice::the owner labeled {head[:7]}, and the head is {now[:7]} now: the label lifts nothing")
    return False


def auto_merge(tries: int, wait: float, sleep: Callable[[float], None] = time.sleep) -> int:
    """Auto-merge with the implementer app's token (MERGER), else GitHub Actions' token. GitHub's "Base branch was
    modified" and a 5xx are tried again; after a 5xx a merge or an auto-merge that is on ends it. When
    every try fails: the label escalated and the issue to Escalated with GitHub's last answer, and the step fails."""
    pr, repo, token = env("PR"), env("GITHUB_REPOSITORY"), env("MERGER") or env("GH_TOKEN")
    if not env("MERGER"):
        say("::warning::no implementer app token: the merge will start no workflow")
    # a clean pull request that changes a workflow file: gh would merge it at once as the app, which GitHub refuses
    if WORKFLOW_FILE.search(Path("files.txt").read_text()) and view("mergeStateStatus", "") in MERGES_AT_ONCE:
        say(must(gh("pr", "comment", pr, "-R", repo, "--body",
                    "Ready to merge. It changes a workflow file, which the app cannot merge: the owner merges it on GitHub.",
                    token=token)).rstrip("\n"))
        return 0
    attempt = 1
    while True:
        done = gh("pr", "merge", pr, "-R", repo, "--auto", "--merge", token=token, both=True)
        answer = (done.stdout or "").rstrip("\n")
        say(answer)
        if done.returncode == 0:
            return 0
        if not RETRY.search(answer):
            return 1  # any other refusal: the step fails, as it did before this loop
        state = word(gh("pr", "view", pr, "-R", repo, "--json", "state,autoMergeRequest", "--jq",
                        'if .state == "MERGED" or .autoMergeRequest != null then "yes" else "no" end'), "no")
        if state == "yes":
            say("::notice::GitHub answered with an error, but the pull request has auto-merge on or merged already")
            return 0
        reason = answer.split("\n")[-1][:200]
        if attempt >= tries:
            say(f"::error::GitHub refused auto-merge {tries} times ({reason}): the pull request waits on the owner")
            add_escalated_label()
            key = issue_key(view("headRefName", ""), view("title", ""))
            if ended(view("state", ""), key):
                pass  # a merged or closed pull request moves no issue
            elif key and env("LINEAR_API_KEY"):
                text = (f"The reviewer approved PR #{pr}, but GitHub refused to turn on auto-merge {tries} times. Its last "
                        f'answer: "{reason}" ("Base branch was modified" means that another pull request merged at the same '
                        "time; a 5xx status is GitHub's own failure). Without auto-merge, nothing updates or merges the pull "
                        f"request. Run the failed job of the review workflow again (run {env('GITHUB_RUN_ID') or '?'}), turn "
                        "on auto-merge on GitHub, or merge the pull request on GitHub: the issue then goes to Done.\n")
                linear_move(key, "Escalated", text, "retry.md")
            else:
                say("::notice::no Linear issue or no Linear key: the label escalated is the only sign")
            return 1
        attempt += 1
        sleep(wait)


def owners_merge(owned: list[str]) -> int:
    """A code owner's file without the owner's label: no auto-merge (an earlier one goes off), the label escalated,
    and the issue to Escalated with the files. A merged pull request keeps Linear's Done."""
    state = view("state")
    if state == "MERGED":
        say("::notice::the pull request changes a code owner's file and merged already: the issue keeps Linear's Done")
        return 0
    if auto_merge_on() == "true":
        disable_auto_merge()
    add_escalated_label()  # the pull request waits on the owner, also without a Linear issue
    key = issue_key(view("headRefName"), view("title"))
    if ended(state, key):
        return 0  # closed while the review ran: it moves no issue
    if key and env("LINEAR_API_KEY"):
        files = "".join(f"- {path}\n" for path in owned)
        limits = env("OWNER_LIMITS_FILE") or "limits.toml"  # as verdict.py reads it
        text = (f"The reviewer approved PR #{env('PR')}. It changes files that only the owner merges (.github/CODEOWNERS, an "
                f"owner's key of {limits}, or AGENTS.md's section Autonomous runs):\n\n"
                f"{files}\n"
                "Read the change and merge the pull request on GitHub (no auto-merge for these files): the issue goes to "
                "Done. Or move the issue to Todo with an answer, or to Canceled to drop it (and close the pull request on "
                "GitHub).\n")
        linear_move(key, "Escalated", text, "owner.md")
    else:
        say("::notice::the pull request changes a code owner's file and has no Linear issue: the owner merges it on GitHub")
    return 0


def merge() -> int:
    """An approve: auto-merge, unless the pull request changes a code owner's file and the owner's label does not stand.
    The files are read first, so a failed read stops the command before any merge."""
    owned = owned_files()
    lift = lifted()
    if owned and lift:
        say(f"::notice::the owner's label owner-approved: auto-merge also for the owner's files ({','.join(owned)})")
    if not owned or lift:
        return auto_merge(int(env("TRIES") or 5), float(env("WAIT") or 10))
    return owners_merge(owned)


COMMANDS: dict[str, Callable[[], int]] = {"early": early, "check": check, "pushed": pushed,
                                          "escalated": escalated, "owner-approved": owner_approved, "merge": merge}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in COMMANDS:
        print(f"usage: review_report.py {{{','.join(COMMANDS)}}}", file=sys.stderr)
        return 2
    return COMMANDS[argv[0]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
