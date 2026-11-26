#!/usr/bin/env python3
"""The leak guard: counts the product's words in the loop's files, with patterns that no repository holds.

The loop's files move to a public repository (owlcode). The product's name, its issue keys and its server must not move
with them. The patterns name the product, so they can never be in a repository, and a public CI log must never show a
match. So this script holds no pattern, and it never prints a matched text or a pattern.

    python3 .github/leak_guard.py files LISTFILE [--root DIR] [--counts-only]
        checks each file that a line of LISTFILE names (`.workflow/loop-files.txt` is the list of the loop's files)
    python3 .github/leak_guard.py stdin
        checks the text on stdin: a commit message, a pull request's title and body

Patterns: one case-insensitive regular expression per line (a blank line counts for nothing). They come from the file
that `LEAK_PATTERNS_FILE` names, else from the variable `LEAK_PATTERNS`. There is no default. A pattern's number N is its
place among the non-blank lines, from 1.

LISTFILE: one path or glob a line, relative to the root (`--root`, default the current folder). `**` matches any folders, but `*` and `**` never
match a name that starts with a dot: a hidden folder or file needs a line of its own (a folder line, or a glob that starts with
the dot, as `.github/*.py`), else the guard does not read it.
A folder names every file beneath it. A line that starts with `!` takes files out of the ones the lines before it named.
A blank line, and a line that starts with `#`, count for nothing. A line that does not start with `!` and names no file
is said on stderr (the list is stale) and does not change the exit status. A file with a NUL byte is binary: it is skipped.
A file that cannot be read is said on stderr (its path only) and makes the exit status 2, so a clean total never hides a file
that nobody read.

Output, on stdout: one line `PATH:LINE: pattern N` for each pattern that a line matches (stdin: `<stdin>` for PATH), then
one line `PATH: COUNT` for each file with a hit, then `total: COUNT in FILES files`. `--counts-only` leaves out the first
group: it is the cleanup's progress (`mise run leak:count`). Exit status: 0 no hit, 1 a hit, 2 no pattern, an invalid
pattern, or a list file or a listed file that cannot be read. Standard library only (DECISIONS.md "Tooling"); it runs on the Mac's
`/usr/bin/python3` (3.9) too.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

STDIN_NAME = "<stdin>"
SKIPPED = {".git", "__pycache__", "node_modules"}  # never the loop's files, wherever a folder line reaches


class UsageError(Exception):
    """A reason to check nothing (exit 2). Its text never holds a pattern."""


def read_patterns(environ: Mapping[str, str]) -> list[re.Pattern[str]]:
    """The patterns of LEAK_PATTERNS_FILE, else of LEAK_PATTERNS, as case-insensitive expressions."""
    where = environ.get("LEAK_PATTERNS_FILE", "")
    if where:
        try:
            text = Path(where).read_text(encoding="utf-8")
        except OSError as err:
            raise UsageError(f"LEAK_PATTERNS_FILE cannot be read ({err.strerror})") from None
    else:
        text = environ.get("LEAK_PATTERNS", "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise UsageError("no pattern: set LEAK_PATTERNS_FILE (a file of one expression a line) or LEAK_PATTERNS")
    found: list[re.Pattern[str]] = []
    for number, line in enumerate(lines, 1):
        try:
            found.append(re.compile(line, re.IGNORECASE))
        except re.error as err:
            # re.error's msg can quote the pattern (an unknown group name, a bad escape): print its number and place only
            where = f" at position {err.pos}" if err.pos is not None else ""
            raise UsageError(f"pattern {number} is not a regular expression{where}") from None
    return found


def hits(text: str, patterns: Sequence[re.Pattern[str]]) -> list[tuple[int, int]]:
    """(line number, pattern number) for each pattern that a line matches, in order."""
    found: list[tuple[int, int]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for number, pattern in enumerate(patterns, 1):
            if pattern.search(line):
                found.append((line_number, number))
    return found


def listed(list_file: Path, root: Path) -> tuple[list[str], list[str]]:
    """The files that the list file names (relative paths, sorted) and the lines that name no file."""
    try:
        lines = list_file.read_text(encoding="utf-8").splitlines()
    except OSError as err:
        raise UsageError(f"the list file cannot be read ({err.strerror})") from None
    files: set[str] = set()
    stale: list[str] = []
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out = line.startswith("!")
        names = expand(line[1:].strip() if out else line, root)
        if out:
            files -= names
        elif names:
            files |= names
        else:
            stale.append(f"line {number}")
    return sorted(files), stale


def expand(pattern: str, root: Path) -> set[str]:
    """Each file that a path or glob names; a folder names every file beneath it."""
    names: set[str] = set()
    for match in glob.glob(os.path.join(glob.escape(str(root)), pattern), recursive=True):
        path = Path(match)
        if path.is_dir():
            names |= {p.relative_to(root).as_posix() for p in path.rglob("*") if p.is_file()}
        elif path.is_file():
            names.add(path.relative_to(root).as_posix())
    return {n for n in names if not SKIPPED.intersection(Path(n).parts)}


def read_text(path: Path) -> str | None:
    """A file's text, or None for a binary file (a NUL byte). An unreadable file raises OSError."""
    data = path.read_bytes()
    return None if b"\0" in data else data.decode("utf-8", errors="replace")


def report(results: Iterable[tuple[str, list[tuple[int, int]]]], counts_only: bool) -> int:
    """Print the hits and the counts as the module's text says. Returns the exit status."""
    counts: list[tuple[str, int]] = []
    for name, found in results:
        if not found:
            continue
        if not counts_only:
            for line_number, number in found:
                print(f"{name}:{line_number}: pattern {number}")
        counts.append((name, len(found)))
    for name, count in counts:
        print(f"{name}: {count}")
    print(f"total: {sum(c for _, c in counts)} in {len(counts)} files")
    return 1 if counts else 0


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None,
         stdin: str | None = None) -> int:
    parser = argparse.ArgumentParser(prog="leak_guard.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    files = sub.add_parser("files", help="check the files that a list file names")
    files.add_argument("list_file", type=Path)
    files.add_argument("--root", type=Path, default=Path("."))
    files.add_argument("--counts-only", action="store_true", help="print the counts only, not each hit")
    sub.add_parser("stdin", help="check the text on stdin")
    args = parser.parse_args(argv)
    try:
        patterns = read_patterns(os.environ if environ is None else environ)
        if args.command == "stdin":
            text = sys.stdin.read() if stdin is None else stdin
            return report([(STDIN_NAME, hits(text, patterns))], counts_only=False)
        names, stale = listed(args.list_file, args.root)
    except UsageError as err:
        print(f"leak_guard: {err}", file=sys.stderr)
        return 2
    for where in stale:
        print(f"leak_guard: {where} of the list names no file", file=sys.stderr)
    results: list[tuple[str, list[tuple[int, int]]]] = []
    unread: list[str] = []
    for name in names:
        try:
            text = read_text(args.root / name)
        except OSError:
            unread.append(name)
            continue
        if text is not None:
            results.append((name, hits(text, patterns)))
    status = report(results, args.counts_only)
    for name in unread:
        print(f"leak_guard: {name} cannot be read", file=sys.stderr)
    return 2 if unread else status


if __name__ == "__main__":
    sys.exit(main())
