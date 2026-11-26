"""Check the run's speed lines and the Claude Code transcript fallback."""

import json
from pathlib import Path
import re
import runpy
import sys

SCRIPT = Path(__file__).resolve().parents[1] / ".agents/metrics/report.py"
REPORT = runpy.run_path(str(SCRIPT))


def test_speed_lines_from_jsonl(tmp_path):
    path = tmp_path / "run.jsonl"
    base = dict(provider="pi-ollama", model="deepseek", input=100, cached=75, output=50, cost=0.015)
    calls = [dict(base, role="run", ttft_s=t, tokens_per_s=100 / t) for t in (1, 2, 4)]
    calls += [dict(base, role="review", ttft_s=1, tokens_per_s=50, cost=None)]
    path.write_text("\n".join(map(json.dumps, calls)) + "\n{\n")
    assert list(REPORT["summary"](REPORT["entries"](path))) == [
        "Speed: pi-ollama/deepseek, 3 calls, TTFT median 2.00 s (p90 4.00 s), "
        "throughput median 50.0 tok/s, tokens 300/225/150 in/cached/out, cost $0.045000",
        "Local review speed: pi-ollama/deepseek, 1 calls, TTFT median 1.00 s (p90 1.00 s), "
        "throughput median 50.0 tok/s, tokens 100/75/50 in/cached/out, cost n/a",
    ]


def test_a_review_invocation_marks_its_throughput_approximate():
    rows = [dict(role="review", provider="claude-code", model="m", input=100, cached=80, output=100,
                 cost=0.5, ttft_s=None, tokens_per_s=50, timing="invocation")]
    assert list(REPORT["summary"](rows)) == [
        "Local review speed: claude-code/m, 1 calls, TTFT median n/a (p90 n/a), "
        "throughput median ~50.0 tok/s, tokens 100/80/100 in/cached/out, cost $0.500000",
    ]


def test_a_claude_session_finds_the_transcript_in_its_project_folder(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    projects = tmp_path / "claude" / "projects"
    folder = projects / re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd()))
    folder.mkdir(parents=True)
    transcript = folder / "mine.jsonl"
    transcript.write_text("")
    environ = {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude"), "CLAUDE_CODE_SESSION_ID": "mine"}
    assert REPORT["session_transcript"](environ) == transcript


def test_a_claude_session_reads_only_its_own_transcript(tmp_path, monkeypatch):
    prompt = "/implement ABC-123 start 20261122T123456Z-test"

    def rows(model, request):
        return [
            dict(type="user", timestamp="2026-11-22T12:34:56Z", message=dict(content=prompt)),
            dict(type="assistant", requestId=request, timestamp="2026-11-22T12:34:58Z", effort="high",
                 message=dict(model=model, content="private reply",
                              usage=dict(input_tokens=10, cache_read_input_tokens=80,
                                         cache_creation_input_tokens=10, output_tokens=100))),
        ]

    projects = tmp_path / "claude" / "projects"
    (projects / "-work").mkdir(parents=True)
    (projects / "-other").mkdir(parents=True)
    (projects / "-work" / "mine.jsonl").write_text("\n".join(map(json.dumps, rows("mine-model", "mine"))))
    (projects / "-other" / "other.jsonl").write_text("\n".join(map(json.dumps, rows("other-model", "other"))))
    run = "ABC-123-20261122T123456Z-test"
    state = tmp_path / ".local/state/agent-metrics" / f"{run}.jsonl"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "mine")
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), run])
    for _ in range(2):
        runpy.run_path(str(SCRIPT), run_name="__main__")
    found = list(REPORT["entries"](state))
    assert len(found) == 1, "a second report of the same run reads no transcript again"
    assert {row["model"] for row in found} == {"mine-model"}, "the other session's calls stay out"
    assert "private" not in state.read_text()


def test_claude_counts_each_request_once_and_does_not_claim_ttft(tmp_path, monkeypatch, capsys):
    prompt = "/implement ABC-123 start 20261122T123456Z-test"
    usage = dict(input_tokens=10, cache_read_input_tokens=80, cache_creation_input_tokens=10, output_tokens=100)
    call = dict(type="assistant", requestId="same", effort="high",
                message=dict(model="model", usage=usage, content="private reply"))
    rows = [
        dict(type="user", timestamp="2026-11-22T12:34:56Z", message=dict(content=prompt)),
        dict(call, timestamp="2026-11-22T12:34:57Z"),
        dict(call, timestamp="2026-11-22T12:34:58Z"),
        dict(type="assistant", timestamp="2026-11-22T12:34:59Z", uuid="failure", isApiErrorMessage=True,
             message=dict(model="model", content="private error")),
    ]
    assert REPORT["claude_rows"](rows, "different run", "anthropic") == []
    success, failed = REPORT["claude_rows"](rows, prompt, "anthropic")
    assert success["ttft_s"] is None and success["tokens_per_s"] == 50
    assert (success["input"], success["cached"], success["output"]) == (100, 80, 100)
    assert failed["error"] == "api_error" and failed["tokens_per_s"] is None
    assert "private" not in json.dumps([success, failed])
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("\n".join(map(json.dumps, rows)))
    run = "ABC-123-20261122T123456Z-test"
    state = tmp_path / ".local/state/agent-metrics" / f"{run}.jsonl"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), run, "--claude-transcript", str(transcript)])
    for _ in range(2):
        runpy.run_path(str(SCRIPT), run_name="__main__")
        assert list(REPORT["entries"](state)) and len(list(REPORT["entries"](state))) == 2
        assert "TTFT median n/a (p90 n/a), throughput median ~50.0 tok/s" in capsys.readouterr().out
    state.write_text(json.dumps(dict(success, role="review")) + "\n")
    transcript.unlink()
    runpy.run_path(str(SCRIPT), run_name="__main__")
    captured = capsys.readouterr()
    assert captured.out.startswith("Local review speed:")
    assert captured.err == "Cannot read the Claude Code transcript.\n"
