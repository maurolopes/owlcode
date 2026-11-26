"""The `run:` blocks of .github/workflows/ stay short: the logic of a workflow lives in a script under .github/.

The implementer's app cannot push a change under .github/workflows/, so each change to logic in a `run:` block
becomes a patch that the owner applies by hand. A script under .github/ is pushed like other code: it needs no patch,
though the owner still reviews and merges it (.github/CODEOWNERS). A block has at most
MAX_LINES lines of code (a blank line or a comment line does not count). A block that had more on 2026-11-23 is in
ALLOWED with its count of that day: it can shrink, never grow. Each issue of the project "Thin workflows" that moves a
block's logic into a script removes the block's line from ALLOWED, and an entry whose block is gone or short fails, so the
list only gets shorter.

A block's key is its file, its job and its step: the step's `name`, else its `id`, else `step N` (N counts from 1).
"""

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
MAX_LINES = 5
MOVE = "move the logic into a script under .github/, and call the script from the workflow"

# The blocks longer than MAX_LINES on 2026-11-23, with their count of that day.
ALLOWED: dict[tuple[str, str, str], int] = {
}


def code_lines(script: str) -> int:
    """The lines of a `run:` block that hold code: not blank, not a shell comment."""
    return sum(1 for line in script.splitlines() if line.strip() and not line.lstrip().startswith("#"))


def run_blocks(folder: Path) -> dict[tuple[str, str, str], int]:
    """Each `run:` block of each workflow in the folder, by (file, job, step), with its count of code lines. A job that
    calls a reusable workflow (`uses:`, no `steps:`) has no block."""
    blocks: dict[tuple[str, str, str], int] = {}
    for path in sorted([*folder.glob("*.yml"), *folder.glob("*.yaml")]):
        for job_name, job in (yaml.safe_load(path.read_text()).get("jobs") or {}).items():
            for number, step in enumerate(job.get("steps") or [], start=1):
                if "run" not in step:
                    continue
                step_key = str(step.get("name") or step.get("id") or f"step {number}")
                key = (path.name, str(job_name), step_key)
                assert key not in blocks, f"{path.name}, job {job_name}: two steps have the key {step_key!r}; give each a name"
                blocks[key] = code_lines(str(step["run"]))
    return blocks


def problems(blocks: dict[tuple[str, str, str], int], allowed: dict[tuple[str, str, str], int]) -> list[str]:
    """What breaks the limit: a new long block, an allowed block that grew, and an entry of `allowed` that is stale."""
    found = []
    for (file, job, step), count in blocks.items():
        limit = allowed.get((file, job, step), MAX_LINES)
        if count > limit:
            where = f"{file}, job {job}, step {step!r}: {count} lines of code in its run block"
            found.append(f"{where}, more than {limit} ({'its count on 2026-11-23' if limit > MAX_LINES else 'the limit'}): {MOVE}")
    for file, job, step in allowed:
        now = blocks.get((file, job, step))
        if now is None or now <= MAX_LINES:
            state = "is gone" if now is None else f"has {now} lines of code, within the limit"
            found.append(f"{file}, job {job}, step {step!r} {state}: remove its entry from ALLOWED in {Path(__file__).name}")
    return found


def test_no_run_block_of_a_workflow_grows_past_its_limit():
    found = problems(run_blocks(WORKFLOWS), ALLOWED)
    assert not found, "\n".join(found)


def workflow(tmp_path: Path, run: str, name: str = "Do it") -> Path:
    (tmp_path / "w.yml").write_text(yaml.safe_dump({"jobs": {"j": {"steps": [{"name": name, "run": run}]}}}))
    return tmp_path


def test_a_new_block_of_six_lines_fails_and_names_its_place(tmp_path):
    five = "a\n\n# a comment\nb\nc\nd\ne\n"
    assert problems(run_blocks(workflow(tmp_path, five)), {}) == []
    found = problems(run_blocks(workflow(tmp_path, five + "f\n")), {})
    assert len(found) == 1
    assert "w.yml, job j, step 'Do it': 6 lines of code" in found[0] and "script under .github/" in found[0]


def test_an_allowed_block_fails_when_it_grows_by_one_line(tmp_path):
    allowed = {("w.yml", "j", "Do it"): 8}
    eight = "".join(f"line {n}\n" for n in range(8))
    assert problems(run_blocks(workflow(tmp_path, eight)), allowed) == []
    assert problems(run_blocks(workflow(tmp_path, eight[:-7])), allowed) == []
    found = problems(run_blocks(workflow(tmp_path, eight + "line 8\n")), allowed)
    assert len(found) == 1 and "9 lines of code" in found[0] and "more than 8 (its count on 2026-11-23)" in found[0]


def test_an_entry_whose_block_is_gone_or_short_fails(tmp_path):
    allowed = {("w.yml", "j", "Do it"): 8}
    assert "remove its entry from ALLOWED" in problems(run_blocks(workflow(tmp_path, "a\n" * 5)), allowed)[0]
    renamed = problems(run_blocks(workflow(tmp_path, "a\n" * 8, name="Renamed")), allowed)
    assert len(renamed) == 2 and "step 'Renamed': 8 lines" in renamed[0] and "step 'Do it' is gone" in renamed[1]


def test_a_step_with_no_name_has_its_id_or_its_number_as_its_key(tmp_path):
    steps = [{"uses": "x/y@1"}, {"run": "a"}, {"id": "diff", "run": "b"}]
    (tmp_path / "w.yaml").write_text(yaml.safe_dump({"jobs": {"j": {"steps": steps}, "call": {"uses": "./x.yml"}}}))
    assert run_blocks(tmp_path) == {("w.yaml", "j", "step 2"): 1, ("w.yaml", "j", "diff"): 1}


def test_two_steps_of_one_job_with_the_same_key_fail(tmp_path):
    steps = [{"name": "Do it", "run": "a"}, {"name": "Do it", "run": "b"}]
    (tmp_path / "w.yml").write_text(yaml.safe_dump({"jobs": {"j": {"steps": steps}}}))
    with pytest.raises(AssertionError, match="two steps have the key 'Do it'"):
        run_blocks(tmp_path)
