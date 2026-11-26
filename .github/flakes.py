#!/usr/bin/env python3
"""Each flaky browser check on CI files or updates its own Linear issue, for the workflow flakes.yml (AGENTS.md
"Rules"; agents/README.md "A flake that did not repeat").

    python3 .github/flakes.py FLAKES (--team KEY | --repo OWNER/NAME) --run URL --head SHA --branch NAME [--dry-run]
        The team of the issues is KEY, else the team whose row in AGENTS.md "The teams" (in the working folder) names the
        repository OWNER/NAME; with neither, the script stops with exit 1 and says what to set.
        FLAKES is the file that the product's browser reporter wrote: one entry a test that
        failed its first try and passed its retry. For each, the team's open issue whose title names its file:line (or,
        else, its name) gets a comment with where it was found (`Found on main` for main's run), the run, the head and the error, and goes to Urgent: its test is flaky again while the
        issue is open. A test that no open issue names gets a new issue in Todo, with the fast lane (agents/README.md "The
        fast lane": XS or S, one Area, no new choice). `--dry-run` prints what it would do and writes nothing.
        The file run.json beside FLAKES (the suite's counts) adds each test that did not run, with the errors
        outside any test as its error, and, when no test did not run, each such error alone: gates-retry reruns such a run
        once, so its issue is the record that it happened.

The rule "a session that sees a flaky test files an issue" depended on a session's attention: of 3 flakes in 200 gates runs,
2 had no issue until the owner asked. Open means not completed and not canceled: Backlog included, where
a flake that did not repeat waits for its next failure. The workflow runs main's copy of this script, never a branch's.
Standard library only (DECISIONS.md "Tooling"); Linear through .github/linear.py. The tests fake `post`, never the network.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

URGENT = "urgent"
LABELS = ["fast-lane", "frontend"]
MAIN = "main"


def load_linear() -> ModuleType:
    """.github/linear.py, beside this file (`python3 -I` puts no folder of the script on the path)."""
    spec = importlib.util.spec_from_file_location("linear", Path(__file__).with_name("linear.py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def names(issue: Mapping[str, Any], flake: Mapping[str, Any]) -> int:
    """How the issue's title names the flake: 2 by its file:line, 1 by its name only, 0 not at all.

    The title only: an issue about the test names it there (the workflow's own issues, and the first three by hand),
    while a description can name a test in passing (one issue named `keep.spec.mjs:138` in its text). The file:line
    is a whole token: `x.spec.mjs:13` is not in `x.spec.mjs:135`, and `keyboard.spec.mjs:9` not in
    `account-keyboard.spec.mjs:9`; a path before it (`app/tests/x.spec.mjs:13`) names the same file. The name is whole
    too: it starts at an opening quote or at the title's start, and ends at a closing quote or at the title's end, as the
    workflow quotes it (`"NAME"`). So "The list draws the items" is not in an issue about "The list draws the items
    on a phone", and "a failed load is described" not in one about "The list: a failed load
    is described"."""
    title = str(issue.get("title") or "")
    if flake.get("location") and re.search(r"(?<![\w.-])" + re.escape(str(flake["location"])) + r"(?!\d)", title):
        return 2
    if not flake.get("name"):
        return 0
    return 1 if re.search(r"(?:^|(?<=[\"“'`]))" + re.escape(str(flake["name"])) + r"(?=[\"”'`]|$)", title) else 0


def open_for_flakes(issues: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The issues a flake may land on: not a duplicate (Linear lists the state Duplicate as open, and nobody reads it)."""
    return [i for i in issues if i.get("state") != "Duplicate" and not i.get("duplicate_of")]


OUTSIDE = "an error outside any test"


def not_run(run: object) -> list[dict[str, Any]]:
    """The entries that the suite's counts add to the flaky tests: each test that did not run, with the errors outside any
    test as its error (on 2026-11-16 `route.fetch: Test ended` stopped a test of home-setup.spec.mjs), else one
    entry for each such error, named OUTSIDE. Only for a run that .github/gates_retry.py's rule may rerun:
    a run that ended otherwise, in which a test failed after its retry or was interrupted, or that ran no test, is a real
    failure (a build that failed, a job that timed out), and its tests that did not run are no flakes."""
    if not isinstance(run, Mapping) or run.get("status") not in ("passed", "failed") or run.get("unexpected"):
        return []
    total = int(run.get("total") or 0)
    if int(run.get("interrupted") or 0) or not total or len(run.get("didNotRun") or []) >= total:
        return []
    errors = [str(e) for e in run.get("errors") or [] if e]
    tests = [t for t in run.get("didNotRun") or [] if isinstance(t, Mapping)]
    text = "\n\n".join(errors) or "(no error outside a test)"
    if tests:
        return [{**t, "error": f"The test did not run. The errors outside any test:\n{text}", "trace": ""} for t in tests]
    # the error's first line is its place in a title: the same error comments on its own issue
    return [{"name": OUTSIDE, "file": "", "line": "", "location": error.splitlines()[0].strip()[:80], "error": error,
             "trace": ""} for error in errors]


def origin(branch: str, key: str | None) -> str:
    """The first line of a new issue: where the flake was found, by the run's branch alone (a run by hand on a branch is
    not main's)."""
    if branch == MAIN:
        return "Found on main"
    return f"Found in {key}" if key else f"Found in the branch {branch}"


def facts(flake: Mapping[str, Any], run: str, head: str) -> str:
    """The run, the head, the error and the trace, for a comment or a new issue."""
    trace = (f"the artifact `screenshots` of the run, at `web/{flake['trace']}`" if flake.get("trace")
             else "none kept")
    error = str(flake.get("error") or "(no error text)").replace("```", "'''")
    return (f"- Run: {run} (head `{head[:12]}`)\n- Test: `{flake.get('file')}:{flake.get('line')}` "
            f"\"{flake.get('name')}\"\n- Trace: {trace}\n- Error of the failed try:\n\n```\n{error}\n```\n")


def plan(flakes: Iterable[Mapping[str, Any]], issues: Iterable[Mapping[str, Any]], run: str, head: str,
         first_line: str) -> list[dict[str, Any]]:
    """What to do: {"action": "comment", "key", "body", "urgent"} for a test that an open issue names, else
    {"action": "create", "title", "body"}. A test that is in the file twice gets one action."""
    open_issues = sorted(open_for_flakes(issues), key=lambda i: str(i.get("created") or ""))
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for flake in flakes:
        location = str(flake.get("location") or f"{flake.get('file')}:{flake.get('line')}")
        if location in seen:
            continue
        seen.add(location)
        best = max(open_issues, key=lambda i: names(i, flake), default=None)
        if best is not None and names(best, flake):
            urgent = best.get("priority") == URGENT
            note = ("The issue is Urgent already." if urgent else
                    "Its test is flaky again while this issue is open, so the issue is Urgent now.")
            actions.append({"action": "comment", "key": best["identifier"], "urgent": urgent,
                            "body": f"{first_line}: flaky again on CI. {note}\n\n{facts(flake, run, head)}"})
        else:
            what = ("did not run on CI: an error outside it stopped it" if str(flake.get("error") or "").startswith(
                "The test did not run") else "failed its first try on CI and passed its retry")
            body = (f"{first_line}\n\n`{location}` \"{flake.get('name')}\" {what}: a flaky test (AGENTS.md \"Rules\"). "
                    f"The workflow `flakes` filed this issue.\n\n{facts(flake, run, head)}")
            actions.append({"action": "create", "title": f"{location} \"{flake.get('name')}\" is flaky on CI"[:200],
                            "body": body})
    return actions


def urgent(client: Any, key: str, priorities: Mapping[str, int]) -> None:
    """Set the issue's priority to Urgent."""
    issue_id = client.issue(key)["id"]
    mutation = "mutation Urgent($id: String!, $p: Int!) { issueUpdate(id: $id, input: { priority: $p }) { success } }"
    if not client.post(mutation, {"id": issue_id, "p": priorities[URGENT]}).get("issueUpdate", {}).get("success"):
        raise RuntimeError(f"issueUpdate refused the priority of {key}")


def apply(client: Any, team: str, actions: Iterable[Mapping[str, Any]], priorities: Mapping[str, int]) -> list[str]:
    """Do each action; one line a done action."""
    done: list[str] = []
    for action in actions:
        if action["action"] == "comment":
            client.comment(action["key"], action["body"])
            if not action["urgent"]:
                urgent(client, action["key"], priorities)
            done.append(f"commented on {action['key']}" + ("" if action["urgent"] else ", now Urgent"))
        else:
            made = client.create(team, action["title"], action["body"], list(LABELS), "Todo", priority="high", size="S")
            done.append(f"created {made['identifier']} {made.get('url') or ''}".strip())
    return done


# A row of the table "The teams" in an AGENTS.md: the team's key, then its repository (OWNER/NAME)
TEAM_ROW = re.compile(r"^\|\s*([A-Z][A-Z0-9]*)\s*\|\s*([A-Za-z0-9-]+/[A-Za-z0-9._-]+)\s*\|", re.M)


def team_of(agents_md: str, repo: str) -> str:
    """The key of the team whose row in the table "The teams" of an AGENTS.md text names `repo`; "" when none does."""
    section = agents_md.split("\n## The teams\n", 1)[1:]
    rows = TEAM_ROW.findall(section[0].split("\n## ", 1)[0]) if section else []
    return next((key for key, name in rows if name.lower() == repo.lower()), "")


def main(argv: list[str], post: Any = None, environ: Mapping[str, str] | None = None, agents_md: Path = Path("AGENTS.md")) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("flakes", type=Path)
    parser.add_argument("--team", default="")
    parser.add_argument("--repo", default="")
    for flag in ("--run", "--head", "--branch"):
        parser.add_argument(flag, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv[1:])
    if not args.team:
        try:
            args.team = team_of(agents_md.read_text(), args.repo) if args.repo else ""
        except OSError:
            args.team = ""
    if not args.team:
        print(f"flakes: no Linear team: no row of {agents_md} \"The teams\" names {args.repo or 'the repository (--repo)'}, "
              "and no --team", file=sys.stderr)
        return 1
    flakes = json.loads(args.flakes.read_text()) if args.flakes.exists() else []
    counts = args.flakes.with_name("run.json")
    try:
        found = json.loads(counts.read_text()) if counts.is_file() else None
    except ValueError:
        found = None
    flakes = (flakes if isinstance(flakes, list) else []) + not_run(found)
    if not flakes:
        print("no flaky test in this run")
        return 0
    linear = load_linear()
    try:
        client = linear.Linear(post or linear.http_post(linear.api_key(os.environ if environ is None else environ)))
        key = linear.issue_key(args.branch)
        actions = plan(flakes, client.issues(args.team, []), args.run, args.head, origin(args.branch, key))
        if args.dry_run:
            for action in actions:
                print(json.dumps(action))
            return 0
        for line in apply(client, args.team, actions, linear.PRIORITIES):
            print(line)
    except (linear.Failed, RuntimeError) as e:
        print(f"flakes: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
