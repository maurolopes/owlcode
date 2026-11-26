"""agents/verdict.schema.json: the rules a reviewer's verdict keeps (agents/reviewer.md "The verdict"), each with a verdict
the schema accepts and one it refuses. `fix_now` and the open fix_now finding in round 2. Every finding is acted
on (blocking, fix_now or a follow-up line)."""
import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

SCHEMA = json.loads((Path(__file__).parents[1] / "agents" / "verdict.schema.json").read_text())


def finding(fid: str = "R1-1", **extra: Any) -> dict[str, Any]:
    return {"id": fid, "blocking": False, "file": "app/x.py", "line": 3, "summary": "s", "failure": "f", "fix": "x",
            "confirmed": True, **extra}


def verdict(word: str, round_: int = 1, findings: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    return {"pull_request": 1, "commit": "a" * 40, "round": round_, "verdict": word, "summary": "s",
            "findings": findings if findings is not None else [], **extra}


def errors(v: dict[str, Any]) -> list[str]:
    return [e.message for e in Draft202012Validator(SCHEMA).iter_errors(v)]


def test_the_schema_is_a_valid_draft_2020_12_schema():
    Draft202012Validator.check_schema(SCHEMA)


BLOCKING = finding("R1-1", blocking=True, bar="behaviour")
FIX_NOW = finding("R1-2", fix_now=True)

ACCEPTED = {
    "an approve with a follow-up line": verdict("approve", findings=[finding(follow_up=True)], issue="ABC-9"),
    "round 1 asks for changes for a blocking finding": verdict("request-changes", findings=[BLOCKING]),
    "round 1 asks for changes for a fix_now finding alone": verdict("request-changes", findings=[FIX_NOW]),
    "round 1 asks for changes for both kinds": verdict("request-changes", findings=[BLOCKING, FIX_NOW, finding("R1-3", follow_up=True)]),
    "round 2 approves with a fix_now finding of round 1 still open":
        verdict("approve", 2, previous=[{"id": "R1-1", "fixed": True}, {"id": "R1-2", "fixed": False, "fix_now": True}], issue="ABC-9"),
    "round 2 approves with every finding of round 1 fixed":
        verdict("approve", 2, previous=[{"id": "R1-1", "fixed": True}, {"id": "R1-2", "fixed": True, "fix_now": True}]),
    "round 2 with a finding that is the owner's call needs a decision":
        verdict("needs-decision", 2, previous=[{"id": "R1-1", "fixed": False}], decision="q"),
    # Three rounds at most; round 2 asks for changes for a blocking finding, round 3 needs the owner's decision
    "round 2 asks for changes for a blocking finding of round 1 still open":
        verdict("request-changes", 2, previous=[{"id": "R1-1", "fixed": False}]),
    "round 2 asks for changes for a new blocking finding":
        verdict("request-changes", 2, findings=[finding("R2-1", blocking=True, bar="behaviour")], previous=[{"id": "R1-1", "fixed": True}]),
    "round 3 approves": verdict("approve", 3, previous=[{"id": "R2-1", "fixed": True}]),
    "round 3 with a blocking finding still open needs a decision":
        verdict("needs-decision", 3, previous=[{"id": "R2-1", "fixed": False}], decision="q"),
}

REFUSED = {
    "a request-changes with neither a blocking nor a fix_now finding": verdict("request-changes", findings=[finding(follow_up=True)]),
    "a fix_now finding that blocks": verdict("request-changes", findings=[finding(blocking=True, bar="behaviour", fix_now=True)]),
    "a fix_now finding that is also a follow-up line": verdict("request-changes", findings=[finding(fix_now=True, follow_up=True)]),
    "an unconfirmed fix_now finding": verdict("request-changes", findings=[finding(fix_now=True, confirmed=False)]),
    "an approve with a fix_now finding open in round 1": verdict("approve", findings=[FIX_NOW]),
    "a fix_now finding in round 2":
        verdict("approve", 2, findings=[finding("R2-1", fix_now=True)], previous=[{"id": "R1-1", "fixed": True}]),
    "a request-changes in round 2, for a fix_now finding": verdict("request-changes", 2, findings=[FIX_NOW], previous=[]),
    "a request-changes in round 2 for a fix_now finding of round 1 still open":
        verdict("request-changes", 2, previous=[{"id": "R1-2", "fixed": False, "fix_now": True}]),
    "a request-changes in round 3": verdict("request-changes", 3, findings=[BLOCKING], previous=[{"id": "R2-1", "fixed": False}]),
    "a fix_now finding in round 3":
        verdict("approve", 3, findings=[finding("R3-1", fix_now=True)], previous=[{"id": "R2-1", "fixed": True}]),
    "round 3 with no previous": verdict("approve", 3),
    "a fourth round": verdict("approve", 4, previous=[]),
    "a fix_now finding in round 2, in a verdict no other rule refuses":
        verdict("needs-decision", 2, findings=[finding("R2-1", fix_now=True)], previous=[{"id": "R1-1", "fixed": False}], decision="q"),
    "an approve with a blocking finding of round 1 still open": verdict("approve", 2, previous=[{"id": "R1-1", "fixed": False}]),
    "a non-blocking finding that is neither fix_now nor a follow-up line": verdict("approve", findings=[finding()]),
    "a preference written as follow_up: false": verdict("approve", findings=[finding(follow_up=False)]),
    "a preference beside a blocking finding": verdict("request-changes", findings=[BLOCKING, finding("R1-2", follow_up=False)]),
    "an approve that says an open blocking finding was fix_now: false":
        verdict("approve", 2, previous=[{"id": "R1-1", "fixed": False, "fix_now": False}]),
}


@pytest.mark.parametrize("name", ACCEPTED)
def test_the_schema_accepts(name: str):
    assert errors(ACCEPTED[name]) == [], name


@pytest.mark.parametrize("name", REFUSED)
def test_the_schema_refuses(name: str):
    assert errors(REFUSED[name]), name


def test_each_refusal_comes_from_its_own_change():
    """A refused verdict differs from an accepted one in the field the rule reads, so each case fails for its own rule,
    never for a shape error the fixture made."""
    v = copy.deepcopy(ACCEPTED["round 1 asks for changes for a fix_now finding alone"])
    assert errors(v) == []
    v["findings"][0]["follow_up"] = True
    assert errors(v)
    v = copy.deepcopy(ACCEPTED["round 2 approves with a fix_now finding of round 1 still open"])
    v["previous"][1]["fix_now"] = False
    assert errors(v), "an open finding of round 1 stops the approve unless it was fix_now"
    v = copy.deepcopy(ACCEPTED["an approve with a follow-up line"])
    v["findings"][0]["follow_up"] = False
    assert errors(v), "a finding nobody acts on is not written"
