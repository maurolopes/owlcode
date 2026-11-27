"""The loop's CI: the workflows under .github/workflows/ and the scripts they call. The tests of the product's own set-up of
`gates.yml` (the commands of its jobs, the tool pins) are in tests/test_product_ci.py, which is not in the loop's list
(.workflow/loop-files.txt)."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
REVIEW = REPO / ".github" / "workflows" / "claude-code-review.yml"


def test_the_hooks_keep_main_for_merged_pull_requests(tmp_path):
    """The ruleset enforces main on GitHub; the hooks keep a clone from trying (AGENTS.md Rules)."""
    def push(refs: str) -> int:
        return subprocess.run(["sh", str(REPO / ".githooks" / "pre-push")], input=refs, text=True, capture_output=True, cwd=REPO, check=False).returncode

    assert push("refs/heads/work abc123 refs/heads/main def456\n") == 1
    deleting = "refs/heads/work " + "0" * 40 + " refs/heads/work " + "0" * 40 + "\n"
    assert push(deleting) == 0, "deleting a branch is not checked"

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    commit = subprocess.run(["sh", str(REPO / ".githooks" / "pre-commit")], cwd=repo, capture_output=True, text=True, check=False)
    assert commit.returncode == 1 and "not on main" in commit.stdout
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "work"], check=True)
    assert subprocess.run(["sh", str(REPO / ".githooks" / "pre-commit")], cwd=repo, capture_output=True, check=False).returncode == 0


def test_the_push_hook_refuses_a_branch_that_is_behind_main(tmp_path):
    """A branch must contain a freshly fetched origin/main; a failed fetch warns and passes; a deletion passes."""
    def git(*args: str, cwd=None) -> str:
        done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, capture_output=True, text=True, check=True)
        return done.stdout.strip()

    origin, repo = tmp_path / "origin.git", tmp_path / "repo"
    git("init", "-q", "--bare", "-b", "main", str(origin))
    git("clone", "-q", str(origin), str(repo))
    (repo / ".github").mkdir()
    # a fake of the product's docs-only check: the hook only needs its answer (the script's own tests: tests/test_product_ci.py)
    (repo / ".github" / "code-changed.sh").write_text("grep -qv '[.]md$' && echo true || echo false\n")
    git("add", ".", cwd=repo)
    git("commit", "-q", "-m", "base", cwd=repo)
    git("push", "-q", "origin", "HEAD:main", cwd=repo)
    git("checkout", "-q", "-b", "abc-9-work", cwd=repo)
    (repo / "NOTES.md").write_text("notes\n")
    git("add", ".", cwd=repo)
    git("commit", "-q", "-m", "docs", cwd=repo)
    head = git("rev-parse", "HEAD", cwd=repo)

    def push(*, deleting: bool = False, ref: str = "refs/heads/abc-9-work") -> subprocess.CompletedProcess[str]:
        sha = "0" * 40 if deleting else head
        refs = f"{ref} {sha} {ref} {'0' * 40}\n"
        return subprocess.run(["sh", str(REPO / ".githooks" / "pre-push")], input=refs, text=True, capture_output=True, cwd=repo, check=False)

    assert push().returncode == 0, "up to date with main"
    other = tmp_path / "other"
    git("clone", "-q", str(origin), str(other))
    (other / "x.md").write_text("x\n")
    git("add", ".", cwd=other)
    git("commit", "-q", "-m", "main moves", cwd=other)
    git("push", "-q", "origin", "HEAD:main", cwd=other)
    behind = push()
    assert behind.returncode == 1 and "git merge origin/main" in behind.stdout, "main moved: the fetch finds it"
    assert push(ref="refs/tags/v1").returncode == 0, "a tag is not a branch: not checked"
    assert push(deleting=True).returncode == 0, "a deletion is not checked"
    git("merge", "-q", "--no-edit", "origin/main", cwd=repo)
    head = git("rev-parse", "HEAD", cwd=repo)
    assert push().returncode == 0, "after the merge"
    git("remote", "set-url", "origin", str(tmp_path / "missing.git"), cwd=repo)
    (other / "y.md").write_text("y\n")
    failed = push()
    assert failed.returncode == 0 and "could not fetch" in failed.stdout, "no network: warn and pass"


def test_the_review_runs_on_the_ready_mark_with_the_reviewer_app_and_never_on_a_push():
    """agents/README.md "The loop": a review on every push gave one pull request seven reviews; the reviewer app's token posts
    a formal review the ruleset counts, so the job needs no OIDC token and every action is pinned to a commit."""
    flow = yaml.safe_load(REVIEW.read_text())
    on = flow[True] if True in flow else flow["on"]
    assert on["pull_request"]["types"] == ["opened", "ready_for_review", "synchronize", "labeled", "closed"] and "workflow_dispatch" in on
    job = flow["jobs"]["review"]
    start = flow["jobs"]["start"]
    review_events = ("github.event.action == 'opened' || github.event.action == 'ready_for_review') && "
                     "github.event.pull_request.draft == false")
    assert job["needs"] == "start" and holds(start["if"], review_events) and "synchronize" not in start["if"] \
        and flow["permissions"] == {"contents": "read"}
    assert holds(start["if"], "github.event.action == 'labeled' && github.event.label.name == 'owner-approved'"), \
        "a label starts the job only when it is the owner's Approved"
    uses = [s["uses"] for j in flow["jobs"].values() for s in j["steps"] if "uses" in s]
    assert all(re.fullmatch(r"[\w.-]+/[\w.-]+(/[\w.-]+)?@[0-9a-f]{40}", u) for u in uses), uses
    review = next(s for s in job["steps"] if s.get("uses", "").startswith("anthropics/claude-code-action@"))
    assert holds(review["with"]["github_token"], "steps.token.outputs.token")
    token = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/create-github-app-token@"))
    assert holds(token["with"]["client-id"], "vars.REVIEWER_APP_CLIENT_ID"), "an ID is public: a variable, never a secret (2026-10-25)"
    assert "app-id" not in token["with"], "the App ID is deprecated for tokens: the Client ID (2026-10-25)"
    assert holds(token["with"]["private-key"], "secrets.REVIEWER_APP_PRIVATE_KEY")
    assert "REVIEWER_APP_ID" not in REVIEW.read_text()
    steps = job["steps"]
    cache = next(s for s in steps if s.get("uses", "").startswith("actions/cache/restore@"))
    pin = review["uses"].split("@")[1].split()[0]
    assert steps.index(cache) < steps.index(review) and cache["with"]["path"] == "~/.bun/install/cache" \
        and pin in cache["with"]["key"], "Bun's install cache comes before the action and is keyed by the action's commit"
    assert not any(s.get("uses", "").startswith("actions/cache@") for s in steps), \
        "The review job only restores the cache; main's writer saves it, so a pull request's runs never write what it runs"
    guard = next(s for s in steps if s.get("id") == "bun-guard")
    assert steps.index(guard) < steps.index(cache) and "steps.bun-guard.outputs.restore == 'true'" in cache["if"] \
        and guard["run"].strip() == f"{CALL} cache-guard" and guard["env"]["KEY"] == cache["with"]["key"], \
        "An entry under the pull request's own ref is never restored (its runs can save one): test_review_steps.py"
    recheck = the_step("review", f"{CALL} bun-check")
    assert steps.index(cache) < steps.index(recheck) < steps.index(review) and recheck["env"]["KEY"] == cache["with"]["key"], \
        "After the restore, a key listed under the pull request's ref (or a failed request) removes the cache: test_review_steps.py"
    # The two programs the action downloads come from main's cache, guarded as the install cache is
    bkey = f"review-binaries-claude-code-action-{pin}"
    bguard = next(s for s in steps if s.get("id") == "binaries-guard")
    brestore = next(s for s in steps if s.get("uses", "").startswith("actions/cache/restore@") and s is not cache)
    bcheck = next(s for s in steps if s.get("id") == "binaries")
    assert steps.index(bguard) < steps.index(brestore) < steps.index(bcheck) < steps.index(review)
    assert brestore["with"] == {"path": "~/.cache/review-binaries", "key": bkey} and bguard["env"]["KEY"] == bkey == bcheck["env"]["KEY"]
    assert "steps.binaries-guard.outputs.restore == 'true'" in brestore["if"] and bguard["run"].strip() == f"{CALL} cache-guard", \
        "Binaries under the pull request's own ref are never restored: test_review_steps.py"
    assert bcheck["run"].strip() == f"{CALL} binaries" and holds(bcheck["env"]["RESTORE"], "steps.binaries-guard.outputs.restore"), \
        "After the restore, a key listed under the pull request's ref (or a failed request) removes the binaries: test_review_steps.py"
    assert holds(review["with"]["path_to_bun_executable"], "steps.binaries.outputs.bun") \
        and holds(review["with"]["path_to_claude_code_executable"], "steps.binaries.outputs.claude"), \
        "A miss leaves both inputs empty, so the action installs them itself"
    tools = review["with"]["claude_args"]
    for tool in ("Bash(gh pr review:*)", "Bash(gh pr checks:*)", "Bash(python3 -I /tmp/trusted/linear.py:*)"):
        assert tool in tools, tool
    assert not (REPO / ".github" / "workflows" / "claude.yml").exists(), "the mention workflow is gone (138 of its runs were skips)"


REVIEW_TOKEN = "${{ steps.token.outputs.token }}"
ROUND_OR_LABELED = "github.event.action == 'labeled' || steps.facts.outputs.round <= 3"
NOT_LABELED = "github.event.action != 'labeled' && steps.facts.outputs.round <= 3"
BUN_KEY = "bun-install-claude-code-action-1d6de8cb0c237e7c15e9e1bdf973826ebae490cc"
BINARIES_KEY = "review-binaries-claude-code-action-1d6de8cb0c237e7c15e9e1bdf973826ebae490cc"
# The steps of the job `review` that call main's script, in the order of the job: (command, step id, `if`, `continue-on-error`,
# `env`). The behaviour of each command is held by test_review_steps.py; here the step's own part is held, which GitHub
# evaluates: when it runs, whether its failure stops the job, which token and which facts the script reads.
REVIEW_STEPS_WIRING = [
    ("trusted", None, None, None, {}),
    ("facts", "facts", None, None, {"GH_TOKEN": REVIEW_TOKEN, "LINEAR_API_KEY": "${{ secrets.LINEAR_API_KEY }}",
                                    "ROUND_INPUT": "${{ inputs.round }}", "OWNER": "${{ github.repository_owner }}"}),
    ("label-off", None, ROUND_OR_LABELED, True, {"GH_TOKEN": REVIEW_TOKEN}),
    ("manual", None, "github.event_name == 'workflow_dispatch' && inputs.round == '1'", True, {"GH_TOKEN": REVIEW_TOKEN}),
    ("past-third", None, "github.event.action != 'labeled' && steps.facts.outputs.round > 3", None,
     {"GH_TOKEN": REVIEW_TOKEN, "ROUND": "${{ steps.facts.outputs.round }}", "KEY": "${{ steps.facts.outputs.key }}",
      "LINEAR_API_KEY": "${{ secrets.LINEAR_API_KEY }}"}),
    ("gates", "gates", ROUND_OR_LABELED, None, {"GH_TOKEN": REVIEW_TOKEN}),
    ("cache-guard", "bun-guard", NOT_LABELED, None, {"GH_TOKEN": REVIEW_TOKEN, "KEY": BUN_KEY}),
    ("bun-check", None, NOT_LABELED, None, {"GH_TOKEN": REVIEW_TOKEN, "KEY": BUN_KEY}),
    ("cache-guard", "binaries-guard", NOT_LABELED, None, {"GH_TOKEN": REVIEW_TOKEN, "KEY": BINARIES_KEY}),
    ("binaries", "binaries", NOT_LABELED, None, {"GH_TOKEN": REVIEW_TOKEN, "KEY": BINARIES_KEY,
                                                 "RESTORE": "${{ steps.binaries-guard.outputs.restore }}"}),
    ("denied", None, "always() && steps.review.outcome != 'skipped'", None,
     {"EXECUTION_FILE": "${{ steps.review.outputs.execution_file }}"}),
]


def test_each_step_of_the_review_job_calls_the_command_that_does_its_work():
    """Every `run:` block of the job `review` is a call of main's review_steps.py (the first one also takes main's
    copy of the script), so the logic is under test_review_steps.py and the workflow holds only what GitHub evaluates. Each
    step keeps its `if`, its `continue-on-error`, its token and the facts it reads (a step with no `env` entry for a fact would
    read an empty one): this table is the rule, and a changed step changes the table in the same pull request."""
    flat = lambda value: " ".join(str(value).split())  # noqa: E731  (spacing of a folded expression is no rule)
    runs = [s for s in yaml.safe_load(REVIEW.read_text())["jobs"]["review"]["steps"]
            if "run" in s and s["run"].strip() != REVIEWER_LOGIN_RUN]  # the reviewer's login: test_each_job_takes_the_reviewer_s_login_from_its_token
    assert all("review_steps.py" in s["run"] for s in runs), [s.get("name") for s in runs if "review_steps.py" not in s["run"]]
    found = [(s["run"].strip().splitlines()[-1].removeprefix(f"{CALL} "), s.get("id"), s.get("if") and flat(s["if"]),
              s.get("continue-on-error"), {("ON" if k is True else k): flat(v) for k, v in (s.get("env") or {}).items()})
             for s in runs]  # YAML 1.1 reads an env key `ON` as the boolean true
    assert found == REVIEW_STEPS_WIRING
    assert all(s["run"].strip().splitlines()[-1].startswith(CALL) for s in runs), "the call runs main's copy, isolated (-I)"


def test_the_review_reads_mains_rules_never_the_branchs():
    """Agents/README.md, implementer.md, planner.md and DECISIONS.md merge on the verdict, so the review applies
    main's copies of its rules. The job's first step takes main's review_steps.py, and only that file, from origin/main, then
    runs its command `trusted`, which writes main's rules to review/rules/ and empties that folder first (the behaviour is
    held by test_review_steps.py); the prompt names the copies."""
    job = yaml.safe_load(REVIEW.read_text())["jobs"]["review"]
    step = the_step("review", COPIES)
    assert [line.strip() for line in step["run"].strip().splitlines()] == [
        "git fetch -q origin main", "mkdir -p /tmp/trusted", "git show origin/main:.github/review_steps.py > /tmp/trusted/review_steps.py",
        f"{CALL} trusted"], "main's script, never the branch's, then its command"
    assert job["steps"].index(step) < job["steps"].index(the_step("review", "facts")), "main's copies come before the facts"
    review = next(s for s in job["steps"] if s.get("uses", "").startswith("anthropics/claude-code-action@"))
    prompt = review["with"]["prompt"]
    assert "Read review/rules/agents/reviewer.md" in prompt and "review/rules/AGENTS.md" in prompt \
        and "review/rules/agents/README.md" in prompt, prompt
    assert "Read agents/reviewer.md" not in prompt, "the branch's brief is the change under review, never its rules"


def test_the_review_job_ends_with_the_model_and_never_waits_for_the_gates():
    """OWL-30 (the owner's question, 2026-11-25): the model took a median 6.4 minutes, the wait before it 4.1 (2026-10-26), and
    a wait after it held the review's runner until the gates ended. The required checks `python` and `web` hold a red gate on
    their own, and the workflow gates-failed.yml sends an approved head with a red gate back: no step of the job polls the
    checks, `sleep`s or reads how the gates ended, and the job's outputs hold the model's outcome only."""
    job = yaml.safe_load(REVIEW.read_text())["jobs"]["review"]
    assert all("gh pr checks" not in s.get("run", "") and "sleep" not in s.get("run", "") for s in job["steps"]), \
        [s.get("name") for s in job["steps"] if "gh pr checks" in s.get("run", "") or "sleep" in s.get("run", "")]
    assert not any(s.get("id") == "wait" or " wait" in s.get("run", "") for s in job["steps"]), "no step waits for the gates"
    assert list(job["outputs"]) == ["model"], "the job's outputs: the model's outcome, no gates result"
    review = next(s for s in job["steps"] if s.get("id") == "review")
    assert "CHECKS:" not in review["with"]["prompt"] and "checks.txt" not in REVIEW.read_text()
    assert "steps.gates.outputs.on" in review["with"]["prompt"], "the model learns whether the gates workflow is on (agents/reviewer.md)"


def run_access(tmp_path: Path, key: str) -> tuple[subprocess.CompletedProcess[str], str]:
    """The review workflow's step `access` (job `start`) run as GitHub runs a `run:` step (bash -e, pipefail), its secret
    in the environment, with main's review_jobs.py in the folder it runs in."""
    start = yaml.safe_load(REVIEW.read_text())["jobs"]["start"]
    access = next(s for s in start["steps"] if s.get("id") == "access")
    (tmp_path / "bin").mkdir(parents=True)
    (tmp_path / "bin" / "python3").symlink_to(sys.executable)
    (tmp_path / "work" / ".github").mkdir(parents=True)
    (tmp_path / "work" / ".github" / "review_jobs.py").write_text((REPO / ".github" / "review_jobs.py").read_text())
    output = tmp_path / "github_output"
    output.write_text("")
    env = {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "APP_ID": "Iv23client", "APP_KEY": key, "GITHUB_OUTPUT": str(output)}
    run = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", access["run"]],
                         cwd=tmp_path / "work", env=env, capture_output=True, text=True, check=False)
    return run, output.read_text()


def test_the_key_shape_check_refuses_a_key_pasted_on_one_line(tmp_path):
    """A paste into the prompt of `gh secret set` keeps one line, which the token step reports only as OpenSSL's "DECODER
    routines::unsupported" (2026-10-25): the step fails first, with the fix in the message, and never prints the key."""
    run, output = run_access(tmp_path, "-----BEGIN RSA PRIVATE KEY----- MIIfake -----END RSA PRIVATE KEY-----")
    assert run.returncode == 1 and output == ""
    assert "::error::" in run.stdout and "(1 line(s))" in run.stdout and "< FILE" in run.stdout
    assert "MIIfake" not in run.stdout + run.stderr


def test_the_key_shape_check_refuses_lines_without_the_pem_markers(tmp_path):
    """The line count alone is not the shape: a key of many lines needs the BEGIN line and the END line too."""
    run, output = run_access(tmp_path, "MIIfake\nmore\nlines\n")
    assert run.returncode == 1 and output == "" and "::error::" in run.stdout and "(4 line(s))" in run.stdout

def test_the_key_shape_check_passes_a_pem_file(tmp_path):
    run, output = run_access(tmp_path, "-----BEGIN RSA PRIVATE KEY-----\nMIIfake\nmore\n-----END RSA PRIVATE KEY-----\n")
    assert run.returncode == 0 and output == "ready=true\n" and "::error::" not in run.stdout


def test_the_key_shape_check_skips_the_review_without_a_key(tmp_path):
    run, output = run_access(tmp_path, "")
    assert run.returncode == 0 and output == "ready=false\n"
    assert "::notice::" in run.stdout and "REVIEWER_APP_PRIVATE_KEY is not set" in run.stdout


def test_the_verdict_is_a_check_that_only_jobs_running_mains_code_write():
    """GitHub counts an approval only from an account with push access (measured 2026-10-25), so the ruleset requires the check
    `verdict`. Only the review workflow's jobs `start`, `report` and `carry` may write a check, and none of them runs a file
    of the branch: `report` and `carry` check out main. The review job, which holds the reviewer app's token, runs main's
    copies of the scripts, Python isolated, and the model without project settings (a review finding: a branch's
    linear.py could plant a module or post an approve). No job anywhere is named `verdict`: its own check would pass."""
    flow = yaml.safe_load(REVIEW.read_text())
    jobs = flow["jobs"]
    assert "checks" not in jobs["review"].get("permissions", {})
    for name in ("report", "carry"):
        checkout = next(s for s in jobs[name]["steps"] if s.get("uses", "").startswith("actions/checkout@"))
        assert holds(checkout["with"]["ref"], "github.event.repository.default_branch"), name
    # start checks out main only: main's verdict.py checks the owner's Approved, and main's linear.py reads the
    # issue's key
    checkouts = [s for s in jobs["start"]["steps"] if s.get("uses", "").startswith("actions/checkout@")]
    assert len(checkouts) == 1 and list(checkouts[0]["with"]) == ["ref"] \
        and holds(checkouts[0]["with"]["ref"], "github.event.repository.default_branch")
    # a job's permissions replace the workflow's: a checkout needs contents in its own scope
    for path in workflow_paths():
        for name, job in yaml.safe_load(path.read_text())["jobs"].items():
            if any(s.get("uses", "").startswith("actions/checkout@") for s in job.get("steps", [])) and "permissions" in job:
                assert job["permissions"].get("contents") in ("read", "write"), f"{path.name}: {name} checks out with no contents"
    assert holds(jobs["report"]["if"], "always()") and jobs["report"]["needs"] == ["start", "review"]
    report = next(s for s in jobs["report"]["steps"] if s.get("id") == "check")["run"]
    assert "exec python3 -I .github/review_report.py check" in report, \
        "main's script writes the check; it also carries to a head pushed during the review (R1-2, test_review_report.py)"
    assert "pull_request_review" in (flow[True] if True in flow else flow["on"]) and "'dismissed'" in next(s for s in jobs["carry"]["steps"] if "COMMAND" in s.get("env", {}))["env"]["COMMAND"]
    runs = "\n".join(s.get("run", "") for s in jobs["review"]["steps"])
    assert ".github/linear.py" not in runs and "sh .github/code-changed.sh" not in runs, "the review job runs main's copies of the scripts"
    assert "python3 -c" not in runs and "python3 .github" not in runs, "Python runs isolated (-I)"
    model = next(s for s in jobs["review"]["steps"] if s.get("uses", "").startswith("anthropics/claude-code-action@"))
    assert "--setting-sources user" in model["with"]["claude_args"], "the model loads no project settings (a branch's hooks would run)"
    # the action writes enableAllProjectMcpServers to the user settings, which the model loads: only the servers the action
    # passes with --mcp-config may start, never a branch's .mcp.json
    assert "--strict-mcp-config" in model["with"]["claude_args"], "a branch's .mcp.json could start a server"
    for path in workflow_paths():
        flow = yaml.safe_load(path.read_text())
        assert isinstance(flow.get("permissions"), dict), f"{path.name}: no top-level permissions takes the repository's default"
        for name, job in flow["jobs"].items():
            assert name != "verdict" and job.get("name") != "verdict", f"{path.name}: a job named verdict reports the check itself"
            for scope in (flow["permissions"], job.get("permissions", {})):
                writes = {k for k, v in scope.items() if v == "write"} & {"checks", "statuses"}
                assert not writes, f"{path.name}: {name} writes {writes} with GitHub Actions' token (the reviewer app writes the check)"
                merges = {k for k, v in scope.items() if v == "write"} & {"contents", "pull-requests"}
                # `report` merges with the implementer app's token. The job `apply` of apply-workflow-patch.yml holds
                # pull-requests write in its own scope only, to comment on its pull request and take the label off (the
                # issues API alone gave HTTP 403): it runs main's script, and the branch is data only (test_apply_workflow_patch.py)
                allowed = (path == REVIEW and name == "report") or (path.name == "apply-workflow-patch.yml" and name == "apply" and merges == {"pull-requests"})
                assert not merges or (allowed and scope is not flow["permissions"]), (path.name, name, merges)
            assert job.get("permissions") != "write-all" and flow["permissions"] != "write-all"


def test_the_verdict_is_written_with_the_reviewer_apps_token():
    """A check written with GitHub Actions' token joins a check suite of the head that already exists; when that is a
    run whose jobs all skipped (a draft's `opened`), the ruleset ignores the check, and a green `verdict` stayed
    "Expected". The reviewer app's check gets the app's own suite. Only the app that created a check can update it, so
    `start`, `report` and `carry` each mint the app's token with checks write, and every call to the Checks API uses it;
    the review job's token reads the checks only."""
    jobs = yaml.safe_load(REVIEW.read_text())["jobs"]
    for name in ("start", "report", "carry"):
        steps = jobs[name]["steps"]
        token = next(s for s in steps if s.get("uses", "").startswith("actions/create-github-app-token@"))
        assert token["id"] == "token" and token["with"]["permission-checks"] == "write", name
        assert holds(token["with"]["client-id"], "vars.REVIEWER_APP_CLIENT_ID"), name
        # the checks of `start` and `carry` are written by review_jobs.py's commands `open` and `carry`
        writes = [s for s in steps if "check-runs" in s.get("run", "") or re.search(r"review_jobs\.py (open|carry)$", s.get("run", ""))]
        assert writes, name
        for step in writes:
            assert holds(step["env"]["GH_TOKEN"], "steps.token.outputs.token"), (name, step["name"])
    script = (REPO / ".github" / "review_jobs.py").read_text()
    assert script.count("token=env(") == 1 and 'token=env("DISPATCH"))' in script, \
        "only the dispatch of a new review uses another token than the step's: every call to the Checks API uses the app's"
    review = next(s for s in jobs["review"]["steps"] if s.get("uses", "").startswith("actions/create-github-app-token@"))
    assert review["with"]["permission-checks"] == "read", "the review job writes no check"
    assert "vars.REVIEWER_APP_CLIENT_ID != ''" in jobs["carry"]["if"], "without the app nothing can write the check"
    assert "needs.start.outputs.check != ''" in jobs["report"]["if"] and "needs.start.outputs.check != ''" in jobs["review"]["if"]


def product_workflows(folder: Path) -> set[str]:
    """The workflow files that .workflow/loop-files.txt leaves out (`!.github/workflows/NAME`): each repository's own
    (its gates, its caches), which its own tests check. Only the folder of this repository's workflows has them."""
    if folder != REVIEW.parent:
        return set()
    lines = (REPO / ".workflow" / "loop-files.txt").read_text().splitlines()
    return {line.rsplit("/", 1)[1] for line in lines if line.startswith("!.github/workflows/")}


def workflow_paths(folder: Path = REVIEW.parent) -> list[Path]:
    """Every workflow file: GitHub runs `*.yaml` as well as `*.yml` (R1-2). The review workflow is among them, so
    a moved folder fails the tests that read it instead of passing on nothing (R1-3)."""
    paths = sorted(p for pattern in ("*.yml", "*.yaml") for p in folder.glob(pattern) if p.name not in product_workflows(folder))
    assert REVIEW.name in [p.name for p in paths], "the workflows are not where the tests read them"
    return paths


def test_the_workflow_tests_read_every_workflow_file(tmp_path: Path):
    """A `.yaml` workflow is read too, and a folder without the review workflow
    fails instead of passing on nothing."""
    for name in (REVIEW.name, "triage.yaml", "notes.txt"):
        (tmp_path / name).write_text("")
    assert [p.name for p in workflow_paths(tmp_path)] == [REVIEW.name, "triage.yaml"]
    (tmp_path / REVIEW.name).unlink()
    with pytest.raises(AssertionError, match="not where the tests read them"):
        workflow_paths(tmp_path)


UPDATE = REPO / ".github" / "workflows" / "branch-update.yml"
REVIEWER_KEY, IMPLEMENTER_KEY = "REVIEWER_APP_PRIVATE_KEY", "IMPLEMENTER_APP_PRIVATE_KEY"
# the steps that may name each app's private key, as (workflow file, job, step id): for the reviewer app the key-shape check
# of `start`, the four token steps, the review watchdog's (comments only, pull requests write) and the rerun's
# check; for the implementer app the token that turns on auto-merge, the branch update's, the token of the workflow
# `gates-failed` (it comments on an approved head whose gates did not pass: pull requests write), the rerun's auto-merge and
# the token of the workflow `attribution`, which removes an attribution line from a pull request's text (pull requests write)
KEY_STEPS = {
    REVIEWER_KEY: {(REVIEW.name, "start", "access"), (REVIEW.name, "start", "token"), (REVIEW.name, "review", "token"),
                   (REVIEW.name, "report", "token"), (REVIEW.name, "carry", "token"),
                   ("review-watchdog.yml", "watch", "token"), ("verdict-rerun.yml", "rerun", "reviewer")},
    IMPLEMENTER_KEY: {(REVIEW.name, "report", "merger"), (UPDATE.name, "update", "token"),
                      ("gates-failed.yml", "failed", "token"), ("verdict-rerun.yml", "rerun", "merger"),
                      ("attribution.yml", "attribution", "token")},
}
# inside an expression, `secrets` followed by anything but another secret's name: the key in any case (GitHub reads secret
# names without case), or every secret at once (`secrets[...]`, `secrets.*`, `toJSON(secrets)`). Measured on a throwaway
# workflow, 2026-10-26: `secrets.linear_api_key`, `secrets.Linear_Api_Key`, `secrets['linear_api_key']` and
# `secrets.*` all resolve
EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)


def names_the_key(value: object, key: str = REVIEWER_KEY) -> bool:
    """Whether an expression in the value can read the key."""
    other_secret = re.compile(rf"\bsecrets\b(?!\s*\.\s*(?!{key}\b)[A-Z_][A-Z0-9_]*)", re.IGNORECASE)
    return any(other_secret.search(e) for e in EXPRESSION.findall(yaml.safe_dump(value)))


def uses_of_the_reviewer_key(flows: dict[str, dict], key: str = REVIEWER_KEY) -> list[str]:
    """The places outside KEY_STEPS that can read an app's private key: with the reviewer app's, a `run:` step signs a JWT
    and asks for an installation token with no permissions named, which can write the check `verdict` (R1-1);
    with the implementer app's, one that can push to any branch. A workflow's or a job's `env`, a step, and a
    call that passes `secrets: inherit` or names the key all count."""
    found = []
    for file, flow in flows.items():
        if names_the_key(flow.get("env") or {}, key):
            found.append(f"{file}: env")
        for name, job in flow["jobs"].items():
            outside = {k: v for k, v in job.items() if k != "steps"}
            if job.get("secrets") == "inherit" or names_the_key(outside, key):
                found.append(f"{file}: {name}")
            for step in job.get("steps", []):
                if (file, name, step.get("id")) in KEY_STEPS[key]:
                    continue
                if names_the_key(step, key):
                    found.append(f"{file}: {name}: {step.get('id') or step.get('name')}")
    return found


def test_only_the_listed_steps_name_the_reviewer_apps_key():
    """A token minted by the action is not the only way to the Checks API. Any other step
    that gets the reviewer app's private key can mint one itself, so only KEY_STEPS may name it, in every workflow file."""
    flows = {path.name: yaml.safe_load(path.read_text()) for path in workflow_paths()}
    assert uses_of_the_reviewer_key(flows) == [] and uses_of_the_reviewer_key(flows, IMPLEMENTER_KEY) == []
    copy = {"id": "access", "run": "echo", "env": {"APP_KEY": "${{ secrets.REVIEWER_APP_PRIVATE_KEY }}"}}
    assert uses_of_the_reviewer_key({REVIEW.name: {"jobs": {"start": {"steps": [copy]}}}}) == []
    assert uses_of_the_reviewer_key({REVIEW.name: {"jobs": {"triage": {"steps": [copy]}}}}) == [f"{REVIEW.name}: triage: access"], \
        "the key-shape step copied into another job"
    assert uses_of_the_reviewer_key({"triage.yaml": {"jobs": {"start": {"steps": [copy]}}}}) == ["triage.yaml: start: access"]
    every = {"name": "echo", "run": "echo", "env": {"ALL": "${{ toJSON(secrets) }}"}}
    assert uses_of_the_reviewer_key({"x.yml": {"jobs": {"a": {"steps": [every]}}}}) == ["x.yml: a: echo"]
    assert uses_of_the_reviewer_key({"x.yml": {"jobs": {"a": {"uses": "./.github/workflows/y.yml", "secrets": "inherit"}}}}) == ["x.yml: a"]
    assert uses_of_the_reviewer_key({"x.yml": {"env": {"K": "${{ secrets.REVIEWER_APP_PRIVATE_KEY }}"}, "jobs": {}}}) == ["x.yml: env"]
    # GitHub reads a secret's name in any case, and `secrets.*` is every secret
    for spelling in ("secrets.reviewer_app_private_key", "secrets.Reviewer_App_Private_Key", "secrets['reviewer_app_private_key']",
                     "toJSON(secrets.*)", "join(secrets.*, ',')"):
        step = {"name": "echo", "run": "echo", "env": {"K": "${{ " + spelling + " }}"}}
        assert uses_of_the_reviewer_key({"x.yml": {"jobs": {"a": {"steps": [step]}}}}) == ["x.yml: a: echo"], spelling
    other = {"name": "echo", "run": "echo secrets are read below", "env": {"K": "${{ secrets.linear_api_key }}"}}
    assert uses_of_the_reviewer_key({"x.yml": {"jobs": {"a": {"steps": [other]}}}}) == [], "another secret, and the word in a command"
    # The implementer app's key, only in the token that turns on auto-merge and in the branch update's token
    token = {"id": "token", "uses": "x", "with": {"private-key": "${{ secrets.IMPLEMENTER_APP_PRIVATE_KEY }}"}}
    assert uses_of_the_reviewer_key({UPDATE.name: {"jobs": {"update": {"steps": [token]}}}}, IMPLEMENTER_KEY) == []
    assert uses_of_the_reviewer_key({REVIEW.name: {"jobs": {"report": {"steps": [token]}}}}, IMPLEMENTER_KEY) == [f"{REVIEW.name}: report: token"], \
        "the reviewer app's token step is not the implementer app's"
    assert uses_of_the_reviewer_key({UPDATE.name: {"jobs": {"update": {"steps": [token]}}}}) == [], "another app's key"
    # A call of a reusable workflow that passes a key by name can read it too
    call = {"uses": "acme/other/.github/workflows/x.yml@" + "a" * 40,
            "secrets": {IMPLEMENTER_KEY: "${{ secrets.IMPLEMENTER_APP_PRIVATE_KEY }}"}}
    assert uses_of_the_reviewer_key({"attribution.yml": {"jobs": {"attribution": call}}}, IMPLEMENTER_KEY) == ["attribution.yml: attribution"]
    assert uses_of_the_reviewer_key({"attribution.yml": {"jobs": {"attribution": call}}}) == []
    reviewer = {"id": "token", "uses": "x", "with": {"private-key": "${{ secrets.reviewer_app_private_key }}"}}
    assert uses_of_the_reviewer_key({UPDATE.name: {"jobs": {"update": {"steps": [reviewer]}}}}) == [f"{UPDATE.name}: update: token"]


def app_tokens_that_may_write_checks(flows: dict[str, dict]) -> list[str]:
    """The steps that mint a GitHub App's token with checks write, or with no `permission-checks` (that token gets all of
    the installation's permissions), outside the review workflow's jobs `start`, `report` and `carry` and the job `rerun`
    of verdict-rerun.yml (the approve's check after the owner's rerun of the gates)."""
    found = []
    for file, flow in flows.items():
        for name, job in flow["jobs"].items():
            if (file == REVIEW.name and name in ("start", "report", "carry")) or (file, name) == ("verdict-rerun.yml", "rerun"):
                continue
            for step in job.get("steps", []):
                if step.get("uses", "").startswith("actions/create-github-app-token@"):
                    if (step.get("with") or {}).get("permission-checks") != "read":
                        found.append(f"{file}: {name}")
    return found


def test_no_other_job_mints_an_app_token_that_writes_a_check():
    """The reviewer app has Checks write, so a job that mints its token without
    `permission-checks: read` could write the check `verdict`. Only `start`, `report`, `carry` and verdict-rerun's `rerun`
    may (agents/README.md "The guards")."""
    flows = {path.name: yaml.safe_load(path.read_text()) for path in workflow_paths()}
    assert app_tokens_that_may_write_checks(flows) == []
    token = {"uses": "actions/create-github-app-token@" + "0" * 40, "with": {"client-id": "x"}}
    new_job = {"jobs": {"triage": {"steps": [token]}}}
    assert app_tokens_that_may_write_checks({"other.yml": new_job}) == ["other.yml: triage"], "no permission-checks: all of them"
    token["with"]["permission-checks"] = "write"
    assert app_tokens_that_may_write_checks({REVIEW.name: new_job}) == [f"{REVIEW.name}: triage"]
    token["with"]["permission-checks"] = "read"
    assert app_tokens_that_may_write_checks({"other.yml": new_job}) == []


RERUN = REPO / ".github" / "workflows" / "verdict-rerun.yml"


RERUN_CHECK = {"run": "python3 -I .github/verdict_rerun.py check"}
RERUN_MERGE = {"run": "python3 -I .github/verdict_rerun.py merge"}


def test_a_passed_rerun_of_the_gates_gives_the_approves_check_back_from_mains_code():
    """Only a passed rerun of the gates on a pull request starts the job; it runs main's code, asks main's
    verdict.py `rerun` whether the approve covers the head, writes the check with the reviewer app's token, and turns on
    auto-merge with the implementer app's token, or escalates a code owner's file as `report` does. Both steps only call
    main's .github/verdict_rerun.py, where the rules live."""
    flow = yaml.safe_load(RERUN.read_text())
    assert flow[True]["workflow_run"] == {"workflows": ["gates"], "types": ["completed"]}  # YAML reads `on` as True
    assert flow["permissions"] == {"contents": "read"}
    job = flow["jobs"]["rerun"]
    for cond in ("github.event.workflow_run.conclusion == 'success'", "github.event.workflow_run.event == 'pull_request'",
                 "github.event.workflow_run.run_attempt > 1"):
        assert cond in job["if"], cond
    steps = {s.get("id") or s.get("name"): s for s in job["steps"]}
    checkout = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@"))
    assert holds(checkout["with"]["ref"], "github.event.repository.default_branch"), "main's code only"
    assert holds(steps["reviewer"]["with"]["client-id"], "vars.REVIEWER_APP_CLIENT_ID")
    assert steps["reviewer"]["with"]["permission-checks"] == "write"
    assert holds(steps["merger"]["with"]["client-id"], "vars.IMPLEMENTER_APP_CLIENT_ID")
    assert steps["merger"]["with"]["permission-checks"] == "read"
    check = steps["check"]
    assert holds(check["env"]["GH_TOKEN"], "steps.reviewer.outputs.token")
    assert check["run"] == RERUN_CHECK["run"]
    merge = next(s for s in job["steps"] if s.get("run") == RERUN_MERGE["run"])
    assert holds(merge["if"], "steps.check.outputs.pr != ''"), "only for a pull request whose approve covers the head"
    assert holds(merge["env"]["GH_TOKEN"], "steps.merger.outputs.token")
    assert holds(merge["env"]["OWNER"], "github.repository_owner")
    assert holds(merge["env"]["RUN_CREATED"], "github.event.workflow_run.created_at")


def rerun_check(tmp_path: Path, rules: list[tuple], more: dict[str, str] | None = None):
    """The check step of the rerun, as GitHub runs it, with GITHUB_OUTPUT in the folder; gh faked. It gives the run, gh's
    calls and the output file's text."""
    output = tmp_path / "output"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("")
    env = {"BRANCH": "abc-9-x", "HEAD": "a" * 40, "REVIEWER": "maurolopes-reviewer[bot]", "UPDATER": "maurolopes-implementer[bot]",
           "GITHUB_OUTPUT": str(output), **(more or {})}
    run, calls = run_step(tmp_path, "rerun", RERUN_CHECK, rules, more=env, flow=RERUN)
    return run, calls, output.read_text()


def review_of(head: str, at: str) -> str:
    """One review of the reviewer app, an approve of `head`, as the reviews API gives it."""
    verdict = {"verdict": "approve", "findings": []}
    body = "```json\n" + json.dumps(verdict) + "\n```"
    return json.dumps({"user": {"login": "maurolopes-reviewer[bot]"}, "state": "APPROVED", "body": body, "commit_id": head,
                       "submitted_at": at, "html_url": "https://example.test/review"}) + "\n"


def test_the_check_step_writes_the_check_only_for_an_approve_that_covers_the_head(tmp_path: Path):
    """No open pull request with the head, nothing to write, so no output; a verdict.py that finds no
    approve, a failed read and a failed write end the step as the shell did."""
    head = "a" * 40
    comments = json.dumps({"user": {"login": "maurolopes-implementer[bot]"}, "created_at": "2026-11-23T10:00:00Z",
                           "body": "The gates failed on this approved head (https://example.test/run)."}) + "\n"
    found = ("--json number,headRefOid", "7\n", 0)
    reads = [("pulls/7/reviews", review_of(head, "2026-11-23T09:00:00Z"), 0), ("issues/7/comments", comments, 0)]
    run, calls, out = rerun_check(tmp_path / "gone", [("--json number,headRefOid", "", 0)])
    assert run.returncode == 0 and out == "pr=\n" and "no open pull request has the head" in run.stdout
    assert not any(c.startswith("api repos/owner/repo/check-runs") for c in calls)
    run, calls, out = rerun_check(tmp_path / "one", [found, *reads, ("check-runs", "{}", 0)])
    assert run.returncode == 0, run.stderr
    assert out == "pr=\npr=7\n", "the output names the pull request once the check is written"
    written = [c for c in calls if c.startswith("api repos/owner/repo/check-runs")]
    assert written == ["api repos/owner/repo/check-runs --input -"]
    assert '"conclusion": "success"' in run.stdout and "the gates passed on a rerun" in run.stdout.lower()
    # the approve covers another commit: verdict.py writes nothing, and the step ends with success and no pull request
    other = [("pulls/7/reviews", review_of("b" * 40, "2026-11-23T09:00:00Z"), 0), ("issues/7/comments", comments, 0)]
    run, calls, out = rerun_check(tmp_path / "other", [found, *other, ("check-runs", "{}", 0)])
    assert run.returncode == 0 and out == "pr=\n" and "no passing check from this rerun" in run.stdout
    assert not any(c.startswith("api repos/owner/repo/check-runs") for c in calls)
    # a failed read or a failed write fails the step, as `set -e` did
    for part in ("--json number,headRefOid", "pulls/7/reviews", "issues/7/comments", "check-runs"):
        run, _, out = rerun_check(tmp_path / f"fail-{part[:8]}", [(part, "", 1), found, *reads, ("check-runs", "{}", 0)])
        assert run.returncode == 1, part
        assert "pr=7" not in out, part


def issue_calls(calls: list[str]) -> list[str]:
    """The moves and comments of the issue in the order they happened: the fake linear.py logs into gh's log."""
    return [c for c in calls if c.startswith("linear ") and not c.startswith("linear key-from")]


def rerun_merge(tmp_path: Path, rules: list[tuple], more: dict[str, str] | None = None):
    base = {"BRANCH": "abc-9-x", "HEAD": "a" * 40, "OWNER": "maurolopes", "RUN_CREATED": "2026-11-23T00:00:00Z", "TRIES": "3",
            "WAIT": "0", "LINEAR_API_KEY": "key", **(more or {})}
    return run_step(tmp_path, "rerun", RERUN_MERGE, [("--json title", "ABC-9: x\n", 0), *rules], {".github/linear.py": FAKE_LINEAR},
                    base, flow=RERUN)


def test_a_rerun_moves_the_issue_to_approved_before_auto_merge_and_escalates_when_auto_merge_fails(tmp_path: Path):
    """Linear's Done at the merge must come after the move to Approved (a passed rerun can merge at once), the
    label escalated goes off only once auto-merge is on, a failed try reads the pull request first, the last failed try
    escalates, and a review's `approved_by` lifts no guard: the owner's label does that in `report` only."""
    code = ("pulls/7/files", json.dumps({"filename": "app/x.py", "status": "modified", "patch": "+a"}) + "\n", 0)
    open_ = ("--json state --jq .state", "OPEN\n", 0)
    run, calls = rerun_merge(tmp_path / "ok", [open_, code])
    assert run.returncode == 0, run.stdout + run.stderr
    assert issue_calls(calls)[0].startswith("linear comment ABC-9") and issue_calls(calls)[1:] == ["linear state ABC-9 Approved"]
    assert calls.index("linear state ABC-9 Approved") < calls.index(MERGE) < calls.index(REMOVE_LABEL), \
        "Approved first, then auto-merge, then the label goes off"
    # a merged pull request keeps Linear's Done: no move, no comment, but auto-merge is still asked for
    run, calls = rerun_merge(tmp_path / "merged", [("--json state --jq .state", "MERGED\n", 0), code])
    assert run.returncode == 0 and issue_calls(calls) == [] and MERGE in calls
    # a state that gh could not read keeps the move, as the shell did
    run, calls = rerun_merge(tmp_path / "unread", [("--json state --jq .state", "", 1), code])
    assert run.returncode == 0 and issue_calls(calls)[-1] == "linear state ABC-9 Approved"
    # GitHub refuses on every try with "Base branch was modified": a pull request read before each new try, then the label,
    # the move to Escalated, and a failed step; the label does not go off
    modified = (MERGE, "GraphQL: Base branch was modified. Review and try the merge again.", 1)
    waits = [("--json state,autoMergeRequest", "no\n", 0)]
    run, calls = rerun_merge(tmp_path / "refused", [open_, code, modified, *waits])
    assert run.returncode == 1, run.stdout
    assert calls.count(MERGE) == 3 and calls.count("pr view 7 -R owner/repo --json state,autoMergeRequest --jq "
                                                    'if .state == "MERGED" or .autoMergeRequest != null then "yes" else "no" end') == 3
    assert ADD_LABEL in calls and issue_calls(calls)[-1] == "linear state ABC-9 Escalated" and REMOVE_LABEL not in calls
    assert "GitHub refused auto-merge 3 times (GraphQL: Base branch was modified" in run.stdout
    # a 5xx answer after GitHub did the work: the next read finds auto-merge on, and the step ends with success
    run, calls = rerun_merge(tmp_path / "done", [open_, code, ("--json state,autoMergeRequest", "yes\n", 0),
                                                 (MERGE, "HTTP 502: Bad Gateway", 1)])
    assert run.returncode == 0 and calls.count(MERGE) == 1 and REMOVE_LABEL in calls and "has auto-merge on" in run.stdout
    # any other refusal fails the step at once, after one try
    run, calls = rerun_merge(tmp_path / "other", [open_, code, (MERGE, "pull request is in clean status", 1)])
    assert run.returncode == 1 and calls.count(MERGE) == 1 and ADD_LABEL not in calls
    # with no token of the implementer app the step warns and does nothing else
    run, calls = rerun_merge(tmp_path / "token", [open_, code], {"GH_TOKEN": ""})
    assert run.returncode == 0 and calls == [] and "no implementer app token" in run.stdout


def test_a_rerun_lifts_the_code_owners_guard_only_for_the_owners_label_on_githubs_facts(tmp_path: Path):
    """The owner's label `owner-approved` lifts the guard in the rerun as in `report`, but only from the issue events
    (the label's actor, its time against the head's first gates run), read by main's verdict.py, never a review's text."""
    owned = ("pulls/7/files", json.dumps({"filename": "mise.toml", "status": "modified", "patch": "+a"}) + "\n", 0)
    open_ = ("--json state --jq .state", "OPEN\n", 0)

    def label(actor: str, at: str = "2026-11-23T01:00:00Z", event: str = "labeled") -> tuple:
        row = {"event": event, "actor": {"login": actor}, "created_at": at, "label": {"name": "owner-approved"}}
        return ("issues/7/events", json.dumps(row) + "\n", 0)
    for case, events in (("none", ("issues/7/events", "", 0)), ("other", label("someone")),
                         ("old", label("maurolopes", "2026-11-22T01:00:00Z")), ("removed", label("maurolopes", event="unlabeled"))):
        run, calls = rerun_merge(tmp_path / case, [open_, owned, events, ("--json autoMergeRequest", "true\n", 0)])
        assert run.returncode == 0, (case, run.stdout + run.stderr)
        assert MERGE not in calls and "pr merge 7 -R owner/repo --disable-auto" in calls and ADD_LABEL in calls, case
        assert issue_calls(calls)[-1] == "linear state ABC-9 Escalated", case
        assert calls.index("pr merge 7 -R owner/repo --disable-auto") < calls.index(ADD_LABEL), "auto-merge goes off first"
    run, calls = rerun_merge(tmp_path / "lift", [open_, owned, label("maurolopes")])
    assert run.returncode == 0 and MERGE in calls and ADD_LABEL not in calls, run.stdout
    assert "auto-merge also for the owner's files (mise.toml)" in run.stdout
    assert issue_calls(calls)[-1] == "linear state ABC-9 Approved"
    # the pull request merged already: no label, no move, nothing turned off
    run, calls = rerun_merge(tmp_path / "merged", [("--json state --jq .state", "MERGED\n", 0), owned, label("someone")])
    assert run.returncode == 0 and issue_calls(calls) == [] and ADD_LABEL not in calls and MERGE not in calls
    assert "merged already: the issue keeps Linear's Done" in run.stdout
    # a failed read of the events fails the step: the guard is never lifted on a read that failed
    run, calls = rerun_merge(tmp_path / "read", [open_, owned, ("issues/7/events", "", 1)])
    assert run.returncode == 1 and MERGE not in calls
    # auto-merge that cannot be turned off fails the step before the label and the move: the pull request stays as it is
    run, calls = rerun_merge(tmp_path / "off", [open_, owned, ("--disable-auto", "", 1), ("--json autoMergeRequest", "true\n", 0)])
    assert run.returncode == 1 and ADD_LABEL not in calls and issue_calls(calls) == []


def test_the_rerun_script_runs_only_its_two_commands(tmp_path: Path):
    """An unknown command prints the usage and fails, so a typo in the workflow never passes as a step that did nothing."""
    script = REPO / ".github" / "verdict_rerun.py"
    for args in ([], ["other"], ["check", "merge"]):
        done = subprocess.run([sys.executable, "-I", str(script), *args], cwd=tmp_path, capture_output=True, text=True, check=False)
        assert done.returncode == 2 and "verdict_rerun.py check" in done.stderr, args


def test_an_approve_turns_on_auto_merge_and_the_owners_files_are_escalated(tmp_path):
    """The owner's call, 2026-10-25 (DECISIONS.md "Delivery"): GitHub merges on the verdict, and only the job `report`, which
    runs main's code, turns auto-merge on, after the check `verdict` concluded success. A change to a code owner's file
    moves the issue to Escalated, the one state that waits on the owner."""
    report = yaml.safe_load(REVIEW.read_text())["jobs"]["report"]
    step, early = the_step("report", REPORT_MERGE), early_disable()
    assert holds(step["if"], "steps.check.outputs.conclusion == 'success'")
    # before the check turns green, auto-merge goes off: GitHub merges seconds after. That step never stops the
    # check being written.
    steps = report["steps"]
    assert steps.index(early) < steps.index(the_step("report", "check")) < steps.index(step)
    assert early.get("continue-on-error") is True
    # verdict.py reads each file's name, its name before a move and its patch (a browser check removed), so the
    # command passes the files API's whole objects: a file moved out of a code owner's folder is still the owner's change
    files = "api repos/owner/repo/pulls/7/files?per_page=100 --paginate --jq .[]"
    code = ("pulls/7/files", json.dumps({"filename": "app/x.py", "status": "modified", "patch": "+a"}) + "\n", 0)
    moved = ("pulls/7/files", json.dumps({"filename": "app/x.yml", "previous_filename": ".github/workflows/x.yml",
                                          "status": "renamed", "patch": "+a"}) + "\n", 0)
    view = [("--json state --jq .state", "OPEN\n", 0), ("--json autoMergeRequest", "true\n", 0),
            ("--json headRefName", "abc-94-x\n", 0), ("--json title", "A title\n", 0)]
    linear, more = {".github/linear.py": FAKE_LINEAR}, {"LINEAR_API_KEY": "key"}
    disable = "pr merge 7 -R owner/repo --disable-auto"
    run, calls = run_step(tmp_path / "code", "report", REPORT_MERGE, [code, *view], linear, more)
    assert run.returncode == 0 and calls.index(files) < calls.index(MERGE), "a merge commit, never a squash; files first"
    assert disable not in calls and ADD_LABEL not in calls and not any(c.startswith("linear ") for c in calls), \
        "the command ends after the merge: the Linear part is for owned files only"
    # a code owner's file never gets auto-merge: the ruleset let three pull requests through on the reviewer app's
    # approve alone. An auto-merge from an earlier approve stays on after a push by an account with write access
    # (GitHub's documentation), so it goes off before the issue goes to Escalated (R1-1)
    run, calls = run_step(tmp_path / "owned", "report", REPORT_MERGE, [moved, *view], linear, more)
    assert run.returncode == 0 and MERGE not in calls, run.stdout + run.stderr
    assert calls.index(files) < calls.index(disable) < calls.index(ADD_LABEL) < calls.index("linear state ABC-9 Escalated")
    assert calls.index("linear comment ABC-9 --body-file owner.md") < calls.index("linear state ABC-9 Escalated")
    assert "\n- .github/workflows/x.yml\n" in (tmp_path / "owned" / "work" / "owner.md").read_text(), "the name before the move"
    # the move to Escalated skips a merged pull request: Linear's Done arrived first once, and the move overwrote it
    run, calls = run_step(tmp_path / "merged", "report", REPORT_MERGE, [moved, ("--json state --jq .state", "MERGED\n", 0), *view],
                          linear, more)
    assert run.returncode == 0 and "merged already" in run.stdout
    assert disable not in calls and ADD_LABEL not in calls and not any(c.startswith("linear ") for c in calls)
    # a pull request closed while the review ran keeps its issue, and still gets the label
    run, calls = run_step(tmp_path / "closed", "report", REPORT_MERGE, [moved, ("--json state --jq .state", "CLOSED\n", 0), *view],
                          linear, more)
    assert run.returncode == 0 and ADD_LABEL in calls and "linear state ABC-9 Escalated" not in calls
    assert "::notice::PR #7 is closed: ABC-9 stays as it is" in run.stdout
    # the step before the check turns off the earlier auto-merge of an owner's file, and nothing else
    run, calls = run_step(tmp_path / "early-owned", "report", EARLY, [moved, *view])
    assert run.returncode == 0 and calls[0] == files and calls[-1] == disable
    run, calls = run_step(tmp_path / "early-code", "report", EARLY, [code, *view])
    assert run.returncode == 0 and disable not in calls
    run, calls = run_step(tmp_path / "early-off", "report", EARLY, [moved, ("--json autoMergeRequest", "false\n", 0), *view])
    assert run.returncode == 0 and disable not in calls



# The label `escalated` marks an open pull request whose issue waits on the owner (agents/README.md "The issue states").
# The steps below run with a fake gh that logs each call and answers by the first rule whose text is in the call.
LOGGING_GH = """#!/usr/bin/env python3
import json, os, sys
fake = os.environ["FAKE"]
call = " ".join(sys.argv[1:])
if "--input -" in call:  # read the body as gh does: a writer into a pipe nobody reads may fail with a broken pipe
    sys.stdin.read()
with open(os.path.join(fake, "log"), "a") as log:
    log.write(call + "\\n")
with open(os.path.join(fake, "tokens"), "a") as log:
    log.write(os.environ.get("GH_TOKEN", "") + " " + call + "\\n")
# a rule [part, reply, code, times] answers its first `times` matching calls only, then the next rule answers
used_file = os.path.join(fake, "used.json")
used = json.load(open(used_file)) if os.path.exists(used_file) else {}
for n, (part, reply, code, *times) in enumerate(json.load(open(os.path.join(fake, "rules.json")))):
    if part in call:
        if times:
            if used.get(str(n), 0) >= times[0]:
                continue
            used[str(n)] = used.get(str(n), 0) + 1
            json.dump(used, open(used_file, "w"))
        sys.stdout.write(reply)
        sys.exit(code)
"""
ADD_LABEL = 'api -X POST repos/owner/repo/issues/7/labels -f labels[]=escalated'
REMOVE_LABEL = "api -X DELETE repos/owner/repo/issues/7/labels/escalated"


def the_step(job: str, does: str, flow: Path = REVIEW) -> dict:
    """The one step of the job whose id is `does`, else the one whose script holds `does`. A test finds a step by what it
    does, never by its display name: a reworded name changes nothing that a test checks."""
    steps = yaml.safe_load(flow.read_text())["jobs"][job]["steps"]
    found = [s for s in steps if s.get("id") == does] or [s for s in steps if does in str(s.get("run", ""))]
    assert len(found) == 1, f"{flow.name} {job}: {len(found)} steps do {does!r}"
    return found[0]


def holds(expression: object, *terms: str) -> bool:
    """Whether a workflow's value holds each term, whatever its spacing, its `${{ }}` and its other terms. A test checks
    the terms that make its rule, never the whole expression's wording."""
    text = " ".join(str(expression).split())
    return bool(terms) and all(" ".join(term.split()) in text for term in terms)


def early_disable() -> dict:
    """The report's step that turns auto-merge off before the check: review_report.py `early`."""
    return the_step("report", EARLY)


# The review job's steps call main's script, one command each; a test finds a step by its call (the_step)
CALL = "python3 -I /tmp/trusted/review_steps.py"
COPIES = "review_steps.py trusted"
LABEL_OFF = "review_steps.py label-off"
PAST_THIRD = "review_steps.py past-third"
MANUAL_RUN = "review_steps.py manual"
DENIED = "review_steps.py denied"
AUTO_MERGE = "--auto --merge"
NEEDS_DECISION = "review_report.py escalated"
OWNER_MOVES = "review_report.py owner-approved"
REPORT_MERGE = "review_report.py merge"  # the report's auto-merge, or Escalated for the owner's files
EARLY = "review_report.py early"  # the report's step that turns auto-merge off before the check


def run_step(tmp_path: Path, job: str, does: str | dict, rules: list[tuple[str, str, int]] | list[tuple], files: dict[str, str] | None = None,
             more: dict[str, str] | None = None, flow: Path = REVIEW) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """One `run:` step of the review workflow (a step, or what the_step finds it by), run as GitHub runs it (bash -e,
    pipefail) in a folder with main's copies of the scripts the report job runs, gh faked. It gives the run and gh's calls."""
    step = does if isinstance(does, dict) else the_step(job, does, flow)
    fake, bin_, work = tmp_path / "fake", tmp_path / "bin", tmp_path / "work"
    for folder in (fake, bin_, work / ".github"):
        folder.mkdir(parents=True)
    for script in ("verdict.py", "linear.py", "review_jobs.py", "review_report.py", "CODEOWNERS", "verdict_rerun.py"):
        (work / ".github" / script).write_text((REPO / ".github" / script).read_text())
    for path, text in (files or {}).items():
        (work / path).parent.mkdir(parents=True, exist_ok=True)
        (work / path).write_text(text)
    (fake / "rules.json").write_text(json.dumps(rules))
    (fake / "log").write_text("")
    (bin_ / "gh").write_text(LOGGING_GH)
    (bin_ / "gh").chmod(0o755)
    (bin_ / "python3").symlink_to(sys.executable)
    env = {"PATH": f"{bin_}:/usr/bin:/bin", "FAKE": str(fake), "PR": "7", "GITHUB_REPOSITORY": "owner/repo",
           "REVIEWER": "maurolopes-reviewer[bot]", "SINCE": "2026-10-28T10:00:00Z", "GH_TOKEN": "token", **(more or {})}
    run = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
                         cwd=work, env=env, capture_output=True, text=True, check=False)
    return run, (fake / "log").read_text().splitlines()


def test_a_pull_request_with_no_issue_key_gets_a_failed_check_and_no_review(tmp_path):
    """A pull request had no issue key in its branch or its title, the review ran anyway, and its verdict moved no issue.
    The start reads the key with main's `linear.py key-from`, as the review's facts do: a key in the branch or in the title
    alone starts the review; no key writes a completed failed check `verdict` with "No issue" and no check id, so `review`
    and `report` skip. The owner's Approved needs no key."""
    name = OPEN_STEP
    flow = yaml.safe_load(REVIEW.read_text())["jobs"]
    checkout = next(s for s in flow["start"]["steps"] if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert "if" not in checkout, "main's code for every start, also for the step `access`, which holds the key"
    steps = flow["start"]["steps"]
    assert steps.index(checkout) < steps.index(the_step("start", "access")) < steps.index(the_step("start", name)), \
        "the checkout comes before the steps that run main's scripts"
    assert holds(flow["review"]["if"], "needs.start.outputs.check != ''") and holds(flow["report"]["if"], "needs.start.outputs.check != ''")

    def start(case: str, branch: str, title: str) -> tuple[subprocess.CompletedProcess[str], list[str], str]:
        out = tmp_path / f"{case}.out"
        pr = json.dumps({"headRefOid": "abc123", "headRefName": branch, "title": title})
        run, calls = run_step(tmp_path / case, "start", name, [("pr view 7", pr, 0), ("check_name=verdict", "[]", 0), ("check-runs", "99", 0)],
                              more={"GITHUB_OUTPUT": str(out), "LABELED_HEAD": ""})
        return run, calls, out.read_text() if out.exists() else ""

    for case, branch, title in (("branch", "abc-730-review-needs-issue-key", "The review needs a key"),
                                ("title", "fix-the-thing", "ABC-721: the settings page")):
        run, calls, out = start(case, branch, title)
        assert run.returncode == 0, run.stderr
        assert "check=99" in out and "head=abc123" in out, case
        assert any("status=in_progress" in c for c in calls) and not any("conclusion=failure" in c for c in calls), case
    run, calls, out = start("none", "fix-the-thing", "Settings: fix the five findings")
    assert run.returncode == 0, run.stderr
    assert "head=abc123" in out and "check=" not in out, "no check id: review and report skip"
    [written] = [c for c in calls if "check-runs" in c and "check_name" not in c]
    assert "status=completed" in written and "conclusion=failure" in written and "head_sha=abc123" in written
    assert "output[title]=No issue: put its key in the title" in written
    assert "::error::" in run.stdout and "status=in_progress" not in written


def test_one_ready_mark_gives_one_review_of_a_head(tmp_path):
    """The runs of the group `review-N` go one after the other, so a second run's `start` reads the check that the first run
    completed (2026-11-25, PR #6). `start` writes no `check` output (so `review` and `report` skip) when the pull request is in
    draft now, or when a check `verdict` of the head completed after the time of the mark in this run's payload (or a review was posted: the next test). A check that
    completed before the mark, and a check in progress, do not hold the head: a mark again after a finished review starts a
    review. A run by hand has no mark time and is not covered: its run starts after the earlier review ended."""
    def start(case: str, draft: bool, listed: list[dict], mark: str | None = "2026-11-25T13:46:00Z",
              reviews: list[dict] | None = None) -> tuple[str, list[str], str]:
        out = tmp_path / f"{case}.out"
        event = tmp_path / f"{case}.event.json"
        event.write_text(json.dumps({"pull_request": {"updated_at": mark}}))
        pr = json.dumps({"headRefOid": "abc123", "headRefName": "abc-1-a-change", "title": "ABC-1: a change", "isDraft": draft})
        more = {"GITHUB_OUTPUT": str(out), "LABELED_HEAD": "", "GITHUB_EVENT_NAME": "pull_request" if mark else "workflow_dispatch",
                "GITHUB_EVENT_PATH": str(event)}
        run, calls = run_step(tmp_path / case, "start", OPEN_STEP, [("pr view 7", pr, 0), ("check_name=verdict", json.dumps(listed), 0),
                                                                    ("pulls/7/reviews", "\n".join(json.dumps(r) for r in reviews or []), 0),
                                                                    ("check-runs", "99", 0)], more=more)
        assert run.returncode == 0, run.stderr
        return run.stdout, calls, out.read_text()

    def done(at: str, status: str = "completed") -> dict:
        return {"name": "verdict", "status": status, "completed_at": at}

    stdout, calls, out = start("second", False, [done("2026-11-25T13:51:13Z")])
    assert "head=abc123" in out and "check=" not in out and "completed after the mark" in stdout
    assert not any("status=in_progress" in c for c in calls), "no second check in progress"
    stdout, calls, out = start("draft", True, [])
    assert "head=abc123" in out and "check=" not in out and "in draft now" in stdout
    for case, listed, mark in (("none", [], "2026-11-25T13:46:00Z"), ("before", [done("2026-11-25T13:40:00Z")], "2026-11-25T13:46:00Z"),
                               ("running", [done("2026-11-25T13:51:13Z", "in_progress")], "2026-11-25T13:46:00Z"),
                               ("other", [{"name": "python", "status": "completed", "completed_at": "2026-11-25T13:51:13Z"}],
                                "2026-11-25T13:46:00Z"),
                               ("by-hand", [done("2026-11-25T13:51:13Z")], None)):
        stdout, calls, out = start(case, False, listed, mark)
        assert "check=99" in out, case


def test_a_review_posted_after_the_mark_gives_no_second_review_of_the_head(tmp_path):
    """OWL-35: the model posts its review before the check `verdict` completes, so a start event that comes after a review of
    the head by the reviewer app, posted since its mark, starts no second review, with no completed check yet. A review of
    another head, by another account, or before the mark does not hold the head."""
    def review(at: str, head: str = "abc123", login: str = "maurolopes-reviewer[bot]", state: str = "APPROVED") -> dict:
        return {"user": {"login": login}, "commit_id": head, "submitted_at": at, "state": state, "body": "see [a][1] and a[1][2]"}

    def start(case: str, reviews: list[dict]) -> tuple[str, str]:
        out, event = tmp_path / f"{case}.out", tmp_path / f"{case}.event.json"
        event.write_text(json.dumps({"pull_request": {"updated_at": "2026-11-25T13:46:00Z"}}))
        pr = json.dumps({"headRefOid": "abc123", "headRefName": "abc-1-a-change", "title": "ABC-1: a change", "isDraft": False})
        more = {"GITHUB_OUTPUT": str(out), "LABELED_HEAD": "", "GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(event)}
        run, _ = run_step(tmp_path / case, "start", OPEN_STEP, [("pr view 7", pr, 0), ("check_name=verdict", "[]", 0),
                                                                ("pulls/7/reviews", "\n".join(json.dumps(r) for r in reviews), 0), ("check-runs", "99", 0)],
                          more=more)
        assert run.returncode == 0, run.stderr
        return run.stdout, out.read_text()

    stdout, out = start("late", [review("2026-11-25T13:50:00Z")])
    assert "check=" not in out and "was posted after the mark" in stdout
    for case, reviews in (("before", [review("2026-11-25T13:40:00Z")]), ("other-head", [review("2026-11-25T13:50:00Z", head="def456")]),
                          ("other-account", [review("2026-11-25T13:50:00Z", login="someone")]),
                          ("inline-comments-only", [review("2026-11-25T13:50:00Z", state="COMMENTED")])):
        assert "check=99" in start(case, reviews)[1], case


def test_a_second_review_of_a_head_posted_since_the_run_started_is_found(tmp_path):
    """OWL-35: the command `posted` counts the reviewer app's reviews of the head since the run's start. The run's own review
    is one, so `second=true` needs two."""
    ended: list[str] = []

    def posted(case: str, reviews: list[dict]) -> str:
        out = tmp_path / f"{case}.out"
        run, calls, _ = run_job(tmp_path / case, "posted", [("pulls/7/reviews", "\n".join(json.dumps(r) for r in reviews), 0)], None,
                                {"GITHUB_OUTPUT": str(out), "STARTED": "2026-11-25T13:46:00Z", "HEAD": "abc123", "CHECK": "99"})
        assert run.returncode == 0, run.stderr
        ended.extend(c for c in calls if "check-runs/99" in c and "conclusion=neutral" in c)
        return out.read_text()

    def review(at: str, head: str = "abc123", state: str = "CHANGES_REQUESTED") -> dict:
        return {"user": {"login": "maurolopes-reviewer[bot]"}, "commit_id": head, "submitted_at": at, "state": state}

    assert posted("two", [review("2026-11-25T13:50:00Z"), review("2026-11-25T13:52:00Z", state="APPROVED")]) == "second=true\n"
    assert len(ended) == 1, "a second review ends this run's check as neutral"
    # OWL-49: a run with an inline comment posts its verdict and a COMMENTED review; that is one verdict, not two
    assert posted("verdict-and-inline", [review("2026-11-25T13:50:00Z"), review("2026-11-25T13:50:05Z", state="COMMENTED")]) == "second=false\n"
    assert posted("one", [review("2026-11-25T13:50:00Z")]) == "second=false\n"
    assert posted("old", [review("2026-11-25T13:40:00Z"), review("2026-11-25T13:50:00Z")]) == "second=false\n"
    assert posted("other-head", [review("2026-11-25T13:50:00Z"), review("2026-11-25T13:52:00Z", head="def456")]) == "second=false\n"
    assert len(ended) == 1, "no other case touches the check"
    # R1-1: a failed PATCH writes no `second`, so the check step writes the verdict as before
    out = tmp_path / "patch-fails.out"
    two = [review("2026-11-25T13:50:00Z"), review("2026-11-25T13:52:00Z")]
    run, _, _ = run_job(tmp_path / "patch-fails", "posted", [("check-runs/99", "", 1), ("pulls/7/reviews", "\n".join(json.dumps(r) for r in two), 0)], None,
                        {"GITHUB_OUTPUT": str(out), "STARTED": "2026-11-25T13:46:00Z", "HEAD": "abc123", "CHECK": "99"})
    assert run.returncode != 0 and not out.exists()


def test_a_round_removes_the_label_escalated_first():
    """The owner's answer starts a new round 1, and a fix round's ready mark round 2: the pull request no longer waits on the
    owner, so the label goes before the review. A failure only warns: the review goes on."""
    steps = yaml.safe_load(REVIEW.read_text())["jobs"]["review"]["steps"]
    step = the_step("review", LABEL_OFF)
    assert holds(step["if"], "github.event.action == 'labeled' || steps.facts.outputs.round <= 3") and step["continue-on-error"] is True
    assert holds(step["env"]["GH_TOKEN"], "steps.token.outputs.token"), "the reviewer app's token: pull requests write"
    order = [steps.index(the_step("review", does)) for does in ("facts", LABEL_OFF, "review")]
    assert order == sorted(order), "the label goes after the facts, before the model"
    assert step["run"].strip() == f"{CALL} label-off", "what it does with each state of the label: test_review_steps.py"


def test_a_fourth_ready_signal_adds_the_label_escalated():
    """A round past the third (three rounds at most) gets a comment and no review: the issue waits on the owner in Escalated. A failed label call only
    warns, and the comment still goes."""
    step = the_step("review", PAST_THIRD)
    assert holds(step["if"], "github.event.action != 'labeled' && steps.facts.outputs.round > 3"), "the owner's Approved is no round"
    assert step["run"].strip() == f"{CALL} past-third", "the label, the comment and the move to Escalated: test_review_steps.py"
    # The issue goes to Escalated too, so the label and the state agree; the step reads the issue's key, the Linear key
    # and the round from the facts and the secrets
    assert holds(step["env"]["KEY"], "steps.facts.outputs.key") and holds(step["env"]["ROUND"], "steps.facts.outputs.round")
    assert holds(step["env"]["LINEAR_API_KEY"], "secrets.LINEAR_API_KEY")


def test_a_conflict_on_an_approved_pull_request_starts_the_count_again():
    """The owner's call, 2026-10-28: the resolution of a conflict that the branch update found is round 1 again.
    The facts step runs main's script, which reads the time from main's verdict.py (the reviews and the comments: the
    behaviour is held by test_review_steps.py), and an older main's script, which has no command `since`, leaves the
    count where it was. The job names the branch update's app, whose comments count."""
    facts = the_step("review", "facts")
    assert facts["run"].strip() == f"{CALL} facts"
    assert holds(facts["env"]["OWNER"], "github.repository_owner") and holds(facts["env"]["ROUND_INPUT"], "inputs.round")
    job = yaml.safe_load(REVIEW.read_text())["jobs"]["review"]
    assert job["env"]["UPDATER"] == IMPLEMENTER_LOGIN and "REVIEWER" not in job["env"], "REVIEWER: test_each_job_takes_the_reviewer_s_login_from_its_token"
    assert job["env"]["HEAD"] and job["env"]["PR"], "the facts read the head and the pull request from the job's environment"
    assert the_step("review", COPIES)["run"].strip().endswith(f"{CALL} trusted"), "main's verdict.py, never the branch's"


NOT_COVERED = "review_jobs.py again"  # carry's step: a real commit after the approve gets a new review


def test_a_real_commit_after_the_approve_gets_a_new_review(tmp_path):
    """(narrowed by the owner's call, 2026-11-02): `carry` gives no check to a real commit, and no implementer
    has anything to fix, so the job comments (verdict.py's PUSHED first: the review counts its rounds again from it) and
    starts a review of main's workflow with GitHub Actions' token, which may start a workflow_dispatch run. An automatic
    merge of main starts none, also when main changed the pull request's lines: `carry` gives it the approve's check. A
    dismissal, a carried check and an older main's verdict.py start nothing."""
    from tests.test_verdict import verdict

    job = yaml.safe_load(REVIEW.read_text())["jobs"]["carry"]
    step = the_step("carry", NOT_COVERED)
    assert job["steps"].index(step) == len(job["steps"]) - 1 and holds(step["if"], "github.event_name == 'pull_request'")
    assert job["permissions"]["actions"] == "write" and holds(step["env"]["DISPATCH"], "github.token")
    assert holds(step["env"]["MAIN"], "github.event.repository.default_branch"), "main's workflow, never the branch's"
    token = next(s for s in job["steps"] if s.get("id") == "token")
    assert token["with"]["permission-pull-requests"] == "write", "the reviewer app comments"
    says = {word: {".github/verdict.py": f"print('{word}')\n"} for word in ("true", "false", "pushed")}  # main's `rereview`, faked
    more = {"HEAD": "b" * 40, "DISPATCH": "actions-token", "MAIN": "main"}
    run, calls = run_step(tmp_path / "again", "carry", NOT_COVERED, [], says["pushed"], more)
    assert run.returncode == 0, run.stdout + run.stderr
    comment = next(c for c in calls if c.startswith("pr comment 7"))
    assert comment.split("--body ", 1)[1].startswith(verdict.PUSHED) and "round 1" in comment
    dispatch = "workflow run claude-code-review.yml -R owner/repo --ref main -f pr=7"
    assert calls.index(comment) < calls.index(dispatch), "the comment first: the review counts its round from it"
    tokens = (tmp_path / "again" / "fake" / "tokens").read_text().splitlines()
    assert f"actions-token {dispatch}" in tokens and any(t.startswith("token pr comment 7") for t in tokens)
    run, calls = run_step(tmp_path / "refused", "carry", NOT_COVERED, [("pr comment", "", 1)], says["pushed"], more)
    assert run.returncode == 0 and "::warning::" in run.stdout and dispatch in calls, "a failed comment still starts the review"
    # No word starts a review but `pushed`: an update that changed the lines is carried, never reviewed again
    for name, files in (("carried", says["false"]), ("changed", says["true"]), ("old", {".github/verdict.py": "import sys\nsys.exit(2)\n"})):
        run, calls = run_step(tmp_path / name, "carry", NOT_COVERED, [], files, more)
        assert run.returncode == 0 and calls == [], name
    # A real commit after the approve gets a comment that says so, never that main changed lines
    run, calls = run_step(tmp_path / "pushed", "carry", NOT_COVERED, [], {".github/verdict.py": "print('pushed')\n"}, more)
    comment = next(c for c in calls if c.startswith("pr comment 7"))
    assert comment.split("--body ", 1)[1].startswith(verdict.PUSHED) and "main changed" not in comment.lower(), comment
    assert dispatch in calls


def run_job(tmp_path: Path, command: str, rules: list[tuple[str, str, int]], verdict_py: str | None, more: dict[str, str]
            ) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    """One command of main's review_jobs.py in a folder that holds only the files that the test gives it (verdict.py or
    none), with gh and git faked: `calls` is the log of gh's and git's calls in the order they ran, each git call starting
    with the word `git`."""
    fake, bin_, work = tmp_path / "fake", tmp_path / "bin", tmp_path / "work"
    for folder in (fake, bin_, work / ".github"):
        folder.mkdir(parents=True)
    (work / ".github" / "review_jobs.py").write_text((REPO / ".github" / "review_jobs.py").read_text())
    if verdict_py is not None:
        (work / ".github" / "verdict.py").write_text(verdict_py)
    (fake / "rules.json").write_text(json.dumps(rules))
    (fake / "log").write_text("")
    for name, text in (("gh", LOGGING_GH), ("git", '#!/bin/sh\necho "git $*" >> "$FAKE/log"\n')):
        (bin_ / name).write_text(text)
        (bin_ / name).chmod(0o755)
    (bin_ / "python3").symlink_to(sys.executable)
    env = {"PATH": f"{bin_}:/usr/bin:/bin", "FAKE": str(fake), "PR": "7", "GITHUB_REPOSITORY": "owner/repo",
           "REVIEWER": "maurolopes-reviewer[bot]", "GH_TOKEN": "token", "HEAD": "b" * 40, **more}
    run = subprocess.run(["python3", "-I", ".github/review_jobs.py", command], cwd=work, env=env, capture_output=True, text=True,
                         check=False)
    return run, (fake / "log").read_text().splitlines(), work


def test_the_carry_step_writes_the_check_that_main_s_verdict_gives_it(tmp_path):
    """The first step of `carry` reads the reviews of the pull request, asks main's verdict.py (`carry` for a push,
    `dismissed` for a dismissal: the workflow's COMMAND) for the check, and writes it with the reviewer app's token, as the
    shell block did. A verdict.py that says nothing writes nothing, and a main with no verdict.py yet does nothing."""
    step = the_step("carry", "review_jobs.py carry")
    assert holds(step["env"]["COMMAND"], "pull_request_review", "'dismissed'", "'carry'")
    fetch = f"git fetch -q origin {'b' * 40}"
    reviews = "api repos/owner/repo/pulls/7/reviews?per_page=100 --paginate --jq .[]"
    says = "import sys, json\nopen('argv.txt', 'w').write(' '.join(sys.argv[1:]))\nprint(json.dumps({'name': 'verdict', 'conclusion': 'success'}))\n"
    run, calls, work = run_job(tmp_path / "check", "carry", [("pulls/7/reviews", '{"id": 1}\n', 0)], says, {"COMMAND": "dismissed"})
    assert run.returncode == 0, run.stdout + run.stderr
    assert calls == [fetch, reviews, "api repos/owner/repo/check-runs --input check.json"], calls
    assert (work / "reviews.jsonl").read_text() == '{"id": 1}\n' and '"conclusion": "success"' in run.stdout
    assert (work / "argv.txt").read_text() == f"dismissed reviews.jsonl --reviewer maurolopes-reviewer[bot] --head {'b' * 40}"
    tokens = (tmp_path / "check" / "fake" / "tokens").read_text().splitlines()
    assert tokens[-1].startswith("token api repos/owner/repo/check-runs"), "the app's token writes the check"
    run, calls, _ = run_job(tmp_path / "nothing", "carry", [], "pass\n", {"COMMAND": "carry"})
    assert run.returncode == 0 and calls == [fetch, reviews], "no check, no write"
    run, calls, _ = run_job(tmp_path / "old", "carry", [], None, {"COMMAND": "carry"})
    assert run.returncode == 0 and calls == [] and "main has no verdict.py yet" in run.stdout
    run, calls, _ = run_job(tmp_path / "unread", "carry", [("pulls/7/reviews", "", 1)], says, {"COMMAND": "carry"})
    assert run.returncode == 1 and "::error::gh failed with status 1" in run.stdout and calls == [fetch, reviews], \
        "a failed read of the reviews fails the step, as `set -e` did"


def test_a_command_that_review_jobs_does_not_know_fails_with_its_help(tmp_path):
    run, calls, _ = run_job(tmp_path / "unknown", "bogus", [], None, {})
    assert run.returncode == 2 and calls == [] and "python3 -I .github/review_jobs.py access" in run.stderr


# The implementer app's slug and login in a workflow: the repository's variable, else the owner's shared pair. A repository with
# its own app pair sets IMPLEMENTER_APP_SLUG (agents/README.md "Setting up a product")
IMPLEMENTER_SLUG = "${{ vars.IMPLEMENTER_APP_SLUG || format('{0}-implementer', github.repository_owner) }}"
IMPLEMENTER_LOGIN = IMPLEMENTER_SLUG + "[bot]"
# Each job that reads REVIEWER, and the id of its reviewer token step: the login is that token's app-slug
REVIEWER_LOGIN_RUN = 'echo "REVIEWER=${SLUG}[bot]" >> "$GITHUB_ENV"'
REVIEWER_JOBS = {(REVIEW.name, "review"): "token", (REVIEW.name, "report"): "token", (REVIEW.name, "carry"): "token",
                 ("review-watchdog.yml", "watch"): "token", ("verdict-rerun.yml", "rerun"): "reviewer"}


def test_no_workflow_names_an_app_by_a_fixed_login():
    """A public repository has its own app pair, so no workflow names an app's login: the reviewer's comes from its token
    (`app-slug`), the implementer's from IMPLEMENTER_APP_SLUG, else OWNER-implementer."""
    for path in workflow_paths():
        text = path.read_text()
        fixed = re.findall(r"[\w.-]+-(?:reviewer|implementer)(?:\[bot\])?(?=[\s,'\"]|$)", text.replace(IMPLEMENTER_SLUG, ""))
        assert [f for f in fixed if not re.match(r"[A-Z_]+-", f)] == [], path.name  # OWNER-implementer in a comment is a pattern
        assert text.count("vars.IMPLEMENTER_APP_SLUG") == text.count(IMPLEMENTER_SLUG), f"{path.name}: one form for the implementer's login"


def test_each_job_takes_the_reviewer_s_login_from_its_token():
    """The reviewer app's login is the app-slug of the token that the job makes, written to GITHUB_ENV right after that
    token, so every later step reads the app that really posts the reviews. A job-level REVIEWER would be a fixed name."""
    for (file, name), token in REVIEWER_JOBS.items():
        job = yaml.safe_load((REVIEW.parent / file).read_text())["jobs"][name]
        assert "REVIEWER" not in (job.get("env") or {}), f"{file} {name}"
        ids = [s.get("id") for s in job["steps"]]
        login = job["steps"][ids.index(token) + 1]
        assert login["env"]["SLUG"] == f"${{{{ steps.{token}.outputs.app-slug }}}}", f"{file} {name}"
        assert login["run"].strip() == REVIEWER_LOGIN_RUN, f"{file} {name}"
    start = the_step("start", OPEN_STEP)
    assert start["env"]["REVIEWER"] == "${{ steps.token.outputs.app-slug }}[bot]", "the start's check for a review already posted"
    rerun = yaml.safe_load((REVIEW.parent / "verdict-rerun.yml").read_text())["jobs"]["rerun"]
    assert rerun["env"]["UPDATER"] == IMPLEMENTER_LOGIN


def test_the_reviewer_s_login_step_writes_the_token_s_app(tmp_path):
    """The step, run as GitHub runs it, gives a second pair's reviewer its own login."""
    login = yaml.safe_load(REVIEW.read_text())["jobs"]["carry"]["steps"][2]
    out = tmp_path / "env"
    subprocess.run(["bash", "-eo", "pipefail", "-c", login["run"]], env={"SLUG": "acme-product-reviewer", "GITHUB_ENV": str(out),
                   "PATH": "/usr/bin:/bin"}, check=True)
    assert out.read_text() == "REVIEWER=acme-product-reviewer[bot]\n"


def test_the_review_that_github_actions_or_the_implementer_starts_may_run():
    """`carry` and `report` start a review with GitHub Actions' token, so its actor is the bot github-actions.
    The agents open their pull requests as the implementer app. The action refuses a bot that `allowed_bots`
    does not list, and the list names these two only, never '*'."""
    review = the_step("review", "review")
    assert review["uses"].startswith("anthropics/claude-code-action@")
    assert review["with"]["allowed_bots"] == f"github-actions,{IMPLEMENTER_SLUG}"


PUSHED_DURING_REVIEW = "review_report.py pushed"  # report's step: a commit pushed while the review ran gets a new review


def test_a_commit_pushed_while_the_review_ran_gets_a_new_review(tmp_path):
    """`report` sees a head that is not the one the review read, `carry` gave it nothing and a real commit follows the
    approved one: the reviewer app comments (verdict.py's PUSHED first: the review counts its rounds again from it) with
    pull-requests write, and a review of main's workflow starts with GitHub Actions' token (actions write). A failed
    comment or dispatch warns, never hides."""
    from tests.test_verdict import verdict

    job = yaml.safe_load(REVIEW.read_text())["jobs"]["report"]
    step = the_step("report", PUSHED_DURING_REVIEW)
    assert job["permissions"]["actions"] == "write" and holds(step["env"]["DISPATCH"], "github.token")
    assert holds(step["env"]["MAIN"], "github.event.repository.default_branch"), "main's workflow, never the branch's"
    token = next(s for s in job["steps"] if s.get("id") == "token")
    assert token["with"]["permission-pull-requests"] == "write", "the reviewer app comments"
    assert job["steps"].index(next(s for s in job["steps"] if s.get("id") == "check")) < job["steps"].index(step)
    says = lambda word: {".github/verdict.py": f"print('{word}')\n", "reviews.jsonl": ""}  # noqa: E731 (main's `rereview`, faked)
    more = {"HEAD": "a" * 40, "DISPATCH": "actions-token", "MAIN": "main"}
    dispatch = "workflow run claude-code-review.yml -R owner/repo --ref main -f pr=7"
    run, calls = run_step(tmp_path / "pushed", "report", PUSHED_DURING_REVIEW, [("pr view", "b" * 40, 0)], says("pushed"), more)
    assert run.returncode == 0, run.stdout + run.stderr
    comment = next(c for c in calls if c.startswith("pr comment 7"))
    assert comment.split("--body ", 1)[1].startswith(verdict.PUSHED) and "round 1" in comment
    assert calls.index(comment) < calls.index(dispatch), "the comment first: the review counts its round from it"
    tokens = (tmp_path / "pushed" / "fake" / "tokens").read_text().splitlines()
    assert f"actions-token {dispatch}" in tokens and any(t.startswith("token pr comment 7") for t in tokens)
    run, calls = run_step(tmp_path / "refused", "report", PUSHED_DURING_REVIEW, [("pr comment", "", 1), ("workflow run", "", 1)],
                          says("pushed"), {**more})
    assert run.returncode == 0 and run.stdout.count("::warning::") == 2 and dispatch in calls
    for name, word in (("none", "false"), ("changed", "true")):
        run, calls = run_step(tmp_path / name, "report", PUSHED_DURING_REVIEW, [("pr view", "b" * 40, 0)], says(word), more)
        assert run.returncode == 0 and not any(c.startswith(("pr comment", "workflow run")) for c in calls), name
    run, calls = run_step(tmp_path / "same", "report", PUSHED_DURING_REVIEW, [("pr view", "a" * 40, 0)], says("pushed"), more)
    assert run.returncode == 0 and not any(c.startswith(("pr comment", "workflow run")) for c in calls), "no push"


def reviews_line(state: str, word: str, when: str) -> str:
    body = f"Summary.\n\n```json\n{json.dumps({'pull_request': 7, 'commit': 'a' * 40, 'round': 2, 'verdict': word})}\n```\n"
    return json.dumps({"user": {"login": "maurolopes-reviewer[bot]"}, "state": state, "body": body, "submitted_at": when,
                       "html_url": "https://github.com/owner/repo/pull/7#review-1"}) + "\n"


def test_a_needs_decision_adds_the_label_escalated(tmp_path):
    """Blocking findings after round 3, or a finding that is the owner's call, give a needs-decision: the reviewer moves the
    issue to Escalated, and the job `report` adds the label from the verdict this run wrote the check from. A failure only
    warns: the check `verdict` is written already."""
    steps = yaml.safe_load(REVIEW.read_text())["jobs"]["report"]["steps"]
    step = the_step("report", NEEDS_DECISION)
    assert holds(step["if"], "steps.check.outputs.conclusion == 'failure'") and step["continue-on-error"] is True
    assert next(i for i, s in enumerate(steps) if s.get("id") == "check") < steps.index(step)
    needs = reviews_line("CHANGES_REQUESTED", "needs-decision", "2026-10-28T10:30:00Z")
    run, calls = run_step(tmp_path / "needs", "report", NEEDS_DECISION, [], {"reviews.jsonl": needs})
    assert run.returncode == 0 and calls == [ADD_LABEL]
    changes = reviews_line("CHANGES_REQUESTED", "request-changes", "2026-10-28T10:30:00Z")
    run, calls = run_step(tmp_path / "changes", "report", NEEDS_DECISION, [], {"reviews.jsonl": changes})
    assert run.returncode == 0 and calls == [], "a request for changes goes to In Progress"
    earlier = reviews_line("CHANGES_REQUESTED", "needs-decision", "2026-10-28T09:00:00Z")
    run, calls = run_step(tmp_path / "earlier", "report", NEEDS_DECISION, [], {"reviews.jsonl": earlier})
    assert run.returncode == 0 and calls == [], "a verdict before this run is not this run's"
    run, calls = run_step(tmp_path / "refused", "report", NEEDS_DECISION, [("-X POST", "", 1)], {"reviews.jsonl": needs})
    assert run.returncode == 0 and "::warning::" in run.stdout and calls == [ADD_LABEL]
    # main's verdict.py before this change has no command `escalates`: the step warns and adds nothing
    run, calls = run_step(tmp_path / "old", "report", NEEDS_DECISION, [],
                          {"reviews.jsonl": needs, ".github/verdict.py": "import sys\nsys.exit(2)\n"})
    assert run.returncode == 0 and "::warning::" in run.stdout and calls == []


def test_an_approve_of_the_owners_files_adds_the_label_escalated(tmp_path):
    """The approved pull request changes a code owner's file: no auto-merge, the issue goes to Escalated, and the pull request
    gets the label, with a Linear issue or without one. Auto-merge adds no label, and a merged pull request gets none."""
    name = REPORT_MERGE
    view = [("--json state", "OPEN\n", 0), ("--json autoMergeRequest", "false\n", 0), ("--json headRefName", "abc-185-x\n", 0),
            ("--json title", "A title\n", 0)]
    owned = ("pulls/7/files", json.dumps({"filename": ".github/workflows/x.yml", "status": "modified", "patch": "+a"}) + "\n", 0)
    run, calls = run_step(tmp_path / "owned", "report", name, [owned, *view])
    assert run.returncode == 0 and ADD_LABEL in calls and not any("--auto --merge" in c for c in calls)
    assert calls.index(ADD_LABEL) > next(i for i, c in enumerate(calls) if "--json autoMergeRequest" in c)
    code = ("pulls/7/files", json.dumps({"filename": "app/x.py", "status": "modified", "patch": "+a"}) + "\n", 0)
    run, calls = run_step(tmp_path / "code", "report", name, [code, *view])
    assert run.returncode == 0 and ADD_LABEL not in calls and "pr merge 7 -R owner/repo --auto --merge" in calls
    run, calls = run_step(tmp_path / "merged", "report", name, [owned, ("--json state", "MERGED\n", 0), *view])
    assert run.returncode == 0 and ADD_LABEL not in calls, "a merged pull request waits on nobody"
    run, calls = run_step(tmp_path / "refused", "report", name, [("-X POST", "", 1), owned, *view])
    assert run.returncode == 0 and "::warning::" in run.stdout and "::notice::" in run.stdout, "the step goes on to its end"

def test_the_owners_label_turns_on_auto_merge_also_for_the_owners_files(tmp_path):
    """Two pull requests waited after the label: the owner's label owner-approved is also the owner's merge, so
    auto-merge goes on for a code owner's file too, and the issue is not sent back to Escalated. Only `start`'s output
    `owner` lifts the guard: it is true only when main's verdict.py took the label as the repository owner's. The reviewer
    app's approve of such a file keeps that rule."""
    start = yaml.safe_load(REVIEW.read_text())["jobs"]["start"]
    assert holds(start["outputs"]["owner"], "steps.owner.outputs.ok")
    name = REPORT_MERGE
    early = early_disable()
    for step in (the_step("report", name), early):
        assert holds(step["env"]["OWNER_APPROVED"], "needs.start.outputs.owner"), step.get("name")
    view = [("--json state", "OPEN\n", 0), ("--json autoMergeRequest", "true\n", 0), ("--json headRefName", "abc-495-x\n", 0),
            ("--json title", "A title\n", 0), ("--json headRefOid", "a" * 40 + "\n", 0)]
    owned = ("pulls/7/files", json.dumps({"filename": ".github/workflows/x.yml", "status": "modified", "patch": "+a"}) + "\n", 0)
    labeled = {"OWNER_APPROVED": "true", "HEAD": "a" * 40}
    run, calls = run_step(tmp_path / "label", "report", name, [owned, *view], more=labeled)
    assert run.returncode == 0 and MERGE in calls, run.stdout + run.stderr
    assert ADD_LABEL not in calls and not any("--disable-auto" in c for c in calls), "no Escalated: GitHub merges it"
    assert "owner-approved" in run.stdout and ".github/workflows/x.yml" in run.stdout
    run, calls = run_step(tmp_path / "early", "report", early, [owned, *view], more=labeled)
    assert run.returncode == 0 and not any("--disable-auto" in c for c in calls), "the step before the check leaves it on"
    # A rerun of the label's run after a later commit lifts nothing: that commit's files wait for the owner
    later = {"OWNER_APPROVED": "true", "HEAD": "b" * 40}
    run, calls = run_step(tmp_path / "later", "report", name, [owned, *view], more=later)
    assert run.returncode == 0 and MERGE not in calls and ADD_LABEL in calls and "lifts nothing" in run.stdout
    assert "pr merge 7 -R owner/repo --disable-auto" in calls
    run, calls = run_step(tmp_path / "later-early", "report", early, [owned, *view], more=later)
    assert "pr merge 7 -R owner/repo --disable-auto" in calls, "off before the check too"
    failed = [("--json headRefOid", "", 1), owned, *view]
    run, calls = run_step(tmp_path / "unread", "report", name, failed, more=labeled)
    assert run.returncode != 0 and MERGE not in calls, "a failed read of the head stops the step before any merge"
    run, calls = run_step(tmp_path / "unread-early", "report", early, failed, more=labeled)
    assert "pr merge 7 -R owner/repo --disable-auto" in calls, "and lifts nothing before the check"
    for folder, value in (("reviewer", ""), ("refused", "false")):
        run, calls = run_step(tmp_path / folder, "report", name, [owned, *view], more={"OWNER_APPROVED": value})
        assert run.returncode == 0 and MERGE not in calls and ADD_LABEL in calls, folder
        assert "pr merge 7 -R owner/repo --disable-auto" in calls, f"{folder}: an earlier auto-merge goes off"
        run, calls = run_step(tmp_path / f"{folder}-early", "report", early, [owned, *view], more={"OWNER_APPROVED": value})
        assert "pr merge 7 -R owner/repo --disable-auto" in calls, f"{folder}: off before the check too"


def test_the_implementer_apps_token_turns_on_auto_merge(tmp_path):
    """(measured 2026-10-28): GitHub merges as the account that turned auto-merge on, and a merge by GitHub
    Actions' token starts no workflow, so neither caches.yml nor the branch update ran after an auto-merge. The implementer
    app's token turns it on; without it the step warns and GitHub Actions' token still turns it on."""
    report = yaml.safe_load(REVIEW.read_text())["jobs"]["report"]
    order = [report["steps"].index(the_step("report", does)) for does in ("check", "merger", REPORT_MERGE)]
    assert order == sorted(order)
    merger = the_step("report", "merger")
    assert holds(merger["with"]["client-id"], "vars.IMPLEMENTER_APP_CLIENT_ID") and merger["continue-on-error"] is True
    assert holds(merger["if"], "steps.check.outputs.conclusion == 'success' && vars.IMPLEMENTER_APP_CLIENT_ID != ''")
    assert merger["with"]["permission-checks"] == "read", "the implementer app writes no check"
    name = REPORT_MERGE
    assert holds(the_step("report", name)["env"]["MERGER"], "steps.merger.outputs.token")
    code = ("pulls/7/files", json.dumps({"filename": "app/x.py", "status": "modified", "patch": "+a"}) + "\n", 0)
    for folder, more, token in (("app", {"MERGER": "app"}, "app"), ("none", {}, "token")):
        run, _ = run_step(tmp_path / folder, "report", name, [code], more=more)
        tokens = (tmp_path / folder / "fake" / "tokens").read_text().splitlines()
        assert run.returncode == 0 and f"{token} pr merge 7 -R owner/repo --auto --merge" in tokens, folder
        assert all(line.startswith("token ") for line in tokens if "--auto --merge" not in line), "only the merge takes the app's token"
        assert ("::warning::" in run.stdout) == (folder == "none")


MODIFIED = "GraphQL: Base branch was modified. Review and try the merge again. (mergePullRequest)\n"
MERGE = "pr merge 7 -R owner/repo --auto --merge"


def test_auto_merge_tries_again_when_the_base_branch_was_modified(tmp_path):
    """A report failed at the auto-merge, since another pull request merged in the same seconds, and
    branch-update queues only a pull request with auto-merge on, so the approved pull request waited with no sign. The step
    tries again on that answer (and on a 5xx, the next test); when every try fails, the label escalated and
    the issue to Escalated with a comment. Any other refusal fails at once."""
    name = REPORT_MERGE
    step = the_step("report", name)
    assert int(step["env"]["TRIES"]) > 1 and int(step["env"]["WAIT"]) > 0
    code = ("pulls/7/files", json.dumps({"filename": "app/x.py", "status": "modified", "patch": "+a"}) + "\n", 0)
    view = [("--json headRefName", "abc-283-x\n", 0), ("--json title", "A title\n", 0)]
    fast = {"TRIES": "3", "WAIT": "0", "LINEAR_API_KEY": "key"}
    files = {".github/linear.py": FAKE_LINEAR}
    # refused twice, then turned on: no label, no Linear call
    run, calls = run_step(tmp_path / "later", "report", name, [code, (MERGE, MODIFIED, 1, 2), *view], files, fast)
    assert run.returncode == 0, run.stdout + run.stderr
    assert calls.count(MERGE) == 3 and ADD_LABEL not in calls and not any(c.startswith("linear ") for c in calls)
    # refused on every try: the label, a comment and the move to Escalated, and the step fails
    run, calls = run_step(tmp_path / "never", "report", name, [code, (MERGE, MODIFIED, 1), *view], files, fast)
    assert run.returncode == 1 and "::error::" in run.stdout
    assert calls.count(MERGE) == 3 and calls.index(ADD_LABEL) > max(i for i, c in enumerate(calls) if c == MERGE)
    linear = [c for c in calls if c.startswith("linear ")]
    assert linear == ["linear key-from abc-283-x A title", "linear comment ABC-9 --body-file retry.md", "linear state ABC-9 Escalated"]
    assert "Base branch was modified" in (tmp_path / "never" / "work" / "retry.md").read_text()
    # with no Linear key the label is the sign
    run, calls = run_step(tmp_path / "nokey", "report", name, [code, (MERGE, MODIFIED, 1), *view], files,
                          {"TRIES": "2", "WAIT": "0"})
    assert run.returncode == 1 and ADD_LABEL in calls and "::notice::" in run.stdout
    assert [c for c in calls if c.startswith("linear ")] == ["linear key-from abc-283-x A title"]
    # any other refusal fails at once, as before: no second try, no label
    run, calls = run_step(tmp_path / "other", "report", name, [code, (MERGE, "GraphQL: Pull request is in clean status\n", 1), *view],
                          files, fast)
    assert run.returncode == 1 and calls.count(MERGE) == 1 and ADD_LABEL not in calls
    assert not any(c.startswith("linear ") for c in calls)


UNAVAILABLE = ('non-200 OK status code: 503 Service Unavailable body: "{\\"message\\": \\"No server is currently available to '
               'service your request.\\"}"\n')
AUTO_ON = "--json state,autoMergeRequest"


def test_auto_merge_tries_again_on_a_5xx_and_ends_when_github_did_the_work(tmp_path):
    """A report failed at the auto-merge on a 503, although GitHub turned auto-merge
    on and merged 10 s later. A 5xx answer is tried again like "Base branch was modified"; before the next try the step
    reads the pull request, and a merge or an auto-merge that is on ends it with success."""
    name = REPORT_MERGE
    code = ("pulls/7/files", json.dumps({"filename": "app/x.py", "status": "modified", "patch": "+a"}) + "\n", 0)
    view = [("--json headRefName", "abc-392-x\n", 0), ("--json title", "A title\n", 0)]
    fast = {"TRIES": "3", "WAIT": "0", "LINEAR_API_KEY": "key"}
    files = {".github/linear.py": FAKE_LINEAR}
    # the 503 came after GitHub turned auto-merge on: one try, success, no label
    run, calls = run_step(tmp_path / "on", "report", name, [code, (MERGE, UNAVAILABLE, 1), (AUTO_ON, "yes\n", 0), *view],
                          files, fast)
    assert run.returncode == 0 and "::notice::" in run.stdout, run.stdout + run.stderr
    assert calls.count(MERGE) == 1 and ADD_LABEL not in calls
    # a 503 and a 502 with nothing done, then turned on
    bad_gateway = "HTTP 502: Bad Gateway (https://api.github.com/graphql)\n"
    run, calls = run_step(tmp_path / "later", "report", name,
                          [code, (MERGE, UNAVAILABLE, 1, 1), (MERGE, bad_gateway, 1, 1), (AUTO_ON, "no\n", 0), *view], files, fast)
    assert run.returncode == 0 and calls.count(MERGE) == 3, run.stdout + run.stderr
    assert sum(AUTO_ON in c for c in calls) == 2 and ADD_LABEL not in calls
    # 503 on every try: the label, a comment with GitHub's own words and the move to Escalated, and the step fails
    run, calls = run_step(tmp_path / "never", "report", name, [code, (MERGE, UNAVAILABLE, 1), (AUTO_ON, "no\n", 0), *view],
                          files, fast)
    assert run.returncode == 1 and "::error::" in run.stdout and "503 Service Unavailable" in run.stdout
    assert calls.count(MERGE) == 3 and ADD_LABEL in calls
    assert [c for c in calls if c.startswith("linear ")][1:] == ["linear comment ABC-9 --body-file retry.md",
                                                                 "linear state ABC-9 Escalated"]
    assert "503 Service Unavailable" in (tmp_path / "never" / "work" / "retry.md").read_text()
    # a view that fails is no proof of a merge: the step tries again
    run, calls = run_step(tmp_path / "unread", "report", name, [code, (MERGE, UNAVAILABLE, 1, 1), (AUTO_ON, "", 1), *view],
                          files, fast)
    assert run.returncode == 0 and calls.count(MERGE) == 2




FAKE_LINEAR = """import os, sys
with open(os.path.join(os.environ["FAKE"], "log"), "a") as log:
    log.write("linear " + " ".join(sys.argv[1:]) + "\\n")
if sys.argv[1] == "key-from":
    print("ABC-9")
"""


def test_the_check_verdict_holds_only_the_review(tmp_path):
    """OWL-30: the check `verdict` is the review's verdict and nothing else. Each gate is a required check of the ruleset on
    its own, and the workflow gates-failed.yml sends an approved head with a red gate back (tests/test_gates_failed.py). So
    the step `check` reads no gates result: an approve is a success, whatever `GATES` or `CODE` say (an old environment
    that still sets them changes nothing), and the job `report` has no step that sends a head back."""
    step = the_step("report", "check")
    assert set(step["env"]) == {"GH_TOKEN", "MODEL"}, "the review's outcome only: no gates result, no code flag"
    assert set(yaml.safe_load(REVIEW.read_text())["jobs"]["review"]["outputs"]) == {"model"}
    report = yaml.safe_load(REVIEW.read_text())["jobs"]["report"]
    assert not any("sent-back" in s.get("run", "") or s.get("id") == "updater" for s in report["steps"]), \
        "gates-failed.yml owns the comment and the move back to In Progress"
    head = "a" * 40
    for name, word, state, want in (("approve", "approve", "APPROVED", "success"),
                                    ("changes", "request-changes", "CHANGES_REQUESTED", "failure")):
        output = tmp_path / f"{name}.out"
        output.write_text("")
        rules = [("pulls/7/reviews", reviews_line(state, word, "2026-10-28T11:00:00Z"), 0), ("headRefOid", head, 0)]
        run, calls = run_step(tmp_path / name, "report", "check", rules,
                              more={"HEAD": head, "CHECK": "99", "GATES": "failed", "CODE": "true", "GITHUB_OUTPUT": str(output)})
        assert run.returncode == 0, run.stderr
        assert output.read_text().splitlines() == [f"conclusion={want}"], name
        assert any("check-runs/99" in call for call in calls) and "The gates" not in run.stdout, name


def test_a_review_with_no_verdict_says_whether_it_ended_failed_or_did_not_run(tmp_path):
    """The check `verdict` of a review that ended and posted no review names that case, from the outcome
    of the review job's model step; a failed run and a round past the third get their own text."""
    step = "check"
    assert holds(the_step("report", step)["env"]["MODEL"], "needs.review.outputs.model")
    assert holds(yaml.safe_load(REVIEW.read_text())["jobs"]["review"]["outputs"]["model"], "steps.review.outcome")
    head = "a" * 40
    for outcome, words in (("success", "The review ended and posted no review."), ("failure", "The review run failed"),
                           ("skipped", "a step before it failed, or the round is past the third")):
        output = tmp_path / f"{outcome}.out"
        output.write_text("")
        run, _ = run_step(tmp_path / outcome, "report", step, [("pulls/7/reviews", "", 0), ("headRefOid", head, 0)],
                          more={"HEAD": head, "CHECK": "99", "MODEL": outcome,
                                "GITHUB_OUTPUT": str(output)})
        assert run.returncode == 0, run.stderr
        assert words in run.stdout and "conclusion=failure" in output.read_text(), outcome


# The owner's Approved on GitHub is the label `owner-approved` (the owner's call, 2026-10-30). The start job posts an
# approve for the owner on the labeled head, the review job only reads the gates, and the report job writes the check and
# moves the issue as for the reviewer's approve.
OWNER_STEP = "owner"  # the step's id: the owner's Approved, from the owner only
OPEN_STEP = "open"  # the head to review, and the check verdict in progress on it
LABELED = "a" * 40


def owner_env(tmp_path: Path, **change: str) -> dict[str, str]:
    output = tmp_path / "output"
    tmp_path.mkdir(parents=True, exist_ok=True)
    output.write_text("")
    return {"LABEL": "owner-approved", "SENDER": "maurolopes", "ACTOR": "maurolopes", "OWNER": "maurolopes", "DRAFT": "false",
            "LABELED_HEAD": LABELED, "GITHUB_OUTPUT": str(output), **change}


def test_the_owners_label_counts_only_from_the_owner(tmp_path):
    """Only the repository owner's own label posts an approve: the account that added it and the account that started the
    run. Anyone else's label, an app's included, gets a notice in the log and no approve; an older main's verdict.py
    gets a warning."""
    step = the_step("start", OWNER_STEP)
    assert holds(step["env"]["SENDER"], "github.event.sender.login") and holds(step["env"]["ACTOR"], "github.triggering_actor")
    assert holds(step["env"]["OWNER"], "github.repository_owner") and holds(step["env"]["LABELED_HEAD"], "github.event.pull_request.head.sha")
    cases = {"owner": ({}, "true"), "another": ({"SENDER": "someone"}, "false"), "app": ({"SENDER": "maurolopes-implementer[bot]"}, "false"),
             "rerun": ({"ACTOR": "someone"}, "false"), "draft": ({"DRAFT": "true"}, "false")}
    for name, (change, ok) in cases.items():
        env = owner_env(tmp_path / name / "out", **change)
        run, calls = run_step(tmp_path / name, "start", OWNER_STEP, [], more=env)
        assert run.returncode == 0 and calls == [], (name, run.stdout + run.stderr)
        assert f"ok={ok}" in Path(env["GITHUB_OUTPUT"]).read_text(), name
        assert ("::notice::the label owner-approved is ignored" in run.stdout) == (ok == "false"), (name, run.stdout)
        review = (tmp_path / name / "work" / "owner-review.json").read_text()
        assert bool(review) == (ok == "true"), name
    approve = json.loads((tmp_path / "owner" / "work" / "owner-review.json").read_text())
    assert approve["event"] == "APPROVE" and approve["commit_id"] == LABELED
    env = owner_env(tmp_path / "old" / "out")
    run, _ = run_step(tmp_path / "old", "start", OWNER_STEP, [], {".github/verdict.py": "import sys\nsys.exit(2)\n"}, env)
    assert run.returncode == 0 and "::warning::" in run.stdout and "ok=false" in Path(env["GITHUB_OUTPUT"]).read_text()


def test_the_owners_approve_is_posted_on_the_labeled_head_before_the_check(tmp_path):
    """The approve covers the head the owner labeled, never a later push, and the report reads the verdicts from GitHub's
    time of that approve, never the runner's clock. Without the label the step reviews the current head as before."""
    start = yaml.safe_load(REVIEW.read_text())["jobs"]["start"]
    step = the_step("start", OPEN_STEP)
    assert "steps.owner.outputs.ok == 'true'" in step["if"] and "github.event.action != 'labeled'" in step["if"]
    token = next(s for s in start["steps"] if s.get("id") == "token")
    assert holds(token["with"]["permission-pull-requests"], "github.event.action == 'labeled' && 'write' || 'read'")
    names = [s.get("id") or s.get("name") for s in start["steps"]]
    assert names.index("token") < names.index("owner") < names.index("open")
    pr = json.dumps({"headRefOid": "b" * 40, "headRefName": "abc-303-owner-approved", "title": "ABC-303: the owner's Approved"})
    rules = [("pulls/7/reviews", "2026-10-30T11:00:00Z\n", 0), ("check_name=verdict", "[]", 0), ("check-runs", "99\n", 0), ("headRefOid", pr, 0)]
    env = owner_env(tmp_path / "owner" / "out")
    run, calls = run_step(tmp_path / "owner", "start", OPEN_STEP, rules, {"owner-review.json": "{}"}, env)
    assert run.returncode == 0, run.stdout + run.stderr
    post = "api repos/owner/repo/pulls/7/reviews --input owner-review.json --jq .submitted_at"
    check = next(c for c in calls if "check-runs" in c)
    assert calls.index(post) < calls.index(check) and f"head_sha={LABELED}" in check and not any("headRefOid" in c for c in calls)
    assert f"head={LABELED}\nstarted=2026-10-30T11:00:00Z\ncheck=99" in Path(env["GITHUB_OUTPUT"]).read_text()
    env = owner_env(tmp_path / "ready" / "out", LABELED_HEAD="")
    run, calls = run_step(tmp_path / "ready", "start", OPEN_STEP, rules, more=env)
    assert run.returncode == 0 and not any("pulls/7/reviews" in c for c in calls)
    assert f"head_sha={'b' * 40}" in next(c for c in calls if "check-runs" in c and "check_name" not in c)
    env = owner_env(tmp_path / "refused" / "out")
    run, calls = run_step(tmp_path / "refused", "start", OPEN_STEP, [("pulls/7/reviews", "", 1), *rules], {"owner-review.json": "{}"}, env)
    assert run.returncode != 0 and not any("check-runs" in c for c in calls), "no approve, no check"


def test_the_owners_approve_runs_no_model():
    """The owner approved: the review job runs no model and counts no round, and it reads no gates (OWL-30): `report` writes the
    check from the owner's approve, and the gates are required checks of their own."""
    assert holds(the_step("review", "review")["if"], "github.event.action != 'labeled' && steps.facts.outputs.round <= 3")
    assert holds(the_step("review", "gates")["if"], "github.event.action == 'labeled' || steps.facts.outputs.round <= 3")
    flow = yaml.safe_load(REVIEW.read_text())
    group = flow["concurrency"]["group"]
    assert holds(group, "github.event.label.name != 'owner-approved') && format('label-{0}', github.run_id)"), \
        "another label's run skips every job and must not cancel a review that waits in the reviews' group"
    assert "github.event.action == 'synchronize'" in flow["jobs"]["carry"]["if"] and "labeled" not in flow["jobs"]["carry"]["if"]


def test_a_pure_bump_needs_no_issue_and_a_bump_with_a_product_file_gets_a_failed_check(tmp_path):
    """OWL-56: a branch `loop-bump-SHORT` whose diff is the pin and files that the loop's list names at the head gets its review
    with no issue key. The same branch with a product file gets the failed check `verdict` titled "A bump that changes a product
    file needs its issue", and no check id. Another branch with no key keeps the failed check "No issue" (the test above)."""
    branch = "loop-bump-0123456789ab"
    loop_sync = (REPO / ".workflow" / "loop_sync.py").read_text()

    def start(case: str, name: str, changed: str, listing: str = "agents/\n.workflow/\n!.workflow/own.sh\n") -> tuple[subprocess.CompletedProcess[str], list[str], str]:
        out = tmp_path / f"{case}.out"
        pr = json.dumps({"headRefOid": "abc123", "headRefName": name, "title": "Bump the loop to 0123456789ab"})
        rules = [("pr view 7", pr, 0), ("pulls/7/files", changed, 0), ("contents/.workflow/loop-files.txt?ref=abc123", listing, 0),
                 ("check_name=verdict", "[]", 0), ("check-runs", "99", 0)]
        run, calls = run_step(tmp_path / case, "start", OPEN_STEP, rules, {".workflow/loop_sync.py": loop_sync},
                              {"GITHUB_OUTPUT": str(out), "LABELED_HEAD": ""})
        return run, calls, out.read_text() if out.exists() else ""

    run, calls, out = start("pure", branch, ".workflow/loop.toml\nagents/a.md\n.workflow/start.py\n")
    assert run.returncode == 0, run.stderr
    assert "check=99" in out and "::notice::" in run.stdout and not any("conclusion=failure" in c for c in calls)
    assert any("contents/.workflow/loop-files.txt?ref=abc123" in c for c in calls), "the list is the head's"
    run, calls, out = start("product", branch, ".workflow/loop.toml\nagents/a.md\nsrc/app.py\n.workflow/own.sh\n")
    assert run.returncode == 0, run.stderr
    assert "head=abc123" in out and "check=" not in out
    [written] = [c for c in calls if "check-runs" in c and "check_name" not in c]
    assert "conclusion=failure" in written and "output[title]=A bump that changes a product file needs its issue" in written
    assert "src/app.py" in written and "::error::" in run.stdout
    run_step_files = {".workflow/loop_sync.py": loop_sync, ".workflow/loop-files.txt": "agents/\ngone/\n"}
    out_gone = tmp_path / "gone.out"
    rules = [("pr view 7", json.dumps({"headRefOid": "abc123", "headRefName": branch, "title": "t"}), 0),
             ("pulls/7/files", ".workflow/loop.toml\ngone/x.md\n", 0), ("contents/.workflow/loop-files.txt", "agents/\n", 0),
             ("check_name=verdict", "[]", 0), ("check-runs", "99", 0)]
    run, calls = run_step(tmp_path / "gone", "start", OPEN_STEP, rules, run_step_files, {"GITHUB_OUTPUT": str(out_gone), "LABELED_HEAD": ""})
    assert run.returncode == 0 and "check=99" in out_gone.read_text(), "a file that only main's list names is a removal: still pure"
    out_file = tmp_path / "unreadable.out"
    rules = [("pr view 7", json.dumps({"headRefOid": "abc123", "headRefName": branch, "title": "t"}), 0), ("pulls/7/files", "", 1),
             ("check-runs", "99", 0)]
    run, calls = run_step(tmp_path / "unreadable", "start", OPEN_STEP, rules, {".workflow/loop_sync.py": loop_sync},
                          {"GITHUB_OUTPUT": str(out_file), "LABELED_HEAD": ""})
    assert run.returncode == 0 and "check=" not in out_file.read_text(), "a read that fails is no bump: the check fails"
    assert any("A bump that changes a product file needs its issue" in c for c in calls)
    _, calls, out = start("other", "loop-bump-notahash", ".workflow/loop.toml\n")
    assert "check=" not in out and any("No issue: put its key in the title" in c for c in calls), "only the exact branch name is a bump"


OWNER_LINEAR = """import sys
open("linear.log", "a").write(" ".join(sys.argv[1:]) + "\\n")
if sys.argv[1] == "key-from":
    print("ABC-303")
"""


def test_the_owners_approve_moves_the_issue_to_approved(tmp_path):
    """The reviewer moves the issue to Approved after its own approve; after the owner's label the report does, before the
    auto-merge step, which moves it on to Escalated for a code owner's file. Without a key or an issue it moves nothing,
    and a Linear failure only warns."""
    name = OWNER_MOVES
    step = the_step("report", name)
    assert holds(step["if"], "github.event.action == 'labeled' && steps.check.outputs.conclusion == 'success'")
    assert step["continue-on-error"] is True
    steps = yaml.safe_load(REVIEW.read_text())["jobs"]["report"]["steps"]
    order = [steps.index(the_step("report", does)) for does in ("check", name, REPORT_MERGE)]
    assert order == sorted(order)
    view = [("--json headRefName", "abc-303-x\n", 0), ("--json title", "A title\n", 0)]
    files = {".github/linear.py": OWNER_LINEAR}
    run, _ = run_step(tmp_path / "moved", "report", name, view, files, {"LINEAR_API_KEY": "k", "HEAD": LABELED})
    log = (tmp_path / "moved" / "work" / "linear.log").read_text().splitlines()
    assert run.returncode == 0 and log[1].startswith("comment ABC-303") and log[2] == "state ABC-303 Approved", log
    assert "aaaaaaa" in (tmp_path / "moved" / "work" / "approved.md").read_text()
    run, _ = run_step(tmp_path / "nokey", "report", name, view, files, {"HEAD": LABELED})
    assert run.returncode == 0 and "::notice::" in run.stdout
    assert (tmp_path / "nokey" / "work" / "linear.log").read_text().splitlines() == ["key-from abc-303-x A title"]
    failing = {".github/linear.py": OWNER_LINEAR + "if sys.argv[1] == 'state':\n    sys.exit(1)\n"}
    run, _ = run_step(tmp_path / "failed", "report", name, view, failing, {"LINEAR_API_KEY": "k", "HEAD": LABELED})
    assert run.returncode == 0 and "::warning::" in run.stdout


def test_the_close_of_a_pull_request_cancels_its_review_and_starts_no_job():
    """A review that ended after the merge moved a Done issue back to In Progress. The close (a merge
    too) runs in the reviews' group with cancel-in-progress, so it cancels the review that runs or waits there, and no job
    of the workflow runs on it."""
    flow = yaml.safe_load(REVIEW.read_text())
    group, cancel = flow["concurrency"]["group"], flow["concurrency"]["cancel-in-progress"]
    assert "github.event.action == 'closed'" in cancel and "'closed'" not in group, "the close falls in the reviews' group"
    for name, job in flow["jobs"].items():
        assert "closed" not in job.get("if", ""), f"{name} must not run on a close"
    assert holds(flow["jobs"]["start"]["if"], "github.event.action == 'opened' || github.event.action == 'ready_for_review'")
    assert "action == 'synchronize'" in flow["jobs"]["carry"]["if"]


def moves_of_issues(flow: Path) -> list[tuple[str, dict]]:
    """Every step of a workflow whose script moves a Linear issue, as (job, step)."""
    jobs = yaml.safe_load(flow.read_text())["jobs"]
    return [(job, s) for job, spec in jobs.items() for s in spec.get("steps", []) if "linear.py state" in s.get("run", "")]


def test_every_move_from_a_review_reads_the_pull_requests_state_first():
    """Each step that moves an issue from a review asks verdict.py `ended` before the move, so a merged or closed
    pull request keeps its issue as it is (Linear's Done, or the owner's close). The behaviour of each move of the job
    `report` has its own test with a merged or closed pull request."""
    found = 0
    for flow in (REVIEW,):  # the rerun's moves are in verdict_rerun.py: its tests read the state first
        for job, step in moves_of_issues(flow):
            run, name = step["run"], (job, step.get("name"))
            assert "verdict.py ended" in run, (flow.name, name)
            prev = 0
            for move in re.finditer(r"linear\.py state", run):  # each move has its own check, after the move before it
                assert run.find("verdict.py ended", prev, move.start()) != -1, (flow.name, name, "each move reads the state first")
                prev = move.end()
            found += 1
    assert found == 0, "no workflow step moves an issue itself: the moves are in the scripts, checked below and in verdict_rerun.py's tests"
    # the job `report` moves its issues in main's review_report.py: each move there reads the state first too
    import ast
    tree = ast.parse((REPO / ".github" / "review_report.py").read_text())
    movers = []
    steps_tree = ast.parse((REPO / ".github" / "review_steps.py").read_text())
    third = next(node for node in steps_tree.body if isinstance(node, ast.FunctionDef) and node.name == "past_third")
    ended_at = [c.lineno for c in ast.walk(third) if isinstance(c, ast.Call) and "ended" in [a.value for a in c.args if isinstance(a, ast.Constant)]]
    moves_at = [c.lineno for c in ast.walk(third) if isinstance(c, ast.Call)
                and [a.value for a in c.args[:2] if isinstance(a, ast.Constant)] == ["linear.py", "state"]]
    assert ended_at and moves_at and min(ended_at) < min(moves_at), "the third round's move reads the state first (review_steps.py)"
    for function in (node for node in tree.body if isinstance(node, ast.FunctionDef)):
        calls = [c for c in ast.walk(function) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)]
        moves = [c.lineno for c in calls if c.func.id == "linear_move"]
        if moves:
            reads = [c.lineno for c in calls if c.func.id == "ended"]
            assert reads and min(reads) < min(moves), (function.name, "each move reads the state first")
            movers.append(function.name)
    assert movers == ["owner_approved", "auto_merge", "owners_merge"], \
        "the owner's Approved, auto-merge refused, the owner's files (the failed gates are gates_failed.py's: it reads the state first)"
    def state_moves(node: ast.AST) -> int:
        return sum(1 for c in ast.walk(node) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "script"
                   and [a.value for a in c.args[:2] if isinstance(a, ast.Constant)] == ["linear.py", "state"])
    mover = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "linear_move")
    assert state_moves(tree) == state_moves(mover) == 2, "linear.py state runs in linear_move only (with a comment, and without)"


def test_the_owners_approve_moves_no_issue_of_a_merged_pull_request(tmp_path):
    """The owner's label on a pull request that merged since moves its issue nowhere."""
    name = OWNER_MOVES
    view = [("--json state --jq .state", "MERGED", 0), ("--json headRefName", "abc-303-x\n", 0), ("--json title", "A title\n", 0)]
    run, _ = run_step(tmp_path, "report", name, view, {".github/linear.py": OWNER_LINEAR}, {"LINEAR_API_KEY": "k", "HEAD": LABELED})
    log = (tmp_path / "work" / "linear.log").read_text().splitlines()
    assert run.returncode == 0 and log == ["key-from abc-303-x A title"], log
    assert "::notice::PR #7 is merged: ABC-303 stays as it is" in run.stdout


# The review's `review` and the watchdog's `watch` read a repository variable: a repository that wants Namespace sets
# it to its profile, and one with no variable runs on GitHub's runner (OWL-20).
REVIEW_RUNNER = "${{ vars.LOOP_REVIEW_RUNNER || 'ubuntu-latest' }}"
WATCH_RUNNER = "${{ vars.LOOP_WATCH_RUNNER || 'ubuntu-latest' }}"
GITHUB_RUNNER = "ubuntu-latest"
# A short job (seconds of gh, git and the standard-library scripts) runs on GitHub's runner, which starts in
# seconds; on Namespace it waited in the queue behind the gates. The long jobs keep Namespace's caches and fast CPUs.
# The loop's workflows; a repository's own workflows (its gates, its caches) are in its own tests.
RUNNERS = {
    "apply-workflow-patch.yml": {"apply": GITHUB_RUNNER},
    "attribution.yml": {"attribution": GITHUB_RUNNER},
    "branch-update.yml": {"update": GITHUB_RUNNER},
    "claude-code-review.yml": {"start": GITHUB_RUNNER, "review": REVIEW_RUNNER, "report": GITHUB_RUNNER,
                               "carry": GITHUB_RUNNER},
    "review-watchdog.yml": {"watch": WATCH_RUNNER},
    "verdict-rerun.yml": {"rerun": GITHUB_RUNNER},
    "gates-failed.yml": {"failed": GITHUB_RUNNER},
    "gates-retry.yml": {"retry": GITHUB_RUNNER},
    "flakes.yml": {"file": GITHUB_RUNNER},
}


def test_every_job_runs_on_the_runner_chosen_for_it():
    """A new job, or a job moved to another runner, fails here until RUNNERS names it: the choice is made on purpose."""
    found = {path.name: {name: job.get("runs-on") for name, job in yaml.safe_load(path.read_text())["jobs"].items()}
             for path in workflow_paths()}  # a repository's own workflows (its gates, its caches): its own tests
    assert found == RUNNERS


def test_a_job_on_githubs_runner_is_short():
    """GitHub bills each job as at least 1 minute: a job named there ends within 10 minutes, so a hung one costs little.
    The variable jobs (REVIEW_RUNNER, WATCH_RUNNER) default to it but are the repository's choice: `review` may run 50."""
    for path in workflow_paths():
        for name, job in yaml.safe_load(path.read_text())["jobs"].items():
            if job.get("runs-on") == GITHUB_RUNNER:
                assert job.get("timeout-minutes", 360) <= 10, f"{path.name}: {name}"


def test_the_scripts_of_the_short_jobs_parse_as_python_3_12():
    """The short jobs run main's scripts with the runner's `python3` (3.12 on ubuntu-24.04), while mypy and Ruff
    check them against 3.14 only. A syntax of 3.13 or 3.14 (PEP 758's `except A, B:`) would pass the gates and break
    the jobs after the merge. This holds the syntax only: a new library call still needs the author's care."""
    import ast
    for name in ("verdict.py", "linear.py", "branch_update.py", "gates_retry.py", "gates_failed.py", "review_steps.py", "review_report.py", "review_jobs.py", "verdict_rerun.py", "review_watchdog.py"):
        path = REPO / ".github" / name
        ast.parse(path.read_text(), str(path), feature_version=(3, 12))
    with pytest.raises(SyntaxError):  # the check can fail: 3.14 allows this, 3.12 does not
        ast.parse("try:\n    pass\nexcept A, B:\n    pass\n", feature_version=(3, 12))


def test_the_owners_run_by_hand_with_round_1_posts_the_comment_that_resets_the_count():
    """Only a run by hand with round 1 posts MANUAL's comment, with main's words and the run's id, and the round
    count passes the review workflow's runs and the owner to main's verdict.py, which checks the run the comment names."""
    post = the_step("review", MANUAL_RUN)
    assert holds(post["if"], "github.event_name == 'workflow_dispatch' && inputs.round == '1'")
    assert post["run"].strip() == f"{CALL} manual", "main's words and the run's id: test_review_steps.py"
    assert post.get("continue-on-error") is True, "a failed comment never stops the review"
    facts = the_step("review", "facts")
    # Each run a MANUAL comment names is read by its id, never one page of the list (it drops runs); the runs, the
    # owner and the pull request go to main's verdict.py: test_review_steps.py
    assert facts["run"].strip() == f"{CALL} facts" and holds(facts["env"]["OWNER"], "github.repository_owner")
    # The run's title names its pull request and round, as verdict.MANUAL_TITLE reads it
    from tests.test_verdict import verdict
    flow = yaml.safe_load(REVIEW.read_text())
    expected = verdict.MANUAL_TITLE.replace("{pr}", "{0}").replace("round 1", "round {1}")
    assert f"format('{expected}', inputs.pr, inputs.round)" in flow["run-name"]


COMMENT = "pr comment 7 -R owner/repo --body Ready to merge. It changes a workflow file, which the app cannot merge: the owner merges it on GitHub."


def _merge_case(tmp_path: Path, job: str, files_kind: str, state: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """One `run_step` of the merge step of `job` ("report" or "rerun") for one pull request: its files (`wf`: a workflow file
    is added, `moved`: one is renamed away, `code`: none) and the merge state that `gh pr view` reads."""
    def files(*names: str, moved_from: str = "") -> tuple:
        rows = [{"filename": n, "status": "modified", "patch": "+a"} for n in names]
        if moved_from:
            rows[0].update(status="renamed", previous_filename=moved_from)
        return ("pulls/7/files", "".join(json.dumps(r) + "\n" for r in rows), 0)
    listed = {"code": files("app/x.py"), "wf": files("app/x.py", ".github/workflows/x.yml"),
              "moved": files("app/y.yml", moved_from=".github/workflows/y.yml")}[files_kind]
    # a workflow file is a code owner's file: the step reaches the merge only on the owner's label
    more = {"BRANCH": "abc-579-x", "HEAD": "a" * 40, "OWNER": "maurolopes", "RUN_CREATED": "2026-11-07T00:00:00Z", "TRIES": "2", "WAIT": "0"}
    label = {"event": "labeled", "actor": {"login": "maurolopes"}, "created_at": "2026-11-07T01:00:00Z", "label": {"name": "owner-approved"}}
    if job == "report":
        before, extra, flow, kind = [("--json headRefOid", "a" * 40 + "\n", 0)], {"OWNER_APPROVED": "true", "HEAD": "a" * 40}, REVIEW, REPORT_MERGE
    else:
        before, extra, flow, kind = [("--json state", "OPEN\n", 0), ("issues/7/events", json.dumps(label) + "\n", 0)], more, RERUN, RERUN_MERGE
    return run_step(tmp_path, job, kind, [listed, *before, ("--json mergeStateStatus", state + "\n", 0)], more=extra, flow=flow)


# Each case is its own test: the 20 runs of one test took 20 s alone and passed pytest's 300 s limit on a loaded machine (OWL-37)
@pytest.mark.parametrize("job", ["report", "rerun"])
@pytest.mark.parametrize("files_kind", ["wf", "moved"])
@pytest.mark.parametrize("state", ["CLEAN", "UNSTABLE", "HAS_HOOKS"])
def test_a_pull_request_that_merges_at_once_with_a_workflow_file_gets_a_comment_and_no_merge_call(tmp_path, job, files_kind, state):
    """The implementer app has no permission `workflows`, so `gh pr merge --auto` on a pull request that merges at once
    (CLEAN, UNSTABLE or HAS_HOOKS: gh's isImmediatelyMergeable; one read UNSTABLE) fails for a workflow file. Both call sites
    comment instead, read from the files the step has read already, and the step succeeds."""
    run, calls = _merge_case(tmp_path, job, files_kind, state)
    assert run.returncode == 0 and COMMENT in calls and MERGE not in calls, (job, state, run.stdout + run.stderr)
    assert not any("--json files" in c for c in calls), "the files come from files.txt"
    if job == "rerun":
        assert REMOVE_LABEL in calls, "the step ends as after an auto-merge: the label escalated goes off"


@pytest.mark.parametrize("job", ["report", "rerun"])
@pytest.mark.parametrize("files_kind,state", [("wf", "BLOCKED"), ("wf", "UNKNOWN"), ("wf", ""), ("code", "CLEAN")])
def test_any_other_state_and_a_pull_request_without_a_workflow_file_keep_the_auto_merge_call(tmp_path, job, files_kind, state):
    """Any other merge state (BLOCKED, UNKNOWN) and any pull request without a workflow file keep the auto-merge call."""
    run, calls = _merge_case(tmp_path, job, files_kind, state)
    assert run.returncode == 0 and MERGE in calls and COMMENT not in calls, (job, files_kind, state)


def test_the_denied_tool_step_runs_after_a_failed_review_too():
    """The step prints one `denied:` line per denied tool call and exits 0 whatever the execution file holds
    (test_review_steps.py); it runs after a failed review too, and reads the model step's execution file."""
    step = the_step("review", DENIED)
    assert step["run"].strip() == f"{CALL} denied"
    assert holds(step["if"], "always() && steps.review.outcome != 'skipped'"), "it runs after a failed review too"
    assert holds(step["env"]["EXECUTION_FILE"], "steps.review.outputs.execution_file")


def test_the_review_workflow_and_verdict_py_name_one_round_limit():
    """Three review rounds at most. The workflow's limit is a number in its `if`s, and verdict.py's ROUNDS is the
    one constant the documents name: they stay equal."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("verdict_rounds", REPO / ".github" / "verdict.py")
    assert spec and spec.loader
    verdict = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verdict)
    text = REVIEW.read_text()
    limits = set(re.findall(r"steps\.facts\.outputs\.round (?:<=|>) (\d+)", text))
    assert limits == {str(verdict.ROUNDS)} == {"3"}

