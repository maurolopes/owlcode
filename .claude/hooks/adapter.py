#!/usr/bin/env python3
"""Claude Code's adapter of the agents' guards (PreToolUse: Edit, Write, NotebookEdit and Bash; .claude/settings.json).

It turns Claude Code's tool call (JSON on stdin) into an action of the guards' contract (.agents/guards/guard.py), and a
refusal into Claude Code's: the message on stderr and exit status 2. Edit, Write and NotebookEdit are an `edit` of their
`file_path` or `notebook_path`; Bash is a `shell` command. Every other call, and an input that is not a tool call, passes.
Standard library only, and Python 3.9 (settings.json runs `python3` from PATH, which can be macOS's own).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".agents" / "guards"))

import guard  # after the line above: the entry point (.agents/guards/guard.py), not a file of this folder

EDITS = {"Edit", "Write", "NotebookEdit"}


def action(event: object) -> dict[str, object] | None:
    """The guards' action for one Claude Code tool call, or None when no guard reads it."""
    if not isinstance(event, dict):
        return None
    tool = event.get("tool_input")
    tool = tool if isinstance(tool, dict) else {}
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else ""
    if event.get("tool_name") in EDITS:
        return {"action": "edit", "path": tool.get("file_path") or tool.get("notebook_path"), "cwd": cwd}
    if event.get("tool_name") == "Bash":
        return {"action": "shell", "command": tool.get("command"), "cwd": cwd}
    return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    answer = guard.decide(action(event))
    if answer["allow"]:
        return 0
    print(answer["message"], file=sys.stderr)
    return 2  # Claude Code: refuse the call and show the agent this message


if __name__ == "__main__":
    sys.exit(main())
