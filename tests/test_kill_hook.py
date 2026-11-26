"""The kill guard (.agents/guards/kill_by_pid.py), read through the guards' contract: a kill by name or of a process group is refused, wherever
it stands in the command line (also in a command substitution, `eval` or after a shell keyword), and so is a kill of the PIDs that `pgrep` or
`pidof` found; a kill by PID, and a command that only names `pkill`, pass. Other runs share the agents' user."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / ".agents" / "guards" / "guard.py"


def answer(action: str, python: str = sys.executable) -> dict[str, object]:
    done = subprocess.run([python, str(GUARD)], input=action, capture_output=True, text=True, check=True)
    reply: dict[str, object] = json.loads(done.stdout)
    return reply


def guard(command: str) -> dict[str, object]:
    return answer(json.dumps({"action": "shell", "command": command, "cwd": "/tmp"}))


@pytest.mark.parametrize("command", [
    'pkill -f "code-changed.sh"',  # a critique session's command
    "killall node",
    "/usr/bin/pkill vite",
    "cd web && pkill -f playwright",
    "true; killall chrome",
    "ps aux | grep vite | pkill -f vite",
    "sudo pkill node",
    "sudo -u agents killall node",
    "FOO=1 nohup pkill x",
    "timeout 5 pkill -f x",
    "kill -1",
    "kill -9 -1",
    "kill -- -1234",
    "kill -TERM -4321",
    "kill -s TERM -$pgid",
    "kill -9 -$(cat server.pgid)",
    "(sleep 1; kill -9 -42) &",
    'bash -c "pkill -f server"',
    "sh -lc 'killall node'",
    "echo ok\npkill node",
    # a comment ends at its line end, and a `#` inside a word starts none
    "# stop the dev server\npkill -f vite",
    "echo a#b; pkill node",
    "curl -s http://localhost:8000/#top && killall node",
    "echo '# not a comment'\npkill x",
    "pkill -f vite # it's ours",
    "echo ${#arr[@]} $#; kill -9 -1",
    'echo "a \\" # b"; pkill x',
    # a command substitution and `eval` run commands too
    "echo `pkill node`",
    'x="$(killall node)"',
    'eval "pkill x"',
    "eval pkill x",
    "diff <(pkill x) b",
    "echo $(echo $(killall node))",
    'bash -c "echo $(pkill x)"',
    "kill -9 -`cat server.pgid`",
    # the PIDs that pgrep or pidof found are a kill by name
    "kill $(pgrep -f vite)",
    "kill -9 `pgrep node`",
    'kill "$(pidof node)"',
    "pgrep -f vite | xargs kill",
    "pgrep -f vite | grep -v 1 | xargs kill -9",
    "pidof node | xargs kill",
    'bash -c "kill $(pgrep x)"',
    "sh -c 'kill $(pgrep x)'",
    # a `#` after the `)` of `$(...)` and after a line continuation is inside a word
    "echo $(date)#x; pkill node",
    "echo a\\\n#x; pkill y",
    "echo $((1+2))#x; pkill y",
    "cat <(true)#x; pkill y",
    "tee >(cat)#x; pkill y",
    # a heredoc's body is no command, but the substitutions of an unquoted one run, and the lines after it are read
    "cat <<EOF\n$(pkill node)\nEOF",
    "cat <<EOF\n`killall node`\nEOF",
    "cat <<EOF\ndon't $(pkill x)\nEOF",
    "cat <<-EOF\n\t$(pkill x)\n\tEOF\nls",
    "cat <<A <<'B'\n$(pkill x)\nA\ntext\nB",
    "cat <<'EOF'\ntext\nEOF\npkill node",
    "cat <<EOF\ndon't\nEOF\npkill node",
    "cat <<EOF | grep x\nok\nEOF\nkillall node",
    "x=\"$(cat <<'EOF'\nok\nEOF\n)\"; pkill node",
    "ls # <<EOF\npkill node",
    "echo $((1 << 2))\npkill node",
    "(( x <<= 1 ))\npkill node",
    # a here-string `<<<` starts no heredoc: the next line is read
    "cat <<<'x'\npkill node",
    'grep x <<< "$out"\nkillall node',
    # a shell runs the lines of the heredoc it reads as its script, quoted delimiter or not
    "bash <<EOF\npkill -f vite\nEOF",
    "sh <<'EOF'\nkillall node\nEOF",
    "zsh -s <<\"EOF\"\necho ok\nkill -9 -1\nEOF",
    "/bin/bash -l <<-EOF\n\tpkill x\n\tEOF",
    "cat <<'EOF' | sh\npkill node\nEOF",
    "source /dev/stdin <<'EOF'\nkillall node\nEOF",
    ". /dev/stdin <<EOF\npkill x\nEOF",
    "bash <<'EOF'\nkill $(pgrep -f vite)\nEOF",
    "cat <<'A' && bash <<'B'\ntext pkill\nA\npkill x\nB",
    # a redirection or an option's value after the heredoc is no script file
    "bash <<'EOF' 2>&1\npkill x\nEOF",
    "bash <<'EOF' > out.log\npkill x\nEOF",
    "bash <<'EOF' >>out.log 2>/dev/null\npkill x\nEOF",
    "bash -o pipefail <<'EOF'\npkill x\nEOF",
    "bash --rcfile rc <<'EOF'\nkillall node\nEOF",
    "if true; then bash <<'EOF'\npkill x\nEOF\nfi",  # a keyword before the shell
    # a heredoc inside a substitution: the lines after its body are commands
    "x=$(cat <<'EOF'\nhello\nEOF\npkill node\n)",
    'x="$(cat <<EOF\nhello\nEOF\nkillall node\n)"',
    "echo `cat <<'EOF'\nhello\nEOF\npkill node\n`",
    # a shell's heredoc inside a substitution, and the `&` of a redirection that is no separator
    "out=$(bash <<'EOF'\npkill x\nEOF\n)",
    'out="$(sh <<EOF\nkillall node\nEOF\n)"',
    "echo `bash <<'EOF'\npkill x\nEOF\n`",
    "x=$(cat <<'A' | sh\npkill x\nA\n)",
    "cat <<'EOF' 2>&1 | sh\npkill x\nEOF",
    "pgrep vite 2>&1 | xargs kill",
    "pgrep vite &>/dev/null | xargs kill",
    "pgrep vite 2>&1 | while read p; do kill $p; done",
    "bash <<'EOF' >&2\npkill x\nEOF",
    "bash 2>& 1 <<'EOF'\npkill x\nEOF",
    "bash &> /dev/null <<'EOF'\npkill x\nEOF",
    "bash >& /dev/null <<'EOF'\npkill x\nEOF",
    # a shell keyword or a brace before the program
    "if pgrep -f vite >/dev/null; then pkill -f vite; fi",
    'for p in 5173 8000; do pkill -f "port $p"; done',
    "cd web && { pkill -f playwright; npm test; }",
    "if true; then kill -9 -1; fi",
    "pgrep -f vite | while read p; do kill $p; done",
    "! pkill -f vite",
    "while true; do killall node; done",
    "if ! killall node; then echo gone; fi",
    "true\nelse pkill x",
    "until false; do time -p pkill x; done",
    "pgrep x | while read p; do if true; then kill $p; fi; done",
    "pgrep x | { read p; kill $p; }",
    "for p in $(pgrep -f vite); do kill $p; done",
    "for p in `pidof node`; do kill -9 $p; done",
    "pkill -f vite",
    "(pkill -f vite)",
    "true && pkill -f vite",
    # what a body that pgrep fed or that ran pgrep prints, and a body fed at its closer
    "{ pgrep -f vite; } | while read p; do kill $p; done",
    "(pgrep -f vite) | xargs kill",
    "pgrep x | while read p; do echo $p; done | xargs kill",
    "pgrep x | (read p; kill $p)",
    "pgrep x | if read p; then kill $p; fi",
    "pgrep x | case 1 in 1) read p; kill $p;; esac",
    "while read p; do kill $p; done < <(pgrep x)",
    'while read p; do kill $p; done <<< "$(pgrep x)"',
    "while read p; do if true; then kill $p; fi; done < <(pidof node)",
    "(pgrep x;) | xargs kill",
    # a body fed at its closer prints PIDs found by name, and a heredoc on the closer feeds it
    "while read p; do echo $p; done < <(pgrep x) | xargs kill",
    "{ cat; } < <(pgrep x) | xargs kill",
    "while read p; do kill $p; done <<EOF\n$(pgrep x)\nEOF",
    "while read p; do echo $p; done <<EOF | xargs kill\n`pidof node`\nEOF",
    # a subshell fed at its `)`, and a quoted paren that is no subshell's
    "(read p; kill $p) < <(pgrep x)",
    "(cat) < <(pgrep x) | xargs kill",
    "(read p; kill $p) <<EOF\n$(pgrep x)\nEOF",
    'pgrep x | (read p; echo ")"; kill $p)',
    "pgrep x | (read p; echo ')' \\); kill $p)",
    # a redirection written before the program
    ">/dev/null pkill x",
    "2>/dev/null killall node",
    "2> log pkill x",
    "&>/dev/null killall node",
    "2>&1 >log FOO=1 pkill x",
    "< /dev/null kill -9 -1234",
    # a here-string, `>|` and a named file descriptor before the program
    "<<< hi pkill x",
    ">| log pkill x",
    ">|log killall node",
    "{fd}>log pkill x",
    "{fd}> log pkill x",
    "pgrep -f vite\\>| xargs kill",  # an escaped `\>`: the `|` after it is a pipe
    "pgrep -f node'|'x\\>| xargs kill",
    # an escaped or quoted `>` before `&`: the `&` runs the command before it in the background
    "true \\>& pkill x",
    "true '>'& pkill x",
    'true ">"& killall node',
    "cat \\<& kill -9 -1234",
])
def test_a_kill_by_name_or_of_a_group_is_refused(command: str) -> None:
    done = guard(command)
    assert done["allow"] is False, command
    message = str(done["message"])
    assert "by their PID" in message and "agents/implementer.md" in message


@pytest.mark.parametrize("command", [
    "kill 1234",
    "kill -9 1234",
    "kill -TERM 1234 5678",
    "kill -s TERM 1234",
    "kill $pid",
    "kill -0 $pid && echo alive",
    "sudo kill 1234",
    "command -v pkill",
    "grep -rn pkill agents/implementer.md",
    "echo 'never pkill or killall'",
    "git log --grep=killall",
    "pgrep -f vite",
    "ls; echo 'unclosed",
    "ls # pkill x",
    "# killall node\nls",
    "(echo a)#x; pkill y",  # after a subshell's `)` the `#` starts a comment
    "echo $(date) # pkill x",
    "echo '$(pkill x)' '`killall y`'",
    'x="$(git rev-parse HEAD)"; echo `date`',
    "kill $(cat server.pid)",
    "kill -9 $(lsof -ti tcp:5173)",
    "pgrep -f vite | wc -l",
    "pgrep -f vite; kill 1234",
    "cat server.pid | xargs kill",
    "eval echo ok",
    "echo $((1 + 2))",
    # the body of a quoted heredoc is literal, and an unquoted one runs only its substitutions
    "gh pr create --body \"$(cat <<'EOF'\nThe hook refuses `pkill node`.\nEOF\n)\"",
    "git commit -F - <<'EOF'\nrefuse `killall node`\nEOF",
    'cat <<"EOF"\n$(pkill x)\nEOF',
    "cat <<\\EOF\n`killall x`\nEOF",
    "cat <<-'EOF'\n\tkillall x\n\tEOF",
    "cat <<EOF\npkill node is a word of the text\nEOF",
    "cat <<EOF\ndon't $(git rev-parse HEAD)\nEOF\nls",
    "cat <<<'pkill x'",
    "echo $((1 << 2)); ls",
    # only a shell that reads the heredoc as its script runs its lines
    "cat <<EOF\npkill -f vite is a word of the text\nEOF",
    "bash <<'EOF'\nkill 1234\nEOF",
    "bash script.sh <<'EOF'\npkill x\nEOF",
    "bash script.sh <<'EOF' > out.log 2>&1\npkill x\nEOF",
    "bash -o pipefail script.sh <<'EOF'\npkill x\nEOF",
    "bash -c 'cat' <<'EOF'\nkillall node\nEOF",
    "cat <<'A' && bash <<'B'\npkill x\nA\nls\nB",
    "cat <<'EOF' | grep pkill\npkill x\nEOF",
    "x=$(cat <<'EOF'\npkill node\nEOF\n)\necho \"$x\"",  # the body inside a substitution is still text
    "out=$(bash <<'EOF'\nkill 1234\nEOF\n)",  # a shell's body inside a substitution, a kill by PID
    # the `&` of a redirection leaves a background `&` a separator
    "ls 2>&1 | grep pkill",
    "pgrep vite >/dev/null 2>&1 & kill 1234",
    "sleep 1 & kill $!",
    "make >log 2>&1 && kill 1234",
    # a keyword is no program, and only a loop that pgrep feeds kills by name
    "if true; then kill 1234; fi",
    "for p in $(cat pids); do kill $p; done",
    "for pkill in a b; do echo $pkill; done",
    "pgrep -f vite | while read p; do echo $p; done; kill 1234",
    "{ pgrep x; }; kill 1234",
    "echo then pkill done",
    "time ls",
    # a body that no finder fed, ran in or fed at its closer
    "case $x in a) kill 1234;; esac; ls",
    "(echo a); kill 1234",
    "(cd web && npm test) | tee log",
    "while read p; do kill $p; done < pids",
    "while read p; do kill $p; done < <(cat pids)",
    "if pgrep x; then kill 1234; fi",
    "while read p; do kill $p; done <<'EOF'\n$(pgrep x)\nEOF",  # a quoted delimiter: the body is text
    "while read p; do kill $p; done <<EOF\n$(cat pids)\nEOF",
    "while read p; do echo $p; done < <(cat pids) | xargs kill",
    # a subshell fed at its `)` by no finder, and a quoted paren that opens no body
    "(read p; kill $p) < <(cat pids)",
    'pgrep x | grep "(" ; kill 1234',
    "pgrep x | grep \\( ; kill 1234",
    'pgrep x | grep "a\\"b(" ; kill 1234',
    ">/dev/null kill 1234",  # a redirection before a kill by PID
    "2> log ls",
    "echo a >| log; kill 1234",  # `>|` cuts no pipeline
    'echo "a>|b" | cat; kill 1234',  # a quoted `>|` is text
    "ls &>log; kill 1234",  # the `&` of a redirection cuts no pipeline
    "ls >&2 | cat; kill 1234",
    'echo "pkill x >&" | cat',  # a quoted `>&` is text
])
def test_a_kill_by_pid_and_other_commands_pass(command: str) -> None:
    assert guard(command) == {"allow": True}, command


def test_an_action_with_no_command_passes() -> None:
    for action in ("not json", json.dumps({"action": "shell"}), json.dumps({"action": "shell", "command": 1}), json.dumps([1]),
                   json.dumps({"action": "run", "command": "pkill x"})):
        assert answer(action) == {"allow": True}, action


def test_the_guard_runs_on_the_macs_own_python() -> None:
    """An adapter runs `python3` from PATH, which can be macOS's 3.9: an annotation 3.9 cannot read broke the hook."""
    if not Path("/usr/bin/python3").exists():
        pytest.skip("platform: no /usr/bin/python3 here")
    assert answer(json.dumps({"action": "shell", "command": "pkill x"}), "/usr/bin/python3")["allow"] is False
