#!/usr/bin/env python3
"""Linear, for the runs without a person (agents/README.md "The record") and for the skills in any harness: read an issue,
move it, comment, file a follow-up or a planner's issues, list a team's issues for the triage.

    python3 .github/linear.py issue KEY                        the issue as JSON: identifier, title, state, labels, team, url, text
    python3 .github/linear.py state KEY NAME                   move the issue to the workflow state NAME of its team
    python3 .github/linear.py comment KEY --body-file FILE     add a comment to the issue
    python3 .github/linear.py trigger KEY --when TEXT          put the line `Take it when: TEXT` in the issue's text, right
                                                               after its origin line (`Found in KEY`, `From: ...`), or first
                                                               with none; it replaces a `Take it when:` line the text holds
    python3 .github/linear.py unlabel KEY NAME                 remove the label from the issue; fails when the issue has
                                                               none (a run's claim of `restart`: another run took it first)
    python3 .github/linear.py label KEY NAME                   add the label to the issue (a difficulty label that is too
                                                               low); passes when the issue has it already, fails
                                                               when the team and the workspace have no such label
    python3 .github/linear.py create TEAM --title TITLE --body-file FILE [--label NAME]... [--state NAME]
                                     [--priority urgent|high|medium|low] [--size XS|S|M|L] [--assignee me|NAME]
                                     [--related KEY]... [--blocks KEY]... [--blocked-by KEY]... [--project NAME]
                                                               a new issue, in Triage unless --state names another state;
                                                               --assignee: `me` is the key's user (the owner: agents/README.md
                                                               "Setting up a product"), else a name, display name or email;
                                                               --related, --blocks and --blocked-by link it to the
                                                               issues named;
                                                               --project puts it in the project of that name
    python3 .github/linear.py follow-up TEAM --area AREA --from "PR #N, round R, HEAD, depth D" --body-file FILE
                                     --priority urgent|high|medium|low [--fast-lane]
                                                               the reviewer's follow-up findings (the file: one checklist line
                                                               each) into the open collecting issue of the Area, or a new one
                                                               (`follow_up` below); prints its key, `created` or
                                                               `appended`, and its url
    python3 .github/linear.py link KEY [--related OTHER]... [--blocks OTHER]... [--blocked-by OTHER]...
                                                               link an issue that exists to others that exist (a run whose
                                                               base is not on main yet: `--blocked-by` the issue that holds
                                                               it, agents/implementer.md "While you work"); every key is
                                                               read first, and a link that exists is not made again
    python3 .github/linear.py project TEAM --name NAME --body-file FILE
                                                               a new project of the team, the file its content (a feature's
                                                               specification: agents/planner.md); refused when the name is taken
    python3 .github/linear.py escalated KEY                    the issue's stays in "Escalated" as JSON, oldest first: when it
                                                               entered and left (ISO 8601; "" when the history does not reach
                                                               the entry) and the state it went to; `verdict.py answered`
                                                               reads which exits were the owner's answers
    python3 .github/linear.py key-from TEXT...                 the first issue key in the texts (a branch name, a title), or nothing
    python3 .github/linear.py list TEAM [--state NAME]... [--key KEY]... [--since] [--updated-since DAYS]
                                                               the team's issues in the states named (every open one by
                                                               default) as JSON, with labels, the Area labels (`areas`), priority, size, relations,
                                                               comments (every page of each); --key reads the issues named
                                                               instead, in any state; --since adds when each entered its state;
                                                               --updated-since keeps those updated in the last DAYS days
    python3 .github/linear.py triage-checks TEAM [--repo OWNER/NAME]
                                                               the mechanical checks of the triage as JSON (agents/planner.md "The
                                                               triage", step 3; .github/triage.py): every issue in Triage in
                                                               full, and the cases of each check over every open issue, with
                                                               each issue's relations read to the last page; the open pull
                                                               requests come from `gh` (each with its last commit); it only reads
    python3 .github/linear.py triage-set KEY [--priority urgent|high|medium|low] [--size XS|S|M|L]
                                                               the priority and size of an issue in Triage; refused in any
                                                               other state (the triage: agents/planner.md)
    python3 .github/linear.py setup TEAM [--dry-run]           the team's states, labels, git automations and triage inbox as
                                                               agents/README.md "Setting up a product" asks; safe to run again

The API key is in the environment, never a file (AGENTS.md "How to work here": no key in a file on the owner's machine;
on a runner it is the Actions secret): the variable of the team that the command names, by its TEAM or by its issue
key's prefix (AGENTS.md "The teams", `TEAM_KEYS`), and LINEAR_API_KEY for a command that names no team. The
product's Area labels come from the file that LINEAR_AREAS_FILE names, when it is set (`read_areas`). One GraphQL endpoint, every reply checked; any failure exits 1 with the reason, so
a workflow step fails loud. Every connection in a query names its page size (`first`): Linear caps a query's complexity
at 10000, an unsized connection counts as 50 rows, and nested ones multiply (the first setup run, 2026-10-25). Standard library only (DECISIONS.md "Tooling"). The tests fake `post`, never the network.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

ENDPOINT = "https://api.linear.app/graphql"
TIMEOUT = 30
ESCALATED = "Escalated"
FOLLOW_UP = "follow-up"
FAST_LANE = "fast-lane"
# The first line of an issue that an agent files says where it came from (agents/planner.md "The issue"), and a parked
# issue's trigger comes right after it.
ORIGIN = re.compile(r"(Found in |From:)")
TRIGGER = "Take it when:"
KEY = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,7}-\d{1,6})(?![0-9])")
# The variable of each team's Linear key (AGENTS.md "The teams"; a test keeps the two equal). A key reaches
# one workspace. Every team of this workspace, and a command that names no team, takes DEFAULT_KEY. A product that moves to
# a workspace of its own names its team and its variable in TEAM_KEYS.
DEFAULT_KEY = "LINEAR_API_KEY"
TEAM_KEYS: dict[str, str] = {}

# What `setup` makes (agents/README.md "The issue states", "Who moves an issue"). Triage is the state Linear adds with the
# triage inbox. The git automations are Linear's five events: a draft pull request, a pull request opened (the ready mark of
# a draft, when it fires there), a review requested, ready to merge, merged. None is no rule: without a rule for an event
# Linear does nothing, and it refuses a rule for every branch whose state is null (the first run, 2026-10-25); a null
# state is only for a rule that overrides the default on one branch.
STATES = (("Todo", "unstarted"), ("In Progress", "started"), ("In Review", "started"), (ESCALATED, "started"),
          ("Approved", "started"), ("Done", "completed"))
# `needs-judgment` and `intricate` are the difficulty labels: the orchestrator picks a run's model by them
LABELS = ("follow-up", "fast-lane", "hands-off", "restart", "needs-judgment", "intricate")
# The label group Area: one per issue, the part of the product it changes most (agents/README.md "The issue states"). The
# areas and their texts are the product's: LINEAR_AREAS_FILE, when set, names a JSON object of area name to text that
# replaces DEFAULT_AREAS (`read_areas`).
AREA = "Area"
DEFAULT_AREAS = {
    "backend": "The server's code: the logic, the store, the API, the emails, the command line.",
    "frontend": "The browser app: the pages, the styles, the browser tests.",
    "workflow": "How changes are made: agents/, .github/, the hooks, the gates, the skills.",
    "infra": "The deploy, the servers, the backups and the outside services.",
    "orchestrator": "orchestrator/: the process that starts the runs, its install and its service.",
    "docs": "Markdown only: the rules, the decisions, the plans.",
    "business": "No code: legal, tax, accounts, the launch, the owner's own use.",
}


def read_areas(environ: Mapping[str, str]) -> dict[str, str]:
    """The areas and their texts: the JSON object of the file that LINEAR_AREAS_FILE names, else DEFAULT_AREAS. A file
    that cannot be read, or that holds no object of texts, stops the script: a wrong area list would make wrong labels."""
    where = environ.get("LINEAR_AREAS_FILE", "")
    if not where:
        return dict(DEFAULT_AREAS)
    try:
        found = json.loads(Path(where).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise SystemExit(f"linear.py: LINEAR_AREAS_FILE cannot be read as JSON ({err})") from None
    if not isinstance(found, dict) or not found or not all(isinstance(v, str) and k for k, v in found.items()):
        raise SystemExit("linear.py: LINEAR_AREAS_FILE holds no JSON object of area name to text")
    return {str(name): str(text) for name, text in found.items()}


AREAS = read_areas(os.environ)
AREA_TEXT = "The part of the product an issue changes most. One per issue. Two runs on the same area at once are likely to conflict."
# Linear's priority numbers, and the values behind its T-shirt estimates (read from the team, 2026-10-26). The issue's
# author sets both in Triage; the owner's triage is the last word (agents/README.md "The issue states").
PRIORITIES = {"urgent": 1, "high": 2, "medium": 3, "low": 4}
SIZES = {"XS": 1, "S": 2, "M": 3, "L": 5}
# The owner's order of the issues within a priority, as an issue's `manual_order`: Linear's `prioritySortOrder`,
# "the order of the item in relation to other items in the organization, when ordered by priority" (the API's schema);
# `sortOrder` is the order of a view ordered by hand. Read from the API on 2026-11-10: the team's Todo issues had both
# fields, each a number, the lower shown first. A drag inside one priority group of a view ordered by priority must set
# `prioritySortOrder`; no drag checked it yet (a run cannot drag in Linear's app): the owner's check is the issue's
# "Done when". When the drag sets `sortOrder` instead, this read takes that field.
AUTOMATIONS: dict[str, str | None] = {"draft": "In Progress", "start": "In Review", "review": None, "mergeable": None, "merge": "Done"}
COLORS = {"Todo": "#e2e2e2", "In Progress": "#f2c94c", "In Review": "#bb87fc", ESCALATED: "#eb5757", "Approved": "#4cb782",
          "Done": "#5e6ad2", "follow-up": "#5e6ad2", "fast-lane": "#4cb782", "hands-off": "#eb5757", "restart": "#f2994a",
          "needs-judgment": "#d97757", "intricate": "#26b5ce", AREA: "#4ea7fc",
          "backend": "#26b5ce", "frontend": "#bb87fc", "workflow": "#f2c94c", "infra": "#4cb782", "docs": "#95a2b3",
          "business": "#f2994a", "orchestrator": "#d97757"}
# The connections of an issue that `list` reads after their first page (Linear.rest), each with the fields of one row: the
# same fields as the Issues query asks for them.
MORE = {
    "labels": "labels(first: 50, after: $after) { nodes { name parent { name } } pageInfo { hasNextPage endCursor } }",
    "relations": "relations(first: 50, after: $after) { nodes { type relatedIssue { identifier state { name } } } pageInfo { hasNextPage endCursor } }",
    "inverseRelations": "inverseRelations(first: 50, after: $after) { nodes { type issue { identifier state { name } } } pageInfo { hasNextPage endCursor } }",
    "comments": "comments(first: 50, after: $after) { nodes { createdAt body user { name } } pageInfo { hasNextPage endCursor } }",
}
LABELS_QUERY = """query Labels($names: [String!]) { issueLabels(first: 50, filter: { name: { in: $names } }) { nodes { id name
          isGroup team { key } parent { name } } } }"""

# The reviewer's follow-ups collect in one open issue for each Area (agents/README.md "Follow-ups"), up to COLLECT
# findings: six is the issue's number (six times the median follow-up of 18 changed lines is about 110 lines, size S), and
# the owner may choose another. A finding is one checklist line; each review's origin is one `From: PR #N` line at the top.
COLLECT = 6
FINDING = re.compile(r"^\s*- \[[ xX]\] ", re.MULTILINE)
FROM_PR = re.compile(r"^From:\s*PR #(\d+)")
SECTION = re.compile(r"^## From PR #(\d+)\s*$")
# Two reviews of one Area can append at the same moment, and an issue's text has no compare-and-swap: the later write
# drops the earlier one's section. So each write is read back after SETTLE_SECONDS, and written again, up to WRITES times,
# while a section that the writer wanted is missing.
SETTLE_SECONDS = 2.0
WRITES = 3
ORIGIN_LINE = re.compile(r"^PR #(\d+), round \d+, \S+, depth \d+$")

Post = Callable[[str, Mapping[str, Any]], dict[str, Any]]
Pulls = Callable[[], list[dict[str, Any]]]


class Failed(Exception):
    """A step that cannot go on: the message says why."""


def issue_key(*texts: str) -> str | None:
    """The first issue key in the texts, upper case (`abc-12-fix-title` -> `ABC-12`), or None."""
    for text in texts:
        found = KEY.search(text or "")
        if found:
            return found.group(1).upper()
    return None


def with_trigger(text: str, when: str) -> str:
    """The issue's text with the line `Take it when: WHEN` right after its origin line, or first when it has none. Every
    trigger line the text held goes, also the bold `**Take it when:**` of older issues: an issue has one trigger
    (agents/planner.md "The issue"). After a collecting follow-up's block of `From: PR` lines, not inside it: the
    orchestrator and the reviewer read the whole block. Blank lines that the removal leaves at the top go too, so the origin line is first."""
    when = " ".join(when.split())
    if not when:
        raise Failed("the trigger is empty: give the event or the number that a person can check")
    lines = [line for line in text.splitlines() if not line.lstrip().lstrip("*").startswith(TRIGGER)]
    while lines and not lines[0].strip():
        lines.pop(0)
    at = 1 if lines and ORIGIN.match(lines[0].lstrip()) else 0
    if at and FROM_PR.match(lines[0]):  # a collecting follow-up's `From: PR` lines are one block
        while at < len(lines) and FROM_PR.match(lines[at]):
            at += 1
    lines.insert(at, f"{TRIGGER} {when}")
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def findings(text: str) -> int:
    """The findings in a follow-up's text: its checklist lines, ticked or not."""
    return len(FINDING.findall(text))


def with_origin(text: str, origin: str, body: str) -> str:
    """The collecting issue's text with one more review's findings: its `From:` line right under the `From: PR` lines at
    the top (the first stays the issue's origin: agents/planner.md), and its findings at the end under `## From PR #N`."""
    number = ORIGIN_LINE.match(origin)
    lines = text.rstrip().splitlines()
    at = 0
    while at < len(lines) and FROM_PR.match(lines[at]):
        at += 1
    lines.insert(at, f"From: {origin}")
    return "\n".join(lines) + f"\n\n## From PR #{number.group(1) if number else '?'}\n\n{body.strip()}\n"


def entries(text: str) -> list[tuple[str, str]]:
    """A collecting issue's reviews: for each `From: PR #N` line at the top, that line and the `## From PR #N` section
    that holds its findings ("" when the text has none). Two origins of one pull request take its sections in order."""
    lines = text.splitlines()
    origins: list[str] = []
    while len(origins) < len(lines) and FROM_PR.match(lines[len(origins)]):
        origins.append(lines[len(origins)])
    sections: list[tuple[str, list[str]]] = []
    for line in lines[len(origins):]:
        if (head := SECTION.match(line)) is not None:
            sections.append((head.group(1), [line]))
        elif sections:
            sections[-1][1].append(line)
    pairs: list[tuple[str, str]] = []
    for origin in origins:
        number = FROM_PR.match(origin)
        at = next((i for i, (n, _) in enumerate(sections) if number and n == number.group(1)), None)
        pairs.append((origin, "\n".join(sections.pop(at)[1]).strip() if at is not None else ""))
    return pairs


def restored(text: str, wanted: Sequence[tuple[str, str]]) -> str:
    """The text with each wanted review that it lacks: its `From:` line under the others at the top, its section at the
    end. A review is there when its `From:` line is (the line names the pull request, the round and the head)."""
    there = {origin for origin, _ in entries(text)}
    lines = text.rstrip().splitlines()
    at = 0
    while at < len(lines) and FROM_PR.match(lines[at]):
        at += 1
    missing = [(origin, section) for origin, section in wanted if origin not in there]
    lines[at:at] = [origin for origin, _ in missing]
    return "\n".join(lines) + "".join(f"\n\n{section}" for _, section in missing if section) + "\n"


def started(issue: Mapping[str, Any]) -> bool:
    """A run started on the issue: it has a run line (`Run: MODEL, effort EFFORT`, agents/implementer.md step 4). The
    orchestrator writes it when it starts the session, before the run reads the issue and long before its claim moves
    the issue to In Progress: from then on, findings added to it would be missed or worked on too
    early. A run line of an earlier run counts too: a new issue takes the findings, which is the safe side."""
    return any(str(c.get("body") or "").startswith("Run: ") for c in issue.get("comments") or [])


def linked(links: list[tuple[str, dict[str, Any]]], kind: str) -> list[dict[str, str]]:
    """The other issues of the relations of one type ("blocks", "related", "duplicate"), each with its state."""
    return [{"key": other["identifier"], "state": other["state"]["name"]} for t, other in links if t == kind]


def load_triage() -> ModuleType:
    """.github/triage.py, beside this file (`python3 -I` puts no folder of the script on the path)."""
    spec = importlib.util.spec_from_file_location("github_triage", Path(__file__).with_name("triage.py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PULLS_QUERY = """query($owner: String!, $repo: String!, $endCursor: String) { repository(owner: $owner, name: $repo) {
  pullRequests(states: OPEN, first: 100, after: $endCursor) { nodes { number headRefName isDraft
  commits(last: 1) { nodes { commit { committedDate } } } } pageInfo { hasNextPage endCursor } } } }"""


def open_pulls(repo: str | None = None, run: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> list[dict[str, Any]]:
    """Every open pull request (every page), each with its number, branch, draft mark and last commit, in the shape of `gh pr
    list --json number,headRefName,isDraft,commits`. One GraphQL query reads only the last commit: `gh pr list --limit
    500 --json commits` asks for more nodes than GitHub allows (it refused, 2026-11-13). Failed when gh cannot read them.
    `repo` is OWNER/NAME, the repository of the issues' team (AGENTS.md "The teams"); with none, gh reads the
    repository of the current folder. `run` is for the tests: subprocess.run, read at the call."""
    owner, name = repo.split("/", 1) if repo else ("{owner}", "{repo}")
    command = ["gh", "api", "graphql", "--paginate", "-F", f"owner={owner}", "-F", f"repo={name}", "-f", f"query={PULLS_QUERY}",
               "--jq", ".data.repository.pullRequests.nodes[]"]
    try:
        done = (run or subprocess.run)(command, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Failed(f"gh did not run: {type(e).__name__}") from None
    if done.returncode != 0:
        raise Failed("gh could not read the open pull requests: " + done.stderr.strip()[:300])
    pulls: list[dict[str, Any]] = []
    for line in done.stdout.splitlines():
        if line.strip():
            node = json.loads(line)
            commits = [c["commit"] for c in (node.get("commits") or {}).get("nodes") or []]
            pulls.append({"number": node["number"], "headRefName": node["headRefName"], "isDraft": node["isDraft"], "commits": commits})
    return pulls


def key_variable(team: str | None) -> str:
    """The variable that holds the key of the team (`TEAM_KEYS`); DEFAULT_KEY for any other team and for no team."""
    return TEAM_KEYS.get((team or "").upper(), DEFAULT_KEY)


def command_team(args: argparse.Namespace) -> str | None:
    """The team that a command names: its TEAM, else the prefix of its issue key (`state OWL-3 Done` names OWL); None for a
    command that names neither."""
    team = getattr(args, "team", None)
    if isinstance(team, str):
        return team.upper()
    key = issue_key(args.key) if isinstance(getattr(args, "key", None), str) else None
    return key.split("-", 1)[0] if key else None


def api_key(environ: Mapping[str, str], team: str | None = None) -> str:
    """The key of the team (`key_variable`) from the environment; Failed when it is not there. Never from a file (the
    owner's rule, 2026-10-24): a runner has the key as an Actions secret, and a session on the owner's machine reaches Linear
    through its MCP server instead (agents/README.md "The record")."""
    variable = key_variable(team)
    key = environ.get(variable, "").strip()
    if key:
        return key
    whose = f" (the key of the team {team.upper()}: AGENTS.md \"The teams\")" if team and variable != DEFAULT_KEY else ""
    raise Failed(f"no {variable} in the environment{whose}")


def http_post(key: str) -> Post:
    """A `post(query, variables)` against the API with this key; a GraphQL error or an HTTP error is Failed."""

    def post(query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        body = json.dumps({"query": query, "variables": dict(variables)}).encode()
        request = urllib.request.Request(ENDPOINT, data=body, method="POST",
                                         headers={"Content-Type": "application/json", "Authorization": key})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as reply:
                parsed: dict[str, Any] = json.loads(reply.read().decode())
        except urllib.error.HTTPError as e:
            said = e.read().decode(errors="replace")[:300]
            e.close()
            raise Failed(f"Linear answered {e.code}: {said}") from None
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            reason = str(getattr(e, "reason", e)) or type(e).__name__  # the cause, never the request: the key is in it
            if "CERTIFICATE_VERIFY_FAILED" in reason:
                reason += " (this Python has no root certificates: AGENTS.md 'Traps')"
            raise Failed(f"Linear did not answer: {reason}") from None
        if parsed.get("errors"):
            raise Failed("Linear refused: " + "; ".join(str(err.get("message")) for err in parsed["errors"]))
        data = parsed.get("data")
        return dict(data) if isinstance(data, dict) else {}

    return post


class Linear:
    """The few operations the runs need, each one query or mutation."""

    def __init__(self, post: Post, sleep: Callable[[float], None] | None = None) -> None:
        self.post = post
        self.sleep = sleep if sleep is not None else time.sleep

    def issue(self, key: str) -> dict[str, Any]:
        """The issue's fields the runs read; Failed when the key names no issue."""
        query = """query Issue($id: String!) { issue(id: $id) { id identifier title description url
          state { name type } team { id key } labels(first: 50) { nodes { name } } } }"""
        found = self.post(query, {"id": key}).get("issue")
        if not isinstance(found, dict):
            raise Failed(f"no issue {key}")
        return {"id": found["id"], "identifier": found["identifier"], "title": found["title"],
                "description": found.get("description") or "", "url": found.get("url"),
                "state": found["state"]["name"], "team": found["team"]["key"], "team_id": found["team"]["id"],
                "labels": [node["name"] for node in found.get("labels", {}).get("nodes", [])]}

    def team(self, key: str) -> dict[str, Any]:
        """The team's id and its workflow states (name, type, id); Failed when the key names no team."""
        query = """query Team($key: String!) { teams(first: 1, filter: { key: { eq: $key } }) { nodes { id key
          states(first: 50) { nodes { id name type position } } } } }"""
        nodes = self.post(query, {"key": key}).get("teams", {}).get("nodes", [])
        if not nodes:
            raise Failed(f"no team {key}")
        team = nodes[0]
        states = sorted(team.get("states", {}).get("nodes", []), key=lambda s: float(s.get("position") or 0))
        return {"id": team["id"], "states": states}

    def state_id(self, team: Mapping[str, Any], name: str) -> str:
        for state in team["states"]:
            if state["name"].casefold() == name.casefold():
                return str(state["id"])
        names = ", ".join(s["name"] for s in team["states"])
        raise Failed(f"no state {name!r} in the team (its states: {names})")

    def move(self, key: str, name: str) -> str:
        """Move the issue to the state named `name`; the state's name as Linear has it."""
        issue = self.issue(key)
        team = self.team(issue["team"])
        mutation = """mutation Move($id: String!, $state: String!) { issueUpdate(id: $id, input: { stateId: $state }) {
          success issue { state { name } } } }"""
        done = self.post(mutation, {"id": issue["id"], "state": self.state_id(team, name)}).get("issueUpdate", {})
        if not done.get("success"):
            raise Failed(f"issueUpdate refused for {key}")
        return str(done["issue"]["state"]["name"])

    def comment(self, key: str, body: str) -> None:
        issue = self.issue(key)
        mutation = "mutation Comment($id: String!, $body: String!) { commentCreate(input: { issueId: $id, body: $body }) { success } }"
        if not self.post(mutation, {"id": issue["id"], "body": body}).get("commentCreate", {}).get("success"):
            raise Failed(f"commentCreate refused for {key}")

    def trigger(self, key: str, when: str) -> None:
        """Put the trigger line in the issue's text (`with_trigger`); one read and one write, so an edit of the text in
        between is lost: the run that parks an issue is the only one on it (agents/implementer.md)."""
        issue = self.issue(key)
        text = with_trigger(issue["description"], when)
        mutation = "mutation Describe($id: String!, $text: String!) { issueUpdate(id: $id, input: { description: $text }) { success } }"
        if not self.post(mutation, {"id": issue["id"], "text": text}).get("issueUpdate", {}).get("success"):
            raise Failed(f"issueUpdate refused for {key}")

    def unlabel(self, key: str, name: str) -> None:
        """Remove the label `name` from the issue; Failed when the issue does not carry it. The read and the removal are two
        requests: two runs that read within one round trip both pass (agents/README.md "Until an orchestrator exists")."""
        issue = self.issue(key)
        if name not in issue["labels"]:
            raise Failed(f"{key} has no label {name!r}")
        label = self.label_ids(issue["team"], [name])[0]
        mutation = "mutation Unlabel($id: String!, $label: String!) { issueRemoveLabel(id: $id, labelId: $label) { success } }"
        if not self.post(mutation, {"id": issue["id"], "label": label}).get("issueRemoveLabel", {}).get("success"):
            raise Failed(f"issueRemoveLabel refused for {key}")

    def label(self, key: str, name: str) -> bool:
        """Add the label `name` to the issue; False when the issue carries it already (nothing is sent), Failed when the
        team and the workspace have no such label."""
        issue = self.issue(key)
        if name in issue["labels"]:
            return False
        label = self.label_ids(issue["team"], [name])[0]
        mutation = "mutation Label($id: String!, $label: String!) { issueAddLabel(id: $id, labelId: $label) { success } }"
        if not self.post(mutation, {"id": issue["id"], "label": label}).get("issueAddLabel", {}).get("success"):
            raise Failed(f"issueAddLabel refused for {key}")
        return True

    def label_ids(self, team_key: str, names: list[str]) -> list[str]:
        """The ids of the labels named, the team's own or the workspace's; Failed when one does not exist."""
        if not names:
            return []
        nodes = self.post(LABELS_QUERY, {"names": names}).get("issueLabels", {}).get("nodes", [])
        ids: list[str] = []
        for name in names:
            fits = [n for n in nodes if n["name"] == name and (n.get("team") is None or n["team"].get("key") == team_key)]
            if not fits:
                raise Failed(f"no label {name!r} for team {team_key} (make it in Linear first)")
            ids.append(str(fits[0]["id"]))
        return ids

    def user_id(self, who: str) -> str:
        """The id of the user `who` names: `me` is the API key's user, anything else must match exactly one user's name,
        display name or email (case ignored); Failed otherwise."""
        if who.casefold() == "me":
            viewer = self.post("query Viewer { viewer { id } }", {}).get("viewer")
            if not isinstance(viewer, dict):
                raise Failed("no user for the API key")
            return str(viewer["id"])
        query = """query Users($who: String!) { users(first: 50, filter: { or: [{ name: { eqIgnoreCase: $who } },
          { displayName: { eqIgnoreCase: $who } }, { email: { eqIgnoreCase: $who } }] }) { nodes { id name } } }"""
        nodes = self.post(query, {"who": who}).get("users", {}).get("nodes", [])
        if len(nodes) != 1:
            names = ", ".join(str(n["name"]) for n in nodes)
            raise Failed(f"no user {who!r}" if not nodes else f"{who!r} names {len(nodes)} users ({names}): give the email")
        return str(nodes[0]["id"])

    def relate(self, issue_id: str, other: str, kind: str) -> None:
        """Link the issue to the issue keyed `other`: `related`, `blocks` (the issue blocks the other one), or `blocked-by`
        (the other one blocks the issue: Linear's `blocks` from the other side)."""
        mutation = "mutation Relate($input: IssueRelationCreateInput!) { issueRelationCreate(input: $input) { success } }"
        other_id = self.issue(other)["id"]
        if kind == "blocked-by":
            relation = {"issueId": other_id, "relatedIssueId": issue_id, "type": "blocks"}
        else:
            relation = {"issueId": issue_id, "relatedIssueId": other_id, "type": kind}
        if not self.post(mutation, {"input": relation}).get("issueRelationCreate", {}).get("success"):
            raise Failed(f"issueRelationCreate refused for {other}")

    def create(self, team_key: str, title: str, body: str, labels: list[str], state: str | None,
               priority: str | None = None, size: str | None = None, assignee: str | None = None,
               related: Sequence[str] = (), blocks: Sequence[str] = (), project: str | None = None,
               blocked_by: Sequence[str] = ()) -> dict[str, Any]:
        """A new issue in the team, in Triage (or the state named), with the labels, the priority, the size and the
        assignee, linked as related to the issues `related` names, as a blocker of the ones `blocks` names and as blocked
        by the ones `blocked_by` names, in the project named `project`; its identifier and url. Every name and key is checked before the issue exists, so a typo files nothing. Every link
        is tried; when some fail after the issue exists, the failure names the issue and each missing link, so a run
        links them instead of filing the issue again."""
        team = self.team(team_key)
        issue_input: dict[str, Any] = {"teamId": team["id"], "title": title, "description": body}
        if assignee:
            issue_input["assigneeId"] = self.user_id(assignee)
        for key in [*related, *blocks, *blocked_by]:
            self.issue(key)
        if project:
            found = self.projects(project)
            if not found:
                raise Failed(f"no project {project!r} (make it first: `project TEAM --name NAME --body-file FILE`)")
            issue_input["projectId"] = str(found[0]["id"])
        if priority:
            issue_input["priority"] = PRIORITIES[priority]
        if size:
            issue_input["estimate"] = SIZES[size]
        if state:
            issue_input["stateId"] = self.state_id(team, state)
        else:
            triage = [s for s in team["states"] if s.get("type") == "triage"]
            if triage:
                issue_input["stateId"] = str(triage[0]["id"])
        ids = self.label_ids(team_key, labels)
        if ids:
            issue_input["labelIds"] = ids
        mutation = "mutation Create($input: IssueCreateInput!) { issueCreate(input: $input) { success issue { id identifier url } } }"
        done = self.post(mutation, {"input": issue_input}).get("issueCreate", {})
        if not done.get("success"):
            raise Failed(f"issueCreate refused for team {team_key}")
        made = done["issue"]
        missing: list[str] = []  # the issue exists now: a run that tries again must link it, never file a second one
        for kind, keys in (("related", related), ("blocks", blocks), ("blocked-by", blocked_by)):
            for key in keys:
                try:
                    self.relate(str(made["id"]), key, kind)
                except Failed as e:
                    missing.append(f"{kind} {key} ({e})")
        if missing:
            raise Failed(f"{made['identifier']} {made.get('url') or ''} was created, but these links were not: "
                         + "; ".join(missing) + ". Link them by hand, do not create the issue again")
        return {"identifier": made["identifier"], "url": made.get("url")}

    def follow_up(self, team_key: str, area: str, origin: str, body: str, priority: str, fast_lane: bool) -> dict[str, Any]:
        """One review's follow-up findings (`body`, one checklist line each) into the open collecting issue of the Area, or a
        new one. The collecting issue carries `follow-up` and AREA and has not started: in Todo with `fast-lane`
        when these findings pass the fast lane's test (`fast_lane`), else in Triage without it, so a finding never enters
        the fast lane by another's test, nor an issue that the owner moved out of Triage. It holds fewer than COLLECT
        findings, and these must fit: else a new issue takes them all (the seventh finding opens a new issue). The oldest
        such issue takes them: one more `From:` line at its top, the findings under `## From PR #N`, and its priority raised
        when this one is higher. An issue with a run line takes none (`started`): a run starts before it claims the
        issue. Its state and its findings are read again right before the write: a run may have claimed it since, or
        another review filled it. The write is read back after SETTLE_SECONDS: when another review's write removed a
        section that this one wanted (its own, or one that the text held before), it is written again with all of them,
        up to WRITES times, then a new issue takes the findings. Two writes that land more than SETTLE_SECONDS
        apart, the later from a text read before the earlier, still lose the earlier: the text has no compare-and-swap."""
        found = ORIGIN_LINE.match(origin)
        if not found:
            raise Failed(f"--from {origin!r} is not `PR #N, round R, HEAD, depth D`")
        count = findings(body)
        if not count:
            raise Failed("the body holds no finding: one checklist line (`- [ ] R1-2 path:line: ...`) each")
        state = "Todo" if fast_lane else "Triage"
        open_ones = sorted((i for i in self.issues(team_key, [state])
                            if FOLLOW_UP in i["labels"] and area in i["labels"] and (FAST_LANE in i["labels"]) == fast_lane
                            and findings(str(i["description"])) + count <= COLLECT and not started(i)),
                           key=lambda i: str(i["created"]))
        mine = entries(with_origin("", origin, body).lstrip())[0]
        mutation = "mutation Append($id: String!, $input: IssueUpdateInput!) { issueUpdate(id: $id, input: $input) { success } }"
        for candidate in open_ones:
            wanted: list[tuple[str, str]] = []
            for written in range(WRITES + 1):
                now = self.issue(str(candidate["identifier"]))
                if now["state"] != state:
                    break  # a run started it, or the owner moved it, since the list was read
                text = str(now["description"] or "")
                there = {line for line, _ in entries(text)}
                if written and all(line in there for line, _ in wanted):
                    return {"identifier": now["identifier"], "url": now.get("url"), "done": "appended"}
                if mine[0] not in there and findings(text) + count > COLLECT:
                    break  # read again: another review's findings filled it since the list was read
                if written == WRITES:
                    break  # each write was removed by another one: a new issue takes the findings
                wanted = wanted or [*entries(text), mine]
                update: dict[str, Any] = {"description": restored(text, wanted)}
                if PRIORITIES[priority] < PRIORITIES.get(str(candidate.get("priority")), 0) or not candidate.get("priority"):
                    update["priority"] = PRIORITIES[priority]
                if not self.post(mutation, {"id": now["id"], "input": update}).get("issueUpdate", {}).get("success"):
                    raise Failed(f"issueUpdate refused for {now['identifier']}")
                self.sleep(SETTLE_SECONDS)  # a write of another review that read the text before ours lands meanwhile
        made = self.create(team_key, f"Follow-ups: {area}, from PR #{found.group(1)}",
                           with_origin("", origin, body).lstrip(), [FOLLOW_UP, area, *([FAST_LANE] if fast_lane else [])],
                           "Todo" if fast_lane else None, priority, "S")
        return {**made, "done": "created"}

    def link(self, key: str, related: Sequence[str] = (), blocks: Sequence[str] = (),
             blocked_by: Sequence[str] = ()) -> list[str]:
        """Link an issue that exists to others that exist: a run that finds its base on another issue's open
        pull request links its issue as blocked by that issue, and the start reads the link (agents/implementer.md "While
        you work"). Every key is read before the first link, so a typo links nothing. A link that the issue has already
        is not made again, so a second run can give the same command. Every other link is tried; a failure names each
        missing one. One line for each link asked."""
        wanted = [(kind, other.upper()) for kind, others in (("related", related), ("blocks", blocks), ("blocked-by", blocked_by))
                  for other in others]
        if not wanted:
            raise Failed("link needs --related, --blocks or --blocked-by")
        issue = self.issue(key)
        for _, other in wanted:
            if other == str(issue["identifier"]).upper():
                raise Failed(f"{other} cannot link to itself")
            self.issue(other)
        now = self.issues(str(issue["team"]), [], keys=[str(issue["identifier"])])
        fields = {"related": "related", "blocks": "blocks", "blocked-by": "blocked_by"}
        have = {(kind, str(row["key"]).upper()) for kind, name in fields.items() for row in (now[0][name] if now else [])}
        lines: list[str] = []
        missing: list[str] = []
        for kind, other in wanted:
            if (kind, other) in have:
                lines.append(f"{kind} {other}: linked already")
                continue
            try:
                self.relate(str(issue["id"]), other, kind)
                lines.append(f"{kind} {other}: linked")
            except Failed as e:
                missing.append(f"{kind} {other} ({e})")
        if missing:
            raise Failed(f"{issue['identifier']}: these links were not made: " + "; ".join(missing))
        return lines

    def projects(self, name: str) -> list[dict[str, Any]]:
        """The projects whose name is `name`, case ignored: Linear's project names are unique in a workspace."""
        query = "query Projects($name: String!) { projects(first: 5, filter: { name: { eqIgnoreCase: $name } }) { nodes { id name url } } }"
        return list(self.post(query, {"name": name}).get("projects", {}).get("nodes", []))

    def project(self, team_key: str, name: str, content: str) -> dict[str, Any]:
        """A new project of the team with the content given; its name and url. Failed when a project has the name already:
        the planner adds to a project that exists, never a second copy (agents/planner.md "Before you write")."""
        if self.projects(name):
            raise Failed(f"a project {name!r} exists already: add the issues to it")
        team = self.team(team_key)
        mutation = """mutation ProjectCreate($input: ProjectCreateInput!) { projectCreate(input: $input) {
          success project { name url } } }"""
        done = self.post(mutation, {"input": {"name": name, "teamIds": [team["id"]], "content": content}}).get("projectCreate", {})
        if not done.get("success"):
            raise Failed(f"projectCreate refused for {name!r}")
        return {"name": done["project"]["name"], "url": done["project"].get("url")}

    def escalated(self, key: str) -> list[dict[str, str]]:
        """The issue's stays in Escalated, oldest first: `entered` and `left` (ISO 8601) and `to`, the state it went to. A
        stay that has not ended is left out, and `entered` is "" when the history (its first 250 entries) does not reach
        the move into Escalated. Linear merges two close moves by one actor into one entry: the owner's answer
        and the claim after it can show as one move from Escalated to In Progress, so the exit's state alone does not
        say who moved the issue (`verdict.py answered` decides)."""
        query = """query History($id: String!) { issue(id: $id) { history(first: 250) { nodes { createdAt
          fromState { name } toState { name } } } } }"""
        found = self.post(query, {"id": key}).get("issue")
        if not isinstance(found, dict):
            raise Failed(f"no issue {key}")
        moves = sorted([(str(n["createdAt"]), (n.get("fromState") or {}).get("name"), (n.get("toState") or {}).get("name"))
                        for n in found.get("history", {}).get("nodes", []) if n.get("fromState") or n.get("toState")],
                       key=lambda move: move[0])
        stays: list[dict[str, str]] = []
        entered = ""
        for when, before, after in moves:
            if after == ESCALATED and before != ESCALATED:
                entered = when
            elif before == ESCALATED and after != ESCALATED:
                stays.append({"entered": entered, "left": when, "to": str(after or "")})
                entered = ""
        return stays

    def issues(self, team_key: str, states: list[str], since: bool = False, keys: Sequence[str] = (),
               updated_days: int | None = None) -> list[dict[str, Any]]:
        """The team's issues in the states named, or every open one (not completed, not canceled), as the triage reads
        them (agents/planner.md "The triage"): text, labels, priority, size, relations and comments. `keys` reads the
        issues named instead, in any state (a skill reads one issue with its comments and relations). `since` adds when
        each issue entered its state, one request per issue. `updated_days` keeps the issues updated in the last so many days
        (the filter `updatedAt: {gt: ...}` goes to Linear, so a closed issue of an old date is never read)."""
        numbers: list[int] = []
        for key in keys:
            prefix, _, number = key.upper().partition("-")
            if prefix != team_key or not number.isdigit():
                raise Failed(f"{key} is not an issue key of team {team_key}")
            numbers.append(int(number))
        scope: dict[str, Any] = {"team": {"key": {"eq": team_key}}}
        if numbers:
            scope["number"] = {"in": numbers}
        if states or not numbers:
            scope["state"] = {"name": {"in": states}} if states else {"type": {"nin": ["completed", "canceled"]}}
        if updated_days is not None:
            scope["updatedAt"] = {"gt": (datetime.now(UTC) - timedelta(days=updated_days)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        query = """query Issues($filter: IssueFilter, $after: String) { issues(first: 20, after: $after, filter: $filter) {
          nodes { identifier title description url priority estimate prioritySortOrder createdAt updatedAt state { name } assignee { name }
          project { name } labels(first: 10) { nodes { name parent { name } } pageInfo { hasNextPage endCursor } }
          relations(first: 20) { nodes { type relatedIssue { identifier state { name } } } pageInfo { hasNextPage endCursor } }
          inverseRelations(first: 20) { nodes { type issue { identifier state { name } } } pageInfo { hasNextPage endCursor } }
          comments(first: 30) { nodes { createdAt body user { name } } pageInfo { hasNextPage endCursor } } }
          pageInfo { hasNextPage endCursor } } }"""
        found: list[dict[str, Any]] = []
        after: str | None = None
        while True:
            page = self.post(query, {"filter": scope, "after": after}).get("issues", {})
            found += page.get("nodes", [])
            if not page.get("pageInfo", {}).get("hasNextPage"):
                break
            after = page["pageInfo"]["endCursor"]
        sizes = {value: name for name, value in SIZES.items()}
        priorities = {value: name for name, value in PRIORITIES.items()}
        listed: list[dict[str, Any]] = []
        for n in found:
            rows = {name: self.rest(n["identifier"], name, n.get(name) or {}) for name in MORE}
            out_links = [(r["type"], r["relatedIssue"]) for r in rows["relations"]]
            in_links = [(r["type"], r["issue"]) for r in rows["inverseRelations"]]
            duplicate = linked(out_links, "duplicate")
            issue = {"identifier": n["identifier"], "title": n["title"], "state": n["state"]["name"],
                     "labels": [label["name"] for label in rows["labels"]],
                     "areas": [label["name"] for label in rows["labels"] if (label.get("parent") or {}).get("name") == AREA],
                     "priority": priorities.get(n.get("priority") or 0), "size": sizes.get(n.get("estimate") or 0),
                     "manual_order": n.get("prioritySortOrder"),
                     "project": (n.get("project") or {}).get("name"), "assignee": (n.get("assignee") or {}).get("name"),
                     "url": n.get("url"), "created": n["createdAt"], "updated": n["updatedAt"],
                     "blocked_by": linked(in_links, "blocks"), "blocks": linked(out_links, "blocks"),
                     "related": linked(out_links, "related") + linked(in_links, "related"),
                     "duplicate_of": duplicate[0]["key"] if duplicate else None,
                     "description": n.get("description") or "",
                     "comments": sorted(({"author": (c.get("user") or {}).get("name"), "created": c["createdAt"], "body": c["body"]}
                                         for c in rows["comments"]), key=lambda c: str(c["created"]))}
            if since:
                issue["since"] = self.entered(n["identifier"], issue["state"]) or n["createdAt"]
            listed.append(issue)
        return listed

    def rest(self, key: str, name: str, first: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Every row of the issue's connection `name`: the first page as the Issues query read it, then each page after it,
        one request each. An issue with more comments than the first page once showed the triage only part of its
        discussion, and the owner's answer can be the missing comment."""
        rows: list[dict[str, Any]] = list(first.get("nodes") or [])
        info = first.get("pageInfo") or {}
        query = "query More($id: String!, $after: String) { issue(id: $id) { " + MORE[name] + " } }"
        while info.get("hasNextPage"):
            found = self.post(query, {"id": key, "after": info.get("endCursor")}).get("issue")
            if not isinstance(found, dict):
                raise Failed(f"no issue {key}")
            page = found.get(name) or {}
            if not page.get("nodes"):  # a next page with no rows ends the loop: a cursor that does not move never loops
                break
            rows += page["nodes"]
            info = page.get("pageInfo") or {}
        return rows

    def entered(self, key: str, state: str) -> str | None:
        """When the issue last moved into the state named (ISO 8601), or None when no move led there (made in it)."""
        query = """query Entered($id: String!) { issue(id: $id) { history(first: 250) { nodes { createdAt toState { name } } } } }"""
        found = self.post(query, {"id": key}).get("issue")
        if not isinstance(found, dict):
            raise Failed(f"no issue {key}")
        moves = [str(n["createdAt"]) for n in found.get("history", {}).get("nodes", []) if (n.get("toState") or {}).get("name") == state]
        return max(moves) if moves else None

    def triage_checks(self, team_key: str, pulls: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """The report of .github/triage.py over every open issue of the team, each read in full (`issues`), with when each
        issue in Escalated entered it (one request each); `closed` is the team's completed, canceled and duplicate states
        (a state Duplicate has the type `duplicate`, which `issues` reads as open), so a blocker in any of them is closed,
        and an issue in one of them is not checked; `canceled` is the states of type `canceled`. Reads only."""
        triage = load_triage()
        states = self.team(team_key)["states"]
        closed = [str(s["name"]) for s in states if s.get("type") in ("completed", "canceled", "duplicate")]
        canceled = [str(s["name"]) for s in states if s.get("type") == "canceled"]
        issues = [issue for issue in self.issues(team_key, []) if issue["state"] not in closed]
        for issue in issues:
            if issue["state"] == ESCALATED:
                issue["since"] = self.entered(issue["identifier"], ESCALATED) or issue["created"]
        found: dict[str, Any] = triage.report(issues, pulls, set(AREAS), closed, datetime.now(UTC), canceled)
        return found

    def triage_set(self, key: str, priority: str | None, size: str | None) -> None:
        """Set the priority and the size of an issue in Triage; Failed in any other state: once the owner moved it out, no
        agent changes them (agents/README.md "The issue states")."""
        issue = self.issue(key)
        if issue["state"].casefold() != "triage":
            raise Failed(f"{key} is in {issue['state']}, not Triage: its priority and size are the owner's now")
        update: dict[str, Any] = {}
        if priority:
            update["priority"] = PRIORITIES[priority]
        if size:
            update["estimate"] = SIZES[size]
        if not update:
            raise Failed("nothing to set: give --priority, --size or both")
        mutation = "mutation Set($id: String!, $input: IssueUpdateInput!) { issueUpdate(id: $id, input: $input) { success } }"
        if not self.post(mutation, {"id": issue["id"], "input": update}).get("issueUpdate", {}).get("success"):
            raise Failed(f"issueUpdate refused for {key}")

    def team_setup(self, key: str) -> dict[str, Any]:
        """The team as `setup` compares it: its states, its own labels, its git automations for every branch, the triage switch."""
        query = """query Setup($key: String!) { teams(first: 1, filter: { key: { eq: $key } }) { nodes { id key triageEnabled
          states(first: 50) { nodes { id name type position } }
          gitAutomationStates(first: 50) { nodes { id event state { id name } targetBranch { id } } } } } }"""
        nodes = self.post(query, {"key": key}).get("teams", {}).get("nodes", [])
        if not nodes:
            raise Failed(f"no team {key}")
        team = nodes[0]
        return {"id": team["id"], "triage": bool(team.get("triageEnabled")),
                "states": sorted(team.get("states", {}).get("nodes", []), key=lambda s: float(s.get("position") or 0)),
                "automations": [n for n in team.get("gitAutomationStates", {}).get("nodes", []) if not n.get("targetBranch")]}

    def setup_labels(self, key: str, team_id: str, dry: bool) -> list[str]:
        """The labels of "The issue states": `follow-up`, `fast-lane`, `hands-off`, `restart`, the difficulty labels
        `needs-judgment` and `intricate`, and the group Area with one label per area
        inside it. A label counts when it is the team's or the workspace's; an area's label only inside the group."""
        names = [*LABELS, AREA, *AREAS]
        existing = [n for n in self.post(LABELS_QUERY, {"names": names}).get("issueLabels", {}).get("nodes", [])
                    if n.get("team") is None or n["team"].get("key") == key]
        lines: list[str] = []
        group: str | None = None
        no_group = False  # the name Area is taken by a label that is not the group: no area label can go in
        for name in names:
            parent = AREA if name in AREAS else None
            fits = [n for n in existing if n["name"] == name and bool(n.get("isGroup")) == (name == AREA)
                    and (n.get("parent") or {}).get("name") == parent]
            where = f" (in {AREA})" if parent else ""
            if fits:
                lines.append(f"label {name}{where}: exists" + (" (the workspace's)" if fits[0].get("team") is None else ""))
                if name == AREA:
                    group = str(fits[0]["id"])
                continue
            if any(n["name"] == name for n in existing):  # Linear keeps one name per team: setup never makes a second
                wanted = "the group" if name == AREA else f"a label in {AREA}" if parent else "a label outside any group"
                lines.append(f"label {name}: exists, but not as {wanted}: move it in Linear, then run setup again")
                no_group = no_group or name == AREA
                continue
            if parent and no_group:
                lines.append(f"label {name}{where}: waits for the group")
                continue
            lines.append(f"label {name}{where}: new")
            if dry:
                continue
            label: dict[str, Any] = {"teamId": team_id, "name": name, "color": COLORS[name]}
            if name == AREA:
                label |= {"isGroup": True, "description": AREA_TEXT}
            elif parent:
                label |= {"parentId": group, "description": AREAS[name]}
            mutation = "mutation LabelCreate($input: IssueLabelCreateInput!) { issueLabelCreate(input: $input) { success issueLabel { id } } }"
            made = self.post(mutation, {"input": label}).get("issueLabelCreate", {})
            if not made.get("success"):
                raise Failed(f"issueLabelCreate refused for {name}")
            if name == AREA:
                group = str(made["issueLabel"]["id"])
        return lines

    def setup(self, key: str, dry: bool) -> list[str]:
        """Make the team what agents/README.md "Setting up a product" asks for: the states, the labels, the git automations,
        the triage inbox. Each step reads first and changes only what differs, so a second run changes nothing. The lines
        say what exists and what changes; `dry` sends no mutation."""
        team = self.team_setup(key)
        lines: list[str] = []
        ids = {str(s["name"]).casefold(): str(s["id"]) for s in team["states"]}
        for name, kind in STATES:
            if name.casefold() in ids:
                lines.append(f"state {name}: exists")
                continue
            position = max([float(s.get("position") or 0) for s in team["states"] if s.get("type") == kind] or [0.0]) + 1
            lines.append(f"state {name}: new ({kind})")
            team["states"].append({"id": f"new:{name}", "name": name, "type": kind, "position": position})
            if dry:
                continue
            mutation = """mutation StateCreate($input: WorkflowStateCreateInput!) { workflowStateCreate(input: $input) {
              success workflowState { id } } }"""
            state_input = {"teamId": team["id"], "name": name, "type": kind, "color": COLORS[name], "position": position}
            made = self.post(mutation, {"input": state_input}).get("workflowStateCreate", {})
            if not made.get("success"):
                raise Failed(f"workflowStateCreate refused for {name}")
            ids[name.casefold()] = str(made["workflowState"]["id"])
        lines += self.setup_labels(key, team["id"], dry)
        rules = {str(r["event"]): r for r in team["automations"]}
        for event, wanted in AUTOMATIONS.items():
            rule = rules.get(event)
            current = (rule.get("state") or {}).get("name") if rule else None
            if str(current or "").casefold() == (wanted or "").casefold():
                lines.append(f"automation {event}: {wanted or 'none'} (unchanged)")
                continue
            lines.append(f"automation {event}: {current or 'none'} -> {wanted or 'none'}")
            if dry:
                continue
            if rule is not None:
                mutation = "mutation AutomationDelete($id: String!) { gitAutomationStateDelete(id: $id) { success } }"
                if not self.post(mutation, {"id": rule["id"]}).get("gitAutomationStateDelete", {}).get("success"):
                    raise Failed(f"gitAutomationStateDelete refused for {event}")
            if wanted is None:
                continue
            mutation = """mutation AutomationCreate($input: GitAutomationStateCreateInput!) { gitAutomationStateCreate(input: $input) {
              success } }"""
            rule_input = {"teamId": team["id"], "event": event, "stateId": ids[wanted.casefold()]}
            if not self.post(mutation, {"input": rule_input}).get("gitAutomationStateCreate", {}).get("success"):
                raise Failed(f"gitAutomationStateCreate refused for {event}")
        if team["triage"]:
            lines.append("triage: on")
        else:
            lines.append("triage: off -> on")
            if not dry:
                mutation = "mutation TeamUpdate($id: String!, $input: TeamUpdateInput!) { teamUpdate(id: $id, input: $input) { success } }"
                if not self.post(mutation, {"id": team["id"], "input": {"triageEnabled": True}}).get("teamUpdate", {}).get("success"):
                    raise Failed("teamUpdate refused for the triage inbox")
        return lines


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="linear.py", description=(__doc__ or "").split("\n\n")[0])
    sub = top.add_subparsers(dest="command", required=True)
    sub.add_parser("issue", help="the issue as JSON").add_argument("key")
    state = sub.add_parser("state", help="move the issue to a workflow state of its team")
    state.add_argument("key")
    state.add_argument("name")
    comment = sub.add_parser("comment", help="add a comment to the issue")
    comment.add_argument("key")
    comment.add_argument("--body-file", required=True, type=Path)
    create = sub.add_parser("create", help="a new issue, in Triage unless --state says otherwise")
    create.add_argument("team")
    create.add_argument("--title", required=True)
    create.add_argument("--body-file", required=True, type=Path)
    create.add_argument("--label", action="append", default=[], metavar="NAME")
    create.add_argument("--state", default=None, metavar="NAME")
    create.add_argument("--priority", default=None, choices=PRIORITIES)
    create.add_argument("--size", default=None, choices=SIZES, help="the T-shirt estimate: XS, S, M; L means split it")
    create.add_argument("--assignee", default=None, metavar="me|NAME", help="`me` (the key's user) or a name, display name or email")
    create.add_argument("--related", action="append", default=[], metavar="KEY", help="link the new issue to KEY as related")
    create.add_argument("--blocks", action="append", default=[], metavar="KEY", help="the new issue blocks KEY")
    create.add_argument("--blocked-by", action="append", default=[], metavar="KEY", help="KEY blocks the new issue")
    create.add_argument("--project", default=None, metavar="NAME", help="the project of that name, which exists already")
    follow = sub.add_parser("follow-up", help="a review's follow-up findings into the Area's collecting issue, or a new one")
    follow.add_argument("team")
    follow.add_argument("--area", required=True, metavar="AREA", help="the Area label of the pull request's issue")
    follow.add_argument("--from", dest="origin", required=True, metavar="ORIGIN", help='"PR #N, round R, HEAD, depth D"')
    follow.add_argument("--body-file", required=True, type=Path, help="the findings, one checklist line each")
    follow.add_argument("--priority", required=True, choices=PRIORITIES)
    follow.add_argument("--fast-lane", action="store_true", help="the findings pass the fast lane's test: Todo, `fast-lane`")
    link = sub.add_parser("link", help="link an issue that exists to others that exist")
    link.add_argument("key")
    link.add_argument("--related", action="append", default=[], metavar="OTHER", help="KEY is related to OTHER")
    link.add_argument("--blocks", action="append", default=[], metavar="OTHER", help="KEY blocks OTHER")
    link.add_argument("--blocked-by", action="append", default=[], metavar="OTHER", help="OTHER blocks KEY")
    project = sub.add_parser("project", help="a new project of the team")
    project.add_argument("team")
    project.add_argument("--name", required=True)
    project.add_argument("--body-file", required=True, type=Path)
    trigger = sub.add_parser("trigger", help="put the `Take it when:` line right after the issue's origin line")
    trigger.add_argument("key")
    trigger.add_argument("--when", required=True, metavar="TEXT", help="the event or the number that a person can check")
    unlabel = sub.add_parser("unlabel", help="remove a label from the issue")
    unlabel.add_argument("key")
    unlabel.add_argument("name")
    label = sub.add_parser("label", help="add a label to the issue")
    label.add_argument("key")
    label.add_argument("name")
    sub.add_parser("escalated", help="the issue's stays in Escalated as JSON").add_argument("key")
    sub.add_parser("key-from", help="the first issue key in the texts").add_argument("texts", nargs="+")
    listing = sub.add_parser("list", help="the team's issues as JSON, every open one unless --state names states")
    listing.add_argument("team")
    listing.add_argument("--state", action="append", default=[], metavar="NAME")
    listing.add_argument("--key", action="append", default=[], metavar="KEY", help="the issues named, in any state")
    listing.add_argument("--updated-since", type=int, default=None, metavar="DAYS", help="only the issues updated in the last DAYS days")
    listing.add_argument("--since", action="store_true", help="add when each issue entered its state (one request per issue)")
    checks = sub.add_parser("triage-checks", help="the mechanical checks of the triage as JSON (reads only)")
    checks.add_argument("team")
    checks.add_argument("--repo", default=None, metavar="OWNER/NAME",
                        help="the team's repository, for its open pull requests (default: the current folder's)")
    triage_set = sub.add_parser("triage-set", help="the priority and the size of an issue in Triage")
    triage_set.add_argument("key")
    triage_set.add_argument("--priority", default=None, choices=PRIORITIES)
    triage_set.add_argument("--size", default=None, choices=SIZES)
    setup = sub.add_parser("setup", help="the team's states, labels, git automations and triage inbox as the loop needs them")
    setup.add_argument("team")
    setup.add_argument("--dry-run", action="store_true", help="read the team and print the changes; change nothing")
    return top


def run(argv: list[str], post: Post | None = None, environ: Mapping[str, str] | None = None, pulls: Pulls | None = None) -> int:
    """The command; `post`, `environ` and `pulls` are for the tests. Prints the result; 1 with the reason on stderr when it fails."""
    args = parser().parse_args(argv)
    if args.command == "key-from":
        key = issue_key(*args.texts)
        print(key or "")
        return 0
    try:
        if post is None:
            post = http_post(api_key(environ if environ is not None else os.environ, command_team(args)))
        api = Linear(post)
        if args.command == "issue":
            print(json.dumps(api.issue(args.key), indent=2))
        elif args.command == "state":
            print(api.move(args.key, args.name))
        elif args.command == "comment":
            api.comment(args.key, args.body_file.read_text())
            print("commented")
        elif args.command == "trigger":
            api.trigger(args.key, args.when)
            print("set")
        elif args.command == "unlabel":
            api.unlabel(args.key, args.name)
            print("removed")
        elif args.command == "label":
            print("added" if api.label(args.key, args.name) else "added already")
        elif args.command == "create":
            made = api.create(args.team, args.title, args.body_file.read_text(), list(args.label), args.state, args.priority,
                              args.size, args.assignee, list(args.related), list(args.blocks), args.project,
                              list(args.blocked_by))
            print(f"{made['identifier']} {made['url'] or ''}".strip())
        elif args.command == "follow-up":
            made = api.follow_up(args.team.upper(), args.area, args.origin, args.body_file.read_text(), args.priority,
                                 args.fast_lane)
            print(f"{made['identifier']} {made['done']} {made.get('url') or ''}".strip())
        elif args.command == "link":
            for line in api.link(args.key, list(args.related), list(args.blocks), list(args.blocked_by)):
                print(line)
        elif args.command == "project":
            made = api.project(args.team.upper(), args.name, args.body_file.read_text())
            print(f"{made['name']} {made['url'] or ''}".strip())
        elif args.command == "escalated":
            print(json.dumps(api.escalated(args.key)))
        elif args.command == "list":
            print(json.dumps(api.issues(args.team.upper(), list(args.state), args.since, list(args.key), args.updated_since), indent=2))
        elif args.command == "triage-checks":
            print(json.dumps(api.triage_checks(args.team.upper(), pulls() if pulls else open_pulls(args.repo)), indent=2))
        elif args.command == "triage-set":
            api.triage_set(args.key, args.priority, args.size)
            print("set")
        elif args.command == "setup":
            key = args.team.upper()
            for line in api.setup(key, args.dry_run):
                print(line)
            if args.dry_run:
                print("dry run: nothing changed")
            else:
                after = api.team_setup(key)
                names = ", ".join(str(s["name"]) for s in after["states"])
                triage = any(s.get("type") == "triage" for s in after["states"])
                print(f"team {key} now: states {names}; triage {'on' if after['triage'] else 'off'}"
                      + ("" if triage else "; no Triage state yet: turn the triage inbox on in the team's settings"))
    except Failed as e:
        print(f"linear.py: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
