"""The check `verdict` (.github/verdict.py): the reviewer's last verdict read from its JSON, success only for an approve, and
an approve carried to a branch update only when every commit since is the automatic merge of main with the same changed
lines. The carry runs on small real repositories, never the network."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.conftest import REAL_REPO

BOT = "maurolopes-reviewer[bot]"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("github_verdict", REAL_REPO / ".github" / "verdict.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


verdict = load()


def review(state: str, word: str, commit: str, when: str, user: str = BOT, round: int = 1) -> dict:
    body = f"Summary.\n\n```json\n{json.dumps({'pull_request': 7, 'commit': commit, 'round': round, 'verdict': word})}\n```\n"
    return {"user": {"login": user}, "state": state, "body": body, "commit_id": "f" * 40, "submitted_at": when,
            "html_url": f"https://github.com/o/r/pull/7#review-{when}"}


def test_only_an_approve_that_is_still_approved_passes():
    approve = review("APPROVED", "approve", "a" * 40, "2026-10-25T10:00:00Z")
    assert verdict.check_body(verdict.last_verdict([approve], BOT), "b" * 40)["conclusion"] == "success"
    for state, word in (("CHANGES_REQUESTED", "request-changes"), ("CHANGES_REQUESTED", "needs-decision"), ("DISMISSED", "approve"),
                        ("APPROVED", "")):
        body = verdict.check_body(verdict.last_verdict([review(state, word, "a" * 40, "2026-10-25T10:00:00Z")], BOT), "b" * 40)
        assert body["conclusion"] == "failure" and body["head_sha"] == "b" * 40 and body["name"] == "verdict", (state, word)
    assert verdict.check_body(None, "b" * 40)["output"]["title"] == "No verdict"


def test_no_verdict_says_whether_the_review_ended_failed_or_did_not_run(tmp_path):
    """A review that ended with success and posted no review (run 42313827551) is told apart from a
    failed run and from a round past the third, by the outcome of the review job's model step."""
    def summary(outcome: str) -> str:
        body = verdict.check_body(None, "b" * 40, outcome)
        assert body["conclusion"] == "failure" and body["output"]["title"] == "No verdict", outcome
        return str(body["output"]["summary"])
    assert summary("success").startswith("The review ended and posted no review.")
    assert "Denied tool calls of the review" in summary("success")
    assert summary("failure").startswith("The review run failed")
    assert summary("cancelled").startswith("The review run was cancelled")
    # R1-1: GitHub skips the model step after any failed step before it too, so a round-1 run whose `facts` step failed
    # also gives `skipped`: the text names that cause, never only the round past the third
    assert summary("skipped").startswith("The review did not run: a step before it failed, or the round is past the third")
    assert summary("") == summary("something else") and "a failed run, or a round past the third" in summary("")
    approve = review("APPROVED", "approve", "a" * 40, "2026-10-25T10:00:00Z")
    assert verdict.check_body(verdict.last_verdict([approve], BOT), "b" * 40, "success")["conclusion"] == "success", \
        "a verdict wins over the outcome"
    reviews = tmp_path / "reviews.jsonl"
    reviews.write_text("")
    done = subprocess.run([sys.executable, "-I", str(REAL_REPO / ".github" / "verdict.py"), "check", str(reviews), "--reviewer", BOT, "--head", "b" * 40,
                           "--review", "success"], capture_output=True, text=True, check=True)
    assert json.loads(done.stdout)["output"]["summary"] == verdict.NO_VERDICT["success"]


def test_the_last_verdict_is_the_reviewers_newest_since_the_run_began():
    reviews = [review("APPROVED", "approve", "a" * 40, "2026-10-25T09:00:00Z"),
               review("CHANGES_REQUESTED", "request-changes", "c" * 40, "2026-10-25T10:00:00Z"),
               review("APPROVED", "approve", "d" * 40, "2026-10-25T11:00:00Z", user="someone"),
               {"user": {"login": BOT}, "state": "COMMENTED", "body": "", "submitted_at": "2026-10-25T12:00:00Z"}]
    last = verdict.last_verdict(reviews, BOT)
    assert last["verdict"] == "request-changes" and last["commit"] == "c" * 40, "inline comments (COMMENTED) and other people are no verdict"
    assert verdict.last_verdict(reviews, BOT, since="2026-10-25T10:30:00Z") is None, "a verdict before the run is not this run's"


def test_the_commit_comes_from_the_verdict_json_and_a_body_without_one_is_no_approve():
    """GitHub moves a review's commit_id onto a later update merge: the verdict's own `commit` is the one read."""
    assert verdict.last_verdict([review("APPROVED", "approve", "a" * 40, "t")], BOT)["commit"] == "a" * 40
    bare = {"user": {"login": BOT}, "state": "APPROVED", "body": "LGTM", "commit_id": "e" * 40, "submitted_at": "t"}
    assert verdict.check_body(verdict.last_verdict([bare], BOT), "b" * 40)["conclusion"] == "failure"


def test_the_reviews_file_is_one_review_a_line_or_a_list(tmp_path):
    one = review("APPROVED", "approve", "a" * 40, "t")
    (tmp_path / "lines").write_text(json.dumps(one) + "\n" + json.dumps(one) + "\n")
    (tmp_path / "list").write_text(json.dumps([one]))
    (tmp_path / "empty").write_text("")
    assert len(verdict.read_reviews(tmp_path / "lines")) == 2 and verdict.read_reviews(tmp_path / "list") == [one]
    assert verdict.read_reviews(tmp_path / "empty") == []


def git(repo: Path, *args: str) -> str:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    return subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True).stdout.strip()


def commit(repo: Path, name: str, text: str, message: str = "") -> str:
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-qm", message or name)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    """main with two files; the branch `pr` changes one line of a.txt, and its head is the approved commit."""
    monkeypatch.chdir(tmp_path)
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "a.txt", "one\ntwo\nthree\n")
    commit(tmp_path, "b.txt", "main\n")
    git(tmp_path, "checkout", "-qb", "pr")
    commit(tmp_path, "a.txt", "one\nTWO\nthree\n")
    return tmp_path


def carry(repo: Path, approved: str) -> tuple[dict | None, str]:
    last = verdict.last_verdict([review("APPROVED", "approve", approved, "t")], BOT)
    return verdict.carry_body(last, git(repo, "rev-parse", "HEAD"), "main")


def update(repo: Path) -> None:
    """Another pull request merges first (b.txt on main), and the branch merges main."""
    git(repo, "checkout", "-q", "main")
    commit(repo, "b.txt", (repo / "b.txt").read_text() + "main moved\n")
    git(repo, "checkout", "-q", "pr")
    git(repo, "merge", "-q", "--no-edit", "main")


def test_a_branch_update_carries_the_approve(repo):
    approved = git(repo, "rev-parse", "HEAD")
    update(repo)
    body, why = carry(repo, approved)
    assert why == "" and body["conclusion"] == "success" and body["head_sha"] == git(repo, "rev-parse", "HEAD")
    update(repo)
    assert carry(repo, approved)[0] is not None, "two updates in a row carry the same approve"


def test_a_push_beyond_the_update_carries_nothing(repo):
    approved = git(repo, "rev-parse", "HEAD")
    update(repo)
    commit(repo, "c.txt", "new\n")
    body, why = carry(repo, approved)
    assert body is None and "not a merge from main" in why


def test_a_merge_resolved_by_hand_carries_nothing(repo):
    approved = git(repo, "rev-parse", "HEAD")
    update(repo)
    (repo / "a.txt").write_text("one\nTWO, by hand\nthree\n")
    git(repo, "commit", "-q", "--amend", "-a", "--no-edit")  # the merge commit now holds more than the automatic merge
    body, why = carry(repo, approved)
    assert body is None and "not the automatic merge" in why
    # A resolved merge is a real commit, so it gets a new review, never silence
    assert rereview(repo, approved), "the review runs again"
    assert words(repo, approved, git(repo, "rev-parse", "HEAD")) == "pushed"


def test_a_merge_of_another_branch_or_an_unknown_commit_carries_nothing(repo):
    approved = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-qb", "side", "main")
    commit(repo, "d.txt", "side\n")
    git(repo, "checkout", "-q", "pr")
    git(repo, "merge", "-q", "--no-edit", "side")
    assert "not a merge from main" in carry(repo, approved)[1]
    assert carry(repo, "0" * 40)[0] is None
    assert carry(repo, git(repo, "rev-parse", "HEAD"))[1] == "the head is the approved commit"


def test_no_carry_without_an_approve(repo):
    approved = git(repo, "rev-parse", "HEAD")
    update(repo)
    for state, word in (("CHANGES_REQUESTED", "request-changes"), ("DISMISSED", "approve")):
        last = verdict.last_verdict([review(state, word, approved, "t")], BOT)
        assert verdict.carry_body(last, git(repo, "rev-parse", "HEAD"), "main")[0] is None
    assert verdict.carry_body(None, git(repo, "rev-parse", "HEAD"), "main")[0] is None


def test_a_diff_that_main_absorbed_still_carries(repo):
    """(the owner's call, 2026-11-02): main lands the pull request's own line, the automatic merge is clean and the
    pull request's diff shrinks. The approve still carries: the gates on main and the deploy's tests catch a combined break."""
    approved = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    commit(repo, "a.txt", "one\nTWO\nthree\n", "main's own")  # another message: the same one would make the same commit
    git(repo, "checkout", "-q", "pr")
    git(repo, "merge", "-q", "--no-edit", "main")
    body, why = carry(repo, approved)
    assert why == "" and body["conclusion"] == "success"


def rereview(repo: Path, approved: str, state: str = "APPROVED", word: str = "approve") -> bool:
    last = verdict.last_verdict([review(state, word, approved, "t")], BOT)
    return verdict.rereview(last, git(repo, "rev-parse", "HEAD"), "main")


def test_an_update_that_changed_the_lines_gets_no_new_review(repo):
    """The automatic merge of main changed the pull request's lines, and the approve still covers the head."""
    approved = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    commit(repo, "a.txt", "one\nTWO\nthree\n", "main's own")  # main lands the pull request's line: a clean merge
    git(repo, "checkout", "-q", "pr")
    git(repo, "merge", "-q", "--no-edit", "main")
    assert carry(repo, approved)[0] is not None and not rereview(repo, approved)


def test_a_carried_update_gets_no_new_review(repo):
    approved = git(repo, "rev-parse", "HEAD")
    update(repo)
    assert carry(repo, approved)[0] is not None and not rereview(repo, approved), "the check is carried"


def test_a_real_commit_after_the_approve_gets_a_new_review(repo):
    """A push while the review ran, then merges of main: carry never moves the approve, so the review runs again."""
    approved = git(repo, "rev-parse", "HEAD")
    commit(repo, "c.txt", "new\n")
    assert rereview(repo, approved)
    update(repo)
    assert carry(repo, approved)[0] is None and rereview(repo, approved)
    assert not rereview(repo, approved, "CHANGES_REQUESTED", "request-changes"), "no approve: the implementer's round"


def words(repo: Path, approved: str, head: str) -> str:
    """What the command `rereview` prints for `head` after an approve of `approved`."""
    (repo / "reviews.jsonl").write_text(json.dumps(review("APPROVED", "approve", approved, "t")) + "\n")
    run = subprocess.run([sys.executable, "-I", str(REAL_REPO / ".github" / "verdict.py"), "rereview", "reviews.jsonl",
                          "--reviewer", BOT, "--head", head, "--main", "main"],
                         cwd=repo, capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr
    return run.stdout.strip()


def test_rereview_on_the_command_line(repo):
    approved = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    commit(repo, "a.txt", "one\nTWO\nthree\n", "main's own")
    git(repo, "checkout", "-q", "pr")
    git(repo, "merge", "-q", "--no-edit", "main")
    (repo / "reviews.jsonl").write_text(json.dumps(review("APPROVED", "approve", approved, "t")) + "\n")
    run = subprocess.run([sys.executable, "-I", str(REAL_REPO / ".github" / "verdict.py"), "rereview", "reviews.jsonl",
                          "--reviewer", BOT, "--head", git(repo, "rev-parse", "HEAD"), "--main", "main"],
                         cwd=repo, capture_output=True, text=True, check=False)
    assert run.returncode == 0 and run.stdout.strip() == "false", run.stderr
    assert words(repo, approved, git(repo, "rev-parse", "HEAD")) == "false", "Changed lines, still carried"
    assert words(repo, approved, approved) == "false", "the head is the approved commit"
    git(repo, "checkout", "-q", "-b", "later", approved)
    real = commit(repo, "c.txt", "new\n")
    assert words(repo, approved, real) == "pushed", "a real commit after the approve"


def test_the_owned_files_follow_codeowners_rules():
    rules = verdict.read_owners("# comment\n/.github/ @o\n/AGENTS.md @o\n*.lock @o\ndocs/ @o\n/app/package.json @o\n/.github/free.md\n")
    paths = [".github/workflows/a.yml", "AGENTS.md", "x/AGENTS.md", "uv.lock", "app/x.lock", "a/docs/b.md", "docs",
             "app/package.json", "app/src/package.json", "app/a.py", ".github/free.md", ".githubx/a"]
    assert verdict.owned(rules, paths) == [".github/workflows/a.yml", "AGENTS.md", "uv.lock", "app/x.lock", "a/docs/b.md",
                                           "app/package.json"], "anchored, folder-only, any depth, and a later rule with no owner"
    assert verdict.owned(verdict.read_owners((REAL_REPO / ".github" / "CODEOWNERS").read_text()), ["src/a.py", ".githooks/x"]) == [".githooks/x"]


def test_the_guards_are_the_owners_and_the_workflows_prose_merges_on_the_verdict():
    """The owner's call 2026-10-28: the files that check the agents' work, run beside a key or ship stay the owner's;
    the workflow's prose and DECISIONS.md leave CODEOWNERS. A skill's `SKILL.md` stays the owner's: its frontmatter can
    grant tools or declare hooks; the skill's other files merge on the verdict. A new file in
    agents/, .claude/, .agents/ or .pi/ stays the owner's, except one under .agents/skills/ that is not a `SKILL.md`."""
    rules = verdict.read_owners((REAL_REPO / ".github" / "CODEOWNERS").read_text())
    prose = ["agents/README.md", "agents/implementer.md", "agents/planner.md", "DECISIONS.md"]
    assert verdict.owned(rules, prose) == [], "the verdict merges these"
    guards = ["agents/reviewer.md", "agents/verdict.schema.json", "agents/new.md", ".claude/settings.json",
              ".claude/hooks/adapter.py", ".agents/guards/kill_by_pid.py", ".claude/new.json", ".agents/new.md", ".claude/skills/issue/SKILL.md",
              ".pi/settings.json", ".pi/new.json",
              ".claude/skills/triage", ".agents/skills/triage/SKILL.md", ".agents/skills/measure/SKILL.md", ".github/linear.py",
              ".github/code-changed.sh", ".github/verdict.py", ".github/attribution.sh", ".github/workflows/gates.yml",
              ".githooks/pre-push", "pyproject.toml", "uv.lock"]
    assert verdict.owned(rules, guards) == guards
    # the pull requests of 2026-10-28 that waited for the owner's merge although they changed no guard
    for files, kept in ((["DECISIONS.md", "tests/test_ci.py"], []),
                        ([".github/linear.py", "agents/README.md", "tests/test_linear.py"], [".github/linear.py"]),
                        ([".github/linear.py", "agents/README.md", "agents/implementer.md", "tests/test_linear.py"], [".github/linear.py"])):
        assert verdict.owned(rules, files) == kept, files


CHECKS = "app/tests/checks.txt"


def test_a_doc_and_a_skills_script_merge_on_the_verdict_and_each_skills_skill_md_stays_owned():
    """The owner's call 2026-11-23: CODEOWNERS keeps only the guards. A skill's other files check nothing and merge on
    the verdict; a skill's `SKILL.md` stays the owner's, and the other guards stay. The last matching line wins. `*` crosses a slash here, more files owned, never fewer, where
    GitHub's `*` stops at one folder level."""
    rules = verdict.read_owners((REAL_REPO / ".github" / "CODEOWNERS").read_text())
    assert verdict.owned(rules, [".agents/skills/measure/measure.py"]) == [], "the verdict merges these"
    kept = [".agents/skills/measure/SKILL.md", ".agents/guards/kill_by_pid.py"]
    assert verdict.owned(rules, kept) == kept


def change(filename: str, patch: str | None = "@@ -1,2 +1,3 @@\n a\n+b\n c", status: str = "modified", **more: str) -> dict:
    """One object of the files API (GET /pulls/N/files); `patch` None stands for a diff GitHub left out."""
    return {"filename": filename, "status": status, **({"patch": patch} if patch is not None else {}), **more}


def test_the_browser_checks_list_is_never_the_owners_the_reviewer_judges_a_removed_check():
    """The owner's call 2026-11-16: a check that leaves the list is the reviewer's to judge (the product's frontend rules), so a
    change of the list merges on the verdict. Before, a line taken away waited for the owner: 9 pull requests on
    2026-11-15, 17 hours in all, only for a renamed check."""
    rules = verdict.read_owners((REAL_REPO / ".github" / "CODEOWNERS").read_text())
    assert verdict.owned(rules, [CHECKS]) == [], "CODEOWNERS does not name it"
    for one in (change(CHECKS), change(CHECKS, status="added"), change(CHECKS, "@@ -1,3 +1,2 @@\n a\n-b\n c"),
                change(CHECKS, "@@ -1,3 +1,3 @@\n a\n-b\n+b2\n c"),
                change(CHECKS, "@@ -1,3 +0,0 @@\n-a\n-b\n-c", status="removed"), change(CHECKS, None),
                change("app/tests/names.txt", "", status="renamed", previous_filename=CHECKS)):
        assert verdict.owned_changes(rules, [one]) == [], one
    assert verdict.owned_changes(rules, verdict.read_changes(CHECKS)) == [], "a bare name, with no patch"
    # the other guards stay: a CODEOWNERS file beside it is still the owner's
    assert verdict.owned_changes(rules, [change(CHECKS, "@@ -1 +0,0 @@\n-a"), change(".github/verdict.py")]) == \
        [".github/verdict.py"]


def test_the_rerun_workflow_reads_the_files_api_in_its_script():
    """The scripts of the review workflow's job `report` (.github/review_report.py) and of verdict-rerun
    (.github/verdict_rerun.py) always read the files API's objects (with their patches, for limits.toml and
    AGENTS.md), so no step greps verdict.py for a name list."""
    rerun = (REAL_REPO / ".github" / "verdict_rerun.py").read_text()
    assert "pulls/{pr}/files?per_page=100" in rerun
    flow = (REAL_REPO / ".github" / "workflows" / "verdict-rerun.yml").read_text()
    assert "grep" not in flow, "the logic is in verdict_rerun.py"


LIMITS = "limits.toml"
CTX = " idle_days = 14                  # an account not used for this many days\n"


def limits_change(*lines: str, **more: str) -> dict:
    """A patch of limits.toml: the lines given (each starts with + or -), between two lines of context."""
    return change(LIMITS, "@@ -1,3 +1,4 @@\n" + CTX + "\n".join(lines) + "\n" + CTX, **more)


def test_limits_toml_is_the_owners_only_for_a_changed_value_or_a_key_of_the_owners_list():
    """The owner's call 2026-11-04: a new key that is not on the list merges on the verdict."""
    rules = verdict.read_owners((REAL_REPO / ".github" / "CODEOWNERS").read_text())
    assert verdict.owned(rules, [LIMITS]) == [] and verdict.owned(rules, ["pyproject.toml"]) == ["pyproject.toml"], "CODEOWNERS does not name it"

    def owner(*lines: str, **more: str) -> list[str]:
        return verdict.owned_changes(rules, [limits_change(*lines, **more)])

    # a new limit, as a dry run of its diff in the new layout: it merges on the verdict
    assert owner("+lookup_checks_per_day = 10      # live checks of the hiring systems an account may start in any 24 hours") == []
    assert owner("+# a new comment", "+", "+max_listing_pages = 40") == [], "comment lines and blank lines come with a new key"
    assert owner("+monthly_budget_x = 5") == [], "a name that only starts like a key of the list"
    for key in ("monthly_budget", "unpriced_call_cost", "install_daily_budget", "install_daily_cap", "anon_daily_budget", "anon_kit_reserve",
                "anon_reads_without_kit", "tool_sandbox", "tool_memory_mb", "tool_timeout_seconds", "free_stories", "premium_new_quota"):
        assert owner(f"+{key} = 1") == [LIMITS], f"a new key {key} is on the owner's list"
    assert owner('+tool_sandbox = "off"') == [LIMITS]
    assert owner("-idle_days = 14  # a", "+idle_days = 15  # a") == [LIMITS], "a changed value"
    assert owner("-idle_days = 14", "+idle_days = 14.0") == [LIMITS], "the same number in another type"
    assert owner("-matches_page = 50  # a") == [LIMITS], "a removed key"
    assert owner("-matches_page = 50", "+matches_per_page = 50") == [LIMITS], "a renamed key"
    assert owner("+[extra]") == [LIMITS], "a line that is not one key and one value"
    assert owner("+[[extra]]") == [LIMITS], "an array-of-tables header moves every limit below it out of the top level"
    assert owner("+a = 1", "+b = 2") == [] and owner("+a = [1,", "+  2]") == [LIMITS], "a value over two lines is read by the owner"
    # a comment only: a comment line, and the comment after a value
    assert owner("-# old words", "+# new words") == []
    assert owner("-idle_days = 14  # old words", "+idle_days = 14     # new words") == [], "the value is the same"
    assert owner("-tool_sandbox = \"auto\"  # old", "+tool_sandbox = \"auto\"  # new") == [], "a comment on the owner's key"
    assert owner("-idle_days = 14", "+idle_days = 14", "+probe_workers_x = 1") == [], "a line moved with its value"
    assert owner("+x = 1", status="added") == [], "a new file is read by its lines"
    assert verdict.owned_changes(rules, [change(LIMITS, None)]) == [LIMITS], "no patch: nothing shows the value is the same"
    assert verdict.owned_changes(rules, [change(LIMITS, "@@ -1 +0,0 @@\n-a = 1", status="removed")]) == [LIMITS], "the file deleted"
    moved = change("config/limits.toml", "", status="renamed", previous_filename=LIMITS)
    assert verdict.owned_changes(rules, [moved]) == [LIMITS], "the file moved away"
    assert verdict.owned_changes(rules, [limits_change("+a = 1"), change("app/a.py")]) == [], "other files do not matter"
    assert verdict.owned_changes(rules, [limits_change("+a = 1"), change("pyproject.toml")]) == ["pyproject.toml"]
    assert verdict.owned_changes(rules, [{"filename": LIMITS}]) == [LIMITS], "a bare name has no patch"


AGENTS = "AGENTS.md"
# main's AGENTS.md in small: line 1 the title, 3-4 "## Rules", 6-8 "## Autonomous runs", 10-11 "## Traps"
MAIN = "# Title\n\n## Rules\n- rule\n\n## Autonomous runs\n- the merge bar\n- the rounds\n\n## Traps\n- trap\n"


def test_agents_md_is_the_owners_only_in_its_section_autonomous_runs():
    """The owner's call 2026-11-09: a hunk outside "## Autonomous runs" merges on the verdict; a hunk inside it, or
    one that adds, removes or moves a `## ` heading, waits for the owner. The section's place is read in main's copy."""
    rules = verdict.read_owners((REAL_REPO / ".github" / "CODEOWNERS").read_text())
    assert verdict.owned(rules, [AGENTS]) == [], "CODEOWNERS does not name it"

    def owner(patch: str | None, main: str | None = MAIN, **more: str) -> list[str]:
        return verdict.owned_changes(rules, [change(AGENTS, patch, **more)], main)

    # outside
    assert owner("@@ -3,2 +3,3 @@\n ## Rules\n - rule\n+- rule two\n+- rule three") == [], "a line of Rules"
    assert owner("@@ -10,2 +10,3 @@\n ## Traps\n - trap\n+- trap two") == [], "a line of Traps"
    assert owner("@@ -4,3 +4,4 @@\n - rule\n+- new rule\n \n ## Autonomous runs") == [], "added at the end of Rules, before the heading"
    assert owner("@@ -0,0 +1 @@\n+<!-- a note -->") == [], "added at the file's start"
    assert owner("@@ -4,0 +5 @@\n+- new rule") == [], "an insertion with no context, after line 4"
    # inside
    assert owner("@@ -6,3 +6,3 @@\n ## Autonomous runs\n-- the merge bar\n+- a lower bar\n - the rounds") == [AGENTS], "a changed line"
    assert owner("@@ -7,2 +7,3 @@\n - the merge bar\n - the rounds\n+- a new rule") == [AGENTS], "added at the section's end"
    assert owner("@@ -8,3 +8,4 @@\n - the rounds\n \n+- a new rule\n ## Traps") == [AGENTS], "added right before the next heading"
    assert owner("@@ -7,0 +8 @@\n+- a new rule") == [AGENTS], "an insertion with no context, after line 7"
    # the headings
    assert owner("@@ -5,2 +5,2 @@\n \n-## Autonomous runs\n+## Autonomous run") == [AGENTS], "the heading renamed"
    assert owner("@@ -3,2 +3,4 @@\n ## Rules\n - rule\n+\n+## Autonomous runs") == [AGENTS], "a second heading moves the section"
    assert owner("@@ -9,2 +9,1 @@\n \n-## Traps\n - trap") == [AGENTS], "the next heading removed: Traps joins the section"
    assert owner("@@ -3,2 +3,3 @@\n ## Rules\n - rule\n+## New") == [AGENTS], "any new heading"
    # main moved since the pull request's base: the hunk is found by its lines
    moved = "# Title\n\nintro\nmore\n" + MAIN[len("# Title\n"):]
    assert owner("@@ -3,2 +3,3 @@\n ## Rules\n - rule\n+- rule two", moved) == [], "found lower, in Rules"
    assert owner("@@ -7,2 +7,3 @@\n - the merge bar\n - the rounds\n+- new", moved) == [AGENTS], "found lower, in the section"
    assert owner("@@ -3,2 +3,3 @@\n ## Gone\n - x\n+- y") == [AGENTS], "lines main does not hold: the owner reads it"
    # the owner reads what cannot be placed
    assert owner(None) == [AGENTS], "no patch"
    assert owner("@@ -3,2 +3,3 @@\n ## Rules\n - rule\n+- x", None) == [AGENTS], "no copy of main's file"
    assert owner("@@ -3,2 +3,3 @@\n ## Rules\n - rule\n+- x", "# Title\n") == [AGENTS], "main has no such section"
    assert owner("", status="renamed", previous_filename="x") == [AGENTS], "the file moved"
    assert owner("@@ -1 +0,0 @@\n-# Title", status="removed") == [AGENTS], "the file deleted"
    assert owner("@@ -3,2 +3,3 @@\n ## Rules\n - rule\n+- x\n@@ -7,1 +8,2 @@\n - the merge bar\n+- y") == [AGENTS], "the second hunk is inside"


def test_the_section_guard_reads_the_real_agents_md():
    """The heading is in the shipped file once, so the guard has a section to keep."""
    lines = (REAL_REPO / AGENTS).read_text().splitlines()
    bounds = verdict.section_bounds(lines)
    assert bounds is not None and lines[bounds[0] - 1] == verdict.SECTION
    assert "The merge bar" in "\n".join(lines[bounds[0]:bounds[1] - 1])


def test_the_owned_changes_keep_codeowners_rules_for_both_names():
    rules = verdict.read_owners("/.github/ @o\n")
    out = change("tools/x.py", "", status="renamed", previous_filename=".github/x.py")
    assert verdict.owned_changes(rules, [out, change(".github/y.yml"), change("app/a.py")]) == [".github/x.py", ".github/y.yml"]
    assert verdict.owned_changes(rules, [change(".github/y.yml"), change(".github/y.yml")]) == [".github/y.yml"], "each name once"


def test_owned_reads_the_files_api_one_object_a_line(tmp_path):
    """The command as the job `report` runs it: `gh api .../files --paginate --jq '.[]' | verdict.py owned CODEOWNERS`.
    A bare path, with no patch, still works. The browser checks' list is no guard."""
    lines = [json.dumps(change(CHECKS)), json.dumps(change("src/a.py")), json.dumps(change(".githooks/x"))]

    def run(text: str) -> list[str]:
        out = subprocess.run([sys.executable, "-I", str(REAL_REPO / ".github" / "verdict.py"), "owned", str(REAL_REPO / ".github" / "CODEOWNERS")],
                             input=text, capture_output=True, text=True, check=True)
        return out.stdout.splitlines()

    assert run("\n".join(lines) + "\n") == [".githooks/x"]
    assert run(json.dumps(change(CHECKS, "@@ -1,2 +1 @@\n a\n-b")) + "\n") == []
    assert run(f"src/a.py\n{CHECKS}\n.githooks/x\n\n") == [".githooks/x"]


def test_owned_reads_the_limits_file_and_the_owners_keys_from_the_environment():
    """Another product names its own limits file and the owner's keys; the defaults are this product's."""
    added = "@@ -1,1 +1,2 @@\n a = 1\n+{key} = 2"

    def run(files: list[dict], **env: str) -> list[str]:
        text = "".join(json.dumps(one) + "\n" for one in files)
        out = subprocess.run([sys.executable, "-I", str(REAL_REPO / ".github" / "verdict.py"), "owned", str(REAL_REPO / ".github" / "CODEOWNERS")],
                             input=text, capture_output=True, text=True, check=True, env={"PATH": "/usr/bin:/bin", **env})
        return out.stdout.splitlines()

    other = {"OWNER_LIMITS_FILE": "quota.toml", "OWNER_LIMITS": "seats", "OWNER_LIMIT_PREFIXES": "team_"}
    assert run([change("quota.toml", added.format(key="seats"))], **other) == ["quota.toml"]
    assert run([change("quota.toml", added.format(key="team_cap"))], **other) == ["quota.toml"]
    assert run([change("quota.toml", added.format(key="monthly_budget"))], **other) == [], "the default keys are gone"
    assert run([change(LIMITS, added.format(key="monthly_budget"))], **other) == [], "the default file is no limits file now"
    assert run([change(LIMITS, added.format(key="monthly_budget"))]) == [LIMITS]
    assert run([change(LIMITS, added.format(key="free_seats"))]) == [LIMITS]
    assert run([change(LIMITS, added.format(key="a_new_key"))]) == []


def test_owned_reads_mains_agents_md_from_the_current_folder(tmp_path):
    """The workflows run `verdict.py owned` in a checkout of main, and the command reads
    the section's place from the AGENTS.md there. With no AGENTS.md in the folder, every change of it is the owner's."""
    outside = json.dumps(change(AGENTS, "@@ -3,2 +3,3 @@\n ## Rules\n - rule\n+- rule two")) + "\n"
    inside = json.dumps(change(AGENTS, "@@ -7,2 +7,3 @@\n - the merge bar\n - the rounds\n+- a new rule")) + "\n"

    def run(text: str, folder: Path) -> list[str]:
        out = subprocess.run([sys.executable, "-I", str(REAL_REPO / ".github" / "verdict.py"), "owned", str(REAL_REPO / ".github" / "CODEOWNERS")],
                             input=text, capture_output=True, text=True, check=True, cwd=folder)
        return out.stdout.splitlines()

    main = tmp_path / "main"
    main.mkdir()
    (main / AGENTS).write_text(MAIN)
    assert run(outside, main) == [] and run(inside, main) == [AGENTS]
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run(outside, empty) == [AGENTS], "no copy of main's file: the owner reads the change"


def test_a_double_star_also_matches_no_folder():
    rules = verdict.read_owners("**/foo @o\na/**/b @o\nc/** @o\n")
    paths = ["foo", "x/y/foo", "a/b", "a/x/y/b", "a/b/z.md", "c/d.md", "ab", "c"]
    assert verdict.owned(rules, paths) == ["foo", "x/y/foo", "a/b", "a/x/y/b", "a/b/z.md", "c/d.md"], "gitignore's ** is zero folders or more"


def test_a_dismissal_fails_the_check_unless_the_last_verdict_is_still_an_approve():
    approve = review("APPROVED", "approve", "a" * 40, "2026-10-25T10:00:00Z")
    dismissed = review("DISMISSED", "approve", "a" * 40, "2026-10-25T10:00:00Z")
    assert verdict.dismissed_body(verdict.last_verdict([approve], BOT), "b" * 40) is None
    body = verdict.dismissed_body(verdict.last_verdict([dismissed], BOT), "b" * 40)
    assert body["conclusion"] == "failure" and body["output"]["title"] == "Dismissed" and body["head_sha"] == "b" * 40


def test_only_a_needs_decision_escalates():
    """A needs-decision sends the issue to Escalated (agents/reviewer.md "The verdict"), so the review workflow adds
    the label `escalated` to the pull request. A request for changes, an approve or a dismissed verdict does not."""
    when = "2026-10-25T10:00:00Z"
    assert verdict.escalates(verdict.last_verdict([review("CHANGES_REQUESTED", "needs-decision", "a" * 40, when, round=2)], BOT))
    for state, word in (("CHANGES_REQUESTED", "request-changes"), ("APPROVED", "approve"), ("DISMISSED", "needs-decision")):
        assert not verdict.escalates(verdict.last_verdict([review(state, word, "a" * 40, when)], BOT)), (state, word)
    assert not verdict.escalates(None)


def test_escalates_reads_only_the_verdicts_since_the_run_began(tmp_path):
    reviews = tmp_path / "reviews.jsonl"
    reviews.write_text(json.dumps(review("CHANGES_REQUESTED", "needs-decision", "a" * 40, "2026-10-25T10:00:00Z")) + "\n")
    script = str(REAL_REPO / ".github" / "verdict.py")
    for since, answer in (("2026-10-25T09:00:00Z", "true"), ("2026-10-25T11:00:00Z", "false")):
        run = subprocess.run([sys.executable, "-I", script, "escalates", str(reviews), "--reviewer", BOT, "--since", since],
                             capture_output=True, text=True, check=True)
        assert run.stdout == f"{answer}\n", since


UPDATER = "maurolopes-implementer[bot]"


def conflict(when: str, user: str = UPDATER, body: str = verdict.CONFLICT + " Auto-merge is off.") -> dict:
    return {"user": {"login": user}, "body": body, "created_at": when}


def test_a_conflict_on_an_approved_pull_request_starts_the_count_again():
    """The owner's call, 2026-10-28: the branch update's conflict comment on an approved pull request starts the
    rounds again, so the review after the resolution is round 1, not a third round that nobody reviews."""
    approve = review("APPROVED", "approve", "a" * 40, "2026-10-28T10:00:00Z", round=2)
    changes = review("CHANGES_REQUESTED", "request-changes", "a" * 40, "2026-10-28T09:00:00Z")
    late = "2026-10-28T11:00:00Z"
    assert verdict.rounds_since([changes, approve], [conflict(late)], BOT, UPDATER) == late
    assert verdict.rounds_since([changes, approve], [conflict(late)], BOT, UPDATER, "2026-10-28T12:00:00Z") == "2026-10-28T12:00:00Z", \
        "the owner's later answer counts"
    assert verdict.rounds_since([changes, approve], [], BOT, UPDATER, "x") == "x"


def test_only_the_branch_updates_comment_on_an_approve_counts():
    """A run cannot escape the three rounds with a comment: the text must be the branch update's, from its app, after an
    approve that is still the reviewer's last verdict."""
    approve = review("APPROVED", "approve", "a" * 40, "2026-10-28T10:00:00Z")
    changes = review("CHANGES_REQUESTED", "request-changes", "a" * 40, "2026-10-28T10:30:00Z")
    dismissed = dict(approve, state="DISMISSED", submitted_at="2026-10-28T10:00:00Z")
    late = "2026-10-28T11:00:00Z"
    assert verdict.rounds_since([approve, changes], [conflict(late)], BOT, UPDATER) == "", "the last verdict asks for changes"
    assert verdict.rounds_since([dismissed], [conflict(late)], BOT, UPDATER) == "", "a dismissed approve is no approve"
    assert verdict.rounds_since([approve], [conflict("2026-10-28T09:00:00Z")], BOT, UPDATER) == "", "a comment before any verdict"
    assert verdict.rounds_since([approve], [conflict(late, user="maurolopes")], BOT, UPDATER) == "", "another author"
    assert verdict.rounds_since([approve], [conflict(late, body="Please count again. " + verdict.CONFLICT)], BOT, UPDATER) == "", \
        "the comment's first words only"


def test_failed_gates_and_an_update_the_approve_does_not_cover_start_the_count_again():
    """The owner's calls, 2026-10-28: the branch update's comment on failed gates (the implementer app's) and
    `carry`'s comment on an update with other changed lines (the reviewer app's, written earlier) start the rounds again, like a conflict.
    Each counts only from its own app."""
    approve = review("APPROVED", "approve", "a" * 40, "2026-10-28T10:00:00Z", round=2)
    late = "2026-10-28T11:00:00Z"
    failed = conflict(late, body=verdict.FAILED_GATES + " (https://run). An implementer fixes it.")
    changed = conflict(late, user=BOT, body=verdict.NOT_CARRIED + " The review runs again as round 1.")
    assert verdict.rounds_since([approve], [failed], BOT, UPDATER) == late
    assert verdict.rounds_since([approve], [changed], BOT, UPDATER) == late
    assert verdict.rounds_since([approve], [dict(failed, user={"login": BOT})], BOT, UPDATER) == "", "not the reviewer's words"
    assert verdict.rounds_since([approve], [dict(changed, user={"login": UPDATER})], BOT, UPDATER) == "", "not the updater's words"
    assert verdict.rounds_since([approve], [conflict(late, user=BOT)], BOT, UPDATER) == "", "a conflict is the updater's"


RUN = {"id": 42, "event": "workflow_dispatch", "triggering_actor": {"login": "maurolopes"}, "status": "completed",
       "display_title": "Claude Code Review by hand: PR #4, round 1",
       "created_at": "2026-11-05T10:00:00Z", "updated_at": "2026-11-05T10:20:00Z"}


def manual(when: str, run: int = 42, user: str = BOT) -> dict:
    return {"user": {"login": user}, "body": f"{verdict.MANUAL} (run {run}). The next ready mark is round 2.", "created_at": when}


def test_the_owners_run_by_hand_with_round_1_starts_the_count_again():
    """(the owner's call, 2026-11-05: option A): a run by hand with round 1 asked for changes, and the next
    ready mark counted round 4. The run's comment now starts the count: its own verdict is round 1, the next mark round 2.
    Unlike the other comments, it needs no approve before it: the owner's run follows the owner's answer."""
    old = [review("CHANGES_REQUESTED", "request-changes", "a" * 40, f"2026-11-05T0{h}:00:00Z") for h in (7, 8, 9)]
    run_verdict = review("CHANGES_REQUESTED", "request-changes", "b" * 40, "2026-11-05T10:10:00Z")
    since = verdict.rounds_since([*old, run_verdict], [manual("2026-11-05T10:01:00Z")], BOT, UPDATER, "", [RUN], "maurolopes",
                                 4)
    assert since == "2026-11-05T10:01:00Z"
    counted = [r for r in [*old, run_verdict] if r["submitted_at"] > since]
    assert counted == [run_verdict], "the verdicts before the run are not counted: the next ready mark is round 2"
    assert verdict.rounds_since(old, [manual("2026-11-05T10:01:00Z")], BOT, UPDATER, "2026-11-05T11:00:00Z", [RUN],
                                "maurolopes", 4) == "2026-11-05T11:00:00Z", "a later answer of the owner counts"


def test_only_the_owners_run_by_hand_makes_the_comment_count():
    """The model posts with the reviewer app's token too, so a comment alone must not start the rounds again: it counts only
    for a workflow_dispatch run that the owner started, while that run ran, and once per run."""
    when = "2026-11-05T10:01:00Z"

    def since(comments: list[dict], runs: list[dict] = [RUN]) -> str:  # noqa: B006 (read only)
        return verdict.rounds_since([], comments, BOT, UPDATER, "", runs, "maurolopes", 4)

    assert since([manual(when)]) == when
    assert since([manual(when)], []) == "", "no run list (a failed request): no reset"
    assert since([manual(when, run=7)]) == "", "a run that is not in the list"
    assert since([manual(when)], [dict(RUN, event="pull_request")]) == "", "a ready mark's run, not a run by hand"
    assert since([manual(when)], [dict(RUN, triggering_actor={"login": "maurolopes-implementer[bot]"})]) == "", \
        "carry's and report's runs by an app"
    assert since([manual(when, user="maurolopes")]) == "", "not the reviewer app's comment"
    assert since([manual("2026-11-05T11:00:00Z")]) == "", "after the run ended"
    assert since([manual("2026-11-05T09:00:00Z")]) == "", "before the run began"
    assert since([manual("2026-11-05T11:00:00Z")], [dict(RUN, status="in_progress")]) == "2026-11-05T11:00:00Z", \
        "a run still going has no end yet"
    assert since([manual(when), manual("2026-11-05T10:15:00Z")]) == when, "only the first comment that names the run"
    assert since([{**manual(when), "body": "Please count again. " + manual(when)["body"]}]) == "", "the first words only"
    # The model could name the owner's run with round 2, or the owner's run on another pull request
    assert since([manual(when)], [dict(RUN, display_title="Claude Code Review by hand: PR #4, round 2")]) == "", \
        "the owner's run with round 2"
    assert since([manual(when)], [dict(RUN, display_title="Claude Code Review by hand: PR #5, round 1")]) == "", \
        "the owner's run on another pull request"
    assert since([manual(when)], [dict(RUN, display_title="Claude Code Review by hand: PR #43, round 1")]) == "", \
        "the whole title, not its start"
    assert verdict.rounds_since([], [manual(when)], BOT, UPDATER, "", [RUN], "maurolopes") == "", "no pull request named"


def test_since_on_the_command_line(tmp_path: Path):
    approve = review("APPROVED", "approve", "a" * 40, "2026-10-28T10:00:00Z")
    (tmp_path / "reviews.jsonl").write_text(json.dumps(approve) + "\n")
    (tmp_path / "comments.jsonl").write_text(json.dumps(conflict("2026-10-28T11:00:00Z")) + "\n")
    run = subprocess.run([sys.executable, "-I", str(REAL_REPO / ".github" / "verdict.py"), "since", str(tmp_path / "reviews.jsonl"),
                          str(tmp_path / "comments.jsonl"), "--reviewer", BOT, "--updater", UPDATER, "--since", ""],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0 and run.stdout.strip() == "2026-10-28T11:00:00Z", run.stderr
    (tmp_path / "comments.jsonl").write_text("")
    run = subprocess.run([sys.executable, "-I", str(REAL_REPO / ".github" / "verdict.py"), "since", str(tmp_path / "reviews.jsonl"),
                          str(tmp_path / "comments.jsonl"), "--reviewer", BOT, "--updater", UPDATER, "--since", "x"],
                         capture_output=True, text=True, check=False)
    assert run.stdout.strip() == "x"
    (tmp_path / "comments.jsonl").write_text(json.dumps(manual("2026-11-05T10:01:00Z")) + "\n")
    (tmp_path / "runs.jsonl").write_text(json.dumps(RUN) + "\n")
    base = [sys.executable, "-I", str(REAL_REPO / ".github" / "verdict.py"), "since", str(tmp_path / "reviews.jsonl"),
            str(tmp_path / "comments.jsonl"), "--reviewer", BOT, "--updater", UPDATER, "--since", ""]
    run = subprocess.run([*base, "--runs", str(tmp_path / "runs.jsonl"), "--owner", "maurolopes", "--pr", "4"],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0 and run.stdout.strip() == "2026-11-05T10:01:00Z", run.stderr
    run = subprocess.run(base, capture_output=True, text=True, check=False)
    assert run.returncode == 0 and run.stdout.strip() == "", "without the runs the comment counts for nothing"


# The owner's Approved on GitHub is the label `owner-approved` (the owner's call, 2026-10-30), posted by the review
# workflow as the reviewer app's approve, so every command reads it as an approve.
OWNER = "maurolopes"


def owner_approve(labeled: str, **change) -> tuple[dict | None, str]:
    args = {"label": verdict.OWNER_LABEL, "sender": OWNER, "actor": OWNER, "owner": OWNER, "draft": False, "pr": 7, "head": labeled,
            **change}
    return verdict.owner_review(**args)


def as_posted(approve: dict, when: str) -> dict:
    """The review GitHub gives back for the approve the workflow posted."""
    return {"user": {"login": BOT}, "state": "APPROVED", "body": approve["body"], "commit_id": approve["commit_id"],
            "submitted_at": when, "html_url": "https://github.com/o/r/pull/7#review-9"}


def test_only_the_owners_own_label_gives_an_approve():
    approve, why = owner_approve("a" * 40)
    assert why == "" and approve["event"] == "APPROVE" and approve["commit_id"] == "a" * 40
    refused = {"another label": {"label": "escalated"}, "another person": {"sender": "someone"},
               "an app": {"sender": "maurolopes-implementer[bot]"}, "a re-run by another account": {"actor": "someone"},
               "no owner": {"owner": ""}, "a draft": {"draft": True}, "no commit": {"head": "main"}}
    for name, change in refused.items():
        approve, why = owner_approve("a" * 40, **change)
        assert approve is None and why, name
    assert "someone added the label" in owner_approve("a" * 40, sender="someone")[1]


def test_the_owners_approve_is_an_approve_of_the_labeled_head():
    approve, _ = owner_approve("a" * 40)
    last = verdict.last_verdict([review("CHANGES_REQUESTED", "needs-decision", "c" * 40, "2026-10-30T09:00:00Z"),
                                 as_posted(approve, "2026-10-30T11:00:00Z")], BOT)
    assert verdict.approves(last) and last["commit"] == "a" * 40 and last["owner"] == OWNER
    body = verdict.check_body(last, "a" * 40)
    assert body["conclusion"] == "success" and body["output"]["title"] == "Approved by the owner"
    assert not verdict.escalates(last), "the needs-decision before it is answered"
    reviewer = verdict.last_verdict([review("APPROVED", "approve", "a" * 40, "t", round=2)], BOT)
    assert verdict.check_body(reviewer, "a" * 40)["output"]["title"] == "Approved, round 2", "the reviewer's own approve keeps its title"


def test_a_head_pushed_after_the_owners_approve_gets_the_rules_of_any_approve(repo):
    """The rules: a branch update carries the owner's approve; a push beyond it carries nothing and gets a new review; an update that changed
    the diff against main is carried too."""
    labeled = git(repo, "rev-parse", "HEAD")
    approve, _ = owner_approve(labeled)
    last = verdict.last_verdict([as_posted(approve, "t")], BOT)
    update(repo)
    body, why = verdict.carry_body(last, git(repo, "rev-parse", "HEAD"), "main")
    assert why == "" and body["conclusion"] == "success"
    commit(repo, "c.txt", "new\n")
    assert verdict.carry_body(last, git(repo, "rev-parse", "HEAD"), "main")[0] is None
    assert verdict.rereview(last, git(repo, "rev-parse", "HEAD"), "main"), "A real commit gets a review, owner's approve too"


def test_owner_on_the_command_line():
    script = str(REAL_REPO / ".github" / "verdict.py")
    base = [sys.executable, "-I", script, "owner", "--label", "owner-approved", "--actor", OWNER, "--owner", OWNER, "--draft", "false",
            "--pr", "7", "--head", "a" * 40]
    run = subprocess.run([*base, "--sender", OWNER], capture_output=True, text=True, check=False)
    assert run.returncode == 0 and json.loads(run.stdout)["commit_id"] == "a" * 40, run.stderr
    run = subprocess.run([*base, "--sender", "someone"], capture_output=True, text=True, check=False)
    assert run.returncode == 0 and run.stdout == "" and "not the repository owner" in run.stderr


def test_the_comment_on_a_push_during_the_review_starts_the_rounds_again():
    comment = {"user": {"login": BOT}, "body": verdict.PUSHED + ", so the approve does not cover the new head."}
    assert verdict.restarts(comment, BOT, "updater")


# On 2026-11-04 round 2 approved dc598d3f at 13:18, the gate `web` failed, the implementer app commented
# the failed gates at 13:21, and the owner's rerun of `web` passed at 13:49 on the same head.
HEAD_411 = "dc598d3f" + "0" * 32
APPROVED_411 = review("APPROVED", "approve", HEAD_411, "2026-11-04T13:18:00Z", round=2)
FAILED_411 = conflict("2026-11-04T13:21:00Z", body=verdict.FAILED_GATES + " (https://run). An implementer fixes what made them fail.")


def rerun(reviews: list[dict], comments: list[dict], head: str = HEAD_411, gates: str = "success"):
    return verdict.rerun_body(verdict.last_verdict(reviews, BOT), comments, head, BOT, UPDATER, gates)


def test_a_rerun_that_passes_on_the_approved_head_gives_the_check_back():
    body, why = rerun([APPROVED_411], [FAILED_411])
    assert why == ""
    assert body is not None
    assert (body["name"], body["head_sha"], body["status"], body["conclusion"]) == ("verdict", HEAD_411, "completed", "success")
    assert "rerun" in body["output"]["title"]


def test_a_rerun_gives_nothing_for_another_head_or_gates_that_did_not_pass():
    assert rerun([APPROVED_411], [FAILED_411], head="b" * 40)[0] is None, "the head changed after the approve"
    for gates in ("failure", "cancelled", "skipped", ""):
        assert rerun([APPROVED_411], [FAILED_411], gates=gates)[0] is None, gates


def test_a_rerun_gives_nothing_when_the_verdict_failed_for_another_reason():
    assert rerun([APPROVED_411], [])[0] is None, "no comment: the check did not fail"
    conflicted = conflict("2026-11-04T13:30:00Z")
    assert rerun([APPROVED_411], [FAILED_411, conflicted])[0] is None, "the last restart is a conflict"
    assert rerun([APPROVED_411], [conflict("2026-11-04T13:30:00Z", user=BOT, body=verdict.PUSHED)])[0] is None
    assert rerun([APPROVED_411], [dict(FAILED_411, user={"login": "maurolopes"})])[0] is None, "not the updater's words"
    changes = review("CHANGES_REQUESTED", "request-changes", HEAD_411, "2026-11-04T13:40:00Z")
    assert rerun([APPROVED_411, changes], [FAILED_411])[0] is None, "the last verdict asks for changes"
    assert rerun([], [FAILED_411])[0] is None, "no verdict"
    older = conflict("2026-11-04T13:00:00Z", body=verdict.FAILED_GATES + " (https://run).")
    assert rerun([APPROVED_411], [older])[0] is None, "the comment is older than the approve"


def test_a_later_approve_on_the_same_head_needs_its_own_failed_gates_comment():
    """A new review of the same head (the owner's ready mark) wrote its own check: only a comment after it counts."""
    third = review("APPROVED", "approve", HEAD_411, "2026-11-04T13:43:00Z", round=1)
    assert rerun([APPROVED_411, third], [FAILED_411])[0] is None
    again = conflict("2026-11-04T13:44:00Z", body=verdict.FAILED_GATES + " (https://run).")
    assert rerun([APPROVED_411, third], [FAILED_411, again])[0] is not None


def test_a_rerun_on_the_command_line(tmp_path):
    reviews, comments = tmp_path / "reviews.jsonl", tmp_path / "comments.jsonl"
    reviews.write_text(json.dumps(APPROVED_411) + "\n")
    comments.write_text(json.dumps(FAILED_411) + "\n")
    script = str(REAL_REPO / ".github" / "verdict.py")

    def run(gates: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-I", script, "rerun", str(reviews), str(comments), "--reviewer", BOT,
                               "--updater", UPDATER, "--head", HEAD_411, "--gates", gates], capture_output=True, text=True, check=True)

    assert json.loads(run("success").stdout)["conclusion"] == "success"
    failed = run("failure")
    assert failed.stdout == "" and "did not pass" in failed.stderr


def label_event(kind: str, actor: str, when: str, name: str = "owner-approved") -> dict:
    return {"event": kind, "actor": {"login": actor}, "created_at": when, "label": {"name": name}}


def test_the_owners_label_lifts_the_guard_only_on_githubs_events_after_the_heads_first_run():
    """The last event of the label decides, its actor is the repository owner, and it is newer than the head's run."""
    since = "2026-11-06T10:00:00Z"
    ok = [label_event("labeled", "maurolopes", "2026-11-06T11:00:00Z")]
    assert verdict.owner_label_stands(ok, "maurolopes", since) == (True, "")
    cases = {
        "no event": [],
        "another label": [label_event("labeled", "maurolopes", "2026-11-06T11:00:00Z", "escalated")],
        "another account": [label_event("labeled", "someone", "2026-11-06T11:00:00Z")],
        "an app": [label_event("labeled", "maurolopes-implementer[bot]", "2026-11-06T11:00:00Z")],
        "taken off": ok + [label_event("unlabeled", "maurolopes", "2026-11-06T12:00:00Z")],
        "put back by another": ok + [label_event("unlabeled", "maurolopes", "2026-11-06T12:00:00Z"),
                                      label_event("labeled", "someone", "2026-11-06T13:00:00Z")],
        "on an older head": [label_event("labeled", "maurolopes", "2026-11-06T09:00:00Z")],
    }
    for name, events in cases.items():
        stands, why = verdict.owner_label_stands(events, "maurolopes", since)
        assert not stands and why, name
    assert not verdict.owner_label_stands(ok, "", since)[0], "no owner"
    assert not verdict.owner_label_stands(ok, "maurolopes", "")[0], "no time to compare"
    # the label put on again by the owner after a removal stands
    again = ok + [label_event("unlabeled", "maurolopes", "2026-11-06T12:00:00Z"), label_event("labeled", "maurolopes", "2026-11-06T13:00:00Z")]
    assert verdict.owner_label_stands(again, "maurolopes", since)[0]


def test_labeled_on_the_command_line(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps(label_event("labeled", "maurolopes", "2026-11-06T11:00:00Z")) + "\n")
    script = REAL_REPO / ".github" / "verdict.py"

    def run(owner: str) -> str:
        done = subprocess.run([sys.executable, "-I", str(script), "labeled", str(events), "--owner", owner,
                               "--since", "2026-11-06T10:00:00Z"], capture_output=True, text=True, check=True)
        return done.stdout.strip()

    assert run("maurolopes") == "true" and run("someone") == "false"


def stay(entered: str, left: str, to: str) -> dict:
    return {"entered": entered, "left": left, "to": to}


def test_answered_counts_an_exit_to_in_progress_that_linear_merged_with_the_claim():
    """The owner moved the issue from Escalated to Todo, and a session claimed it at once. Linear merged both moves
    into one exit to In Progress. No verdict came while the issue was in Escalated, so that exit is the owner's answer. The
    needs-decision that sent it there came before the stay."""
    escalate = review("CHANGES_REQUESTED", "needs-decision", "a" * 40, "2026-11-10T20:00:00Z", round=2)
    stays = [stay("2026-11-10T20:00:05Z", "2026-11-10T23:49:54Z", "In Progress")]
    assert verdict.answered(stays, [escalate], BOT) == "2026-11-10T23:49:54Z"
    assert verdict.answered([], [escalate], BOT) == "", "never in Escalated: the count starts at the first verdict"


def test_answered_skips_the_reviewers_move_after_a_request_for_changes():
    """A review that `carry` started on an issue in Escalated asks for changes, and the reviewer moves the issue to
    In Progress. That exit answers nothing: the rounds count from `carry`'s comment, so the fix after it is round 2. The
    owner's earlier answer is still the last one."""
    carried = review("CHANGES_REQUESTED", "request-changes", "a" * 40, "2026-10-24T09:59:00Z")
    stays = [stay("2026-10-21T10:00:00Z", "2026-10-22T10:00:00Z", "Canceled"),
             stay("2026-10-23T10:00:00Z", "2026-10-24T10:00:00Z", "In Progress")]
    assert verdict.answered(stays, [carried], BOT) == "2026-10-22T10:00:00Z"
    assert verdict.answered(stays, [dict(carried, user={"login": "maurolopes"})], BOT) == "2026-10-24T10:00:00Z", \
        "only the reviewer's verdict moves an issue"
    before = dict(carried, submitted_at="2026-10-23T09:00:00Z")
    assert verdict.answered(stays, [before], BOT) == "2026-10-24T10:00:00Z", "a verdict before the stay is not during it"


def test_answered_reads_the_last_verdict_during_the_stay():
    """A review that `carry` started can end with a needs-decision: the issue stays in Escalated, and the owner's answer
    after it is an answer. A request-changes before a later needs-decision did not cause the exit."""
    changes = review("CHANGES_REQUESTED", "request-changes", "a" * 40, "2026-10-24T09:00:00Z")
    decide = review("CHANGES_REQUESTED", "needs-decision", "b" * 40, "2026-10-24T10:00:00Z", round=2)
    stays = [stay("2026-10-23T10:00:00Z", "2026-10-25T10:00:00Z", "In Progress")]
    assert verdict.answered(stays, [decide, changes], BOT) == "2026-10-25T10:00:00Z"
    assert verdict.answered(stays, [changes], BOT) == ""


def test_answered_takes_the_answer_states_whatever_the_verdicts():
    """An exit to Todo, Approved or Canceled is always an answer (the workflows' moves to Approved carry the owner's label or
    rerun); an exit to any other state is none."""
    changes = review("CHANGES_REQUESTED", "request-changes", "a" * 40, "2026-10-24T09:00:00Z")
    for to in verdict.ANSWERS:
        assert verdict.answered([stay("2026-10-23T10:00:00Z", "2026-10-25T10:00:00Z", to)], [changes], BOT) == "2026-10-25T10:00:00Z"
    assert verdict.answered([stay("2026-10-23T10:00:00Z", "2026-10-25T10:00:00Z", "In Review")], [], BOT) == ""


def test_answered_on_the_command_line(tmp_path):
    """The review workflow passes `linear.py escalated`'s JSON and the reviews file; an empty stays file (no issue, or a
    failed read) prints nothing, so the count starts at the first verdict, as before."""
    (tmp_path / "reviews.jsonl").write_text(json.dumps(review("APPROVED", "approve", "a" * 40, "2026-10-24T09:00:00Z")) + "\n")
    (tmp_path / "stays.json").write_text(json.dumps([stay("2026-10-23T10:00:00Z", "2026-10-25T10:00:00Z", "In Progress")]))
    script = str(REAL_REPO / ".github" / "verdict.py")
    for stays, out in (("stays.json", "2026-10-25T10:00:00Z"), ("empty.json", "")):
        (tmp_path / "empty.json").write_text("")
        run = subprocess.run([sys.executable, "-I", script, "answered", str(tmp_path / stays), str(tmp_path / "reviews.jsonl"),
                              "--reviewer", BOT], capture_output=True, text=True, check=True)
        assert run.stdout.strip() == out


def test_a_merged_or_closed_pull_request_ends_the_moves_of_its_review(tmp_path):
    """A step that moves an issue from a review asks `ended` first. MERGED and CLOSED give the log line and exit 0,
    so nothing moves; OPEN, and an empty state (gh could not read it), exit 1 and keep the move."""
    assert verdict.ended("MERGED", 7, "ABC-9") == "PR #7 is merged: ABC-9 stays as it is"
    assert verdict.ended("closed\n", 7, "ABC-9") == "PR #7 is closed: ABC-9 stays as it is"
    assert verdict.ended("MERGED", 7) == "PR #7 is merged: its issue stays as it is"
    assert verdict.ended("OPEN", 7, "ABC-9") == "" and verdict.ended("", 7, "ABC-9") == ""
    script = REAL_REPO / ".github" / "verdict.py"
    for state, code in (("MERGED", 0), ("CLOSED", 0), ("OPEN", 1), ("", 1)):
        done = subprocess.run([sys.executable, "-I", str(script), "ended", state, "--pr", "7", "--key", "ABC-9"],
                              capture_output=True, text=True, check=False)
        assert done.returncode == code, (state, done.stderr)
        assert done.stdout == (f"::notice::PR #7 is {state.lower()}: ABC-9 stays as it is\n" if code == 0 else "")
