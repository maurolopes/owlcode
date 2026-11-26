# orchestrator/: the service that starts the runs

AGENTS.md points here: read it before you change the orchestrator or its install. The rules a run follows, and the
workflow end to end, are in AGENTS.md "Autonomous runs" and agents/README.md. A product does not copy this folder
(`.workflow/loop-files.txt`): every `owlcode` command runs from the loop's own checkout only.

The orchestrator (agents/README.md "Until an orchestrator exists"): `orchestrator.py`, standard
library, Python 3.11+, names no product (its settings: `orchestrator.example.toml`). The session that opened a pull request
runs its fix round: the orchestrator wakes it with a message when the host shows it at rest, idle or done, a fix that no session
took within `claim_wait_seconds` gets one new session, and an escalation when that one takes it neither. An issue that goes back to Todo while its first session waits idle (it asked the owner) gets one wake
for that session, not a second session beside it; the start goes on when the host finds no session to wake. A setup
failure does not count as a try. It halts all teams' starts and fix escalations. Wakes, closes and queue updates continue.
Preflight must pass before the next launch attempt, unless the host confirms the pending launch. The
owner's pause is a halt that neither one ends: the file `orchestrator.paused` beside the config holds the
starts and the fix escalations (cause `paused by the owner`) until `owlcode resume` removes it. The orchestrator
starts issues from Todo as sessions in a session host (herdr, Paseo, or a plain process: each developer's own choice),
within `max_runs` (one number for all teams: one round reads every team of the config, the first from its own
tables and each later one from a `[[product]]` table with its team, repo and `run_cwd`; `--check`, `--watch` and
the state file show each team apart, the queue issue holds all; each started session gets `PYTEST_XDIST_AUTO_NUM_WORKERS`, the
Mac's CPU count divided by `max_runs` and at least 2, so `pytest -n auto` of the runs together does not overload the Mac, and its run line says `pytest workers N`), none related to an issue that runs and one per Area while
`distinct_areas` is on, a follow-up only when its pull request is no longer open, each on the model and
effort level that the issue's difficulty labels map to (the cells of the inference profile in force, `inference_profile`
and `[profiles.NAME]`; each profile names its harness, whose `[harnesses.NAME]` table holds the prompt
that starts the skill and the words of a stopped turn), and writes its run line on the issue. A cell may name
another `[profiles.NAME]` table as its third field, whose provider and harness then run that cell, so one
profile mixes providers and harnesses; the local review stays the profile in force's. On Paseo the daemon holds the keys of the
profile in force and of each profile that a cell names (`agent_user.py paseo-daemon`, from `inference-profile.json` and the `cells` of
`local-review.json`, which `install` and `restart` write), and the service keeps it so (`daemon_step`): no daemon, it
starts one; a daemon on other profiles, it halts the starts and the fix escalations (`switching to NAME: waiting for N agents`)
until no agent is in a turn, then stops the daemon, starts it again in a session of its own and lifts the halt; a failed start
keeps the halt with its cause and tries again each round; each agent's start command empties the key variables of every other profile of the catalog
(`paseo run --env VAR=`: Claude Code and pi read an empty variable as a missing key, `hidden_keys`), and a stopped agent's log
goes through the `stop_patterns` of its own harness, by the part of Paseo's `provider` of the agent before the first `/`. Each start labels its agent
`profile=NAME` (the cell's profile), and a stop or a failed setup check of one profile holds only the cells on that profile: its stop has its own retry and window, its issues in Todo wait (`its cell runs on NAME, which is paused`), and the
other cells start. A failed check that no profile owns (Linear, GitHub, the service, a Paseo daemon that cannot be read), a stop of
an agent with no `profile` label, a start that failed, and an agent in error before its claim still hold every start. The
setup failure above ("It halts all teams' starts") holds all of them only for such a check.

Each team has its own Linear key: a Linear key reaches one workspace, and a team of another Linear workspace needs
its own. A team names the environment variable of its key with `linear_key_env` (in `[linear]` for the first team, in its
`[[product]]` table for a later one); the default is `LINEAR_API_KEY`, so a config with no such key works as before.
`linear_api` is the one place that builds a Linear client: it hands the Linear module the key of the team that it serves and
no other, so every read, move, comment and run line of a team carries that team's key. The queue comment and the setup notice stay in the first
team's workspace. A key that is not set fails that team's round only (`team_round` catches it as it does a failed read:
`MissingKey` in the log and the state file), and `--check` prints one `keys:` line for each team, never a value. `--check` also warns when the agents' home copy of `agent_user.py` differs from the installed one (a pane's `claude` or `pi` then runs old options): run `owlcode admin`. The service gets each
key besides `LINEAR_API_KEY` from the agents' keychain: the item is the variable in lower case with `-api-key` as `-key`
(`ABC_LINEAR_API_KEY`: `abc-linear-key`; a team of this workspace, OWL included, needs none; until the owner stores the item the lookup fails and that team's key is reported as not set), and `deploy/service.py --env-from` puts it in the
environment inside `agent_user.py run`, which clears the key variables of its caller. Only `install` writes the plist that holds that
lookup: a new `linear_key_env` takes effect after `owlcode install`, never after `owlcode restart` (a restart keeps the
plist, and `install.py check` says so in its FAIL line for that key).

`dry_run` (the default) writes nothing to Linear or GitHub and starts nothing. Each round writes its plan as JSON to
`state_file` (default `~/.local/state/orchestrator/state.json`): a GUI, `--check` and `--watch` read that file,
never the text. The plan holds each open pull request's state, a stale one with its reason, and what waits on the owner with
the reason. A round writes its start time into that file at its beginning, beside the last plan, which stays until the round's own plan replaces it. While the start is newer than the plan and the process that wrote it still runs (the mark holds its id, and a process that started after the mark is not the round: a reboot can give the old id to another process), `--watch` and `--check` show `round running for 6 min (since 16:46)`, so a slow round does not look like a stopped service. The log prints the plan as text (one queue per Area) only when it changed or a round has an
event, and a heartbeat line after 15 quiet minutes. `--watch` draws the plan in place, for a pane on the phone.
With `queue_issue` set it keeps that queue in one comment of a pinned issue, for Linear's app on the phone. It
closes the herdr workspace of a finished run `close_after_hours` (12) after its issue closed. It closes the open
pull request of a Canceled issue `cancel_grace_minutes` (10) after the cancel; of a `hands-off` issue it only
lists it in the plan's row `yours`.

A switch of `session_host` (`owlcode set session_host=herdr`) needs no wait for the agents that run (see
`Hosts`). Each round records each session of the orchestrator's label with its host in the state file (`hosts`), and the
service that the switch restarts reads that record: it makes each old host that a recorded session uses. New sessions start
on the host in force. A wake goes to the host of the key's newest session, a close to the session's own host. An old host
leaves when its list shows no session of the orchestrator's label (its last one closed, `close_after_hours` after its issue
closed). The service never starts or stops an old host: it keeps only the daemon of a Paseo in force. An old host that
does not answer gets no new session, its wake fails as before, and a fix that no session took gets one new session on the
host in force after `claim_wait_seconds`; it leaves after 12 hours with no answer. While two hosts are in use,
`--watch` names each host with the keys of its sessions. The record is not in the run line: the host is each developer's
own view, and Linear is shared.

`deploy/` holds the service. It merges on the verdict, and the owner checks it when it runs: each `owlcode` command
goes through the owner's launcher `.workflow/approved_run.py`, which shows what changed in `install.py`, `service.py`
and `agent_user.py` since the owner's last yes and asks one yes first. The service is a LaunchAgent (root's plist) in the agents' user domain that runs a
copy root owns, never the checkout (after a restart `owlcode unlock` unlocks its keychain in its own
session); `owlcode install` installs it after a dry round of the old and the new code side by side.

**Retired config keys.** A change that retires a config key (the code refuses it, or the example drops it) adds it to `RETIRED` in
`deploy/install.py` (a key or a whole table; a retired value goes into `RENAMED`), and the refusal's message names
`owlcode install` as the fix. The install then removes the key from root's config, shows the edit with the new keys as one diff and
applies it on the same yes. No README line asks the owner to edit the config by hand before an install.
