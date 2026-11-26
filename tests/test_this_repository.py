"""This repository's own set-up, which a product does not copy (.workflow/loop-files.txt leaves this file out): its guards
(.github/CODEOWNERS), its gates workflow and its scripts, and the rows of its README.md and the tasks of its mise.toml
that a product writes in its own words (OWL-45)."""

import glob
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.test_attribution import CHECK, commit, env_for_git
from tests.test_verdict import verdict

REPO = Path(__file__).resolve().parents[1]
OWNER = "@maurolopes"

_spec = importlib.util.spec_from_file_location("leak_guard", REPO / ".github" / "leak_guard.py")
assert _spec and _spec.loader
leak_guard = importlib.util.module_from_spec(_spec)
sys.modules["leak_guard"] = leak_guard
_spec.loader.exec_module(leak_guard)


def owners_lines() -> list[list[str]]:
    text = (REPO / ".github" / "CODEOWNERS").read_text()
    return [line.split() for line in text.splitlines() if line.strip() and not line.startswith("#")]


def test_every_line_of_the_code_owners_names_a_path_of_this_repository() -> None:
    """A line is a path with the owner, or a path alone (a file that merges on the verdict inside an owned folder). A
    path that names nothing here guards nothing: a product's path (its code, its deploy files) has no place in it."""
    lines = owners_lines()
    assert lines and all(len(l) == 1 or (len(l) == 2 and l[1] == OWNER) for l in lines), lines
    for path in (l[0] for l in lines):
        assert path.startswith("/"), path
        assert glob.glob(str(REPO / path.strip("/")), include_hidden=True), f"{path} names no file of this repository"


def test_the_guards_and_the_gates_tools_are_the_owners() -> None:
    owned = {l[0] for l in owners_lines() if len(l) == 2}
    for path in ("/.github/", "/.githooks/", "/.workflow/", "/.claude/", "/.agents/", "/.agents/skills/*/SKILL.md", "/.pi/",
                 "/agents/", "/mise.toml", "/pyproject.toml", "/uv.lock", "/.python-version", "/.gitleaks.toml", "/tests/conftest.py"):
        assert path in owned, path
    alone = [l[0] for l in owners_lines() if len(l) == 1]
    assert alone == ["/.workflow/agent_user.py", "/.agents/skills/", "/agents/README.md", "/agents/implementer.md", "/agents/planner.md"]
    assert "/AGENTS.md" not in owned, "AGENTS.md is the owner's only in its section Autonomous runs: verdict.py holds it"


def test_the_gates_run_every_gate_on_githubs_runner_with_the_whole_history() -> None:
    """The job `python` is the required check (AGENTS.md "Rules"): it runs `mise run gates` (gitleaks reads every commit,
    so the checkout has the whole history) on a pull request and on main. The job never skips as a whole (OWL-30: GitHub counts
    a skipped required check as passed, so a skipped job would stand for a gate that did not run): on a docs-only change the job
    `changes` finds, its steps are skipped and the job passes, and a failed `changes` fails the job in its first step."""
    flow = yaml.safe_load((REPO / ".github" / "workflows" / "gates.yml").read_text())
    on = flow[True]  # YAML 1.1 reads the key `on` as true
    assert set(on) == {"pull_request", "push", "workflow_dispatch"} and on["push"] == {"branches": ["main"]}
    assert flow["permissions"] == {"contents": "read"} and set(flow["jobs"]) == {"changes", "python"}
    plan = flow["jobs"]["changes"]
    assert [s["run"] for s in plan["steps"] if "run" in s] == ["sh .github/gates-plan.sh", "sh .github/gates-attribution.sh"]
    job = flow["jobs"]["python"]
    assert job["needs"] == "changes" and job["if"] == "${{ !cancelled() }}", "it runs also when `changes` failed, never skips"
    assert job["runs-on"] == "ubuntu-latest" and job["timeout-minutes"] <= 20
    refuse, *work = job["steps"]
    assert refuse["if"] == "needs.changes.result != 'success'" and refuse["run"].endswith("exit 1"), "a failed `changes` fails the gate"
    assert all(s["if"] == "needs.changes.outputs.needed == 'python'" for s in work), "a docs-only change skips the steps, not the job"
    checkout = work[0]
    assert checkout["uses"].startswith("actions/checkout@") and checkout["with"]["fetch-depth"] == 0
    assert checkout["with"]["persist-credentials"] is False
    assert [s["run"] for s in work if "run" in s] == ["uv sync --frozen", "mise run gates"]
    gates = (REPO / "mise.toml").read_text().split('[tasks.gates]', 1)[1].split("\n[", 1)[0]
    assert "gitleaks git" in gates and "uv run pytest" in gates


def test_a_docs_only_change_needs_no_gate_and_any_other_change_needs_python(tmp_path: Path) -> None:
    import subprocess

    def plan(names: str) -> str:
        return subprocess.run(["sh", str(REPO / ".github" / "gates-needed.sh")], input=names, capture_output=True, text=True,
                              check=True).stdout.strip()

    assert plan("README.md\nagents/README.md\n") == "none"
    assert plan("README.md\ntests/test_ci.py\n") == "python"
    assert plan("orchestrator/README.md\n") == "python", "a doc in a folder of code is code"


def test_the_readme_gives_the_owners_admin_row_through_the_launcher() -> None:
    text = (REPO / "README.md").read_text()
    assert "`owlcode admin`" in text
    assert "sudo /usr/bin/python3 -I .workflow/agent_user.py" not in text


# -- .github/gates-attribution.sh: this repository's own script (a product has its own, with another interface) ----------

GATES = REPO / ".github" / "gates-attribution.sh"


@pytest.fixture
def gates_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A clone with the pattern file and the gates' script at their places, and no hooks: the commits come in as with
    `--no-verify`."""
    where, env = tmp_path / "repo", env_for_git(tmp_path)
    subprocess.run(["git", "init", "-q", "-b", "work", str(where)], env=env, check=True)
    (where / ".github").mkdir()
    for path in (CHECK, GATES):
        (where / ".github" / path.name).write_bytes(path.read_bytes())
    subprocess.run(["git", "-C", str(where), "config", "core.hooksPath", "/dev/null"], env=env, check=True)
    subprocess.run(["git", "-C", str(where), "config", "commit.gpgsign", "false"], env=env, check=True)
    return where, env


def gates_run(where: Path, env: dict[str, str], base: str | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["sh", ".github/gates-attribution.sh"], cwd=where, env={**env, **({"BASE": base} if base else {})},
                          capture_output=True, text=True, check=False)


def head(where: Path, env: dict[str, str]) -> str:
    return subprocess.run(["git", "-C", str(where), "rev-parse", "HEAD"], env=env, capture_output=True, text=True, check=True).stdout.strip()


def test_the_gates_refuse_a_pull_request_commit_with_an_attribution_line(gates_repo: tuple[Path, dict[str, str]]) -> None:
    """The job `changes` runs the branch's `gates-attribution.sh` with BASE: a commit that the hook did not see (made with
    `--no-verify`) fails there, and a commit of main before BASE does not count."""
    where, env = gates_repo
    assert commit(gates_repo, "ABC-0: old\n\nClaude-Session: https://claude.ai/code/session_01old\n").returncode == 0
    base = head(where, env)
    assert gates_run(where, env, None).returncode == 0, "no BASE: a push to main checks nothing"
    assert commit(gates_repo, "ABC-1: clean\n\nNo such line.\n").returncode == 0
    assert gates_run(where, env, base).returncode == 0, "the commit before BASE is not the pull request's"
    assert commit(gates_repo, "ABC-2: a change\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n").returncode == 0
    refused = gates_run(where, env, base)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "line 3: Co-Authored-By: Claude <noreply@anthropic.com>" in refused.stdout and "commit " in refused.stdout
    assert "ABC-0" not in refused.stdout and 'AGENTS.md "Rules"' in refused.stdout


def test_the_gates_check_fails_on_an_unknown_base_and_skips_merge_commits(gates_repo: tuple[Path, dict[str, str]]) -> None:
    where, env = gates_repo
    assert commit(gates_repo, "ABC-0: first\n").returncode == 0
    base = head(where, env)
    unknown = gates_run(where, env, "0" * 40)
    assert unknown.returncode == 2, unknown.stdout + unknown.stderr
    subprocess.run(["git", "-C", str(where), "switch", "-q", "-c", "side"], env=env, check=True)
    assert commit(gates_repo, "ABC-1: side\n").returncode == 0
    subprocess.run(["git", "-C", str(where), "switch", "-q", "work"], env=env, check=True)
    (where / "other.txt").write_text("x")
    subprocess.run(["git", "-C", str(where), "add", "-A"], env=env, check=True)
    subprocess.run(["git", "-C", str(where), "commit", "-q", "-m", "ABC-2: work"], env=env, check=True)
    merged = subprocess.run(["git", "-C", str(where), "merge", "--no-ff", "-m", "Merge side\n\nClaude-Session: https://claude.ai/code/session_01abc", "side"],
                            env=env, capture_output=True, text=True, check=False)
    assert merged.returncode == 0, merged.stdout + merged.stderr
    passed = gates_run(where, env, base)
    assert passed.returncode == 0, "git writes a merge commit's message: the check leaves it out\n" + passed.stdout + passed.stderr


# -- this repository's README.md row and mise.toml task of owlcode profile (a product writes its own) -----------------


def test_the_readme_row_of_the_pi_profiles_switches_with_the_profile_command_not_a_hand_edit() -> None:
    row = next(line for line in (REPO / "README.md").read_text().splitlines() if line.startswith("| Ollama Cloud and other pi profiles |"))
    for name in ("pi-ollama", "pi-chatgpt", "pi-lithosai"):
        assert f"`owlcode profile {name}`" in row
    assert "set `inference_profile" not in row and "Set `inference_profile" not in row, "no hand edit of the config's line"
    assert "owlcode restart" not in row, "the command restarts the service itself"
    assert "the command does not change it: `owlcode set session_host=paseo`" in row, "the host stays the owner's"


# -- the Mac's tools stay out of a product's copy (OWL-46) ---------------------------------------------------------------

MAC_TOOLS = ("orchestrator/", ".workflow/agent_user.py", ".workflow/approved_run.py", ".workflow/model-catalog.json",
             "tests/test_orchestrator.py", "tests/test_orchestrator_service.py", "tests/test_orchestrator_teams.py",
             "tests/test_agent_user.py", "tests/test_approved_run.py")


def test_the_copied_tests_pass_collection_in_a_folder_with_only_the_listed_files(tmp_path: Path) -> None:
    """A product copies the files of `.workflow/loop-files.txt` and nothing else of the Mac's tools: no listed file is one
    of them, and the copied tests collect in a folder that holds only the listed files (with the test set-up that a
    product writes itself: `tests/__init__.py`, `tests/conftest.py`, `pyproject.toml`, `uv.lock`)."""
    files, stale = leak_guard.listed(REPO / ".workflow" / "loop-files.txt", REPO)
    assert not stale, stale
    for path in MAC_TOOLS:
        assert not any(f == path or f.startswith(path) for f in files if path.endswith("/") or f == path), f"{path} is copied"
    own = ["tests/__init__.py", "tests/conftest.py", "pyproject.toml", "uv.lock", ".python-version"]
    for name in [*files, *own]:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, target)
    ran = subprocess.run([sys.executable, "-I", "-m", "pytest", "--collect-only", "-q", "-n", "0", "-p", "no:cacheprovider", "tests"],
                         check=False, cwd=tmp_path, capture_output=True, text=True, timeout=300, env={**os.environ, "PYTHONPATH": str(tmp_path)})
    assert ran.returncode == 0, ran.stdout[-3000:] + ran.stderr[-2000:]


def test_the_waiting_line_names_a_command_that_this_repositorys_bin_defines() -> None:
    """The line's text is a product's too (tests/test_orchestrator_service.py); only this repository holds the command."""
    assert '"unlock":' in (REPO / "bin" / "owlcode").read_text()


def test_the_docs_name_owlcode_not_the_old_tasks() -> None:
    """OWL-54: no `mise run orchestrator:...`, `mise run models` or `models:check` is left in a tracked doc, script or test."""
    old = re.compile(r"mise run (orchestrator:|models\b)|\borchestrator:(install|check|restart|set|profile|pause|resume|unlock)\b|models:check")
    tracked = subprocess.run(["git", "ls-files"], cwd=REPO, check=True, capture_output=True, text=True).stdout.split()
    here = "tests/test_this_repository.py"
    for name in tracked:
        if name != here and (REPO / name).is_file() and name.endswith((".md", ".py", ".toml", ".txt", ".sh")):
            assert not old.search((REPO / name).read_text()), name


def test_the_scripts_the_owner_runs_merge_on_the_verdict_and_their_launcher_stays_owned():
    """The owner's call 2026-11-23: `orchestrator/deploy/` and `.workflow/agent_user.py` run only when the owner
    runs them, so the owner checks them then, through `.workflow/approved_run.py`, which shows their diff since the last yes.
    The launcher and every other file of `.workflow/` stay the owner's."""
    rules = verdict.read_owners((REPO / ".github" / "CODEOWNERS").read_text())
    run_time = ["orchestrator/deploy/install.py", "orchestrator/deploy/service.py", ".workflow/agent_user.py"]
    assert verdict.owned(rules, run_time) == [], "the verdict merges these"
    kept = [".workflow/approved_run.py", ".workflow/start.py", ".workflow/new.py", ".workflow.toml"]
    assert verdict.owned(rules, kept) == kept
