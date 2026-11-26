#!/usr/bin/env python3
"""The agents' guards, one entry point for every harness (agents/README.md "The guards of a run").

The contract. The input is one action as a JSON object:
  {"action": "edit", "path": FILE, "cwd": FOLDER}      a change to a file (a relative FILE is read from FOLDER)
  {"action": "shell", "command": COMMAND, "cwd": FOLDER}  a shell command
The answer is one JSON object: {"allow": true}, or {"allow": false, "message": TEXT}, with the text to show the agent.
An action of another kind, or an input that is not such an object, is allowed: a guard refuses only what it knows.

From a shell: the action on stdin, the answer on stdout, exit status 0 (`python3 .agents/guards/guard.py`). From Python:
`decide(action)`. Each harness has a small adapter that turns its own tool call into the action and the answer into its own
reply (Claude Code's: .claude/hooks/adapter.py; pi's: .agents/guards/pi.ts). The guards: edit_in_worktree.py (no
change in the main checkout) and kill_by_pid.py (no kill by name or of a process group). Standard library only, and Python 3.9 (macOS's own python3).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import edit_in_worktree  # after the line above: the guards are found in this folder
import kill_by_pid


def decide(action: object) -> dict[str, object]:
    """The answer of the guards for one action of the contract."""
    message = None
    if isinstance(action, dict):
        cwd = action.get("cwd")
        cwd = cwd if isinstance(cwd, str) else ""
        if action.get("action") == "edit" and isinstance(action.get("path"), str):
            message = edit_in_worktree.check(action["path"], cwd)
        elif action.get("action") == "shell" and isinstance(action.get("command"), str):
            message = kill_by_pid.check(action["command"])
    return {"allow": True} if message is None else {"allow": False, "message": message}


def main() -> int:
    try:
        action = json.load(sys.stdin)
    except ValueError:
        action = None
    print(json.dumps(decide(action)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
