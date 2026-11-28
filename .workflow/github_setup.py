#!/usr/bin/python3 -I
"""`owlcode github-setup`: a product's GitHub repository set up the way the loop needs it (agents/README.md "Setting up a
product", step 3). One of the owner's Mac tools: it runs from this repository's checkout, never from a product's copy
(`.workflow/loop-files.txt` leaves it out).

    owlcode github-setup OWNER/REPO [--create] [--check NAME]... [--no-checks] [--approvals N] [--dismiss-stale]
                         [--code-owners] [--bypass-admin] [--auto-merge] [--app SLUG]... [--variable NAME=VALUE]...
                         [--dry-run] [--no-browser]

What it does, each step safe to run again:

1. With --create, makes the repository (private) when it does not exist yet.
2. Merges: merge commits only (the commits are the record; no squash, no rebase), head branches deleted after a merge,
   auto-merge off unless --auto-merge (no agent merges by hand: the loop's review workflow turns on auto-merge after an
   approve, so a product on the loop needs --auto-merge).
3. A ruleset "main" on the default branch: a pull request for every change, no deletion, no force-push, merge commits
   as the only method. With --check NAME, those status checks are required. The branch need not be up to date
   (agents/README.md "The guards": pull requests merge in parallel, and nothing merges main into a pull request that is
   only behind). The agents' reviewer gates the merge with a check, not an approval: --check verdict, the check its
   review workflow reports (green on approve). A GitHub App's approve never counts toward --approvals, since GitHub
   counts approvals from accounts with push access only (measured 2026-10-25). With --approvals N the pull request
   needs N approving reviews from people with push access; --dismiss-stale dismisses them on a push; --code-owners
   requires the owners that CODEOWNERS names for the files that shape the gates and the rules. Nobody is on the bypass
   list (so no agent's GitHub App can push to main) unless --bypass-admin puts the repository admin role there: the
   owner can then always merge by hand, recorded as a bypass. Rulesets on a private repository need a paid plan
   (GitHub Pro for a personal account). When the ruleset exists, the script reads it in full and changes only its own
   rules (deletion, non_fast_forward, pull_request, required_status_checks). Without --check, the required checks stay
   as they are; --check replaces them; --no-checks removes them. Other rules and other bypass actors stay as they are,
   and the script names them. It prints each rule and check that it adds or removes. When nothing differs, it prints
   "ruleset: unchanged" and sends nothing.
4. The agents' GitHub Apps, each --app SLUG (the product's implementer and reviewer apps: agents/README.md "Setting up a
   product", step 1). GitHub accepts a change to an app's installation only from a classic personal access token, which
   reaches every repository the owner has. So the script opens each app's installation page instead, where the owner
   ticks the repository once. --no-browser prints the addresses only.
5. With --variable NAME=VALUE, an Actions variable of the repository: made, or changed when its value differs. A variable
   is public, so the script prints it (an app's Client ID, such as `REVIEWER_APP_CLIENT_ID`: an ID is no secret, and a
   secret's value would be masked in every log). Never a secret: the script takes none, and the owner sets one from a
   terminal (agents/README.md "Setting up a product", step 1).

It talks to GitHub through `gh api` (the owner's `gh auth login`), so it holds no token of its own. --dry-run reads the
current state and prints what it would change, and changes nothing. Standard library only; Python 3.9
(`/usr/bin/python3`) and newer.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import webbrowser
from typing import Any

RULESET = "main"

ADMIN = {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}  # the repository admin role: the owner


def merges(auto_merge: bool) -> dict[str, Any]:
    return {
        "allow_merge_commit": True,
        "allow_squash_merge": False,
        "allow_rebase_merge": False,
        "delete_branch_on_merge": True,
        "allow_auto_merge": auto_merge,
    }


class Failed(Exception):
    """A step that cannot go on: the message says why and what to do."""


def gh(*args: str, body: dict[str, Any] | None = None, allow_404: bool = False) -> Any:
    """`gh api` with a JSON body on stdin; the parsed reply, or None for an empty one (or a 404 when allowed)."""
    cmd = ["gh", "api", *args]
    if body is not None:
        cmd += ["--input", "-"]
    done = subprocess.run(cmd, input=json.dumps(body) if body is not None else None, capture_output=True, text=True, check=False)
    if done.returncode:
        said = (done.stdout or done.stderr).strip()
        if allow_404 and '"status":"404"' in said.replace(" ", ""):
            return None
        if "ruleset" in " ".join(args) and "upgrade" in said.lower():
            raise Failed("GitHub refused the ruleset: rulesets on a private repository need a paid plan (GitHub Pro).")
        raise Failed(f"gh api {' '.join(args)} failed: {said[:400]}")
    return json.loads(done.stdout) if done.stdout.strip() else None


def ruleset(checks: list[str], approvals: int = 0, dismiss_stale: bool = False, code_owners: bool = False,
            bypass_admin: bool = False) -> dict[str, Any]:
    rules: list[dict[str, Any]] = [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {"type": "pull_request", "parameters": {
            "required_approving_review_count": approvals,  # 0: the owner merges their own; an app's approve never counts toward N
            "dismiss_stale_reviews_on_push": dismiss_stale,
            "require_code_owner_review": code_owners,
            "require_last_push_approval": False,
            "required_review_thread_resolution": False,
            "allowed_merge_methods": ["merge"],
        }},
    ]
    if checks:
        rules.append({"type": "required_status_checks", "parameters": {
            "strict_required_status_checks_policy": False,  # no up-to-date branch: pull requests merge in parallel
            "do_not_enforce_on_create": False,
            "required_status_checks": [{"context": c} for c in checks],
        }})
    return {"name": RULESET, "target": "branch", "enforcement": "active", "bypass_actors": [dict(ADMIN)] if bypass_admin else [],
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}}, "rules": rules}


def step_create(repo: str, create: bool, dry: bool) -> dict[str, Any] | None:
    info = gh(f"repos/{repo}", allow_404=True)
    if info is not None:
        print(f"repository: {repo} exists ({'private' if info.get('private') else 'PUBLIC'})")
        return dict(info)
    if not create:
        raise Failed(f"{repo} does not exist (or gh cannot see it): --create makes it, private")
    print(f"repository: {repo} does not exist; {'would create' if dry else 'creating'} it, private")
    if dry:
        return None
    done = subprocess.run(["gh", "repo", "create", repo, "--private"], capture_output=True, text=True, check=False)
    if done.returncode:
        raise Failed(f"gh repo create failed: {(done.stderr or done.stdout).strip()[:400]}")
    return dict(gh(f"repos/{repo}"))


def step_merges(repo: str, info: dict[str, Any] | None, auto_merge: bool, dry: bool) -> None:
    current = info or {}
    change = {k: v for k, v in merges(auto_merge).items() if current.get(k) != v}
    if not change:
        print(f"merges: already merge commits only, branches deleted after a merge, auto-merge {'allowed' if auto_merge else 'off'}")
        return
    for k, v in change.items():
        print(f"merges: {k} {current.get(k)} -> {v}")
    if not dry:
        gh(f"repos/{repo}", "--method", "PATCH", body=change)


MANAGED = ("deletion", "non_fast_forward", "pull_request", "required_status_checks")  # the rule types this script sets
CHECKS = "required_status_checks"


def _rule(rules: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    return next((r for r in rules if r.get("type") == kind), None)


def _checks(rules: list[dict[str, Any]]) -> list[str]:
    rule = _rule(rules, CHECKS)
    if rule is None:
        return []
    return [str(c.get("context")) for c in rule.get("parameters", {}).get("required_status_checks", [])]


def merge_ruleset(current: dict[str, Any] | None, checks: list[str], no_checks: bool, approvals: int = 0,
                  dismiss_stale: bool = False, code_owners: bool = False, bypass_admin: bool = False) -> dict[str, Any]:
    """The ruleset to send: a new one from `ruleset`, or `current` with only this script's rules set.

    Without `checks` and without `no_checks`, the current required checks stay. `checks` replaces them; `no_checks`
    removes the rule. A rule type outside MANAGED, other bypass actors and the extra branch patterns stay as they are;
    `bypass_admin` adds the repository admin role to the bypass list (and never removes it: the owner does that by hand).
    """
    wanted = ruleset(checks, approvals, dismiss_stale, code_owners, bypass_admin)
    if current is None:
        return wanted
    ours = wanted["rules"]
    rules: list[dict[str, Any]] = []
    for rule in current.get("rules", []):
        kind = rule.get("type")
        if kind not in MANAGED:
            rules.append(rule)  # the owner's own rule: kept as it is
            continue
        if kind == CHECKS:
            if no_checks:
                continue
            if not checks:
                rules.append(rule)  # no --check: the current checks stay
                continue
        want = _rule(ours, str(kind))
        if want is None:
            continue
        # Keep the parameters this script does not set (GitHub adds new ones over time).
        params = {**rule.get("parameters", {}), **want.get("parameters", {})}
        if kind == CHECKS:
            known = {c.get("context"): c for c in rule.get("parameters", {}).get("required_status_checks", [])}
            params["required_status_checks"] = [known.get(c, {"context": c}) for c in checks]
        rules.append({"type": kind, "parameters": params} if params else {"type": kind})
    have = {r.get("type") for r in rules}
    rules += [r for r in ours if r["type"] not in have and not (r["type"] == CHECKS and no_checks)]
    ref = current.get("conditions", {}).get("ref_name", {})
    include = list(ref.get("include", []))
    if "~DEFAULT_BRANCH" not in include:
        include.append("~DEFAULT_BRANCH")
    bypass = list(current.get("bypass_actors", []))
    if bypass_admin and not any(a.get("actor_type") == ADMIN["actor_type"] and a.get("actor_id") == ADMIN["actor_id"] for a in bypass):
        bypass.append(dict(ADMIN))
    return {"name": RULESET, "target": "branch", "enforcement": "active",
            "bypass_actors": bypass,
            "conditions": {"ref_name": {"include": include, "exclude": list(ref.get("exclude", []))}}, "rules": rules}


def _canon(rule: dict[str, Any]) -> str:
    return json.dumps(rule, sort_keys=True)


def ruleset_changes(current: dict[str, Any] | None, wanted: dict[str, Any]) -> list[str]:
    """One line for each difference between the current ruleset and the wanted one; empty when they are the same."""
    cur = current or {"rules": [], "bypass_actors": [], "conditions": {}, "enforcement": None}
    lines: list[str] = []
    if current is not None and cur.get("enforcement") != wanted["enforcement"]:
        lines.append(f"enforcement {cur.get('enforcement')} -> {wanted['enforcement']}")
    if current is not None and cur.get("conditions", {}).get("ref_name") != wanted["conditions"]["ref_name"]:
        lines.append(f"branches {cur.get('conditions', {}).get('ref_name')} -> {wanted['conditions']['ref_name']}")
    old_rules, new_rules = list(cur.get("rules", [])), list(wanted["rules"])
    for kind in dict.fromkeys([str(r.get("type")) for r in old_rules + new_rules]):
        before = sorted(_canon(r) for r in old_rules if r.get("type") == kind)
        after = sorted(_canon(r) for r in new_rules if r.get("type") == kind)
        if before == after:
            continue
        if not before:
            lines.append(f"adds rule {kind}")
        elif not after:
            lines.append(f"removes rule {kind}")
        elif kind != CHECKS:
            lines.append(f"changes rule {kind}")
    old_checks, new_checks = _checks(old_rules), _checks(new_rules)
    lines += [f"adds check {c}" for c in new_checks if c not in old_checks]
    lines += [f"removes check {c}" for c in old_checks if c not in new_checks]
    if _rule(old_rules, CHECKS) and _rule(new_rules, CHECKS) and old_checks == new_checks and \
            _canon(_rule(old_rules, CHECKS) or {}) != _canon(_rule(new_rules, CHECKS) or {}):
        lines.append(f"changes rule {CHECKS}")
    if sorted(map(_canon, cur.get("bypass_actors", []))) != sorted(map(_canon, wanted["bypass_actors"])):
        lines.append("changes the bypass list")
    return lines


def kept_by_owner(wanted: dict[str, Any]) -> list[str]:
    """The parts of the ruleset that this script does not manage and keeps as they are."""
    lines = [f"keeps rule {r.get('type')} (not managed by this script)" for r in wanted["rules"] if r.get("type") not in MANAGED]
    others = [a for a in wanted["bypass_actors"] if (a.get("actor_type"), a.get("actor_id")) != (ADMIN["actor_type"], ADMIN["actor_id"])]
    if others:
        who = ", ".join(f"{a.get('actor_type')} {a.get('actor_id')}" for a in others)
        lines.append(f"keeps the bypass list ({who}; not managed by this script)")
    return lines


def step_ruleset(repo: str, checks: list[str], no_checks: bool, dry: bool, approvals: int = 0, dismiss_stale: bool = False,
                 code_owners: bool = False, bypass_admin: bool = False) -> None:
    listed = gh(f"repos/{repo}/rulesets", allow_404=True) or []
    same = next((r for r in listed if r.get("name") == RULESET), None)
    current = dict(gh(f"repos/{repo}/rulesets/{same['id']}")) if same else None  # the list holds no rules
    wanted = merge_ruleset(current, checks, no_checks, approvals, dismiss_stale, code_owners, bypass_admin)
    for line in kept_by_owner(wanted):
        print(f"ruleset: {line}")
    changes = ruleset_changes(current, wanted)
    if not changes:
        print(f"ruleset: unchanged (required checks: {', '.join(_checks(wanted['rules'])) or 'none'})")
        return
    verb = ("would update" if dry else "updating") if current else ("would create" if dry else "creating")
    print(f"ruleset: {verb} '{RULESET}'")
    for line in changes:
        print(f"ruleset:   {line}")
    if dry:
        return
    if current:
        gh(f"repos/{repo}/rulesets/{current['id']}", "--method", "PUT", body=wanted)
    else:
        gh(f"repos/{repo}/rulesets", "--method", "POST", body=wanted)
    if checks:
        print("ruleset: a required check that no workflow reports leaves every pull request waiting; add the workflow first")


def step_variables(repo: str, variables: list[tuple[str, str]], dry: bool) -> None:
    """Each --variable NAME=VALUE as an Actions variable of the repository: made, changed when its value differs, else left.
    A variable is public, so its value is printed; a secret never passes through here."""
    for name, value in variables:
        current = gh(f"repos/{repo}/actions/variables/{name}", allow_404=True)
        if current is None:
            print(f"variable {name}: new, {value}")
            if not dry:
                gh(f"repos/{repo}/actions/variables", "--method", "POST", body={"name": name, "value": value})
        elif current.get("value") != value:
            print(f"variable {name}: {current.get('value')} -> {value}")
            if not dry:
                gh(f"repos/{repo}/actions/variables/{name}", "--method", "PATCH", body={"name": name, "value": value})
        else:
            print(f"variable {name}: already {value}")


def step_apps(repo: str, apps: list[str], browser: bool, dry: bool) -> None:
    name = repo.split("/", 1)[1]
    if not apps:
        print("apps: none named (--app SLUG opens the installation page of each app, to tick the repository)")
    for slug in apps:
        url = f"https://github.com/apps/{slug}/installations/new"
        print(f"app {slug}: tick '{name}' under 'Only select repositories' and save: {url}")
        if browser and not dry:
            webbrowser.open(url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("repo", help="OWNER/REPO")
    parser.add_argument("--create", action="store_true", help="make the repository, private, when it does not exist")
    parser.add_argument("--check", action="append", default=[], metavar="NAME",
                        help="a required status check (repeat); replaces the ruleset's current checks. Without it they stay")
    parser.add_argument("--no-checks", action="store_true", help="remove the ruleset's required status checks")
    parser.add_argument("--approvals", type=int, default=0, metavar="N", help="approving reviews a pull request needs, from people with push access (default 0); "
                             "a GitHub App's approve never counts: gate an agent reviewer with --check verdict")
    parser.add_argument("--dismiss-stale", action="store_true", help="a push dismisses the approvals of --approvals")
    parser.add_argument("--code-owners", action="store_true", help="a review from the code owners (CODEOWNERS) is required")
    parser.add_argument("--bypass-admin", action="store_true", help="the repository admin role on the bypass list: the owner can always merge")
    parser.add_argument("--auto-merge", action="store_true", help="allow auto-merge in the repository (a tool may queue a merge)")
    parser.add_argument("--app", action="append", default=[], metavar="SLUG",
                        help="a GitHub App to install on the repository (repeat): the product's implementer and reviewer apps")
    parser.add_argument("--variable", action="append", default=[], metavar="NAME=VALUE",
                        help="an Actions variable of the repository (repeat): a public value such as an app's Client ID, never a secret")
    parser.add_argument("--dry-run", action="store_true", help="read the state and print the changes; change nothing")
    parser.add_argument("--no-browser", action="store_true", help="print the apps' addresses instead of opening them")
    args = parser.parse_args(argv)
    if args.repo.count("/") != 1:
        parser.error("REPO is OWNER/REPO")
    if args.check and args.no_checks:
        parser.error("--check and --no-checks exclude each other")
    variables: list[tuple[str, str]] = []
    for item in args.variable:
        name, sep, value = item.partition("=")
        if not sep or not value or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            parser.error(f"--variable takes NAME=VALUE, got {item!r}")
        variables.append((name, value))
    try:
        info = step_create(args.repo, args.create, args.dry_run)
        step_merges(args.repo, info, args.auto_merge, args.dry_run)
        if info is None:
            print("ruleset: after the repository exists")
            if variables:
                print("variables: after the repository exists")
        else:
            step_ruleset(args.repo, args.check, args.no_checks, args.dry_run, args.approvals, args.dismiss_stale,
                         args.code_owners, args.bypass_admin)
            step_variables(args.repo, variables, args.dry_run)
        step_apps(args.repo, args.app, not args.no_browser, args.dry_run)
    except Failed as e:
        print(f"github-setup: {e}", file=sys.stderr)
        return 1
    print("dry run: nothing changed" if args.dry_run else "done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
