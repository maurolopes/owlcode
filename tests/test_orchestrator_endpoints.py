"""Each product's session endpoint: `paseo_host`, `paseo_password_item` and `cpus`. Every `paseo` call for a product goes to
its daemon with `--host`, with the password only in that call's environment and no key of the service; the worker share
uses the endpoint's CPU count; `--check` asks each endpoint. No network, no keychain, no `paseo`: the runs and the password
reads are fakes."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

REAL_REPO = Path(__file__).resolve().parent.parent


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("orchestrator_endpoints_main", REAL_REPO / "orchestrator" / "orchestrator.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


orch = _load()
EXAMPLE_TEXT = re.sub(r'^team = ".*"$', 'team = "ABC"', (REAL_REPO / "orchestrator" / "orchestrator.example.toml").read_text(),
                      count=1, flags=re.MULTILINE)
PASSWORD = "fake-daemon-password"  # a fake: no real password in a test
HOST_A, HOST_B = "127.0.0.1:6768", "10.0.0.2:6767"
XYZ = '\n[[product]]\nteam = "XYZ"\nrepo = "acme/product-two"\nrun_cwd = "~/product-two"\n'


@pytest.fixture(autouse=True)
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def config(tmp_path: Path, top: str = "", extra: str = "", host: str = "paseo") -> Any:
    """The example with `top` lines at the top of the file (the first product's keys) and `extra` tables at its end."""
    path = tmp_path / "c.toml"
    path.write_text(top + EXAMPLE_TEXT.replace('session_host = "herdr"', f'session_host = "{host}"', 1) + extra)
    return orch.load_config(path)


AGENTS = [{"id": "agent-0001", "shortId": "agent0", "name": "ABC-7 ·", "provider": "claude", "status": "idle",
           "created": "2026-11-03T22:00:00Z", "workspaceId": "wks_1", "labels": {"profile": "claude"}},
          {"id": "agent-0002", "shortId": "agent1", "name": "ABC-9 ·", "provider": "claude", "status": "error",
           "created": "2026-11-03T23:00:00Z", "workspaceId": "wks_2", "labels": {"profile": "claude"}}]


class Daemon:
    """`run` for one Paseo daemon: it records each argv with its environment, and answers by the command."""

    def __init__(self, agents: Sequence[Mapping[str, Any]] = AGENTS, down: bool = False) -> None:
        self.calls: list[tuple[list[str], Mapping[str, str] | None]] = []
        self.agents, self.down = list(agents), down

    def __call__(self, argv: Sequence[str], env: Mapping[str, str] | None = None) -> Any:
        self.calls.append((list(argv), env))
        if self.down:
            return orch.subprocess.CompletedProcess(argv, 1, "", "The daemon rejected the password. Check PASEO_PASSWORD and retry.\n")
        args = list(argv[3:]) if argv[1:2] == ["--host"] else list(argv[1:])
        verb = "workspace-" + args[1] if args[0] == "workspace" else args[0]
        reply = {"run": "agent-0003\n", "ls": json.dumps(self.agents), "logs": "[System Error] 429 rate limit\n", "archive": "",
                 "send": "", "workspace-create": "wks_9\n", "workspace-archive": "",
                 "workspace-ls": json.dumps([{"workspaceId": "wks_1", "name": "ABC-7"}])}[verb]
        return orch.subprocess.CompletedProcess(argv, 0, reply, "")

    def verbs(self) -> list[str]:
        """Each call's command: `ls`, `run`, `workspace create`, ..."""
        args = [argv[3:] if argv[1:2] == ["--host"] else argv[1:] for argv, _ in self.calls]
        return [" ".join(a[:2]) if a[0] == "workspace" else a[0] for a in args]


class Keychain:
    """The password read: it counts the reads of each item."""

    def __init__(self) -> None:
        self.reads: list[str] = []

    def __call__(self, item: str) -> str:
        self.reads.append(item)
        return PASSWORD


def exercise(host: Any) -> None:
    """Each call kind of a Paseo host: the start (workspace create, run), the list, the wake (send), the stop read and the
    tail (logs), the close (archive, workspace ls, workspace archive)."""
    host.start("ABC-7 · #2", "paseo run --no-wait --quiet --cwd /clone --title x 'p'", "/clone")
    host.workspaces()
    assert host.wake("ABC-7", "wake up") is True
    host.paused()
    host.tail("agent-0001")
    host.close("agent-0001")
    host.retry.add("ABC-7")
    host.workspaces()  # the workspace of a failed archive: `workspace ls`
    host.close("workspace:ABC-7:wks_1")


def test_with_no_endpoint_key_every_call_goes_to_the_local_daemon_as_before(tmp_path: Path) -> None:
    cfg = config(tmp_path, extra=XYZ)
    assert [c.endpoint for c in orch.team_configs(cfg)] == [orch.LOCAL, orch.LOCAL]
    host = orch.make_host(cfg)
    assert isinstance(host, orch.PaseoHost) and host.endpoint == orch.LOCAL and orch.local_paseo(host) is host
    daemon = Daemon()
    exercise(orch.PaseoHost(daemon))
    assert all(argv[1] != "--host" and env is None for argv, env in daemon.calls)


def test_every_call_kind_passes_the_host_and_the_password_only_in_the_call_s_environment() -> None:
    daemon, keychain = Daemon(), Keychain()
    host = orch.PaseoHost(daemon, endpoint=orch.Endpoint(HOST_A, "abc-paseo-password"), password=keychain)
    exercise(host)
    assert set(daemon.verbs()) == {"workspace create", "run", "ls", "send", "logs", "archive", "workspace ls", "workspace archive"}
    for argv, env in daemon.calls:
        assert argv[:3] == ["paseo", "--host", HOST_A], argv
        assert env is not None and env["PASEO_PASSWORD"] == PASSWORD
        assert not any(PASSWORD in word for word in argv), "the password is never on a command line"
    assert keychain.reads == ["abc-paseo-password"] * len(daemon.calls), "read at each call, never kept in a file"


SECRETS = {"LINEAR_API_KEY": "lin-a", "XYZ_LINEAR_API_KEY": "lin-b", "GH_TOKEN": "gh-fake", "OPENROUTER_API_KEY": "or-fake",
           "SOME_TOKEN": "t-fake", "PASEO_WORKSPACE_ID": "wks_0"}  # fakes: the service's keys, which no `paseo` call may get


def test_every_paseo_call_gets_only_the_few_variables_and_the_endpoint_s_password(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real runner (`run_paseo`) of each call kind: the host's calls, the local daemon's status and stop, and the endpoint
    probe. Each call gets PATH, HOME, USER, LANG and TMPDIR, and PASEO_PASSWORD only at an endpoint with a password item."""
    for name, value in {**SECRETS, "PATH": "/usr/bin:/bin", "USER": "agents", "LANG": "C.UTF-8", "TMPDIR": "/tmp/x"}.items():
        monkeypatch.setenv(name, value)
    daemon = Daemon()

    def run(argv: Sequence[str], **kwargs: Any) -> Any:
        if argv[1] == "daemon":  # the local daemon's status and stop: no daemon runs
            daemon.calls.append((list(argv), kwargs["env"]))
            return orch.subprocess.CompletedProcess(argv, 0, '{"localDaemon": "stopped"}' if argv[2] == "status" else "", "")
        return daemon(argv, kwargs["env"])
    monkeypatch.setattr(orch.subprocess, "run", run)
    local = orch.PaseoHost()
    exercise(local)
    paseo_daemon = orch.PaseoDaemon("claude", ("claude",), local, tmp_path)
    assert paseo_daemon.profiles() == ()
    paseo_daemon.stop()
    orch.ask_endpoint(orch.LOCAL)
    exercise(orch.PaseoHost(endpoint=orch.Endpoint(HOST_A), password=Keychain()))
    first = len(daemon.calls)  # the calls from here on go to the endpoint with a password item
    exercise(orch.PaseoHost(endpoint=orch.Endpoint(HOST_A, "abc-pw"), password=Keychain()))
    orch.ask_endpoint(orch.Endpoint(HOST_A, "abc-pw"), password=Keychain())
    assert {"workspace create", "run", "ls", "send", "logs", "archive", "workspace ls", "workspace archive", "daemon"} <= set(
        " ".join(argv[1:3]) if argv[1] == "workspace" else argv[3] if argv[1] == "--host" else argv[1] for argv, _ in daemon.calls)
    few = {"PATH", "HOME", "USER", "LANG", "TMPDIR"}
    for at, (argv, env) in enumerate(daemon.calls):
        assert env is not None, argv
        assert set(env) == (few | {"PASEO_PASSWORD"} if at >= first else few), argv
        assert at < first or env["PASEO_PASSWORD"] == PASSWORD
        assert env["PATH"] == "/usr/bin:/bin" and env["USER"] == "agents" and env["HOME"] == str(tmp_path / "home")
        assert not any("LINEAR" in name or "TOKEN" in name or name.endswith("_KEY") for name in env), argv
        assert not set(SECRETS.values()) & set(env.values()), argv


def test_a_host_with_no_password_item_passes_the_host_and_no_password() -> None:
    daemon = Daemon()
    orch.PaseoHost(daemon, endpoint=orch.Endpoint(HOST_A)).workspaces()
    assert daemon.calls == [(["paseo", "--host", HOST_A, "ls", "-g", "--json"], None)]


def test_a_password_that_cannot_be_read_fails_the_call_and_never_names_the_output() -> None:
    def security(argv: Sequence[str]) -> Any:
        assert argv == ["/usr/bin/security", "find-generic-password", "-s", "abc-paseo-password", "-w"]
        return orch.subprocess.CompletedProcess(argv, 44, "", "The specified item could not be found in the keychain.")
    with pytest.raises(RuntimeError, match=r"cannot read the keychain item abc-paseo-password \(security exited 44\)"):
        orch.keychain_password("abc-paseo-password", security)
    found = orch.keychain_password("abc-paseo-password", lambda argv: orch.subprocess.CompletedProcess(argv, 0, PASSWORD + "\n", ""))
    assert found == PASSWORD
    with pytest.raises(RuntimeError, match="refused"):
        orch.keychain_password("-a x")
    daemon = Daemon()

    def locked(item: str) -> str:
        raise RuntimeError(f"cannot read the keychain item {item} (security exited 51)")
    with pytest.raises(RuntimeError, match="exited 51"):
        orch.PaseoHost(daemon, endpoint=orch.Endpoint(HOST_A, "abc-paseo-password"), password=locked).workspaces()
    assert daemon.calls == [], "no call without its password"


def test_the_keys_load_for_the_first_product_and_for_a_product_table(tmp_path: Path) -> None:
    top = f'paseo_host = "{HOST_A}"\npaseo_password_item = "abc-paseo-password"\ncpus = 14\n'
    cfg = config(tmp_path, top, XYZ + f'paseo_host = "{HOST_B}"\npaseo_password_item = "xyz-paseo-password"\ncpus = 8\n')
    first, second = orch.team_configs(cfg)
    assert first.endpoint == orch.Endpoint(HOST_A, "abc-paseo-password", 14)
    assert second.endpoint == orch.Endpoint(HOST_B, "xyz-paseo-password", 8)
    # a product with no keys runs in the local daemon: it never takes the first product's endpoint
    assert orch.team_configs(config(tmp_path, top, XYZ))[1].endpoint == orch.LOCAL


def test_the_example_s_endpoint_lines_load_when_they_are_on(tmp_path: Path) -> None:
    text = EXAMPLE_TEXT
    lines = [line for line in text.splitlines() if re.match(r"# (paseo_host|paseo_password_item|cpus) = ", line)]
    assert len(lines) == 6, "three for the first product, three in the [[product]] example"
    on = "".join(line[2:].split("  #")[0] + "\n" for line in lines[:3])
    cfg = config(tmp_path, on)
    assert cfg.endpoint == orch.Endpoint("127.0.0.1:6768", "abc-paseo-password", 14)


@pytest.mark.parametrize("top, extra, words", [
    ("paseo_host = 6768\n", "", "paseo_host must be text"),
    ('paseo_host = "-x"\n', "", "paseo_host must be text"),
    ('paseo_host = "a b"\n', "", "paseo_host must be text"),
    ('paseo_host = "h:1"\npaseo_password_item = ["x"]\n', "", "paseo_password_item must be text"),
    ('paseo_host = "h:1"\npaseo_password_item = "a b"\n', "", "paseo_password_item must be text"),
    ('paseo_password_item = "x"\n', "", "paseo_password_item needs paseo_host"),
    ("cpus = 0\n", "", "cpus must be a whole number, 1 or more"),
    ('cpus = "8"\n', "", "cpus must be a whole number"),
    ("cpus = true\n", "", "cpus must be a whole number"),
    ("", XYZ + 'cpus = 1.5\n', r"\[\[product\]\] XYZ cpus must be a whole number"),
    ("", XYZ + 'paseo_password_item = "x"\n', r"\[\[product\]\] XYZ paseo_password_item needs paseo_host"),
    ('paseo_host = "h:1"\npaseo_password_item = "x"\n', XYZ + 'paseo_host = "h:1"\npaseo_password_item = "y"\n',
     "two tables of the endpoint h:1 name two values of paseo_password_item"),
    ("cpus = 8\n", XYZ + "cpus = 4\n", "two tables of the endpoint the local daemon name two values of cpus"),
    ("", XYZ + "paseo_hots = 'h:1'\n", "unknown paseo_hots"),
])
def test_a_wrong_endpoint_key_stops_the_start(tmp_path: Path, top: str, extra: str, words: str) -> None:
    with pytest.raises(orch.ConfigError, match=words):
        config(tmp_path, top, extra)


def test_the_tables_of_one_endpoint_share_its_password_item_and_cpu_count(tmp_path: Path) -> None:
    cfg = config(tmp_path, f'paseo_host = "{HOST_A}"\n', XYZ + f'paseo_host = "{HOST_A}"\npaseo_password_item = "p"\ncpus = 6\n')
    assert [c.endpoint for c in orch.team_configs(cfg)] == [orch.Endpoint(HOST_A, "p", 6)] * 2
    host = orch.make_host(cfg)
    assert isinstance(host, orch.PaseoHost) and host.endpoint == orch.Endpoint(HOST_A, "p", 6)
    assert orch.local_paseo(host) is None, "the service keeps no daemon of another user"


def endpoints_host() -> tuple[Any, Daemon, Daemon]:
    """ABC on endpoint A, XYZ on endpoint B."""
    a = Daemon()
    b = Daemon([{**AGENTS[0], "id": "agent-0101", "name": "XYZ-3 ·"}])
    stops = [("429|rate limit", "the rate or usage limit", True)]
    host_a = orch.PaseoHost(a, stops, endpoint=orch.Endpoint(HOST_A, "abc-pw"), password=Keychain())
    host_b = orch.PaseoHost(b, stops, endpoint=orch.Endpoint(HOST_B, "xyz-pw"), password=Keychain())
    return orch.PaseoEndpoints({"ABC": host_a, "XYZ": host_b}, host_a), a, b


def test_two_endpoints_route_each_call_to_the_endpoint_of_its_product() -> None:
    host, a, b = endpoints_host()
    assert host.start("XYZ-3 ·", "paseo run --title x 'p'", "/two") == "agent-0003"
    assert host.start("ABC-7 ·", "paseo run --title x 'p'", "/one") == "agent-0003"
    assert [v for v in b.verbs() if v in ("workspace create", "run")] == ["workspace create", "run"]
    assert [v for v in a.verbs() if v in ("workspace create", "run")] == ["workspace create", "run"]
    a.calls.clear()
    b.calls.clear()
    listed = host.workspaces()
    assert {s["label"] for s in listed} == {"ABC-7 ·", "ABC-9 ·", "XYZ-3 ·"}
    assert host.wake("XYZ-3", "wake") is True and "send" in b.verbs() and "send" not in a.verbs()
    host.close("agent-0101")
    assert "archive" in b.verbs() and "archive" not in a.verbs()
    host.tail("agent-0002")
    assert "logs" in a.verbs()
    assert all(argv[2] == HOST_A for argv, _ in a.calls) and all(argv[2] == HOST_B for argv, _ in b.calls)
    assert host.paused() == {"claude": ("ABC-9", "the rate or usage limit")}, "the stop of endpoint A, read in endpoint A's logs"


def test_a_list_fails_when_one_endpoint_does_not_answer() -> None:
    a = Daemon()
    host = orch.PaseoEndpoints({"ABC": orch.PaseoHost(a), "XYZ": orch.PaseoHost(Daemon(down=True), endpoint=orch.Endpoint(HOST_B))},
                               orch.PaseoHost(a))
    with pytest.raises(RuntimeError, match="paseo ls failed"):
        host.workspaces()


def test_make_host_gives_one_host_for_each_endpoint(tmp_path: Path) -> None:
    keychain = Keychain()
    cfg = config(tmp_path, extra=XYZ + f'paseo_host = "{HOST_B}"\npaseo_password_item = "xyz-pw"\n'
                 + '\n[[product]]\nteam = "DEF"\nrepo = "acme/def"\nrun_cwd = "~/def"\n')
    host = orch.make_host(cfg, password=keychain)
    assert isinstance(host, orch.PaseoEndpoints)
    assert host.of_key("ABC-1") is host.of_key("DEF-1") and host.of_key("ABC-1").endpoint == orch.LOCAL
    assert host.of_key("XYZ-1").endpoint == orch.Endpoint(HOST_B, "xyz-pw") and host.of_key("XYZ-1").password is keychain
    assert len(host.hosts) == 2 and orch.local_paseo(host) is host.of_key("ABC-1")
    assert host.of_key("QRS-1") is host.of_key("ABC-1"), "a key of no team goes to the first product's endpoint"
    assert isinstance(orch.make_host(config(tmp_path, extra=XYZ, host="herdr")), orch.HerdrHost)


def test_the_worker_share_is_the_endpoint_s_cpus_divided_by_max_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orch.os, "cpu_count", lambda: 12)
    cfg = dataclasses.replace(config(tmp_path, "cpus = 16\n", XYZ + f'paseo_host = "{HOST_B}"\ncpus = 6\n'), max_runs=3)
    first, second = orch.team_configs(cfg)

    def workers(c: Any) -> str:
        found = re.search(r"PYTEST_XDIST_AUTO_NUM_WORKERS=(\d+)", orch.format_run(c, f"{c.team}-1", "start", "claude-sonnet-5-5",
                                                                                  "medium", f"{c.team}-1 ·", "/clone", "20261126T120000Z-x"))
        assert found is not None
        return found.group(1)

    assert (workers(first), workers(second)) == ("5", "2"), "16 // 3, and 6 // 3"
    assert orch.Runner(first, orch.ProcessHost(tmp_path)).workers_given("", "medium") == 5
    # no cpus: the orchestrator's machine's count, as before
    plain = dataclasses.replace(config(tmp_path, extra=XYZ), max_runs=3)
    assert {workers(c) for c in orch.team_configs(plain)} == {"4"}


def ok_probe(endpoint: Any) -> None:
    return None


def test_the_check_prints_one_line_for_each_product_s_endpoint(tmp_path: Path) -> None:
    cfg = config(tmp_path, f'paseo_host = "{HOST_A}"\npaseo_password_item = "abc-pw"\n',
                 XYZ + '\n[[product]]\nteam = "DEF"\nrepo = "acme/def"\nrun_cwd = "~/def"\n')
    asked: list[Any] = []

    def probe(endpoint: Any) -> None:
        asked.append(endpoint)
        if endpoint.host:
            raise RuntimeError("The daemon rejected the password. Check PASEO_PASSWORD and retry.")
    assert orch.check_endpoints(orch.team_configs(cfg), probe) == [
        (orch.FAIL, f"ABC: Paseo at {HOST_A}: The daemon rejected the password. Check PASEO_PASSWORD and retry."),
        (orch.OK, "XYZ: Paseo at the local daemon answers"),
        (orch.OK, "DEF: Paseo at the local daemon answers")]
    assert len(asked) == 2, "one call for each endpoint"

    def slow(endpoint: Any) -> None:
        raise orch.subprocess.TimeoutExpired(["paseo"], 60)
    assert orch.check_endpoints(orch.team_configs(cfg)[:1], slow) == [(orch.FAIL, f"ABC: Paseo at {HOST_A}: no answer within 60 s")]
    assert orch.check_endpoints(orch.team_configs(config(tmp_path, host="herdr")), probe) == [], "herdr has no Paseo endpoint"


def test_run_check_prints_the_endpoint_lines(tmp_path: Path) -> None:
    lines: list[str] = []
    ok: Callable[[], object] = lambda: None  # noqa: E731 (a one-line stub)
    probes = orch.Probes(environ={"LINEAR_API_KEY": "x"}, linear=ok, github=ok, log=lambda: "", sessions=lambda: [],
                         installed=lambda: (None, []), state=lambda: None, run_started=lambda key: None,
                         paseo_profile=lambda: ("claude",), paseo_endpoint=ok_probe)
    orch.run_check(config(tmp_path, f'paseo_host = "{HOST_A}"\n', XYZ), probes, lines.append)
    assert f"ok   ABC: Paseo at {HOST_A} answers" in lines and "ok   XYZ: Paseo at the local daemon answers" in lines


def test_the_endpoint_probe_lists_with_the_host_and_the_password_in_its_environment() -> None:
    daemon = Daemon()
    orch.ask_endpoint(orch.Endpoint(HOST_A, "abc-pw"), daemon, Keychain())
    [(argv, env)] = daemon.calls
    assert argv == ["paseo", "--host", HOST_A, "ls", "-q"] and env is not None and env["PASEO_PASSWORD"] == PASSWORD
    with pytest.raises(RuntimeError, match=r"^The daemon rejected the password\. Check PASEO_PASSWORD and retry\.$"):
        orch.ask_endpoint(orch.Endpoint(HOST_A, "abc-pw"), Daemon(down=True), Keychain())
    local = Daemon()
    orch.ask_endpoint(orch.LOCAL, local, Keychain())
    assert local.calls == [(["paseo", "ls", "-q"], None)]


def test_the_preflight_reads_the_local_daemon_only_when_a_product_runs_there(tmp_path: Path) -> None:
    read: list[int] = []

    def profile() -> tuple[str, ...]:
        read.append(1)
        return ("claude",)
    remote = config(tmp_path, f'paseo_host = "{HOST_A}"\n')
    assert not any("Paseo daemon" in text for _, text in orch.preflight(remote, lambda: True, lambda _: True, profile)) and not read
    mixed = config(tmp_path, f'paseo_host = "{HOST_A}"\n', XYZ)
    assert any("Paseo daemon" in text for _, text in orch.preflight(mixed, lambda: True, lambda _: True, profile)) and read


def test_the_preflight_runs_no_key_check_when_every_product_has_its_own_user_and_endpoint(tmp_path: Path) -> None:
    """OWL-71, design b2: the credentials are in each product's user, whose home the service's user cannot read. `owlcode
    check` runs those key checks as each product's user; the service's preflight runs none and shows no line of them."""
    asked: list[str] = []

    def key() -> bool:
        asked.append("profile")
        return False

    def cell(name: str) -> bool:
        asked.append(name)
        return False

    own_xyz = XYZ + f'agent_user = "acme-xyz"\npaseo_host = "{HOST_B}"\n'
    own = config(tmp_path, f'agent_user = "acme-abc"\npaseo_host = "{HOST_A}"\n', own_xyz)
    assert orch.own_endpoints(own) and [c.agent_user for c in orch.team_configs(own)] == ["acme-abc", "acme-xyz"]
    lines = orch.preflight(own, key, lambda _: True, cell_key=cell)
    assert not asked and not any("inference profile" in text or "cell profile" in text for _, text in lines)
    assert not any(level == orch.FAIL for level, _ in lines)
    # a product on the local daemon, with no user of its own, or in the service's own user keeps today's check
    for top, extra in ((f'agent_user = "acme-abc"\npaseo_host = "{HOST_A}"\n', XYZ),
                       (f'paseo_host = "{HOST_A}"\n', own_xyz),
                       (f'agent_user = "agents"\npaseo_host = "{HOST_A}"\n', own_xyz)):
        asked.clear()
        kept = config(tmp_path, top, extra)
        assert not orch.own_endpoints(kept)
        lines = orch.preflight(kept, key, lambda _: True, cell_key=cell)
        assert asked[:1] == ["profile"] and (orch.FAIL, "inference profile: claude, its key is not in the keychain: store it as the "
                                             "agents' user with `agent_user.py user` (README.md \"The agents' macOS user\")") in lines
