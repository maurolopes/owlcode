#!/usr/bin/env python3
"""The check `verdict`, for the review workflow (agents/README.md "The guards"): the reviewer's verdict as a required check.

    python3 .github/verdict.py check REVIEWS --reviewer LOGIN --head SHA [--since TIME] [--review OUTCOME]
        the check run's body (JSON, for `gh api .../check-runs --input -`) from the reviewer's last verdict on the pull
        request posted at TIME or later: success for an approve, failure for anything else or for no verdict. OUTCOME is
        the outcome of the review job's model step (success, failure, cancelled, skipped): with no verdict, the check's
        text says which case it is
    python3 .github/verdict.py carry REVIEWS --reviewer LOGIN --head SHA [--main REF]
        the check run's body when the head is a branch update of the commit the reviewer's last verdict approved, or
        nothing: every commit since is the automatic merge of main, whatever main changed (the owner's call, 2026-11-02)
    python3 .github/verdict.py rereview REVIEWS --reviewer LOGIN --head SHA [--main REF]
        `pushed` when a real commit came after the approved one, so the approve does not cover the head and the
        review runs again as round 1 (the owner's call, 2026-10-28), else `false`
    python3 .github/verdict.py dismissed REVIEWS --reviewer LOGIN --head SHA
        after a review was dismissed: the failed check run's body when the reviewer's last verdict is no longer an
        approve, or nothing (a dismissal of another review changes nothing)
    python3 .github/verdict.py escalates REVIEWS --reviewer LOGIN [--since TIME]
        `true` when the reviewer's last verdict posted at TIME or later is a needs-decision (the reviewer moved the issue to
        Escalated, so the pull request gets the label `escalated`), else `false`
    python3 .github/verdict.py answered STAYS REVIEWS --reviewer LOGIN
        when the owner last answered the issue in Escalated (ISO 8601), or nothing. STAYS is `linear.py escalated`'s JSON.
        An exit to Todo, Approved or Canceled is an answer. An exit to In Progress is an answer too (Linear merges the
        owner's move to Todo with the claim after it), unless the reviewer's last verdict while the issue was in
        Escalated asks for changes: then the reviewer moved it, after a review that `carry` started
    python3 .github/verdict.py since REVIEWS COMMENTS --reviewer LOGIN --updater LOGIN [--since TIME] [--runs RUNS --owner LOGIN --pr N]
        the time the review rounds count from: TIME (the issue last left Escalated), or the latest comment in COMMENTS
        (the issue comments API's objects, one a line) that starts the count again, when it is later and the reviewer's
        last verdict before it is an approve: the branch update's comment on a conflict or on failed gates, or
        the reviewer app's comment on an update the approve does not cover. The review after it is round 1
        again (the owner's calls, 2026-10-28). Also the reviewer app's comment of the owner's run by hand with round 1
        (MANUAL), whatever the verdict before it: that run's own verdict comes after it, so the next ready mark
        is round 2. It counts only for a run in RUNS (the workflow runs API's objects, one a line) that the event
        `workflow_dispatch` and the owner LOGIN started for pull request N with round 1 (its title, MANUAL_TITLE),
        posted while that run ran, and only the first such comment that names the run
    python3 .github/verdict.py rerun REVIEWS COMMENTS --reviewer LOGIN --updater LOGIN --head SHA --gates CONCLUSION
        the check run's body when a rerun of the gates passed on the head that the reviewer's last verdict approved, and
        the latest comment that starts the rounds again is the
        failed gates' one (FAILED_GATES, from the updater), posted after the approve. Else nothing, and why on stderr:
        another head, another reason, or gates that did not pass. No new review and no new round
    python3 .github/verdict.py owner --label NAME --sender LOGIN --actor LOGIN --owner LOGIN --draft BOOL --pr N --head SHA
        the owner's Approved on GitHub: when the repository owner added the label `owner-approved` to a pull request
        ready for review, the body (JSON, for `gh api .../pulls/N/reviews --input -`) of the approve that the reviewer app
        posts for the owner, on the head the owner labeled; else nothing, and why on stderr. Its verdict JSON makes it an
        approve for every other command, so `check`, `carry`, `rereview` and `dismissed` treat it as the reviewer's
    python3 .github/verdict.py labeled EVENTS --owner LOGIN --since TIME
        `true` when the owner's label `owner-approved` stands on the pull request after a rerun of the gates: EVENTS
        is the issue events API's reply (one object a line), the label's last event is `labeled` by the repository owner, and
        it is newer than TIME, the creation of the gates run on the head. Else `false`, and why on stderr. Only GitHub's own
        facts count, never the text of a review or a comment
    python3 .github/verdict.py owned CODEOWNERS < FILES
        the files of FILES (the pull request's files API objects, one a line) that are the owner's: CODEOWNERS gives them
        an owner, by the name or the name before a move, or they are
        limits.toml and the change is more than new keys that are not on OWNER_LIMITS, or they are AGENTS.md and
        the change touches its section "Autonomous runs" or a `## ` heading, read against the AGENTS.md of the current
        folder, main's. A pull
        request that changes one gets no auto-merge on the reviewer app's approve, the owner merges it, and its issue
        goes to Escalated; the owner's label `owner-approved` turns on auto-merge for the head the owner labeled
    python3 .github/verdict.py ended STATE --pr N [--key KEY]
        exit 0 and a notice line `PR #N is merged: KEY stays as it is` when STATE (`gh pr view N --json state --jq .state`)
        is MERGED or CLOSED, else exit 1: a step that moves an issue from a review runs this first, and moves nothing when the
        pull request ended (a review that ended after the merge once moved a Done issue back to In Progress). A state that
        gh could not read (empty) counts as open, so a failed read keeps the move

REVIEWS is the reply of `gh api repos/OWNER/REPO/pulls/N/reviews --paginate --jq '.[]'` (one review a line) or a JSON list.
GitHub counts an approval only from an account with push access (measured 2026-10-25: the reviewer app's approve
left the pull request at REVIEW_REQUIRED), so the ruleset requires this check instead of an approval. The verdict is read
from the verdict JSON in the review's body (agents/verdict.schema.json), with its `commit`: GitHub moves a review's commit
onto a later update merge (measured), so the review's own commit_id is not the commit the reviewer read. An approve the
owner dismissed (state DISMISSED) is no approve. Standard library only (DECISIONS.md "Tooling"); git runs in the current
folder.

The product's limits file comes from three optional values of the environment, whose defaults are this product's:
OWNER_LIMITS_FILE (the file's path, `limits.toml`), OWNER_LIMITS (the owner's keys, separated by spaces) and
OWNER_LIMIT_PREFIXES (the first words of the owner's keys, separated by spaces, `free_ premium_`).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterable, Mapping
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

NAME = "verdict"
VERDICT_STATES = ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")
# The review rounds at most (AGENTS.md "Autonomous runs", the owner's call, 2026-11-16: three, two before): the
# review workflow reviews a round up to this one, and a pull request still blocked after it goes to Escalated.
# tests/test_ci.py keeps the workflow's `steps.facts.outputs.round <= N` equal to it.
ROUNDS = 3
# The exits from Escalated that are always the owner's answer (agents/README.md "Who moves an issue"); an exit to
# IN_PROGRESS is one unless a request-changes caused it (`answered`)
ANSWERS = ("Todo", "Approved", "Canceled")
IN_PROGRESS = "In Progress"
FENCED = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
TITLES = {"approve": "Approved", "request-changes": "Changes requested", "needs-decision": "Needs the owner's decision"}
# The limits (the owner's call, 2026-11-04): `limits.toml` is not in CODEOWNERS. A change is the owner's when it changes or
# removes the value of an existing key, or adds or changes a key of the owner's list: the money limits, the plans' quotas
# (`free_*`, `premium_*`) and the sandbox. A new key that is not on the list merges on the verdict, and a change to a comment only.
LIMITS_FILE = os.environ.get("OWNER_LIMITS_FILE") or "limits.toml"
OWNER_LIMITS = frozenset(os.environ.get("OWNER_LIMITS", "").split() or (
    "monthly_budget", "unpriced_call_cost", "install_daily_budget", "install_daily_cap", "anon_daily_budget",
    "anon_kit_reserve", "anon_reads_without_kit", "tool_sandbox", "tool_memory_mb", "tool_timeout_seconds"))
OWNER_LIMIT_PREFIXES = tuple(os.environ.get("OWNER_LIMIT_PREFIXES", "").split() or ("free_", "premium_"))
# AGENTS.md (the owner's call, 2026-11-09): not in CODEOWNERS. A change is the owner's when it touches the section
# "## Autonomous runs" (the merge bar, the rounds, the guards: up to the next `## ` heading) or a `## ` heading line; any other
# change merges on the verdict. The section's place comes from main's copy, which the workflows check out and run in
SECTION_FILE = "AGENTS.md"
SECTION = "## Autonomous runs"
HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
# The first words of the comments that start the review rounds again on an approved pull request (`rounds_since`): the
# branch update's on a conflict and on failed gates, from the implementer app
# (.github/workflows/branch-update.yml), and `carry`'s on an update the approve does not cover, from the reviewer app.
# Now `carry` writes it for a real commit only, but the comments written before stay in the pull requests' history
CONFLICT = "The branch update found a merge conflict with main."
FAILED_GATES = "The gates failed on this approved head"
NOT_CARRIED = "The branch update changed the diff against main."
PUSHED = "A commit was pushed after the approved one"  # the reviewer app's comment on a real commit after the approve
# The review job's comment when the owner runs the review by hand with round 1 (the owner's call 2026-11-05:
# option A): `rounds_since` counts from it, so the run's own verdict is round 1 and the next ready mark round 2. The run's
# id follows in parentheses: the comment counts only for the owner's workflow_dispatch run that it names (`manual_starts`)
MANUAL = "Review: a run by hand with round 1 starts the rounds again"
MANUAL_RUN = re.compile(re.escape(MANUAL) + r" \(run (\d+)\)")
# The review workflow's `run-name` for a run by hand: the runs API gives no inputs, so the title binds the
# named run to its pull request and to round 1
MANUAL_TITLE = "Claude Code Review by hand: PR #{pr}, round 1"
# not_an_update's reason when a commit after the approved one is a real commit (a push while the review ran)
NOT_A_MERGE = "is not a merge from main"
# not_an_update's reason when the merge of main was resolved by hand: a real commit too
NOT_AUTOMATIC = "is not the automatic merge of its parents"
# The owner's Approved on GitHub (the owner's call, 2026-10-30): the label the owner adds to a pull request. GitHub
# never lets the author approve, and the owner is the git author of every pull request, so the label stands in for a review
OWNER_LABEL = "owner-approved"
SHA = re.compile(r"[0-9a-f]{40}")
# The pull request states in which a review moves no issue: Linear's Done after a merge, or the owner's close
ENDED = {"MERGED": "merged", "CLOSED": "closed"}


def read_reviews(path: Path) -> list[dict[str, Any]]:
    text = path.read_text().strip()
    if not text:
        return []
    if text.startswith("["):
        reviews: list[dict[str, Any]] = json.loads(text)
        return reviews
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def verdict_of(body: str) -> dict[str, Any]:
    """The verdict JSON of a review's body: the last fenced `json` block that holds a verdict, or {}."""
    for block in reversed(FENCED.findall(body)):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and "verdict" in data:
            return data
    return {}


def last_verdict(reviews: Iterable[Mapping[str, Any]], reviewer: str, since: str = "") -> dict[str, Any] | None:
    """The reviewer's last review with a verdict state, submitted at `since` or later (ISO 8601 compares as text)."""
    found = [r for r in reviews if (r.get("user") or {}).get("login") == reviewer and r.get("state") in VERDICT_STATES
             and str(r.get("submitted_at") or "") >= since]
    if not found:
        return None
    review = max(found, key=lambda r: str(r.get("submitted_at") or ""))
    data = verdict_of(str(review.get("body") or ""))
    return {"state": review["state"], "verdict": data.get("verdict", ""), "round": data.get("round"),
            "commit": str(data.get("commit") or review.get("commit_id") or ""), "url": str(review.get("html_url") or ""),
            "owner": str(data.get("approved_by") or ""), "at": str(review.get("submitted_at") or "")}


def approves(verdict: Mapping[str, Any] | None) -> bool:
    return verdict is not None and verdict["state"] == "APPROVED" and verdict["verdict"] == "approve"


def escalates(verdict: Mapping[str, Any] | None) -> bool:
    """Whether the verdict sent the issue to Escalated: a needs-decision, posted as a request for changes (agents/reviewer.md
    "The verdict"). A dismissed one does not: the owner took it back."""
    return verdict is not None and verdict["state"] == "CHANGES_REQUESTED" and verdict["verdict"] == "needs-decision"


# The text of the check `verdict` when the review posted no verdict, by the outcome of the review job's model step:
# a review that ended and posted nothing is told apart from a failed run and from a model step that did not run. GitHub
# skips the model step both after a failed step before it and in a round past the third, so `skipped` names both causes.
NO_VERDICT = {
    "success": "The review ended and posted no review. The run's step \"Denied tool calls of the review\" names the tools it"
               " was refused. See the run.",
    "failure": "The review run failed, and posted no verdict. See the run.",
    "cancelled": "The review run was cancelled, and posted no verdict. See the run.",
    "skipped": "The review did not run: a step before it failed, or the round is past the third, which is not reviewed (then"
               " the run comments on the pull request with what to do). See the run.",
}


def check_body(verdict: Mapping[str, Any] | None, head: str, review: str = "") -> dict[str, Any]:
    """The completed check run for `head`: success only for an approve that still has the state APPROVED. With no verdict,
    `review` (the outcome of the review job's model step) picks the text; an outcome it does not know keeps the text that
    names both cases."""
    if verdict is None:
        title = "No verdict"
        summary = NO_VERDICT.get(review, "The review run posted no verdict (a failed run, or a round past the third, which is"
                                         " not reviewed). See the run.")
    elif verdict["state"] == "DISMISSED":
        title, summary = "Dismissed", f"The reviewer's last verdict was dismissed: {verdict['url']}"
    else:
        title = TITLES.get(verdict["verdict"], "No verdict JSON")
        if verdict.get("owner") and approves(verdict):
            title = "Approved by the owner"
        elif verdict.get("round"):
            title += f", round {verdict['round']}"
        summary = f"The reviewer's verdict: {verdict['url']}"
    body: dict[str, Any] = {"name": NAME, "head_sha": head, "status": "completed",
                            "conclusion": "success" if approves(verdict) else "failure",
                            "output": {"title": title, "summary": summary}}
    if verdict and verdict["url"]:
        body["details_url"] = verdict["url"]
    return body


def dismissed_body(verdict: Mapping[str, Any] | None, head: str) -> dict[str, Any] | None:
    """After a dismissal: a failed check for `head` unless the last verdict is still an approve."""
    return None if approves(verdict) else check_body(verdict, head)


def git(*args: str) -> str:
    done = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    return done.stdout.strip() if done.returncode == 0 else ""


def is_ancestor(older: str, newer: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", older, newer], capture_output=True, check=False).returncode == 0


def not_an_update(before: str, head: str, main: str) -> str:
    """Why `head` is more than a branch update of `before`, or "" when it is only that."""
    if not is_ancestor(before, head):
        return f"{before[:7]} is not in the head's history"
    for commit in git("rev-list", "--first-parent", f"{before}..{head}").split():
        second = git("rev-parse", "-q", "--verify", f"{commit}^2")
        if not second or not is_ancestor(second, main):
            return f"{commit[:7]} {NOT_A_MERGE}"
        # the automatic merge of its parents, never a resolution by hand: a resolved conflict needs a review
        merged = subprocess.run(["git", "merge-tree", "--write-tree", f"{commit}^1", second], capture_output=True, text=True, check=False)
        if merged.returncode != 0 or merged.stdout.split()[:1] != [git("rev-parse", f"{commit}^{{tree}}")]:
            return f"the merge {commit[:7]} {NOT_AUTOMATIC}"
    return ""


def carry_body(verdict: Mapping[str, Any] | None, head: str, main: str) -> tuple[dict[str, Any] | None, str]:
    """The success check for `head` carried from the approved commit, or None and why not."""
    if verdict is None or not approves(verdict):
        return None, "the reviewer's last verdict is not an approve"
    before = git("rev-parse", "-q", "--verify", f"{verdict['commit']}^{{commit}}")
    if not before:
        return None, "the approved commit is unknown here"
    if before == head:
        return None, "the head is the approved commit"
    why = not_an_update(before, head, main)
    if why:
        return None, why
    return {"name": NAME, "head_sha": head, "status": "completed", "conclusion": "success",
            "details_url": verdict["url"],
            "output": {"title": f"Approved, carried from {before[:7]}",
                       "summary": f"A branch update: every commit since {before[:7]} is the automatic merge of main. "
                                  f"The verdict: {verdict['url']}"}}, ""


def rereview(verdict: Mapping[str, Any] | None, head: str, main: str) -> bool:
    """Whether the approve does not cover `head` and no implementer has anything to fix, so the review runs again: a real
    commit came after the approved one, a push while the review ran, or a merge of main resolved by hand. An automatic merge of main never does, also
    when main changed the pull request's lines."""
    return carry_body(verdict, head, main)[1].endswith((NOT_A_MERGE, NOT_AUTOMATIC))


def rereview_word(verdict: Mapping[str, Any] | None, head: str, main: str) -> str:
    """`pushed` for a real commit after the approved one, else `false`."""
    return "pushed" if rereview(verdict, head, main) else "false"


def rerun_body(verdict: Mapping[str, Any] | None, comments: Iterable[Mapping[str, Any]], head: str, reviewer: str, updater: str,
               gates: str) -> tuple[dict[str, Any] | None, str]:
    """The success check for `head` after a rerun of the gates passed, or None and why not. The approve must be the
    reviewer's last verdict and cover `head` itself (a changed head has the rules of `carry` and `rereview`), and the last
    comment that starts the rounds again must come after it and be the failed gates' one: a conflict is another reason.
    `gates` is the conclusion of the gates run on `head`: only `success` passes."""
    if gates != "success":
        return None, f"the gates did not pass on the head (conclusion {gates or 'unknown'})"
    if verdict is None or not approves(verdict):
        return None, "the reviewer's last verdict is not an approve"
    if verdict["commit"] != head:
        return None, "the head is not the approved commit"
    when, last = "", ""
    for comment in comments:
        stamp = str(comment.get("created_at") or "")
        if restarts(comment, reviewer, updater) and stamp > when:
            when, last = stamp, str(comment.get("body") or "")
    if not when or when < verdict["at"]:
        return None, "no comment after the approve says that the verdict failed"
    if not last.startswith(FAILED_GATES):
        return None, "the verdict failed for a reason other than the gates"
    return {"name": NAME, "head_sha": head, "status": "completed", "conclusion": "success",
            "details_url": verdict["url"],
            "output": {"title": f"Approved, the gates passed on a rerun at {head[:7]}",
                       "summary": "The gates failed on this approved head and a rerun passed on the same head: no new review. "
                                  f"The verdict: {verdict['url']}"}}, ""


def owner_review(label: str, sender: str, actor: str, owner: str, draft: bool, pr: int, head: str) -> tuple[dict[str, Any] | None, str]:
    """The approve the reviewer app posts for the owner's label, or None and why not. Only the repository owner's own label
    counts: the account that added it and the account that started the run (a re-run by another account replays the
    event) must both be the owner, never an app or another person."""
    if label != OWNER_LABEL:
        return None, f"the label {label!r} is not {OWNER_LABEL!r}"
    if not owner:
        return None, "the repository owner is unknown"
    if sender != owner:
        return None, f"{sender or 'an unknown account'} added the label, not the repository owner {owner}"
    if actor != owner:
        return None, f"{actor or 'an unknown account'} started the run, not the repository owner {owner}"
    if draft:
        return None, "the pull request is a draft: mark it ready for review, then add the label again"
    if not SHA.fullmatch(head):
        return None, f"{head!r} is not a commit"
    data = {"pull_request": pr, "commit": head, "verdict": "approve", "approved_by": owner,
            "summary": f"The owner approved this commit on GitHub with the label {OWNER_LABEL}."}
    body = (f"The owner approved {head[:7]} with the label `{OWNER_LABEL}`. This review is the owner's Approved, "
            "posted by the review workflow: it gives this commit the check `verdict`, and a later push gets the rules of any "
            "approve (agents/README.md \"The guards\").\n\n"
            f"```json\n{json.dumps(data, indent=2)}\n```\n")
    return {"commit_id": head, "event": "APPROVE", "body": body}, ""


def owner_label_stands(events: Iterable[Mapping[str, Any]], owner: str, since: str) -> tuple[bool, str]:
    """Whether the owner's label `owner-approved` is on the pull request now, put there by the repository owner after
    `since`. `events` is the issue events API's reply, which GitHub writes: the last event of the label decides
    (an `unlabeled` after the owner's `labeled` takes it away, and so does a later label by another account), its actor must
    be `owner`, and it must come after `since`, the creation of the gates run on the head: a label older than the head's
    first run was put on an older head. Never a review's or a comment's text: the reviewer app posts the model's words."""
    if not owner:
        return False, "the repository owner is unknown"
    last: Mapping[str, Any] | None = None
    for event in events:
        label = event.get("label")
        if event.get("event") in ("labeled", "unlabeled") and isinstance(label, Mapping) and label.get("name") == OWNER_LABEL:
            last = event
    if last is None or last.get("event") != "labeled":
        return False, f"the label {OWNER_LABEL!r} is not on the pull request"
    actor = last.get("actor")
    login = str(actor.get("login") or "") if isinstance(actor, Mapping) else ""
    if login != owner:
        return False, f"{login or 'an unknown account'} added the label, not the repository owner {owner}"
    when = str(last.get("created_at") or "")
    if not since or when <= since:
        return False, f"the label ({when or 'no time'}) is not newer than the head's first gates run ({since or 'unknown'})"
    return True, ""


def read_owners(text: str) -> list[tuple[str, bool]]:
    """CODEOWNERS' rules in order: (pattern, has an owner). A pattern with no owner takes the owner away again."""
    rules = []
    for line in text.splitlines():
        words = line.split("#", 1)[0].split()
        if words:
            rules.append((words[0], len(words) > 1))
    return rules


def matches(pattern: str, path: str) -> bool:
    """gitignore's rules as CODEOWNERS uses them: a slash at the start or inside anchors the pattern to the root, a slash at
    the end matches a folder only, and a pattern that matches a folder matches everything in it. A `*` here also crosses a
    slash: more files owned, never fewer. A `**/` also matches no folder: `**/foo` owns a root `foo`, `a/**/b` owns `a/b`."""
    parts = path.split("/")
    folder = pattern.endswith("/")
    bare = pattern.strip("/")
    last = len(parts) - 1 if folder else len(parts)
    if "/" in pattern.rstrip("/"):
        forms = no_folder_forms(bare)
        return any(fnmatchcase("/".join(parts[:i]), form) for i in range(1, last + 1) for form in forms)
    return any(fnmatchcase(part, bare) for part in parts[:last])


def no_folder_forms(pattern: str) -> set[str]:
    """The pattern with each `**/` kept or taken out, in every combination: fnmatch's `*` needs the slash after it."""
    head, sep, tail = pattern.partition("**/")
    if not sep:
        return {pattern}
    return {head + way + rest for rest in no_folder_forms(tail) for way in (sep, "")}


def owned(rules: list[tuple[str, bool]], paths: Iterable[str]) -> list[str]:
    """The paths whose last matching rule names an owner."""
    found = []
    for path in paths:
        hits = [owner for pattern, owner in rules if matches(pattern, path)]
        if hits and hits[-1]:
            found.append(path)
    return found


def limit_lines(patch: str, sign: str) -> dict[str, tuple[type, object]] | None:
    """The `key = value` pairs of the patch's lines that start with `sign` (`+` or `-`), comments and blank lines left out,
    or None when a line is not one key and one value (the owner reads such a change)."""
    pairs: dict[str, tuple[type, object]] = {}
    for line in patch.splitlines():
        if line.startswith(("+++", "---")) or not line.startswith(sign):
            continue
        text = line[1:].strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("["):  # a table header, [x] or [[x]]: it moves every limit below it out of the top level
            return None
        try:
            data = tomllib.loads(text)
        except ValueError:
            return None
        if len(data) != 1 or any(isinstance(v, dict) for v in data.values()):
            return None
        (key, value), = data.items()
        pairs[key] = (type(value), value)
    return pairs


def limits_owned(change: Mapping[str, Any]) -> bool:
    """Whether a change of limits.toml is the owner's: a file deleted or moved, a diff GitHub left out, a line that is
    not one `key = value` or a comment, a key whose value changed or went away, or a key of the owner's list that was added."""
    if change.get("status") in ("removed", "renamed") or change.get("previous_filename"):
        return True
    patch = change.get("patch")
    if not isinstance(patch, str):
        return True
    gone, came = limit_lines(patch, "-"), limit_lines(patch, "+")
    if gone is None or came is None:
        return True
    if any(came.get(key) != value for key, value in gone.items()):  # a key removed, or its value changed (a comment alone is not)
        return True
    return any(key not in gone and (key in OWNER_LIMITS or key.startswith(OWNER_LIMIT_PREFIXES)) for key in came)


def section_bounds(lines: list[str]) -> tuple[int, int] | None:
    """The 1-based line numbers of SECTION's heading and of the next `## ` heading (or one past the end), or None when the
    heading is not there once."""
    starts = [i for i, line in enumerate(lines, 1) if line.rstrip() == SECTION]
    if len(starts) != 1:
        return None
    end = next((i for i, line in enumerate(lines, 1) if i > starts[0] and line.startswith("## ")), len(lines) + 1)
    return starts[0], end


def hunk_offset(lines: list[str], first: int, old: list[str]) -> int | None:
    """Where the hunk's old lines (context and `-` lines) sit in main's lines: at its header's line `first` when they match
    there, else at their one place in main (main moved since the pull request's base), else None."""
    def at(start: int) -> bool:
        return lines[start - 1:start - 1 + len(old)] == old

    if not old or at(first):
        return first if old or 1 <= first <= len(lines) + 1 else None
    places = [i for i in range(1, len(lines) - len(old) + 2) if at(i)]
    return places[0] if len(places) == 1 else None


def section_owned(change: Mapping[str, Any], main_text: str | None) -> bool:
    """Whether a change of AGENTS.md is the owner's: a file deleted or moved, a diff GitHub left out, no copy of
    main's file or no section in it, a hunk that main's file does not hold once, a `## ` heading line added or removed, a
    line removed inside the section, or a line added inside it (before its last line or right before the next heading)."""
    if change.get("status") in ("removed", "renamed") or change.get("previous_filename"):
        return True
    patch = change.get("patch")
    if not isinstance(patch, str) or main_text is None:
        return True
    lines = main_text.splitlines()
    bounds = section_bounds(lines)
    if bounds is None:
        return True
    start, end = bounds
    hunks: list[tuple[int, list[str]]] = []
    for line in patch.splitlines():
        if (header := HUNK.match(line)) is not None:
            hunks.append((int(header.group(1)), []))
        elif hunks and not line.startswith("\\"):
            hunks[-1][1].append(line)
    if not hunks:
        return True
    for first, body in hunks:
        if any(line[:1] in "+-" and line[1:].startswith("## ") for line in body):
            return True
        old = [line[1:] for line in body if line[:1] in " -"]
        if not old and first == 0:  # an insertion at the file's start: the section never starts there
            continue
        at = hunk_offset(lines, first if old else first + 1, old)
        if at is None:
            return True
        here = at  # the old line number of the next old line
        for line in body:
            if line.startswith("+"):
                if start < here <= end:
                    return True
                continue
            if line.startswith("-") and start <= here < end:
                return True
            here += 1
    return False


def owned_changes(rules: list[tuple[str, bool]], changes: Iterable[Mapping[str, Any]], main_text: str | None = None) -> list[str]:
    """The names in the changes (the files API's `filename` and `previous_filename`) that are the owner's: CODEOWNERS gives
    them an owner, they are limits.toml and `limits_owned`, or they
    are AGENTS.md and `section_owned` with main's copy `main_text` (None: the change is the owner's)."""
    found = []
    for change in changes:
        names = [n for n in (change.get("filename"), change.get("previous_filename")) if isinstance(n, str) and n]
        found += [n for n in names if owned(rules, [n])
                  or (n == LIMITS_FILE and limits_owned(change)) or (n == SECTION_FILE and section_owned(change, main_text))]
    return list(dict.fromkeys(found))


def read_changes(text: str) -> list[dict[str, Any]]:
    """FILES: the files API's objects, one a line (`--jq '.[]'`). A line that is not an object is a bare path, with no
    patch."""
    changes = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            change = json.loads(line)
        except ValueError:
            change = None
        changes.append(change if isinstance(change, dict) else {"filename": line.strip()})
    return changes


def restarts(comment: Mapping[str, Any], reviewer: str, updater: str) -> bool:
    """Whether the comment is one that starts the rounds again: its author's own first words."""
    login, body = (comment.get("user") or {}).get("login"), str(comment.get("body") or "")
    return (login == updater and body.startswith((CONFLICT, FAILED_GATES))) or (login == reviewer and body.startswith((NOT_CARRIED, PUSHED)))


def manual_starts(comments: Iterable[Mapping[str, Any]], reviewer: str, runs: Iterable[Mapping[str, Any]], owner: str,
                  pr: int | None) -> list[str]:
    """The times of the reviewer app's MANUAL comments that count. The reviewer app's token is also the model's,
    so the comment alone proves nothing: it must name a run in `runs` that the event `workflow_dispatch` and the owner
    started for this pull request with round 1 (its title, MANUAL_TITLE), lie within that run's time (a run still going has
    no end yet), and be the first comment that names the run. So every reset needs the owner's own run with round 1 on this
    pull request, and a comment cannot start the rounds again by itself."""
    if pr is None:
        return []
    title = MANUAL_TITLE.format(pr=pr)
    started = {str(r.get("id")): r for r in runs
               if r.get("event") == "workflow_dispatch" and (r.get("triggering_actor") or {}).get("login") == owner
               and r.get("display_title") == title}
    seen: set[str] = set()
    times: list[str] = []
    for comment in sorted(comments, key=lambda c: str(c.get("created_at") or "")):
        if (comment.get("user") or {}).get("login") != reviewer:
            continue
        found = MANUAL_RUN.match(str(comment.get("body") or ""))
        if not found or found.group(1) in seen:
            continue
        seen.add(found.group(1))
        run, when = started.get(found.group(1)), str(comment.get("created_at") or "")
        if run is None:
            continue
        end = str(run.get("updated_at") or "") if run.get("status") == "completed" else "9999"
        if str(run.get("created_at") or "") <= when <= end:
            times.append(when)
    return times


def answered(stays: Iterable[Mapping[str, Any]], reviews: Iterable[Mapping[str, Any]], reviewer: str) -> str:
    """When the owner last answered the issue in Escalated, or "": the latest exit of `stays` (`linear.py escalated`) that
    is an answer. No agent moves an issue from Escalated to In Progress except the reviewer, right after a request-changes
    of a review that `carry` started (agents/README.md "Who moves an issue"), so an exit to In Progress with no
    such verdict during the stay is the owner's answer, merged by Linear with the claim after it."""
    mine = [r for r in reviews if (r.get("user") or {}).get("login") == reviewer and r.get("state") in VERDICT_STATES]
    found = ""
    for stay in stays:
        entered, left, to = str(stay.get("entered") or ""), str(stay.get("left") or ""), stay.get("to")
        if to == IN_PROGRESS:
            during = [r for r in mine if entered < str(r.get("submitted_at") or "") < left]
            last = max(during, key=lambda r: str(r.get("submitted_at") or ""), default=None)
            if last is not None and verdict_of(str(last.get("body") or "")).get("verdict") == "request-changes":
                continue
        elif to not in ANSWERS:
            continue
        found = max(found, left)
    return found


def rounds_since(reviews: Iterable[Mapping[str, Any]], comments: Iterable[Mapping[str, Any]], reviewer: str, updater: str,
                 since: str = "", runs: Iterable[Mapping[str, Any]] = (), owner: str = "", pr: int | None = None) -> str:
    """The time the rounds count from: `since`, or the latest comment that starts the rounds again when
    it is later and the reviewer's last verdict before it is an approve. Such a comment on a pull request that is not
    approved starts nothing, so a run cannot escape the round limit with a comment. The owner's run by hand with round 1
    starts them again whatever the verdict before it (`manual_starts`)."""
    reviews, comments = list(reviews), list(comments)
    start = since
    for when in manual_starts(comments, reviewer, runs, owner, pr):
        start = max(start, when)
    for comment in comments:
        when = str(comment.get("created_at") or "")
        if not restarts(comment, reviewer, updater):
            continue
        before = [r for r in reviews if (r.get("user") or {}).get("login") == reviewer and r.get("state") in VERDICT_STATES
                  and str(r.get("submitted_at") or "") < when]
        last = max(before, key=lambda r: str(r.get("submitted_at") or ""), default=None)
        if last is not None and last.get("state") == "APPROVED" and when > start:
            start = when
    return start


def ended(state: str, pr: int, key: str = "") -> str:
    """The log line when the pull request merged or closed, so that a move from its review would undo Linear's Done or the
    owner's close; empty while it is open, and for a state that gh could not read."""
    word = ENDED.get(state.strip().upper())
    return f"PR #{pr} is {word}: {key or 'its issue'} stays as it is" if word else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("owned").add_argument("codeowners", type=Path)
    one = sub.add_parser("owner")
    for flag in ("--label", "--sender", "--actor", "--owner", "--draft", "--head"):
        one.add_argument(flag, required=True)
    one.add_argument("--pr", type=int, required=True)
    for name in ("check", "carry", "rereview", "dismissed"):
        one = sub.add_parser(name)
        one.add_argument("reviews", type=Path)
        one.add_argument("--reviewer", required=True)
        one.add_argument("--head", required=True)
    one = sub.add_parser("rerun")
    one.add_argument("reviews", type=Path)
    one.add_argument("comments", type=Path)
    for flag in ("--reviewer", "--updater", "--head", "--gates"):
        one.add_argument(flag, required=True)
    one = sub.add_parser("ended")
    one.add_argument("state")
    one.add_argument("--pr", type=int, required=True)
    one.add_argument("--key", default="")
    one = sub.add_parser("labeled")
    one.add_argument("events", type=Path)
    one.add_argument("--owner", required=True)
    one.add_argument("--since", required=True)
    one = sub.add_parser("escalates")
    one.add_argument("reviews", type=Path)
    one.add_argument("--reviewer", required=True)
    one = sub.add_parser("answered")
    one.add_argument("stays", type=Path)
    one.add_argument("reviews", type=Path)
    one.add_argument("--reviewer", required=True)
    one = sub.add_parser("since")
    one.add_argument("reviews", type=Path)
    one.add_argument("comments", type=Path)
    one.add_argument("--reviewer", required=True)
    one.add_argument("--updater", required=True)
    one.add_argument("--runs", type=Path, help="the review workflow's runs: its MANUAL comments count only with them")
    one.add_argument("--owner", default="")
    one.add_argument("--pr", type=int, help="the pull request whose run by hand a MANUAL comment must name")
    for name in ("check", "escalates", "since"):
        sub.choices[name].add_argument("--since", default="")
    sub.choices["check"].add_argument("--review", default="")
    for name in ("carry", "rereview"):
        sub.choices[name].add_argument("--main", default="origin/main")
    args = parser.parse_args()
    if args.command == "owned":
        main_copy = Path(SECTION_FILE)  # the workflows run in a checkout of main
        main_text = main_copy.read_text() if main_copy.is_file() else None
        for path in owned_changes(read_owners(args.codeowners.read_text()), read_changes(sys.stdin.read()), main_text):
            print(path)
        return 0
    if args.command == "owner":
        approve, why = owner_review(args.label, args.sender, args.actor, args.owner, args.draft == "true", args.pr, args.head)
        if approve is None:
            print(f"no approve: {why}", file=sys.stderr)
        else:
            print(json.dumps(approve))
        return 0
    if args.command == "ended":
        line = ended(args.state, args.pr, args.key)
        if line:
            print(f"::notice::{line}")
        return 0 if line else 1
    if args.command == "labeled":
        stands, why = owner_label_stands(read_reviews(args.events), args.owner, args.since)
        print("true" if stands else "false")
        if not stands:
            print(f"the label lifts nothing: {why}", file=sys.stderr)
        return 0
    reviews = read_reviews(args.reviews)
    if args.command == "answered":
        print(answered(read_reviews(args.stays), reviews, args.reviewer))
        return 0
    if args.command == "since":
        runs = read_reviews(args.runs) if args.runs else []
        print(rounds_since(reviews, read_reviews(args.comments), args.reviewer, args.updater, args.since, runs, args.owner, args.pr))
        return 0
    if args.command == "rerun":
        rerun, why = rerun_body(last_verdict(reviews, args.reviewer), read_reviews(args.comments), args.head, args.reviewer,
                                args.updater, args.gates)
        if rerun is None:
            print(f"nothing to write: {why}", file=sys.stderr)
        else:
            print(json.dumps(rerun))
        return 0
    if args.command == "check":
        print(json.dumps(check_body(last_verdict(reviews, args.reviewer, args.since), args.head, args.review)))
        return 0
    if args.command == "escalates":
        print("true" if escalates(last_verdict(reviews, args.reviewer, args.since)) else "false")
        return 0
    if args.command == "rereview":
        print(rereview_word(last_verdict(reviews, args.reviewer), args.head, args.main))
        return 0
    if args.command == "dismissed":
        failed = dismissed_body(last_verdict(reviews, args.reviewer), args.head)
        if failed is not None:
            print(json.dumps(failed))
        return 0
    body, why = carry_body(last_verdict(reviews, args.reviewer), args.head, args.main)
    if body is None:
        print(f"nothing to carry: {why}", file=sys.stderr)
    else:
        print(json.dumps(body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
