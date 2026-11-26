"""`.github/leak_guard.py`: counts invented words in a list of files or in a text, from patterns that come from
the environment. The patterns here are invented (`zebra`, `PROJ-[0-9]+`); the real ones never enter a repository, so the
tests also check that the output holds neither a matched text nor a pattern."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / ".github" / "leak_guard.py"
PATTERNS = "zebra\nPROJ-[0-9]+\n"


def run(*args: str, env: dict[str, str] | None = None, stdin: str = "", cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith("LEAK_")}
    return subprocess.run([sys.executable, "-I", str(GUARD), *args], input=stdin, capture_output=True, text=True,
                          env={**base, **(env or {})}, cwd=cwd, check=False)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A root with a clean file, a file with two hits, a nested file with one, a binary file, and the list."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "clean.md").write_text("nothing here\n")
    (tmp_path / "dirty.md").write_text("one\nA Zebra walks\nPROJ-123 and proj-9 and zebra\n")
    (tmp_path / "sub" / "deep.txt").write_text("ZEBRA\n")
    (tmp_path / "sub" / "blob.bin").write_bytes(b"zebra\0zebra")
    (tmp_path / "list.txt").write_text("# a comment\n\n*.md\nsub/\n")
    return tmp_path


def test_a_hit_prints_its_place_and_pattern_number_and_exits_1(tree: Path) -> None:
    done = run("files", "list.txt", env={"LEAK_PATTERNS": PATTERNS}, cwd=tree)
    assert done.returncode == 1
    assert done.stdout.splitlines() == [
        "dirty.md:2: pattern 1", "dirty.md:3: pattern 1", "dirty.md:3: pattern 2", "sub/deep.txt:1: pattern 1",
        "dirty.md: 3", "sub/deep.txt: 1", "total: 4 in 2 files"]


def test_no_hit_exits_0(tree: Path) -> None:
    done = run("files", "list.txt", env={"LEAK_PATTERNS": "giraffe"}, cwd=tree)
    assert done.returncode == 0
    assert done.stdout == "total: 0 in 0 files\n"


def test_the_output_holds_no_matched_text_and_no_pattern(tree: Path) -> None:
    secret = "zebra|PROJ-[0-9]+"
    for args in (("files", "list.txt"), ("files", "list.txt", "--counts-only")):
        done = run(*args, env={"LEAK_PATTERNS": PATTERNS}, cwd=tree)
        out = (done.stdout + done.stderr).lower()
        assert "zebra" not in out and "proj" not in out and secret.lower() not in out
    done = run("stdin", env={"LEAK_PATTERNS": PATTERNS}, stdin="the zebra and PROJ-7\n")
    assert done.returncode == 1
    assert "zebra" not in (done.stdout + done.stderr).lower() and "proj" not in (done.stdout + done.stderr).lower()


def test_counts_only_leaves_out_the_hit_lines(tree: Path) -> None:
    done = run("files", "list.txt", "--counts-only", env={"LEAK_PATTERNS": PATTERNS}, cwd=tree)
    assert done.stdout.splitlines() == ["dirty.md: 3", "sub/deep.txt: 1", "total: 4 in 2 files"]


def test_stdin_checks_a_text(tmp_path: Path) -> None:
    hit = run("stdin", env={"LEAK_PATTERNS": PATTERNS}, stdin="fine\nfeat: PROJ-42 is done\n")
    assert hit.returncode == 1
    assert hit.stdout.splitlines() == ["<stdin>:2: pattern 2", "<stdin>: 1", "total: 1 in 1 files"]
    assert run("stdin", env={"LEAK_PATTERNS": PATTERNS}, stdin="fine\n").returncode == 0


@pytest.mark.parametrize("args", [("stdin",), ("files", "list.txt")])
def test_no_pattern_exits_2(tree: Path, args: tuple[str, ...]) -> None:
    for env in ({}, {"LEAK_PATTERNS": " \n\n"}):
        done = run(*args, env=env, cwd=tree)
        assert done.returncode == 2
        assert done.stdout == "" and "no pattern" in done.stderr


def test_the_patterns_file_wins_over_the_variable(tree: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    where = tmp_path_factory.mktemp("patterns") / "p.txt"
    where.write_text("one\n")
    done = run("files", "list.txt", "--counts-only", env={"LEAK_PATTERNS_FILE": str(where), "LEAK_PATTERNS": "zebra"}, cwd=tree)
    assert done.stdout.splitlines() == ["dirty.md: 1", "total: 1 in 1 files"]
    gone = run("stdin", env={"LEAK_PATTERNS_FILE": str(where) + ".missing", "LEAK_PATTERNS": "zebra"})
    assert gone.returncode == 2


def test_an_invalid_pattern_exits_2_and_does_not_print_it(tree: Path) -> None:
    done = run("files", "list.txt", env={"LEAK_PATTERNS": "fine\nsecret-word(["}, cwd=tree)
    assert done.returncode == 2
    assert "pattern 2" in done.stderr and "secret-word" not in done.stdout + done.stderr


@pytest.mark.parametrize("bad", ["(?P=secretword)", r"secret\qword", "[z-secretword]"])
def test_an_invalid_pattern_whose_error_quotes_it_prints_none_of_it(tree: Path, bad: str) -> None:
    """re.error's message quotes some patterns (an unknown group name, a bad escape): the guard must not pass it on."""
    done = run("files", "list.txt", env={"LEAK_PATTERNS": "fine\n" + bad}, cwd=tree)
    assert done.returncode == 2
    assert done.stderr.startswith("leak_guard: pattern 2 is not a regular expression")
    assert "secret" not in (done.stdout + done.stderr).lower()


def test_a_missing_list_file_exits_2(tree: Path) -> None:
    assert run("files", "nope.txt", env={"LEAK_PATTERNS": PATTERNS}, cwd=tree).returncode == 2


def test_the_list_takes_globs_folders_and_exclusions(tree: Path) -> None:
    (tree / "list.txt").write_text("**/*.md\nsub\n!sub/deep.txt\n")
    done = run("files", "list.txt", "--counts-only", env={"LEAK_PATTERNS": "zebra"}, cwd=tree)
    assert done.stdout.splitlines() == ["dirty.md: 2", "total: 2 in 1 files"]


def test_a_glob_skips_a_hidden_name_and_a_folder_line_reads_it(tree: Path) -> None:
    """`*` and `**` never match a name that starts with a dot (the module's text says so); a line of its own does."""
    (tree / ".hidden").mkdir()
    (tree / ".hidden" / "x.md").write_text("zebra\n")
    (tree / "list.txt").write_text("**/*.md\n")
    glob_only = run("files", "list.txt", "--counts-only", env={"LEAK_PATTERNS": "zebra"}, cwd=tree)
    assert glob_only.stdout.splitlines() == ["dirty.md: 2", "total: 2 in 1 files"]
    (tree / "list.txt").write_text("**/*.md\n.hidden/\n")
    with_folder = run("files", "list.txt", "--counts-only", env={"LEAK_PATTERNS": "zebra"}, cwd=tree)
    assert with_folder.stdout.splitlines() == [".hidden/x.md: 1", "dirty.md: 2", "total: 3 in 2 files"]


def test_a_list_line_that_names_no_file_is_said_on_stderr(tree: Path) -> None:
    (tree / "list.txt").write_text("dirty.md\ngone/*.py\n")
    done = run("files", "list.txt", "--counts-only", env={"LEAK_PATTERNS": "zebra"}, cwd=tree)
    assert done.returncode == 1
    assert done.stderr.strip() == "leak_guard: line 2 of the list names no file"


def test_root_names_the_folder_and_skips_caches(tree: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    (tree / "__pycache__").mkdir()
    (tree / "__pycache__" / "x.txt").write_text("zebra\n")
    (tree / "list.txt").write_text("./\n")
    elsewhere = tmp_path_factory.mktemp("cwd")
    done = run("files", str(tree / "list.txt"), "--root", str(tree), "--counts-only", env={"LEAK_PATTERNS": "zebra"}, cwd=elsewhere)
    assert done.stdout.splitlines() == ["dirty.md: 2", "sub/deep.txt: 1", "total: 3 in 2 files"]


@pytest.mark.skipif(os.geteuid() == 0, reason="platform: root reads any file")
def test_a_file_that_cannot_be_read_exits_2_with_its_path_only(tree: Path) -> None:
    (tree / "dirty.md").chmod(0)
    done = run("files", "list.txt", "--counts-only", env={"LEAK_PATTERNS": "zebra"}, cwd=tree)
    assert done.returncode == 2
    assert done.stderr.strip() == "leak_guard: dirty.md cannot be read"
    assert done.stdout.splitlines() == ["sub/deep.txt: 1", "total: 1 in 1 files"]


def test_the_loop_list_names_only_files_that_exist() -> None:
    """The real list: each line that is no exclusion names a file of this checkout (a stale line says so on stderr)."""
    done = run("files", ".workflow/loop-files.txt", "--counts-only", env={"LEAK_PATTERNS": "zebra"}, cwd=REPO)
    assert done.returncode in (0, 1) and done.stderr == ""


def test_the_loop_list_leaves_out_the_workflows_that_test_the_product() -> None:
    """gates.yml, caches.yml and race-hunt.yml name this product's paths, so they stay out of the loop list."""
    done = run("files", ".workflow/loop-files.txt", "--counts-only", env={"LEAK_PATTERNS": "."}, cwd=REPO)
    listed = {line.split(":")[0] for line in done.stdout.splitlines()}
    assert ".github/workflows/flakes.yml" in listed
    for name in ("gates", "caches", "race-hunt"):
        assert f".github/workflows/{name}.yml" not in listed
