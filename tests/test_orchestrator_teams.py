"""One orchestrator round over several teams: the config's `[[product]]` tables, the shared `max_runs`, each run in
its team's folder, each team's own repository, and the state file and the queue that show each team apart. No network, no `gh`."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from dataclasses import replace

REAL_REPO = Path(__file__).resolve().parent.parent


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("orchestrator_teams_main", REAL_REPO / "orchestrator" / "orchestrator.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


orch = _load()
def _neutral_example() -> str:
    """The example config's text with the neutral team ABC: the tests read the team's key from it."""
    text = (REAL_REPO / "orchestrator" / "orchestrator.example.toml").read_text()
    text = re.sub(r'^team = ".*"$', 'team = "ABC"', text, count=1, flags=re.MULTILINE)
    for old, new in (("ABC_LINEAR", "DEF_LINEAR"), ("abc-linear", "def-linear"), ("acme/abc", "acme/def"), ("~/abc", "~/def")):
        text = text.replace(old, new)
    return text.replace('# team = "ABC"', '# team = "DEF"')


EXAMPLE_TEXT = _neutral_example()


def example_file(folder: Path) -> Path:
    """The neutral example as a file of the test's own folder."""
    path = folder / "orchestrator.example.toml"
    path.write_text(EXAMPLE_TEXT)
    return path
XYZ = '\n[[product]]\nteam = "XYZ"\nrepo = "acme/product-two"\nrun_cwd = "~/product-two"\n'


@pytest.fixture(autouse=True)
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def config(tmp_path: Path, extra: str = XYZ) -> Any:
    path = tmp_path / "c.toml"
    path.write_text(EXAMPLE_TEXT + extra)
    return orch.load_config(path)


def issue(key: str, state: str = "Todo", area: str = "orchestrator") -> dict[str, Any]:
    return {"identifier": key, "state": state, "labels": [area], "size": "S", "priority": "medium", "manual_order": None,
            "blocked_by": [], "related": [], "description": ""}


def reads(issues: list[dict[str, Any]]) -> Any:
    return orch.Reads(issues=lambda: issues, prs=lambda: [])


class Host:
    def __init__(self) -> None:
        self.starts: list[tuple[str, str, str]] = []
        self.spaces: list[dict[str, Any]] = []
        self.wakes: list[tuple[str, str]] = []
        self.pause: tuple[str, str] | None = None
        self.pauses: dict[str, tuple[str, str]] = {}

    def start(self, label: str, command: str, cwd: str) -> str:
        self.starts.append((label, command, cwd))
        return f"s{len(self.starts)}"

    def alive(self, session: str) -> bool:
        return True

    def workspaces(self) -> list[dict[str, Any]]:
        return self.spaces

    def wake(self, key: str, text: str) -> bool:
        self.wakes.append((key, text))
        return True

    def paused(self) -> dict[str | None, tuple[str, str]]:
        return {**({None: self.pause} if self.pause else {}), **self.pauses}


class Writes:
    def __init__(self) -> None:
        self.done: list[tuple[str, ...]] = []

    def writes(self) -> Any:
        return orch.Writes(label=lambda n, name: self.done.append(("label", str(n), name)),
                           comment=lambda key, body: self.done.append(("comment", key, body)),
                           move=lambda key, state: self.done.append(("move", key, state)))


def teams_of(cfg: Any, host: Any, by_team: dict[str, list[dict[str, Any]]], writes: Any = None,
             clock: Callable[[], float] | None = None) -> list[Any]:
    cfgs = orch.team_configs(cfg)
    stops = orch.Stops()  # the one stop and retry state that the teams share
    out = [orch.Team(c, reads(by_team[c.team]), orch.Runner(c, host, writes, clock=clock or orch.time.time, stops=stops))
           for c in cfgs]
    for t in out:
        t.runner.elsewhere = orch.slots_elsewhere(out, t)
    return out


def test_a_config_with_the_single_tables_is_one_product(tmp_path: Path) -> None:
    cfg = config(tmp_path, "")
    assert cfg.products == () and orch.team_configs(cfg) == [cfg]


def test_each_product_table_is_a_team_with_its_own_repo_and_folder_and_no_queue(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    first, second = orch.team_configs(cfg)
    assert (first.team, first.repo, first.run_cwd) == ("ABC", "acme/product", "~/product")
    assert (second.team, second.repo, second.run_cwd) == ("XYZ", "acme/product-two", "~/product-two")
    assert second.areas == first.areas and second.max_runs == first.max_runs and second.products == ()
    queued = orch.team_configs(dataclasses.replace(cfg, queue_issue="ABC-9"))
    assert [c.queue_issue for c in queued] == ["ABC-9", ""]


@pytest.mark.parametrize("extra, words", [
    ('\n[[product]]\nteam = "XYZ"\nrepo = "a/b"\n', "missing run_cwd"),
    ('\n[[product]]\nteam = "XYZ"\nrepo = "a/b"\nrun_cwd = "."\nmodel = "x"\n', "unknown model"),
    ('\n[[product]]\nteam = "ABC"\nrepo = "a/b"\nrun_cwd = "."\n', "appears twice"),
    (XYZ + XYZ, "appears twice"),
    ('\n[[product]]\nteam = ""\nrepo = "a/b"\nrun_cwd = "."\n', "must be text and not empty"),
    ('\n[[product]]\nteam = "XYZ"\nrepo = "a/b"\nrun_cwd = "."\nareas = "docs"\n', "must be a list of names"),
])
def test_a_wrong_product_table_stops_the_start(tmp_path: Path, extra: str, words: str) -> None:
    with pytest.raises(orch.ConfigError, match=words):
        config(tmp_path, extra)


def test_a_product_may_name_its_own_areas_and_checks(tmp_path: Path) -> None:
    cfg = config(tmp_path, XYZ + 'areas = ["docs"]\nrequired_checks = ["tests"]\n')
    second = orch.team_configs(cfg)[1]
    assert second.areas == ("docs",) and second.required_checks == ("tests",)


def test_a_round_starts_each_team_s_run_in_its_own_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = dataclasses.replace(config(tmp_path), max_runs=2, dry_run=False)
    host = Host()
    teams = teams_of(cfg, host, {"ABC": [issue("ABC-1")], "XYZ": [issue("XYZ-1")]}, Writes().writes())
    assert orch.round_all(teams, dry=False) is True
    assert sorted(cwd for _, _, cwd in host.starts) == sorted(str(Path(p).expanduser()) for p in ("~/product", "~/product-two"))
    out = capsys.readouterr().out
    assert "== ABC ==" in out and "== XYZ ==" in out and "started ABC-1" in out and "started XYZ-1" in out


def test_max_runs_is_one_number_for_all_teams(tmp_path: Path) -> None:
    cfg = dataclasses.replace(config(tmp_path), max_runs=1, dry_run=False)
    host = Host()
    teams = teams_of(cfg, host, {"ABC": [issue("ABC-1")], "XYZ": [issue("XYZ-1")]}, Writes().writes())
    orch.round_all(teams, dry=False)
    assert len(host.starts) == 1 and host.starts[0][0].startswith("ABC-1")
    second = teams[1].runner.last
    assert second is not None and {"key": "XYZ-1", "reason": orch.NO_SLOT} in second["other_waits"] + [
        {"key": w["key"], "reason": orch.NO_SLOT} for q in second["areas"] for w in q["queue"]]
    # The next round: ABC-1 is In Progress now (a run holds it), so XYZ-1 still waits.
    teams[0].reads = reads([issue("ABC-1", "In Progress")])
    orch.round_all(teams, dry=False)
    assert len(host.starts) == 1


def test_the_slot_of_a_run_in_one_team_stops_a_start_in_the_other_in_a_later_round(tmp_path: Path) -> None:
    cfg = dataclasses.replace(config(tmp_path), max_runs=1, dry_run=False)
    host = Host()
    teams = teams_of(cfg, host, {"ABC": [issue("ABC-1", "In Progress")], "XYZ": [issue("XYZ-1")]}, Writes().writes())
    orch.round_all(teams, dry=False)
    assert host.starts == []


def test_a_team_that_fails_to_read_stops_no_other_team(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = dataclasses.replace(config(tmp_path), max_runs=2)
    host = Host()
    teams = teams_of(cfg, host, {"ABC": [], "XYZ": [issue("XYZ-1")]})

    def boom() -> list[dict[str, Any]]:
        raise RuntimeError("Linear did not answer")
    teams[0].reads = orch.Reads(issues=boom, prs=lambda: [])
    state = tmp_path / "s.json"
    assert orch.round_all(teams, state_file=state) is False
    out = capsys.readouterr().out
    assert "round failed (ABC)" in out and "would start XYZ-1" in out
    written = json.loads(state.read_text())
    assert written["failed"]["error"] == "RuntimeError" and written["others"][0]["team"] == "XYZ"
    assert "failed" not in written["others"][0]


def test_two_teams_share_one_retry_and_one_pause(tmp_path: Path) -> None:
    """One host, one stop, one Retry, and both teams hold their starts until the pause ends."""
    cfg = config(tmp_path)
    host = Host()
    now = [1_000_000.0]
    teams = teams_of(cfg, host, {"ABC": [issue("ABC-1")], "XYZ": [issue("XYZ-1")]}, clock=lambda: now[0])
    host.pause = ("ABC-9", "an invalid API key")
    assert orch.round_all(teams, dry=False) is True
    assert host.wakes == [] and host.starts == []  # the wait before the retry, both teams held
    assert [t.runner.last["pause"][0]["phase"] for t in teams] == ["retrying", "retrying"]
    now[0] += 180
    assert orch.round_all(teams, dry=False) is True
    assert host.wakes == [("ABC-9", orch.RETRY_TEXT)] and host.starts == []  # exactly one Retry, from the first team
    host.pause = None  # the retry cleared the stop
    now[0] += 60
    assert orch.round_all(teams, dry=False) is True
    assert [t.runner.last["pause"][0]["phase"] for t in teams] == ["retrying", "retrying"]  # the window still holds both
    host.pause = ("ABC-9", "an invalid API key")  # the retry stopped again
    now[0] += 60
    assert orch.round_all(teams, dry=False) is True
    assert [t.runner.last["pause"][0]["phase"] for t in teams] == ["paused", "paused"]
    now[0] += 3600
    assert orch.round_all(teams, dry=False) is True
    assert host.wakes == [("ABC-9", orch.RETRY_TEXT)] and host.starts == []  # no second retry, no start


def test_one_team_writes_its_plan_as_it_always_did_and_two_write_the_others_apart(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    one, two = tmp_path / "one.json", tmp_path / "two.json"
    solo = teams_of(dataclasses.replace(cfg, products=()), Host(), {"ABC": [issue("ABC-1")]})
    assert orch.round_all(solo, state_file=one) is True
    plan = json.loads(one.read_text())
    assert "others" not in plan and "team" not in plan and plan["version"] == orch.PLAN_VERSION
    both = teams_of(cfg, Host(), {"ABC": [issue("ABC-1")], "XYZ": [issue("XYZ-1")]})
    assert orch.round_all(both, state_file=two) is True
    written = json.loads(two.read_text())
    assert written["team"] == "ABC" and [o["team"] for o in written["others"]] == ["XYZ"]
    assert orch.round_time(two.read_text()) is not None


def test_the_watch_draws_each_team_apart(tmp_path: Path) -> None:
    both = teams_of(config(tmp_path), Host(), {"ABC": [issue("ABC-1")], "XYZ": [issue("XYZ-1")]})
    state = tmp_path / "s.json"
    orch.round_all(both, state_file=state)
    plan = json.loads(state.read_text())
    lines = orch.render_all(plan, orch._seconds(plan["time"]) or 0.0, 60, 40, False)
    assert "ABC" in lines and "XYZ" in lines
    text = "\n".join(lines)
    assert "XYZ-1" in text and text.index("XYZ") > text.index("ABC")
    assert orch.render_all(plan | {"others": []}, 0.0, 60, 40, False) == orch.render(plan | {"others": []}, 0.0, 60, 40, False)


def test_the_one_queue_holds_every_team(tmp_path: Path) -> None:
    both = teams_of(config(tmp_path), Host(), {"ABC": [issue("ABC-1", "In Progress")], "XYZ": [issue("XYZ-1", "In Progress")]})
    orch.round_all(both)
    lines = orch.combined_queue(both)
    heads = [line for line in lines if line.startswith(("ABC: ", "XYZ: "))]
    assert [h.split(":")[0] for h in heads] == ["ABC", "XYZ"]
    assert any("ABC-1" in line for line in lines) and any("XYZ-1" in line for line in lines)
    solo = both[:1]
    assert orch.combined_queue(solo) == orch.queue_lines(solo[0].runner.last, solo[0].cfg.in_progress)


def test_a_team_closes_only_the_workspaces_of_its_own_keys(tmp_path: Path) -> None:
    cfg = dataclasses.replace(config(tmp_path), dry_run=False)
    host = Host()
    host.spaces = [{"label": "XYZ-1 ·", "agent_status": "idle", "workspace_id": "w1"}]
    seen: list[str] = []

    def final(key: str) -> tuple[str, float | None, tuple[str, ...]]:
        seen.append(key)
        return "Done", 0.0, ()
    first = orch.Runner(orch.team_configs(cfg)[0], host, Writes().writes(), lambda: 10 ** 9)
    assert first.close_events([], False, final) == [] and seen == []
    second = orch.Runner(orch.team_configs(cfg)[1], host, Writes().writes(), lambda: 10 ** 9)
    assert [e.kind for e in second.close_events([], True, final)] == ["would_close"] and seen == ["XYZ-1"]


def test_the_writes_of_a_team_go_to_its_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake(argv: Any) -> Any:
        calls.append(list(argv))
        return type("Done", (), {"returncode": 0, "stderr": ""})()
    monkeypatch.setattr(orch, "run_quiet", fake)
    second = orch.team_configs(config(tmp_path))[1]
    writes = orch.real_writes(second)
    writes.label(7, "escalated")
    assert writes.close is not None
    writes.close(7, "bye")
    assert all(argv[argv.index("--repo") + 1] == "acme/product-two" for argv in calls) and len(calls) == 2


def test_the_reads_of_a_team_use_its_team_and_its_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[Any] = []

    class Api:
        def issues(self, team: str, states: list[str]) -> list[dict[str, Any]]:
            asked.append(("issues", team))
            return []

    monkeypatch.setattr(orch, "linear_api", lambda cfg, environ=None: Api())

    def fake(argv: Any, **_: Any) -> Any:
        asked.append(("gh", argv[argv.index("--repo") + 1]))
        return type("Done", (), {"returncode": 0, "stdout": "[]", "stderr": ""})()
    monkeypatch.setattr(orch.subprocess, "run", fake)
    second = orch.team_configs(config(tmp_path))[1]
    got = orch.real_reads(second)
    got.issues()
    got.prs()
    assert asked == [("issues", "XYZ"), ("gh", "acme/product-two")]


def test_a_product_with_its_own_app_pair_names_its_implementer_and_the_others_keep_the_first_one(tmp_path: Path) -> None:
    """A public repository has its own app pair: its table names its `conflict_author`, and a conflict comment counts by that
    app's login in its own team only. A table with no key keeps the first product's app."""
    cfg = config(tmp_path, XYZ + 'conflict_author = "acme-two-implementer"\n' + DEF)
    first, second, third = orch.team_configs(cfg)
    assert (first.conflict_author, second.conflict_author, third.conflict_author) == (
        "acme-implementer", "acme-two-implementer", "acme-implementer")
    comment = {"author": {"login": "acme-two-implementer"}, "body": first.conflict_comment + " Auto-merge is off."}
    assert orch.is_conflict_comment(comment, second) and not orch.is_conflict_comment(comment, first)
    with pytest.raises(orch.ConfigError, match="conflict_author must be text and not empty"):
        config(tmp_path, XYZ + 'conflict_author = ""\n')


APP_WARN = "{team}: the app {app} is also the app of {others}: a public repository's app must reach it only"
INSTALLED_WARN = "{team}: the app {app} is installed on {n} repositories: a public repository's app must reach it only"


def test_the_check_counts_the_repositories_of_the_service_s_app_installation(tmp_path: Path) -> None:
    """The service's gh holds the implementer app's installation token: `GET /installation/repositories` lists the
    repositories that the app reaches. A public product in that list, with other repositories in it, gets the issue's line,
    also when the config gives it its own app (the app may still be installed on more repositories)."""
    own = orch.team_configs(config(tmp_path, XYZ + 'conflict_author = "acme-two-implementer"\n'))
    vis = lambda repo: "public" if repo == "acme/product-two" else "private"  # noqa: E731 (a one-line stub)
    three = lambda: ["acme/product", "acme/product-two", "acme/third"]  # noqa: E731
    assert orch.check_apps(own, vis, three) == [
        (orch.WARN, INSTALLED_WARN.format(team="XYZ", app="acme-two-implementer", n=3))]
    alone = lambda: ["acme/product-two"]  # noqa: E731 (the app reaches the public repository only)
    assert orch.check_apps(own, vis, alone) == [
        (orch.OK, "apps: no public repository shares its implementer app (the service's implementer app is installed on 1 repository)")]
    private = lambda: ["acme/product", "acme/third"]  # noqa: E731 (the public repository is not in this installation)
    assert orch.check_apps(own, vis, private)[0][0] == orch.OK
    shared = orch.team_configs(config(tmp_path, XYZ))
    assert orch.check_apps(shared, vis, three) == [
        (orch.WARN, INSTALLED_WARN.format(team="XYZ", app="acme-implementer", n=3))], "one line for a team: the count first"

    def down() -> list[str]:
        raise RuntimeError("gh api failed")
    assert orch.check_apps(own, vis, down) == [
        (orch.WARN, "apps: cannot list the repositories of the service's app (RuntimeError)")]


APP_KEYS = 'app_client_id = "Iv23liORCHESTRA1"\napp_key_item = "acme-orch-agent.orchestrator-app-key"\n'


def test_with_the_orchestrator_s_own_app_the_count_is_that_app_s_and_gives_no_warn(tmp_path: Path) -> None:
    """OWL-70: with `[service] app_client_id`, the service's gh uses the orchestrator's own app, which is installed on every
    product's repository by design and writes no code. The count is that app's: the `ok` line names it, and a public
    product in it gets no WARN of the count. Two products that name one implementer app still get theirs."""
    def with_app(extra: str) -> list[Any]:
        path = tmp_path / "app.toml"
        path.write_text(EXAMPLE_TEXT.replace("[service]\n", "[service]\n" + APP_KEYS, 1) + extra)
        return orch.team_configs(orch.load_config(path))
    own = with_app(XYZ + 'conflict_author = "acme-two-implementer"\n')
    assert own[0].app_client_id == "Iv23liORCHESTRA1" and own[1].app_client_id == "Iv23liORCHESTRA1"
    vis = lambda repo: "public" if repo == "acme/product-two" else "private"  # noqa: E731 (a one-line stub)
    three = lambda: ["acme/product", "acme/product-two", "acme/third"]  # noqa: E731
    assert orch.check_apps(own, vis, three) == [
        (orch.OK, "apps: no public repository shares its implementer app (the orchestrator's app is installed on 3 repositories)")]
    assert orch.check_apps(with_app(XYZ), vis, three) == [
        (orch.WARN, APP_WARN.format(team="XYZ", app="acme-implementer", others="ABC"))]
    for half in ('app_client_id = "Iv23liORCHESTRA1"\n', 'app_key_item = "x"\n'):
        path = tmp_path / "half.toml"
        path.write_text(EXAMPLE_TEXT.replace("[service]\n", "[service]\n" + half, 1))
        with pytest.raises(orch.ConfigError, match="app_client_id and app_key_item go together: set both, or neither"):
            orch.load_config(path)


def test_the_check_warns_when_a_public_product_shares_its_app_with_another_product(tmp_path: Path) -> None:
    """Two products that name one implementer app, one of them public: a WARN for the public one. Its own pair clears it."""
    public = {"acme/product-two"}
    seen: list[str] = []

    def visibility(repo: str) -> str:
        seen.append(repo)
        return "public" if repo in public else "private"
    shared = orch.team_configs(config(tmp_path, XYZ + DEF))
    assert orch.check_apps(shared, visibility) == [
        (orch.WARN, APP_WARN.format(team="XYZ", app="acme-implementer", others="ABC, DEF"))]
    own = orch.team_configs(config(tmp_path, XYZ + 'conflict_author = "acme-two-implementer"\n' + DEF))
    seen.clear()
    assert orch.check_apps(own, visibility) == [
        (orch.OK, "apps: no public repository shares its implementer app (the service's implementer app is installed on 0 repositories)")]
    assert "acme/product-two" not in seen, "an app of one product only needs no read"
    seen.clear()
    assert orch.check_apps(orch.team_configs(config(tmp_path, "")), visibility)[0][0] == orch.OK and seen == []

    def down(repo: str) -> str:
        raise RuntimeError("gh api failed")
    assert orch.check_apps(orch.team_configs(config(tmp_path, XYZ)), down) == [
        (orch.WARN, "ABC: cannot read the visibility of acme/product (RuntimeError)"),
        (orch.WARN, "XYZ: cannot read the visibility of acme/product-two (RuntimeError)")]


def test_the_check_prints_the_app_line(tmp_path: Path) -> None:
    lines: list[str] = []
    ok: Callable[[], object] = lambda: None  # noqa: E731 (a one-line stub)
    probes = orch.Probes(environ={"LINEAR_API_KEY": "x"}, linear=ok, github=ok, log=lambda: "", sessions=lambda: [],
                         installed=lambda: (None, []), state=lambda: None, run_started=lambda key: None,
                         visibility=lambda repo: "public" if repo == "acme/product-two" else "private",
                         app_repos=lambda: ["acme/product", "acme/product-two"])
    orch.run_check(config(tmp_path, XYZ + 'conflict_author = "acme-two-implementer"\n'), probes, lines.append)
    assert "WARN " + INSTALLED_WARN.format(team="XYZ", app="acme-two-implementer", n=2) in lines


def test_the_check_reads_each_later_product_and_fails_on_a_missing_folder(tmp_path: Path) -> None:
    lines: list[str] = []
    ok: Callable[[], object] = lambda: None  # noqa: E731 (a one-line stub)
    probes = orch.Probes(environ={"LINEAR_API_KEY": "x"}, linear=ok, github=ok, log=lambda: "", sessions=lambda: [], installed=lambda: (None, []),
                         state=lambda: None, run_started=lambda key: None,
                         others=(("XYZ", ok, ok, str(tmp_path)), ("DEF", ok, ok, str(tmp_path / "missing"))))
    orch.run_check(config(tmp_path, ""), probes, lines.append)
    text = "\n".join(lines)
    assert "Linear XYZ" in text and "GitHub XYZ" in text and "ok   XYZ: run_cwd exists" in text
    assert "FAIL DEF: run_cwd" in text


def test_main_runs_each_team_with_the_reads_of_its_own(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "c.toml"
    path.write_text(EXAMPLE_TEXT + XYZ)
    by_team = {"ABC": reads([issue("ABC-1")]), "XYZ": reads([issue("XYZ-1")])}
    assert orch.main(["--config", str(path), "--once"], by_team, lambda s: None, host=Host()) == 0
    out = capsys.readouterr().out
    assert "would start ABC-1" in out and "would start XYZ-1" in out
    assert orch.main(["--config", str(path), "--once"], {"ABC": by_team["ABC"]}, lambda s: None, host=Host()) == 2


def test_distinct_areas_needs_areas_in_each_product(tmp_path: Path) -> None:
    on = EXAMPLE_TEXT.replace("distinct_areas = false", "distinct_areas = true")
    path = tmp_path / "c.toml"
    path.write_text(on + XYZ + "areas = []\n")
    with pytest.raises(orch.ConfigError, match=r"distinct_areas is on, but the areas of \[\[product\]\] XYZ are empty"):
        orch.load_config(path)
    path.write_text(on + XYZ)
    assert orch.team_configs(orch.load_config(path))[1].distinct_areas is True


def test_a_failed_team_keeps_the_pinned_queue_for_the_others(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    puts: list[list[str]] = []

    class Queue:
        def put(self, lines: list[str], stamp: str, now: float) -> list[str]:
            puts.append(lines)
            return []
    teams = teams_of(cfg, Host(), {"ABC": [issue("ABC-1", "In Progress")], "XYZ": [issue("XYZ-1", "In Progress")]})
    teams[0].runner.queue = Queue()  # type: ignore[assignment]
    orch.round_all(teams)
    assert len(puts) == 1

    def boom() -> list[dict[str, Any]]:
        raise RuntimeError("Linear did not answer")
    teams[1].reads = orch.Reads(issues=boom, prs=lambda: [])
    assert orch.round_all(teams) is False
    assert len(puts) == 2 and any("ABC-1" in line for line in puts[1]) and any("XYZ-1" in line for line in puts[1])


def test_a_fix_start_in_one_team_keeps_to_the_slots_that_the_other_team_holds(tmp_path: Path) -> None:
    cfg = dataclasses.replace(config(tmp_path), max_runs=1, dry_run=False, claim_wait_seconds=300)
    host, writes = Host(), Writes()
    kcfg, scfg = orch.team_configs(cfg)
    clock = [1_000_000.0]
    first = orch.Runner(kcfg, host, writes.writes(), lambda: clock[0])
    second = orch.Runner(scfg, host, writes.writes(), lambda: clock[0])
    first.last = {"building": 1}  # ABC's run holds the one slot
    second.elsewhere = lambda: int((first.last or {}).get("building") or 0)
    moment = clock[0] - 400
    stamp = orch.datetime.fromtimestamp(moment, orch.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    waiting = {"number": 7, "headRefName": "xyz-7-x", "isDraft": False, "labels": [], "comments": [], "commits": [{"committedDate": stamp}],
               "reviews": [{"state": "CHANGES_REQUESTED", "submittedAt": stamp}],
               "statusCheckRollup": [{"name": n, "status": "COMPLETED", "conclusion": "SUCCESS"} for n in ("python", "web", "verdict")]}
    found = [issue("XYZ-7", "In Progress")]
    lines = second.fix_round(found, [waiting], dry=False)
    assert host.starts == [] and not any(line.startswith("started a fix session") for line in lines)


def test_the_check_asks_the_team_that_a_key_belongs_to(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[tuple[str, list[str]]] = []

    class Api:
        def issues(self, team: str, states: list[str], keys: list[str]) -> list[dict[str, Any]]:
            asked.append((team, keys))
            return [{"identifier": keys[0], "comments": []}]

    monkeypatch.setattr(orch, "linear_api", lambda cfg, environ=None: Api())
    probes = orch.real_probes(config(tmp_path))
    probes.run_started("XYZ-1")
    probes.run_started("ABC-2")
    assert asked == [("XYZ", ["XYZ-1"]), ("ABC", ["ABC-2"])]


def test_in_a_dry_round_a_would_start_takes_a_slot_from_the_other_team(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = dataclasses.replace(config(tmp_path), max_runs=1)
    teams = teams_of(cfg, Host(), {"ABC": [issue("ABC-1")], "XYZ": [issue("XYZ-1")]})
    assert orch.round_all(teams, dry=True) is True
    out = capsys.readouterr().out
    assert "would start ABC-1" in out and "would start XYZ-1" not in out


# -- one Linear key for each team: a key reaches one workspace, and the team DEF is in a second one ----------------

DEF = '\n[[product]]\nteam = "DEF"\nrepo = "owner/def"\nrun_cwd = "~/def"\nlinear_key_env = "DEF_LINEAR_API_KEY"\n'
KEYS = {"LINEAR_API_KEY": "abc-secret", "DEF_LINEAR_API_KEY": "def-secret"}
MODULE = """
import json
def api_key(environ):
    with open(__file__ + '.seen', 'a') as seen:  # the module loads again for each call: the file is what stays
        seen.write(json.dumps(dict(environ)) + '\\n')
    return environ['LINEAR_API_KEY']
def http_post(key):
    return key
class Linear:
    def __init__(self, post):
        self.post = post
    def issues(self, team, states):
        return [{'identifier': team + '-1', 'key': self.post}]
"""


def with_module(tmp_path: Path, extra: str = XYZ + DEF) -> list[Any]:
    """The Config of each team, whose Linear module is a fake that returns the key it was given."""
    script = tmp_path / "fake_linear.py"
    script.write_text(MODULE)
    path = tmp_path / "c.toml"
    path.write_text(EXAMPLE_TEXT.replace('script = ".github/linear.py"', f'script = "{script}"') + extra)
    return orch.team_configs(orch.load_config(path))


def test_a_team_names_the_variable_of_its_key_and_the_default_is_linear_api_key(tmp_path: Path) -> None:
    assert [(c.team, c.linear_key_env) for c in orch.team_configs(config(tmp_path, XYZ + DEF))] == [
        ("ABC", "LINEAR_API_KEY"), ("XYZ", "LINEAR_API_KEY"), ("DEF", "DEF_LINEAR_API_KEY")]
    assert orch.load_config(example_file(tmp_path)).linear_key_env == "LINEAR_API_KEY"  # a config with no such key works as before
    text = EXAMPLE_TEXT
    assert text.count('team = "ABC"\n') == 1
    path = tmp_path / "first.toml"
    path.write_text(text.replace('team = "ABC"\n', 'team = "ABC"\nlinear_key_env = "ABC_LINEAR_API_KEY"\n') + XYZ)
    first, second = orch.team_configs(orch.load_config(path))
    assert (first.linear_key_env, second.linear_key_env) == ("ABC_LINEAR_API_KEY", "LINEAR_API_KEY"), \
        "a later team with no key of its own has the default, never the first team's variable"


@pytest.mark.parametrize("name", ["", "def_linear_api_key", "1KEY", "A B", "A;rm", "A" * 65])
def test_a_wrong_key_variable_stops_the_start(tmp_path: Path, name: str) -> None:
    with pytest.raises(orch.ConfigError, match=r"\[\[product\]\] DEF linear_key_env must be the name of an environment variable"):
        config(tmp_path, DEF.replace("DEF_LINEAR_API_KEY", name))
    path = tmp_path / "first.toml"
    path.write_text(EXAMPLE_TEXT.replace('team = "ABC"\n', f'team = "ABC"\nlinear_key_env = "{name}"\n'))
    with pytest.raises(orch.ConfigError, match=r"\[linear\] linear_key_env must be the name of an environment variable"):
        orch.load_config(path)


def test_the_example_gives_a_table_for_a_team_of_this_workspace_with_no_key_of_its_own_and_it_loads_when_its_lines_are_on(tmp_path: Path) -> None:
    """A team of the first team's workspace has no `linear_key_env`, so it takes LINEAR_API_KEY."""
    text = EXAMPLE_TEXT
    lines = ['team = "XYZ"', 'repo = "acme/other"', 'run_cwd = "~/other"']
    start = text.index("# [[product]]\n# team = \"XYZ\"")
    block = text[start:].splitlines()[1:4]
    assert [b.split("  #")[0].rstrip() for b in block] == [f"# {line}" for line in lines]
    path = tmp_path / "c.toml"
    path.write_text(text[:start] + "\n".join(["[[product]]", *lines]) + "\n" + text[start:].split("\n", 4)[4])
    assert [(c.team, c.repo, c.linear_key_env) for c in orch.team_configs(orch.load_config(path))[-1:]] == [
        ("XYZ", "acme/other", "LINEAR_API_KEY")]


def test_the_example_gives_a_table_for_a_team_of_another_workspace_with_its_own_key(tmp_path: Path) -> None:
    text = EXAMPLE_TEXT
    lines = ['team = "DEF"', 'repo = "acme/def"', 'run_cwd = "~/def"', 'linear_key_env = "DEF_LINEAR_API_KEY"']
    start = text.index("# [[product]]\n# team = \"DEF\"")
    block = text[start:].splitlines()[1:5]
    assert block == [f"# {line}" for line in lines]
    path = tmp_path / "c.toml"
    path.write_text(text[:start] + "\n".join(["[[product]]", *lines]) + "\n" + text[start:].split("\n", 5)[5])
    assert [(c.team, c.repo, c.linear_key_env) for c in orch.team_configs(orch.load_config(path))[-1:]] == [
        ("DEF", "acme/def", "DEF_LINEAR_API_KEY")]


def test_a_call_of_a_team_carries_that_team_s_key_only(tmp_path: Path) -> None:
    first, second, third = with_module(tmp_path)
    assert [orch.read_linear(c, KEYS)[0]["key"] for c in (first, third, second)] == ["abc-secret", "def-secret", "abc-secret"]
    # the module's key reader got one entry, under the name it reads: the other team's key was never in reach
    seen = [json.loads(line) for line in (tmp_path / "fake_linear.py.seen").read_text().splitlines()]
    assert seen == [{"LINEAR_API_KEY": "abc-secret"}, {"LINEAR_API_KEY": "def-secret"}, {"LINEAR_API_KEY": "abc-secret"}]
    # every Linear call of a team goes through `linear_api`: reads, moves, comments, the run line's read
    assert orch.linear_api(third, KEYS).post == "def-secret" and orch.linear_api(first, KEYS).post == "abc-secret"


def test_a_missing_key_names_its_team_and_variable_and_no_other_key_stands_in(tmp_path: Path) -> None:
    first, _, third = with_module(tmp_path)
    for environ in ({"LINEAR_API_KEY": "abc-secret"}, {"LINEAR_API_KEY": "abc-secret", "DEF_LINEAR_API_KEY": "  "}):
        with pytest.raises(orch.MissingKey) as missing:
            orch.linear_api(third, environ)
        assert "no DEF_LINEAR_API_KEY in the environment" in str(missing.value) and "team DEF" in str(missing.value)
        assert "abc-secret" not in str(missing.value)
    assert orch.read_linear(first, {"LINEAR_API_KEY": "abc-secret"})[0]["key"] == "abc-secret"  # the first team still reads


def test_a_missing_key_of_one_team_fails_that_team_s_round_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first, second, third = with_module(tmp_path)
    environ = {"LINEAR_API_KEY": "abc-secret"}  # DEF's key is not set
    host = Host()
    stops = orch.Stops()
    teams = [orch.Team(c, orch.Reads(issues=(lambda c=c: [issue(f"{c.team}-1")] if c is not third else orch.read_linear(c, environ)),
                                     prs=lambda: []),
                       orch.Runner(c, host, None, clock=orch.time.time, stops=stops)) for c in (first, second, third)]
    for t in teams:
        t.runner.elsewhere = orch.slots_elsewhere(teams, t)
    state = tmp_path / "s.json"
    assert orch.round_all(teams, state_file=state) is False
    out = capsys.readouterr().out
    assert "round failed (DEF): MissingKey: no DEF_LINEAR_API_KEY in the environment" in out
    assert "would start ABC-1" in out and "would start XYZ-1" in out and "abc-secret" not in out
    written = json.loads(state.read_text())
    assert written["others"][1]["failed"]["error"] == "MissingKey" and "failed" not in written and "failed" not in written["others"][0]


def test_the_check_prints_one_keys_line_for_each_team_and_never_a_value(tmp_path: Path) -> None:
    lines: list[str] = []
    ok: Callable[[], object] = lambda: None  # noqa: E731 (a one-line stub)
    cfg = config(tmp_path, XYZ + DEF)
    probes = orch.Probes(environ={"LINEAR_API_KEY": "abc-secret"}, linear=ok, github=ok, log=lambda: "", sessions=lambda: [],
                         installed=lambda: (None, []), state=lambda: None, run_started=lambda key: None)
    orch.run_check(cfg, probes, lines.append)
    keys = [line for line in lines if " keys: " in line]
    assert keys == ["ok   keys: ABC: LINEAR_API_KEY is set", "ok   keys: XYZ: LINEAR_API_KEY is set",
                    "FAIL keys: DEF: DEF_LINEAR_API_KEY is not set"], keys
    assert not any("abc-secret" in line for line in lines)
    assert orch.check_keys({"LINEAR_API_KEY": " ", "DEF_LINEAR_API_KEY": "x"}, orch.team_configs(config(tmp_path, DEF))) == [
        (orch.FAIL, "keys: ABC: LINEAR_API_KEY is not set"), (orch.OK, "keys: DEF: DEF_LINEAR_API_KEY is set")]


def test_the_probe_of_a_run_line_reads_with_the_key_of_the_issue_s_team(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    used: list[tuple[str, str]] = []

    class Api:
        def __init__(self, cfg: Any) -> None:
            self.cfg = cfg

        def issues(self, team: str, states: list[str], keys: list[str]) -> list[dict[str, Any]]:
            used.append((self.cfg.linear_key_env, team))
            return [{"identifier": keys[0], "comments": []}]

    monkeypatch.setattr(orch, "linear_api", lambda cfg, environ=None: Api(cfg))
    probes = orch.real_probes(config(tmp_path, DEF))
    probes.run_started("DEF-4")
    probes.run_started("ABC-2")
    assert used == [("DEF_LINEAR_API_KEY", "DEF"), ("LINEAR_API_KEY", "ABC")]


def test_the_queue_and_the_setup_notice_stay_in_the_first_team_s_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    used: list[str] = []
    monkeypatch.setattr(orch, "linear_api", lambda cfg, environ=None: used.append(cfg.linear_key_env) or type(
        "Api", (), {"post": lambda self, *a: {"issue": None}})())
    cfg = dataclasses.replace(config(tmp_path, DEF), queue_issue="ABC-9")
    def_cfg = orch.team_configs(cfg)[1]
    assert def_cfg.queue_issue == "" and orch.make_queue(def_cfg, dry=False) is None  # DEF has no queue of its own
    queue = orch.make_queue(cfg, dry=False)
    assert queue is not None and queue.comments is not None
    with pytest.raises(RuntimeError, match="no issue ABC-9"):
        queue.comments.find("ABC-9", "mark")
    assert used == ["LINEAR_API_KEY"]


def test_the_config_takes_each_product_s_agents_user_and_paseo_port(tmp_path: Path) -> None:
    """`owlcode admin --project TEAM` and `owlcode check` read them (OWL-62); the orchestrator only lets them through."""
    path = tmp_path / "c.toml"
    path.write_text('agent_user = "acme-abc"\npaseo_port = 6768\n' + EXAMPLE_TEXT
                    + XYZ + 'agent_user = "acme-xyz"\npaseo_port = 6769\n')
    cfg = orch.load_config(path)
    assert [c.team for c in orch.team_configs(cfg)] == ["ABC", "XYZ"]


def test_the_check_leaves_the_folder_of_another_user_to_owlcode_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OWL-77: the service's user cannot read a project user's home, so a product with another `agent_user` gets no
    `run_cwd` line here (install.py `project_lines` tests it as that user); a product of the service's own user still does."""
    monkeypatch.setattr(orch.getpass, "getuser", lambda: "acme-orch")
    extra = (XYZ + 'agent_user = "acme-xyz"\n[[product]]\nteam = "DEF"\nrepo = "acme/def"\nrun_cwd = "~/def"\n'
             'agent_user = "acme-orch"\n')
    probes = orch.real_probes(config(tmp_path, extra))
    assert [(team, cwd) for team, _, _, cwd in probes.others] == [("XYZ", ""), ("DEF", "~/def")]
    lines: list[str] = []
    ok: Callable[[], object] = lambda: None  # noqa: E731 (a one-line stub)
    orch.run_check(config(tmp_path, ""), replace(probes, linear=ok, github=ok, others=(("XYZ", ok, ok, ""),)), lines.append)
    assert not [line for line in lines if "XYZ: run_cwd" in line]
