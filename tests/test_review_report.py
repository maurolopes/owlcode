"""The job `report` of the review workflow runs main's .github/review_report.py. tests/test_ci.py runs each of
its steps with a fake gh (approve, request changes, needs-decision, a code owner's file, the owner's label, the gates are no part of it any more, auto-merge refused); this file holds the branches that only the script has."""

import ast
import json
import subprocess
import sys
from pathlib import Path

import yaml

from tests.test_ci import (ADD_LABEL, FAKE_LINEAR, MERGE, MODIFIED, REPO, REPORT_MERGE, REVIEW, reviews_line,
                           run_step, the_step)

SCRIPT = REPO / ".github" / "review_report.py"
HEAD, PUSHED = "a" * 40, "b" * 40
FAKE_GIT = "#!/bin/sh\necho \"git $*\" >> \"$FAKE/log\"\n"


def git_on_path(tmp_path: Path) -> str:
    """A PATH with a fake git first, which logs its calls to gh's log, then run_step's own folder (gh, python3)."""
    folder = tmp_path / "git"
    folder.mkdir(parents=True)
    (folder / "git").write_text(FAKE_GIT)
    (folder / "git").chmod(0o755)
    return f"{folder}:{tmp_path / 'bin'}:/usr/bin:/bin"


def check_env(tmp_path: Path, **more: str) -> dict[str, str]:
    output = tmp_path / "output"
    tmp_path.mkdir(parents=True, exist_ok=True)
    output.write_text("")
    return {"HEAD": HEAD, "CHECK": "99", "GITHUB_OUTPUT": str(output),
            "PATH": git_on_path(tmp_path), **more}


def test_the_check_carries_the_approve_to_a_head_pushed_while_the_review_ran(tmp_path):
    """R1-2: the carry run of a push during the review read the verdicts before this one, so `check` carries the approve
    to the new head (main's verdict.py `carry`), after it wrote the check on the head the review read. Nothing to carry
    writes nothing, and the same head reads nothing more."""
    approve = reviews_line("APPROVED", "approve", "2026-10-28T11:00:00Z")
    carry = json.dumps({"name": "verdict", "head_sha": PUSHED, "status": "completed", "conclusion": "success"})
    says = lambda text: {".github/verdict.py": VERDICT_FAKE.format(carry=text)}  # noqa: E731 (main's verdict.py, faked)
    rules = [("pulls/7/reviews", approve, 0), ("headRefOid", PUSHED + "\n", 0)]
    run, calls = run_step(tmp_path / "pushed", "report", "check", rules, says(carry), check_env(tmp_path / "pushed"))
    assert run.returncode == 0, run.stdout + run.stderr
    patch, post = "api -X PATCH repos/owner/repo/check-runs/99 --input -", "api repos/owner/repo/check-runs --input carry.json"
    assert calls.index(patch) < calls.index(f"git fetch -q origin {PUSHED}") < calls.index(post)
    assert json.loads((tmp_path / "pushed" / "work" / "carry.json").read_text())["head_sha"] == PUSHED
    run, calls = run_step(tmp_path / "nothing", "report", "check", rules, says(""), check_env(tmp_path / "nothing"))
    assert run.returncode == 0 and patch in calls and post not in calls
    same = [rules[0], ("headRefOid", HEAD + "\n", 0)]
    run, calls = run_step(tmp_path / "same", "report", "check", same, says(carry), check_env(tmp_path / "same"))
    assert run.returncode == 0 and post not in calls and not any(c.startswith("git ") for c in calls)
    # a failed read of the reviews stops the step before any check is written, as the shell's `set -e` did
    run, calls = run_step(tmp_path / "unread", "report", "check", [("pulls/7/reviews", "", 1)], says(carry),
                          check_env(tmp_path / "unread"))
    assert run.returncode != 0 and patch not in calls


VERDICT_FAKE = """import json, sys
if sys.argv[1] == "check":
    print(json.dumps({{"name": "verdict", "head_sha": sys.argv[sys.argv.index("--head") + 1], "status": "completed",
                      "conclusion": "success", "output": {{"title": "Approved", "summary": "ok"}}}}))
elif sys.argv[1] == "carry":
    print({carry!r}, end="")
"""


def test_the_check_is_neutral_while_main_has_no_review_report_py(tmp_path):
    """The pull request that brings the script runs the new workflow on main's checkout, which lacks the script: the check
    is neutral with a title that says so (as for verdict.py before its merge), never left in progress, and the step
    `pushed`, which reads the check's outputs, does not run. A step that runs the script needs it: it fails."""
    step = the_step("report", "check")
    work = tmp_path / "work" / ".github"
    work.mkdir(parents=True)
    fake, bin_ = tmp_path / "fake", tmp_path / "bin"
    fake.mkdir()
    bin_.mkdir()
    (fake / "rules.json").write_text("[]")
    (bin_ / "gh").write_text("#!/bin/sh\necho \"$*\" >> \"$FAKE/log\"\ncat > /dev/null\n")
    (bin_ / "gh").chmod(0o755)
    env = {"PATH": f"{bin_}:/usr/bin:/bin", "FAKE": str(fake), "CHECK": "99", "GITHUB_REPOSITORY": "owner/repo"}
    run = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]], cwd=work.parent, env=env,
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr
    [call] = (fake / "log").read_text().splitlines()
    assert call.startswith("api -X PATCH repos/owner/repo/check-runs/99 -f status=completed -f conclusion=neutral")
    assert "Main has no review_report.py yet" in call
    assert the_step("report", "review_report.py pushed")["if"].replace(" ", "") == "steps.check.outputs.conclusion!=''"
    steps = yaml.safe_load(REVIEW.read_text())["jobs"]["report"]["steps"]
    for later in steps[steps.index(step) + 1:]:
        if "review_report.py" in later.get("run", ""):
            assert "steps.check.outputs." in str(later.get("if", "")), (later["name"], "needs the check's outputs")


def test_only_the_command_check_writes_a_check_run():
    """The reviewer app's token writes the check `verdict` in the step `check` only; no other command of the
    script calls the Checks API, so a token of another step cannot write it."""
    tree = ast.parse(SCRIPT.read_text())
    writers = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)
               for c in ast.walk(node) if isinstance(c, ast.Constant) and isinstance(c.value, str) and "check-runs" in c.value}
    writers |= {node.name for node in tree.body if isinstance(node, ast.FunctionDef)
                for c in ast.walk(node) if isinstance(c, ast.JoinedStr) and "check-runs" in ast.unparse(c)}
    assert writers == {"check"}, writers


def test_a_command_that_the_script_does_not_know_fails_with_its_usage():
    run = subprocess.run([sys.executable, "-I", str(SCRIPT), "approve"], capture_output=True, text=True, check=False)
    assert run.returncode == 2 and "usage: review_report.py" in run.stderr and "merge" in run.stderr
    run = subprocess.run([sys.executable, "-I", str(SCRIPT)], capture_output=True, text=True, check=False)
    assert run.returncode == 2


def test_a_refused_auto_merge_of_a_merged_pull_request_moves_no_issue(tmp_path):
    """On the last refused try: a pull request that merged or closed meanwhile keeps its issue; it still
    gets the label escalated, and the step fails, as before."""
    code = ("pulls/7/files", json.dumps({"filename": "app/x.py", "status": "modified", "patch": "+a"}) + "\n", 0)
    view = [("--json headRefName", "abc-283-x\n", 0), ("--json title", "A title\n", 0)]
    more = {"TRIES": "1", "WAIT": "0", "LINEAR_API_KEY": "key"}
    for state in ("MERGED", "CLOSED"):
        rules = [code, (MERGE, MODIFIED, 1), ("--json state --jq .state", state + "\n", 0), *view]
        run, calls = run_step(tmp_path / state, "report", REPORT_MERGE, rules, {".github/linear.py": FAKE_LINEAR}, more)
        assert run.returncode == 1 and ADD_LABEL in calls, run.stdout + run.stderr
        assert [c for c in calls if c.startswith("linear ")] == ["linear key-from abc-283-x A title"], state
        assert f"::notice::PR #7 is {state.lower()}: ABC-9 stays as it is" in run.stdout


def test_the_texts_name_the_products_limits_file(tmp_path):
    """The limits file comes from OWNER_LIMITS_FILE, as verdict.py reads it: with `quota.toml` the owner's note names it,
    never `limits.toml`. With no value, the text is this product's. (The gates' check names are gates_failed.py's: GATES_CHECKS.)"""
    view = [("--json state --jq .state", "OPEN\n", 0), ("--json autoMergeRequest", "true\n", 0),
            ("--json headRefName", "abc-9-x\n", 0), ("--json title", "A title\n", 0)]
    linear = {".github/linear.py": FAKE_LINEAR}
    for name, more, limits in (("default", {}, "owner's key of limits.toml"),
                               ("other", {"OWNER_LIMITS_FILE": "quota.toml"}, "owner's key of quota.toml")):
        owned = ("pulls/7/files", json.dumps({"filename": ".github/workflows/x.yml", "status": "modified", "patch": "+a"})
                 + "\n", 0)
        run, _ = run_step(tmp_path / name / "owned", "report", REPORT_MERGE, [owned, *view], linear,
                              {"LINEAR_API_KEY": "key", **more})
        assert run.returncode == 0, run.stdout + run.stderr
        assert limits in (tmp_path / name / "owned" / "work" / "owner.md").read_text(), name


FOLLOW_LINEAR = """LABELS = ("follow-up", "fast-lane", "hands-off")
import json, os, sys
if __name__ == "__main__":
    with open("linear.log", "a") as log:
        log.write(" ".join(sys.argv[1:]) + "\\n")
    if sys.argv[1] == "key-from":
        print("ABC-303")
    elif sys.argv[1] == "list":
        print(json.dumps([{"labels": json.loads(os.environ["ISSUE_LABELS"]), "areas": json.loads(os.environ.get("ISSUE_AREAS", '["workflow"]')), "description": os.environ.get("ISSUE_TEXT", "")}]))
    elif sys.argv[1] == "follow-up":
        if os.environ.get("FOLLOW_FAILS"):
            sys.exit(1)
        print("ABC-400 appended")
"""


def verdict_review(word: str, findings: list, when: str, **more: object) -> str:
    data = {"pull_request": 7, "commit": "c" * 40, "round": 2, "verdict": word, "findings": findings, **more}
    state = "CHANGES_REQUESTED" if word != "approve" else "APPROVED"
    return json.dumps({"user": {"login": "maurolopes-reviewer[bot]"}, "state": state, "submitted_at": when,
                       "body": f"Summary.\n\n```json\n{json.dumps(data)}\n```\n"}) + "\n"


LINE = {"id": "R2-1", "blocking": False, "file": "a.py", "line": 3, "summary": "A test is missing.", "follow_up": True}


def owner_approve(tmp_path: Path, reviews: str, labels: str = '["workflow"]', **more: str):
    view = [("--json state --jq .state", "OPEN", 0), ("--json headRefName", "abc-303-x\n", 0), ("--json title", "T\n", 0),
            ("pulls/7/reviews", reviews, 0)]
    env = {"LINEAR_API_KEY": "k", "HEAD": HEAD, "ISSUE_LABELS": labels, **more}
    run, calls = run_step(tmp_path, "report", "review_report.py owner-approved", view, {".github/linear.py": FOLLOW_LINEAR}, env)
    log = tmp_path / "work" / "linear.log"
    return run, calls, log.read_text().splitlines() if log.exists() else []


def test_the_owners_approve_files_the_follow_up_lines_of_the_last_review(tmp_path):
    """OWL-19: a review that ended in a needs-decision wrote follow-up lines for the approve that follows. The owner's
    approve runs no model, so the report files them into the Area's collecting issue. A review with none files nothing,
    and so does the reviewer's own approve (it filed its lines)."""
    old = verdict_review("request-changes", [{**LINE, "id": "R1-9"}], "2026-11-25T10:00:00Z")
    last = verdict_review("needs-decision", [LINE, {**LINE, "id": "R2-2", "follow_up": False, "fix_now": True}], "2026-11-25T11:00:00Z",
                          decision="Q?")
    owner = verdict_review("approve", [], "2026-11-25T12:00:00Z", approved_by="maurolopes")
    run, _, log = owner_approve(tmp_path / "files", old + last + owner)
    assert run.returncode == 0, run.stdout + run.stderr
    command = next(line for line in log if line.startswith("follow-up"))
    assert command.startswith("follow-up ABC --area workflow --from PR #7, round 2, ccccccc, depth 1 ") and "--priority low" in command
    assert (tmp_path / "files" / "work" / "ABC-303-follow-up.md").read_text() == "- [ ] R2-1 a.py:3: A test is missing.\n"
    asked = verdict_review("request-changes", [LINE], "2026-11-25T11:00:00Z")
    run, _, log = owner_approve(tmp_path / "changes", old + asked + owner)
    assert any(line.startswith("follow-up ABC --area workflow") for line in log), log
    for name, reviews in (("none", verdict_review("needs-decision", [{**LINE, "follow_up": False, "fix_now": True}], "2026-11-25T11:00:00Z")),
                          ("approve", verdict_review("approve", [LINE], "2026-11-25T11:00:00Z")), ("no review", "")):
        run, _, log = owner_approve(tmp_path / name, reviews)
        assert run.returncode == 0 and not any(line.startswith("follow-up") for line in log), name
        assert any(line == "state ABC-303 Approved" for line in log), name


def test_the_owners_approve_follow_up_depth_and_failure(tmp_path):
    """A pull request of a follow-up issue files at its depth plus 1. A failure to file, or no Area, writes one comment with
    the lines, and the approve still goes on."""
    last = verdict_review("needs-decision", [LINE], "2026-11-25T11:00:00Z")
    text = "From: PR #3, round 1, abc1234, depth 2\nFrom: PR #4, round 1, abc1234\n"
    run, _, log = owner_approve(tmp_path / "deep", last, '["workflow", "follow-up"]', ISSUE_TEXT=text)
    assert "depth 3" in next(line for line in log if line.startswith("follow-up")), log
    run, calls, log = owner_approve(tmp_path / "fails", last, FOLLOW_FAILS="1")
    comment = [c for c in calls if c.startswith("pr comment 7")]
    assert run.returncode == 0 and "::warning::" in run.stdout and len(comment) == 1 and "R2-1 a.py:3" in "\n".join(calls)
    run, calls, log = owner_approve(tmp_path / "noarea", last, '["follow-up"]', ISSUE_AREAS="[]")
    assert run.returncode == 0 and not any(line.startswith("follow-up") for line in log)
    assert len([c for c in calls if c.startswith("pr comment 7")]) == 1


def test_the_owners_approve_files_an_open_round_one_fix_now_finding_and_reads_the_area_group(tmp_path):
    """OWL-23: a round-1 fix_now finding that round 2 leaves open (`previous`) is a follow-up line too, with its round-1
    id, file, line and summary. The Area is the issue's label of the group Area (`areas`), never another label."""
    one = verdict_review("request-changes", [{"id": "R1-4", "blocking": False, "file": "b.py", "line": 9, "summary": "A name is stale.",
                                              "fix_now": True}], "2026-11-25T10:00:00Z", round=1)
    two = verdict_review("needs-decision", [LINE], "2026-11-25T11:00:00Z",
                         previous=[{"id": "R1-4", "fixed": False, "fix_now": True}, {"id": "R1-5", "fixed": True, "fix_now": True}])
    run, _, log = owner_approve(tmp_path / "open", one + two, '["Bug", "workflow"]', ISSUE_AREAS='["workflow"]')
    assert run.returncode == 0, run.stdout + run.stderr
    assert next(line for line in log if line.startswith("follow-up")).startswith("follow-up ABC --area workflow ")
    assert (tmp_path / "open" / "work" / "ABC-303-follow-up.md").read_text() == (
        "- [ ] R2-1 a.py:3: A test is missing.\n- [ ] R1-4 b.py:9: A name is stale.\n")
    three = verdict_review("needs-decision", [], "2026-11-25T12:00:00Z", round=3, previous=[{"id": "R2-9", "fixed": True}])
    run, _, log = owner_approve(tmp_path / "round3", one + two + three)
    assert run.returncode == 0, run.stdout + run.stderr
    assert (tmp_path / "round3" / "work" / "ABC-303-follow-up.md").read_text() == "- [ ] R1-4 b.py:9: A name is stale.\n"


def test_the_owners_approve_reads_only_the_current_round_count_and_keeps_open_fix_now_lines_past_the_cap(tmp_path):
    """OWL-23 round 1: an open fix_now of an earlier count is not filed again, and five follow_up lines do not drop an open one."""
    old1 = verdict_review("request-changes", [{"id": "R1-4", "blocking": False, "file": "b.py", "line": 9, "summary": "Old.",
                                               "fix_now": True}], "2026-11-25T09:00:00Z", round=1)
    old2 = verdict_review("needs-decision", [], "2026-11-25T09:30:00Z", previous=[{"id": "R1-4", "fixed": False, "fix_now": True}])
    new1 = verdict_review("needs-decision", [LINE], "2026-11-25T10:00:00Z", round=1)
    run, _, _ = owner_approve(tmp_path / "count", old1 + old2 + new1)
    assert run.returncode == 0, run.stdout + run.stderr
    assert (tmp_path / "count" / "work" / "ABC-303-follow-up.md").read_text() == "- [ ] R2-1 a.py:3: A test is missing.\n"
    five = [{**LINE, "id": f"R2-{n}"} for n in range(1, 6)]
    r1 = verdict_review("request-changes", [{"id": "R1-4", "blocking": False, "file": "b.py", "line": 9, "summary": "Stale.",
                                             "fix_now": True}], "2026-11-25T10:00:00Z", round=1)
    r2 = verdict_review("needs-decision", five, "2026-11-25T11:00:00Z", previous=[{"id": "R1-4", "fixed": False, "fix_now": True}])
    run, _, _ = owner_approve(tmp_path / "cap", r1 + r2)
    assert run.returncode == 0, run.stdout + run.stderr
    lines = (tmp_path / "cap" / "work" / "ABC-303-follow-up.md").read_text().splitlines()
    assert len(lines) == 6 and lines[-1] == "- [ ] R1-4 b.py:9: Stale."
