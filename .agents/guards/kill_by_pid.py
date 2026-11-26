"""The kill guard (a `shell` action of guard.py, the agents' neutral entry point): a command that stops processes by name (`pkill`, `killall`) or
a whole process group (`kill` with a negative PID, `-1` included) is refused. Several runs share the agents' user, so such a
kill can stop another run's server or browser (the rule: agents/implementer.md "While you work"). A `kill` of the
PIDs that `pgrep` or `pidof` found is a kill by name too: `kill $(pgrep -f NAME)` and `pgrep -f NAME | xargs kill` are
refused. A plain `kill PID` passes.

The command is split into simple commands at `;`, `&&`, `||`, `|`, `&`, parentheses and line ends; each one's program is
found past the shell's keywords (`if`, `then`, `do`, `{`, `!`, ...), variable assignments and wrappers (`sudo`,
`env`, `nohup`, `timeout`, `xargs`, ...). A loop, a brace group, a subshell, an `if` or a `case` that `pgrep` or `pidof`
feeds (`pgrep x | while read p; do kill $p; done`, `for p in $(pgrep x); do ...`, `done < <(pgrep x)`) kills by name in its
whole body, and what such a body prints, or a body that ran a finder, feeds the rest of its pipeline. The script of `sh -c`,
`bash -c` and `zsh -c`, the words of `eval`, and each command substitution (`$(...)`, backticks, `<(...)`, also inside
double quotes) are read the same way. The body of a heredoc is no command: it is taken out first, and only the command
substitutions of a body whose delimiter is not quoted are read. The exception is a heredoc that a shell reads as
its script (`bash <<EOF`, `cat <<EOF | sh`, `source /dev/stdin <<EOF`): its body is read as a script, quoted delimiter or
not. A command that cannot be split (an open quote)
passes: the shell would refuse it too. Standard library only.
"""

from __future__ import annotations

import re
import shlex

MESSAGE = ("Stop only the processes you started, by their PID (agents/implementer.md): no `pkill`, no `killall`, no `kill` "
           "of the PIDs that `pgrep` or `pidof` found, and no `kill` of a process group (a negative PID or -1). Other runs "
           "share this machine. Keep the PID when you start a process (`echo $!`) and run `kill PID`.")
BY_NAME = {"pkill", "killall"}
SEPARATORS = {";", "&&", "||", "|", "&", "|&", "(", ")", ";;"}
SHELLS = {"sh", "bash", "zsh", "dash"}
SHELL_VALUE_OPTIONS = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}  # a shell's options that take a value
FINDERS = {"pgrep", "pidof"}  # they print the PIDs of the processes that a name matches
DEPTH = 3  # how deep a script inside a script is read (`bash -c`, `eval`, a substitution)
MARK = "\x00"  # wraps the number of a command substitution that substitutions() took out of the line
AMPERSAND = "\x02"  # the `&` of a redirection (`2>&1`, `>&2`, `&>file`): no separator for pipelines()
CLOBBER = "\x05"  # the `|` of `>|`: no pipe for pipelines()
FD = r"(?:\d+|\{[A-Za-z_][A-Za-z0-9_]*\})?"  # the file descriptor before an operator: `2>`, `{fd}>` (bash 4.1+)
REDIRECTION = re.compile(FD + r"[<>&\x02]")  # the start of a redirection word (`>/dev/null`, `2>&1`, `<file`, `{fd}>log`)
# an operator whose file is the next word: `2> log`, `<<< hi`, `>| log`
LONE_OPERATOR = re.compile(FD + r"(<|>|>>|<>|<<<|>[|\x05]|[&\x02]>|[&\x02]>>|<[&\x02]|>[&\x02])")
# a paren in quotes or after a backslash is a word's letter, never a subshell's: shlex takes the
# quotes away, so pipelines() marks such a paren first and gives it back in the word
LITERAL_PARENS = {"(": "\x03", ")": "\x04"}
LETTERS = str.maketrans({mark: paren for paren, mark in LITERAL_PARENS.items()})
HEREDOC = "\x01"  # after a heredoc's `<<WORD`, wraps the number of its body in the list that heredocs() returns
# A wrapper runs the command after it; the options named here take a value, which is skipped too
WRAPPERS: dict[str, set[str]] = {
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U"},
    "env": {"-u", "-C", "-S"},
    "nohup": set(), "exec": set(), "command": set(), "builtin": set(), "time": set(), "caffeinate": set(),
    "nice": {"-n"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "xargs": {"-n", "-I", "-L", "-P", "-d", "-E", "-s"},
}
# A keyword that can start a simple command once the line is split at `;`, `&&`, `|`, ...: the program comes after it
KEYWORDS = {"if", "then", "elif", "else", "fi", "while", "until", "for", "select", "do", "done", "case", "esac", "!", "{",
            "}", "time", "(", ")"}
# Each closer ends the body of one of its openers; pipelines() keeps a subshell's `(` and `)` as words of their own
CLOSERS = {"done": {"while", "until", "for", "select"}, "}": {"{"}, "fi": {"if"}, "esac": {"case"}, ")": {"("}}
OPENERS: set[str] = set().union(*CLOSERS.values())
Heredocs = tuple[list[str], set[int]]  # the bodies of a line's heredocs, and the numbers of the literal ones
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
GROUP = re.compile(r"^-(\d+|\$.*|`.*)$")  # -1234, -1, -$pid, -$(cat pid): a process group, or every process


def heredocs(command: str, outer: Heredocs | None = None) -> tuple[str, list[str], list[str], set[int]]:
    """The command without the bodies of its heredocs, the command substitutions of each body whose delimiter is not
    quoted, every body, and the numbers of the bodies whose delimiter is quoted (literal text). The bodies and
    numbers of `outer` (what an outer read found) come first, this command's after them. Each `<<WORD` that stays in the
    command carries the number of its body in that list between two HEREDOC marks: refused() reads the body as a script
    when a shell takes it as its input, also inside a substitution's script, which keeps the outer marks.
    The shell runs no line of a body: it is the input of a command (a review finding: a pull request's
    text in `<<'EOF'` named `` `pkill node` `` and was refused). With a quoted delimiter (`<<'EOF'`, `<<"EOF"`, `<<\\EOF`)
    the body is literal; else only its `$(...)` and backticks run. A `<<` in quotes, in a comment, in `$((...))` or in
    `((...))` starts no heredoc, and `<<<` is a here-string."""
    kept: list[str] = []
    scripts: list[str] = []
    pending: list[tuple[str, bool, bool, int]] = []  # the line's heredocs: delimiter, quoted, `<<-`, the body's number
    bodies = list(outer[0]) if outer else []
    literal = set(outer[1]) if outer else set()
    frames: list[list[str | int]] = [["", "", 0]]  # each level: its open quote, its kind ("", "$(", "`", "(("), its parens
    starts = True  # the next character starts a word
    at = 0
    while at < len(command):
        char = command[at]
        pair = command[at:at + 2]
        frame = frames[-1]
        quote, kind = frame[0], frame[1]
        step = 1
        if quote == "'":
            frame[0] = "" if char == "'" else quote
        elif char == "\\":
            step = 2
        elif kind == "((":  # arithmetic: `<<` is a shift
            if pair == "))" and not frame[2]:
                frames.pop()
                step = 2
            elif char in "()":
                frame[2] = int(frame[2]) + (1 if char == "(" else -1)
        elif pair == "$(" or (pair == "((" and starts and not quote):
            arithmetic = command[at:at + 3] == "$((" or pair == "(("
            step = (3 if char == "$" else 2) if arithmetic else 2
            frames.append(["", "((" if arithmetic else "$(", 0])
        elif char == "`":
            if kind == "`":
                frames.pop()
            else:
                frames.append(["", "`", 0])
        elif quote == '"':
            frame[0] = "" if char == '"' else quote
        elif char in "'\"":
            frame[0] = char
        elif char == "#" and starts:  # a comment: a `<<` in it starts no heredoc
            end = command.find("\n", at)
            at = len(command) if end < 0 else end
            continue
        elif char == "(":
            frame[2] = int(frame[2]) + 1
        elif char == ")":
            if kind == "$(" and not frame[2]:
                frames.pop()
            else:
                frame[2] = int(frame[2]) - 1
        elif command[at:at + 3] == "<<<":  # a here-string: its word is an ordinary word
            step = 3
        elif pair == "<<":
            at, strip = at + 2, command[at + 2:at + 3] == "-"
            at += strip
            while command[at:at + 1] in (" ", "\t"):
                at += 1
            end, open_quote = at, ""
            while end < len(command) and (open_quote or not (command[end].isspace() or command[end] in ";&|()<>")):
                if command[end] == "\\" and not open_quote:
                    end += 1
                elif command[end] == open_quote or (not open_quote and command[end] in "'\""):
                    open_quote = "" if open_quote else command[end]
                end += 1
            word = command[at:end]
            if HEREDOC in word:
                # an outer heredocs() took this body out already: in a substitution's script read again, the mark stays
                # and starts no new heredoc, so the lines after it are commands
                kept.append("<<" + word)
            elif word:
                pending.append((re.sub(r"\\(.)|['\"]", r"\1", word), any(c in word for c in "'\"\\"), strip, len(bodies)))
                bodies.append("")
                kept.append("<<" + word + HEREDOC + str(len(bodies) - 1) + HEREDOC)
            else:
                kept.append("<<")
            starts, at = False, end
            continue
        elif char == "\n" and pending:  # the bodies start on the next line, one after another
            kept.append(char)
            at += 1
            for delimiter, quoted, strip, number in pending:
                body: list[str] = []
                while at < len(command):
                    end = command.find("\n", at)
                    end = len(command) if end < 0 else end
                    line, at = command[at:end], end + 1
                    if (line.lstrip("\t") if strip else line) == delimiter:
                        break
                    body.append(line)
                bodies[number] = "\n".join(body)
                if quoted:
                    literal.add(number)
                if not quoted:
                    scripts.extend(_body_scripts(bodies[number]))
            pending.clear()
            starts = True
            continue
        kept.append(command[at:at + step])
        last = command[at + step - 1:at + step]
        starts = frames[-1][0] == "" and char != "\\" and (last.isspace() or last in ";&|(")
        at += step
    return "".join(kept), scripts, bodies, literal


def _body_scripts(body: str) -> list[str]:
    """The scripts of the command substitutions in the body of a heredoc whose delimiter is not quoted. A quote there is a
    character of the text: only `\\`, `$(` and backticks count."""
    scripts: list[str] = []
    at = 0
    while at < len(body):
        if body[at] == "\\":
            at += 2
        elif body[at:at + 2] == "$(" or body[at] == "`":
            start = at + (1 if body[at] == "`" else 2)
            stop, _ = _scan(body, start, "`" if body[at] == "`" else ")", scripts)
            scripts.append(body[start:stop])
            at = stop + 1
        else:
            at += 1
    return scripts


def uncommented(command: str) -> str:
    """The command without its comments, each line end kept: as in the shell, a comment starts at an unquoted `#` at the
    start of a word and ends at its line end. shlex alone would also start one inside a word (`a#b`, a URL's `#top`), and
    once the line ends are separators a comment would hide every line after it. A word goes on after
    the `)` of `$(...)`, `<(...)` and `>(...)` (`$(date)#x` is one word) and across a line continuation, which the shell
    removes; after the `)` of a subshell a new word starts."""
    kept: list[str] = []
    quote = ""
    starts = True  # the next character starts a word
    parens: list[bool] = []  # the open parentheses outside quotes: True for `$(`, `<(`, `>(`; False for a subshell's `(`
    at = 0
    while at < len(command):
        char = command[at]
        if quote == "'":
            quote = "" if char == "'" else quote
        elif char == "\\":
            if command[at + 1:at + 2] != "\n":  # a line continuation goes, as in the shell
                kept.append(command[at:at + 2])
                starts = False
            at += 2
            continue
        elif quote == '"':
            quote = "" if char == '"' else quote
        elif char in "'\"":
            quote = char
        elif char == "#" and starts:
            end = command.find("\n", at)
            at = len(command) if end < 0 else end
            continue
        elif char in "$<>" and command[at + 1:at + 2] == "(":  # `$(...)`, `<(...)`, `>(...)`: a word goes on after the `)`
            kept.append(char + "(")
            parens.append(True)
            starts = True
            at += 2
            continue
        elif char == "(":
            parens.append(False)
        elif char == ")":
            starts = not (parens.pop() if parens else False)
            kept.append(char)
            at += 1
            continue
        kept.append(char)
        starts = quote == "" and (char.isspace() or char in ";&|(")
        at += 1
    return "".join(kept)


def substitutions(command: str) -> tuple[str, list[str]]:
    """The command with each command substitution outside single quotes (`$(...)`, backticks, `<(...)`, `>(...)`) replaced
    by a mark, and the scripts of those substitutions. The shell runs each script as a command of its own: `x="$(killall
    node)"` and `` echo `pkill node` `` stop processes. A script is taken whole; the substitutions inside
    it are read when the script is read. An arithmetic `$((...))` is read as a script too: it names no program."""
    scripts: list[str] = []
    _, masked = _scan(command, 0, "", scripts)
    return masked, scripts


def _scan(text: str, at: int, closer: str, scripts: list[str]) -> tuple[int, str]:
    """Reads `text` from `at` up to the unquoted `closer` (`)` or a backtick; "" for the end), and returns where it stopped
    and what it read, each substitution replaced by its mark."""
    kept: list[str] = []
    quote = ""
    depth = 0  # the subshell parentheses open inside a `$(...)`
    while at < len(text):
        char = text[at]
        pair = text[at:at + 2]
        if quote == "'":
            quote = "" if char == "'" else quote
        elif char == "\\":
            kept.append(pair)
            at += 2
            continue
        elif char == "`" and closer == "`":
            return at, "".join(kept)
        elif pair == "$(" or (pair in ("<(", ">(") and not quote) or char == "`":
            start = at + (1 if char == "`" else 2)
            stop, _ = _scan(text, start, "`" if char == "`" else ")", scripts)
            scripts.append(text[start:stop])
            kept.append(char + MARK + str(len(scripts) - 1) + MARK)  # the `$`, `<`, `>` or backtick stays: GROUP reads it
            at = stop + 1
            continue
        elif quote == '"':
            quote = "" if char == '"' else quote
        elif char in "'\"":
            quote = char
        elif char == "(" and closer == ")":
            depth += 1
        elif char == ")" and closer == ")":
            if not depth:
                return at, "".join(kept)
            depth -= 1
        kept.append(char)
        at += 1
    return at, "".join(kept)


def literal_parens(line: str) -> str:
    """The line with each paren that the shell reads as a letter (in single or double quotes, or after a backslash) put as
    its LITERAL_PARENS mark, the `|` of `>|` (outside quotes, after a `>` with no backslash) as CLOBBER, and the `&` of a
    redirection (outside quotes and with no backslash: after a bare `<` or `>`, or before a `>` and not after a bare `&` or
    `|`) as AMPERSAND. substitutions() took every `$(...)` out before, so a paren left in quotes is text."""
    out: list[str] = []
    quote = ""
    at = 0
    while at < len(line):
        char = line[at]
        if quote != "'" and char == "\\" and at + 1 < len(line):
            nxt = line[at + 1]
            out.append(char + LITERAL_PARENS.get(nxt, nxt))
            at += 2
            continue
        if char in "'\"" and quote in ("", char):
            quote = "" if quote else char
        elif quote and char in LITERAL_PARENS:
            char = LITERAL_PARENS[char]
        elif not quote and char == "|" and out and out[-1] == ">":
            char = CLOBBER  # the `|` of `>|`; an escaped `\>` is the item `\>`, so a pipe after it stays
        elif not quote and char == "&" and out and out[-1] in ("<", ">"):
            # `2>&1`, `<&3`; after an escaped `\>` or a quoted `'>'` the `&` runs the command before it in the background,
            # so `true \>& pkill x` stays two commands
            char = AMPERSAND
        elif not quote and char == "&" and line[at + 1:at + 2] == ">" and not (out and out[-1] in ("&", "|")):
            char = AMPERSAND  # `&>file`; a quoted or escaped `>` after it is a letter, and `&&>` and `|&>` keep their operator
        out.append(char)
        at += 1
    return "".join(out)


def pipelines(command: str) -> list[list[list[str]]] | None:
    """The pipelines of the line, each as its simple commands, each as its words; None when the line cannot be split."""
    # the `&` of `2>&1`, `<&3` and `&>file` is part of a redirection: as a separator it would cut the pipeline in two, and
    # `pgrep x 2>&1 | xargs kill` would lose what its first command printed
    # literal_parens() marks that `&`, and the `|` of `>|`, which would cut `>| log pkill x` into `>` and `log pkill x`;
    # its scan sees the quotes and the backslashes, so `true \>& pkill x` keeps its separator
    line = literal_parens(command.replace("\n", " ; "))
    lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|()")
    lexer.commenters = ""  # uncommented() took the comments out, with the shell's rule
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    found: list[list[list[str]]] = [[[]]]
    for token in tokens:
        # shlex joins a run of punctuation (`;)`, `)|`): each operator in it counts alone
        for part in re.findall(r";;|&&|\|\||\|&|[;&|()]", token) if not token.strip(";&|()") else [token]:
            if part in ("|", "|&"):
                found[-1].append([])
            elif part == "(":  # a subshell is a body in the pipeline that feeds it: `pgrep x | (read p; kill $p)`
                found[-1].append(["("])
                found[-1].append([])
            elif part == ")":  # what the subshell printed feeds the rest of its pipeline: `(pgrep x) | xargs kill`
                # the words after it are the closer's own redirections, as for `done`: `(read p; kill $p) < <(pgrep x)`
                found.append([[")"]])
            elif part in SEPARATORS:
                found.append([[]])
            else:
                found[-1][-1].append(part.translate(LETTERS))
    return [[simple for simple in pipeline if simple] for pipeline in found if any(pipeline)]


def keywords(simple: list[str]) -> list[str]:
    """The shell's keywords at the start of the simple command: `then pkill x` runs `pkill`. `for` and `select`
    take the rest of the command as their words, `time -p` takes an option."""
    found: list[str] = []
    for word in simple:
        if word not in KEYWORDS:
            break
        found.append(word)
        if word in ("for", "select"):
            break
    return found


def program(simple: list[str]) -> list[str]:
    """The words from the program on: past the shell's keywords, variable assignments, redirections, wrappers and their
    options."""
    skipped = keywords(simple)
    if skipped and skipped[-1] in ("for", "select"):
        return []  # `for p in WORDS`: the words are no command
    at = len(skipped)
    if skipped and skipped[-1] == "time":
        at -= 1  # `time` is a wrapper too: its options are skipped below
    while at < len(simple):
        word = simple[at]
        name = word.rsplit("/", 1)[-1]
        if ASSIGNMENT.match(word):
            at += 1
        elif REDIRECTION.match(word):  # a redirection before the program: `>/dev/null pkill x`, `2> log killall node`
            at += 2 if LONE_OPERATOR.fullmatch(word) else 1
        elif name == "command" and at + 1 < len(simple) and simple[at + 1] in ("-v", "-V"):
            return []  # `command -v NAME` only looks the name up
        elif name in WRAPPERS:
            at += 1
            if name == "timeout":
                while at < len(simple) and simple[at].startswith("-"):
                    at += 2 if simple[at] in WRAPPERS[name] else 1
                at += 1  # the duration
                continue
            while at < len(simple) and (simple[at].startswith("-") or (name == "env" and ASSIGNMENT.match(simple[at]))):
                at += 2 if simple[at] in WRAPPERS[name] else 1
        else:
            return simple[at:]
    return []


def kills_a_group(arguments: list[str]) -> bool:
    """`kill`'s arguments name a process group or every process: a PID that is negative. The first option is the signal
    (`-9`, `-TERM`, `-s TERM`, `-n 9`), so `kill -9 1234` passes and `kill -9 -1234` does not."""
    at = 0
    if at < len(arguments) and arguments[at] in ("-s", "-n"):
        at += 2
    elif at < len(arguments) and arguments[at].startswith("-") and arguments[at] not in ("--", "-l", "-L"):
        at += 1
    pids = [word for word in arguments[at:] if word != "--"]
    if not pids and at == 1:
        pids = arguments[:1]  # `kill -1` with nothing after it: the issue's case, read as the PID -1 (every process)
    return any(GROUP.match(word) for word in pids)


def unmasked(word: str, scripts: list[str]) -> str:
    """The word with each mark of substitutions() turned back into its substitution: the script of `bash -c` and the
    words of `eval` are read again as a line of their own, with their own list of scripts."""
    def back(match: re.Match[str]) -> str:
        script = scripts[int(match.group(2))]
        return f"`{script}`" if match.group(1) == "`" else f"{match.group(1)}({script})"
    return re.sub("([$<>`])" + MARK + r"(\d+)" + MARK, back, word)


def finds_by_name(script: str) -> bool:
    """The script runs `pgrep` or `pidof`: what it prints are the PIDs of processes chosen by name."""
    for pipeline in pipelines(substitutions(uncommented(heredocs(script)[0]))[0]) or []:
        for simple in pipeline:
            run = program(simple)
            if run and run[0].rsplit("/", 1)[-1] in FINDERS:
                return True
    return False


def reads_its_input(run: list[str]) -> bool:
    """The command runs what it reads on its input as a script: a shell with no `-c` script (`bash`, `sh -s`, `bash -`),
    or `source` / `.` of /dev/stdin."""
    name = run[0].rsplit("/", 1)[-1]
    if name in SHELLS:
        options: list[str] = []
        operands: list[str] = []
        words = iter(run[1:])
        for word in words:
            if HEREDOC in word:
                continue
            if REDIRECTION.match(word):  # a redirection; an operator alone (`>`, `2>`) takes the next word as its file
                if LONE_OPERATOR.fullmatch(word):
                    next(words, None)
            elif word.startswith(("-", "+")):
                options.append(word)
                if word in SHELL_VALUE_OPTIONS:
                    next(words, None)
            else:
                operands.append(word)
        return not any(re.fullmatch(r"-[a-z]*c[a-z]*", word) for word in options) and (not operands or "-s" in options)
    return name in ("source", ".") and any(word in ("/dev/stdin", "/dev/fd/0") for word in run[1:2])


def heredoc_marks(simple: list[str], bodies: list[str]) -> list[int]:
    """The numbers of the heredoc bodies that the simple command's words carry (heredocs() marks them)."""
    return [int(n) for word in simple for n in re.findall(HEREDOC + r"(\d+)" + HEREDOC, word) if int(n) < len(bodies)]


class Body:
    """An open body of the line: a loop, a brace group, a subshell, an `if` or a `case`."""

    def __init__(self, opener: str, fed_by_name: bool, prints_by_name: bool, kills: bool) -> None:
        self.opener = opener  # the keyword that opened it; only a closer of its own ends it
        self.fed_by_name = fed_by_name  # what feeds it are PIDs found by name: each `kill` in it kills by name
        self.prints_by_name = prints_by_name  # it prints PIDs found by name: fed by name, or a finder ran in it
        self.kills = kills  # a `kill` of a PID runs in it: a redirection of its closer can feed it PIDs found by name


def refused(command: str, depth: int = 0, outer: Heredocs | None = None) -> bool:
    """The command stops processes by name or a process group. `outer` holds the heredoc bodies of the line that this
    command is a part of: a substitution's script keeps their marks."""
    text, body_scripts, bodies, literal = heredocs(command, outer)
    found = (bodies, literal)
    masked, scripts = substitutions(uncommented(text))
    if depth < DEPTH and any(refused(script, depth + 1, found) for script in scripts + body_scripts):
        return True
    open_bodies: list[Body] = []  # the open loops, groups, subshells, `if` and `case` bodies, innermost last
    for pipeline in pipelines(masked) or []:
        found_by_name = False  # an earlier command of the pipeline prints PIDs found by name: `pgrep x | xargs kill`
        fed: list[int] = []  # the heredocs of the pipeline so far: `cat <<EOF | sh` feeds the body to the shell
        for simple in pipeline:
            marks = [int(n) for w in simple for n in re.findall(MARK + r"(\d+)" + MARK, w) if int(n) < len(scripts)]
            for word in keywords(simple):
                if word in CLOSERS and open_bodies and open_bodies[-1].opener in CLOSERS[word]:
                    body = open_bodies.pop()
                    # `done < <(pgrep x)`, `done <<< "$(pgrep x)"`, `done <<EOF` with `$(pgrep x)` in its body: the
                    # closer's redirection feeds the whole body PIDs found by name
                    heredoc_scripts = [script for n in heredoc_marks(simple, bodies) if n not in literal
                                       for script in _body_scripts(bodies[n])]
                    if any(finds_by_name(script) for script in [scripts[n] for n in marks] + heredoc_scripts):
                        if body.kills:
                            return True
                        body.prints_by_name = True  # `done < <(pgrep x) | xargs kill`
                    if open_bodies:
                        open_bodies[-1].prints_by_name |= body.prints_by_name
                        open_bodies[-1].kills |= body.kills
                    # `{ pgrep x; } | while ...`, `... done | xargs kill`: what the body printed feeds the pipeline
                    found_by_name = found_by_name or body.prints_by_name
                elif word in OPENERS:
                    # `pgrep x | while read p`, or `for p in $(pgrep x)`: the body kills the PIDs found by name
                    by_name = found_by_name or (word in ("for", "select") and any(finds_by_name(scripts[n]) for n in marks))
                    by_name = by_name or any(body.fed_by_name for body in open_bodies)
                    open_bodies.append(Body(word, by_name, by_name, False))
            found_by_name = found_by_name or any(body.fed_by_name for body in open_bodies)
            fed += heredoc_marks(simple, bodies)
            run = program(simple)
            if not run:
                continue
            name = run[0].rsplit("/", 1)[-1]
            if depth < DEPTH and fed and reads_its_input(run) and any(refused(bodies[n], depth + 1, found) for n in fed):
                return True
            if name in BY_NAME:
                return True
            if name == "kill":
                marks = [int(n) for word in run[1:] for n in re.findall(MARK + r"(\d+)" + MARK, word) if int(n) < len(scripts)]
                if kills_a_group(run[1:]) or found_by_name or any(finds_by_name(scripts[n]) for n in marks):
                    return True
                if open_bodies:
                    open_bodies[-1].kills = True
            if name in FINDERS and open_bodies:
                open_bodies[-1].prints_by_name = True
            found_by_name = found_by_name or name in FINDERS
            if depth >= DEPTH:
                continue
            if name == "eval" and refused(" ".join(unmasked(word, scripts) for word in run[1:]), depth + 1, found):
                return True
            if name in SHELLS:
                # `-c` or joined flags such as `-lc`: the script is the first word after it
                flag = next((i for i, w in enumerate(run[1:-1], 1) if re.fullmatch(r"-[a-z]*c[a-z]*", w)), None)
                if flag is not None and refused(unmasked(run[flag + 1], scripts), depth + 1, found):
                    return True
    return False


def check(command: str) -> str | None:
    """The guard's answer for one shell command: None allows it, a text refuses it with that text."""
    return MESSAGE if refused(command) else None
