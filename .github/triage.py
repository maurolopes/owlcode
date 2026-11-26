"""The mechanical checks of the triage (agents/planner.md "The triage", step 3): rules over data that need no judgment, one
pure function each, over the issues as `linear.py list` gives them and the open pull requests as `gh pr list` gives them.
`linear.py triage-checks TEAM` reads both and prints `report`; the triage session then does only the checks that need
judgment. The orchestrator's schedule can call the same functions.

Each check returns its cases, oldest key first: `{"key", "title", "reason"}`. Standard library only (DECISIONS.md
"Tooling"); no network here, so the tests give fake issues.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

# The same patterns as linear.py's ORIGIN and TRIGGER (a test keeps them equal): the origin line of an issue an agent filed,
# and a parked issue's trigger line (agents/planner.md "The issue").
ORIGIN = re.compile(r"(Found in |From:)")
TRIGGER = "Take it when:"
KEY = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,7}-\d{1,6})(?![0-9])")
HANDS_OFF = "hands-off"
FAST_LANE = "fast-lane"
QUESTION = "Question:"
FAST_SIZES = ("XS", "S")
STALE_PUSH = timedelta(hours=24)
STALE_QUESTION = timedelta(days=3)
NEEDS_OWNER = re.compile(r"^#+\s*Needs the owner\s*$", re.IGNORECASE)
# The field written as a line (agents/planner.md "The issue": `**Needs the owner:** ...`), with an optional bullet and bold,
# the colon inside or outside the bold: the text after it is the field
NEEDS_OWNER_LINE = re.compile(r"^[-*•\s]*Needs the owner(?:\**\s*:|:\s*\**)\**\s*(.*)$", re.IGNORECASE)
HEADING = re.compile(r"^#+\s")
# What a "Needs the owner" section holds when it asks nothing of the owner
EMPTY_NEEDS = {"", "none", "none.", "nothing", "nothing.", "-", "n/a"}

Case = dict[str, str]


def case(issue: Mapping[str, Any], reason: str) -> Case:
    return {"key": str(issue["identifier"]), "title": str(issue.get("title") or ""), "reason": reason}


def number(issue: Mapping[str, Any]) -> int:
    found = re.search(r"\d+$", str(issue["identifier"]))
    return int(found.group()) if found else 0


def in_state(issues: Iterable[Mapping[str, Any]], state: str) -> list[Mapping[str, Any]]:
    return sorted((i for i in issues if i.get("state") == state), key=number)


def when(text: object) -> datetime | None:
    """An ISO 8601 time from Linear or GitHub (`2026-11-11T13:38:29.908Z`), or None."""
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def lines(issue: Mapping[str, Any]) -> list[str]:
    return str(issue.get("description") or "").splitlines()


def origin_line(issue: Mapping[str, Any]) -> bool:
    """The text's first line that is not blank is an origin line (`Found in KEY`, `From: ...`)."""
    first = next((line.strip().lstrip("*").strip() for line in lines(issue) if line.strip()), "")
    return bool(ORIGIN.match(first))


def has_trigger(issue: Mapping[str, Any]) -> bool:
    """The text has a `Take it when:` line, also the bold `**Take it when:**` of older issues."""
    return any(line.lstrip().lstrip("*").startswith(TRIGGER) for line in lines(issue))


def needs_owner(issue: Mapping[str, Any]) -> str:
    """The text of the field "Needs the owner": a section under that heading, or a line `Needs the owner: TEXT` (with the
    lines right under it when TEXT is empty); "" when the issue has none or it says none."""
    held: list[str] = []
    inside = ""
    for line in lines(issue):
        bare = line.strip()
        field = NEEDS_OWNER_LINE.match(bare) if not inside else None
        if not inside and NEEDS_OWNER.match(bare):
            inside = "section"
        elif field:
            if field.group(1).strip():
                held.append(field.group(1).strip())
                break
            inside = "line"
        elif inside and (HEADING.match(bare) or (inside == "line" and not bare and held)):
            break
        elif inside:
            held.append(bare)
    text = "\n".join(line for line in held if line).strip()
    bare = text.lstrip("*-• ").strip()
    return "" if bare.casefold() in EMPTY_NEEDS else text


def makes_pull_request(issue: Mapping[str, Any]) -> bool:
    """The owner's own work (`hands-off`) and a `Question:` issue have no diff (agents/planner.md "The issue")."""
    return HANDS_OFF not in issue.get("labels", []) and not str(issue.get("title") or "").startswith(QUESTION)


def triage_unset(issues: Iterable[Mapping[str, Any]]) -> list[Case]:
    """An issue in Triage with no priority, or with no size when it becomes a pull request."""
    found: list[Case] = []
    for issue in in_state(issues, "Triage"):
        missing = []
        if not issue.get("priority"):
            missing.append("no priority")
        if not issue.get("size") and makes_pull_request(issue):
            missing.append("no size")
        if missing:
            found.append(case(issue, ", ".join(missing)))
    return found


def area_count(issues: Iterable[Mapping[str, Any]], areas: Collection[str]) -> list[Case]:
    """An open issue with no Area label, or with more than one."""
    found: list[Case] = []
    for issue in sorted(issues, key=number):
        held = [label for label in issue.get("labels", []) if label in areas]
        if len(held) != 1:
            found.append(case(issue, "no Area label" if not held else "Area labels " + ", ".join(held)))
    return found


def pull_requests_of(key: str, pulls: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The pull requests whose branch names the key (`abc-12-short-name`): the key in a branch name links them."""
    found = []
    for pull in pulls:
        named = KEY.search(str(pull.get("headRefName") or ""))
        if named and named.group(1).upper() == key.upper():
            found.append(pull)
    return found


def last_push(pull: Mapping[str, Any]) -> datetime | None:
    """The time of the pull request's last commit (agents/planner.md: `gh pr view N --json commits`)."""
    times = [t for c in pull.get("commits") or [] if (t := when(c.get("committedDate"))) is not None]
    return max(times) if times else None


def draft_stack(issue: Mapping[str, Any]) -> bool:
    """The newest comment that starts with "Draft stack" says "Draft stack on" (agents/implementer.md "A draft stack"): such an
    issue has a branch and, on purpose, no pull request yet."""
    marks = [str(c.get("body") or "") for c in issue.get("comments", []) if str(c.get("body") or "").startswith("Draft stack")]
    return bool(marks) and marks[-1].startswith("Draft stack on")


def in_progress_idle(issues: Iterable[Mapping[str, Any]], pulls: Sequence[Mapping[str, Any]], now: datetime) -> list[Case]:
    """An issue in In Progress with no open pull request (the key in a branch name), or whose pull request had no push in
    24 hours. A draft stack has no pull request on purpose: it is not listed."""
    found: list[Case] = []
    for issue in in_state(issues, "In Progress"):
        own = pull_requests_of(str(issue["identifier"]), pulls)
        if not own:
            if not draft_stack(issue):
                found.append(case(issue, "no open pull request"))
            continue
        pushes = [t for pull in own if (t := last_push(pull)) is not None]
        if pushes and now - max(pushes) > STALE_PUSH:
            numbers = ", ".join(f"#{pull.get('number')}" for pull in own)
            hours = int((now - max(pushes)).total_seconds() // 3600)
            found.append(case(issue, f"no push for {hours} hours ({numbers})"))
    return found


def open_blockers(issue: Mapping[str, Any], closed: Collection[str]) -> list[str]:
    return [str(b["key"]) for b in issue.get("blocked_by", []) if b.get("state") not in closed]


def backlog_unblocked(issues: Iterable[Mapping[str, Any]], closed: Collection[str],
                      canceled: Collection[str] = ()) -> list[Case]:
    """An issue in Backlog that has blockers, all of them closed (`closed`: the team's completed and canceled states). A
    canceled blocker (`canceled`: the team's states of type `canceled`; Duplicate is no such state) can void the issue's
    premise, so the reason asks a person to check it."""
    found: list[Case] = []
    for issue in in_state(issues, "Backlog"):
        blockers = issue.get("blocked_by", [])
        if blockers and not open_blockers(issue, closed):
            keys = ", ".join(f"{b['key']} ({b.get('state')})" for b in blockers)
            gone = [str(b["key"]) for b in blockers if b.get("state") in canceled]
            if gone:
                gone_keys = ", ".join(f"{b['key']} ({b.get('state')})" for b in blockers if b.get("state") in canceled)
                others = ", ".join(f"{b['key']} ({b.get('state')})" for b in blockers if b.get("state") not in canceled)
                reason = f"a blocker was canceled: {gone_keys}; check that the issue still holds, or cancel it"
                found.append(case(issue, reason + (f". Other blockers: {others}" if others else "")))
            else:
                found.append(case(issue, "every blocker is closed: " + keys))
    return found


def backlog_untriggered(issues: Iterable[Mapping[str, Any]]) -> list[Case]:
    """An issue in Backlog with no blocker and no `Take it when:` line. Whether a trigger is one that a person can check is
    the session's judgment, not this check's."""
    return [case(issue, "no blocker and no `Take it when:` line") for issue in in_state(issues, "Backlog")
            if not issue.get("blocked_by") and not has_trigger(issue)]


def escalated_stale(issues: Iterable[Mapping[str, Any]], now: datetime) -> list[Case]:
    """An issue in Escalated for more than 3 days: `since` is when it entered the state (`linear.py list --since`)."""
    found: list[Case] = []
    for issue in in_state(issues, "Escalated"):
        entered = when(issue.get("since") or issue.get("created"))
        if entered is not None and now - entered > STALE_QUESTION:
            found.append(case(issue, f"in Escalated for {(now - entered).days} days, since {entered.date().isoformat()}"))
    return found


def fast_lane_broken(issues: Iterable[Mapping[str, Any]]) -> list[Case]:
    """An issue in Todo with `fast-lane` that fails a rule of agents/README.md "The fast lane" that data can check: its size
    XS or S (rule 3), no `hands-off` (rule 5), an origin line (rule 1), an empty "Needs the owner" (rule 4). Rule 2 (the
    issue it came from had no `fast-lane`) and the owner's files of rule 4 need the diff or the origin: the session's."""
    found: list[Case] = []
    for issue in in_state(issues, "Todo"):
        if FAST_LANE not in issue.get("labels", []):
            continue
        failed = []
        if issue.get("size") not in FAST_SIZES:
            failed.append(f"size {issue.get('size') or 'none'}, not XS or S")
        if HANDS_OFF in issue.get("labels", []):
            failed.append("hands-off")
        if not origin_line(issue):
            failed.append("no origin line (`Found in KEY`, `From: ...`) first")
        if needs_owner(issue):
            failed.append("\"Needs the owner\" is not empty")
        if failed:
            found.append(case(issue, "; ".join(failed)))
    return found


CHECKS: dict[str, str] = {
    "triage_unset": "an issue in Triage with no priority, or no size when it becomes a pull request",
    "area_count": "an open issue with no Area label, or more than one",
    "in_progress_idle": "an issue in In Progress with no pull request, or no push in 24 hours",
    "backlog_unblocked": "an issue in Backlog whose blockers are all closed",
    "backlog_untriggered": "an issue in Backlog with no blocker and no `Take it when:` line",
    "escalated_stale": "an issue in Escalated for more than 3 days",
    "fast_lane_broken": "an issue in Todo with `fast-lane` that fails a rule of the fast lane that data can check",
}


def report(issues: Sequence[Mapping[str, Any]], pulls: Sequence[Mapping[str, Any]], areas: Collection[str],
           closed: Collection[str], now: datetime, canceled: Collection[str] = ()) -> dict[str, Any]:
    """Every issue in Triage in full, and the cases of each check (an empty list when it found none), with what was read."""
    found: dict[str, list[Case]] = {
        "triage_unset": triage_unset(issues),
        "area_count": area_count(issues, areas),
        "in_progress_idle": in_progress_idle(issues, pulls, now),
        "backlog_unblocked": backlog_unblocked(issues, closed, canceled),
        "backlog_untriggered": backlog_untriggered(issues),
        "escalated_stale": escalated_stale(issues, now),
        "fast_lane_broken": fast_lane_broken(issues),
    }
    return {"read": {"open_issues": len(issues), "open_pull_requests": len(pulls), "at": now.isoformat(timespec="seconds")},
            "triage": in_state(issues, "Triage"),
            "checks": {name: {"rule": rule, "cases": found[name]} for name, rule in CHECKS.items()}}
