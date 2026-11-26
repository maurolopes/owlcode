#!/bin/sh
# Runs a read command of .github/linear.py with the read-only Linear key from the login keychain.
# The key lives in this one command's environment: never in a file, the chat or the session. Only the read commands
# run here; a write goes through the harness's Linear connection. Used by `mise run linear:read`, `triage:checks`
# and `measure`. LINEAR_READ_PYTHON names another interpreter (the tests' fake).
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
case "${1:-}" in
  list | issue | triage-checks) ;;
  *)
    echo "linear:read: '${1:-}' is not a read command. Only list, issue and triage-checks run here; writes go through the Linear connection." >&2
    exit 2
    ;;
esac
# The keychain item is NAME-linear-read. NAME is AGENT_USER, else the login name in the agents' account (the account whose
# primary group is `agents`: read from the uid, never $USER), else the name of the repository that the checkout's `origin` names.
name=${AGENT_USER:-}
if [ -z "$name" ]; then
  if [ "$(id -gn)" = agents ]; then
    name=$(id -un)
  else
    name=$(git -C "$here" remote get-url origin 2>/dev/null | sed -e 's|/*$||' -e 's|\.git$||' -e 's|.*[/:]||') || name=
  fi
fi
if [ -z "$name" ]; then
  echo "linear:read: no name for the keychain item: the checkout has no origin remote. Set AGENT_USER." >&2
  exit 1
fi
item="$name-linear-read"
key=$(security find-generic-password -s "$item" -w 2>/dev/null) || {
  echo "linear:read: the login keychain has no item $item. Add a read-only Linear key once:" >&2
  # shellcheck disable=SC2016 # the line is for the owner to copy: $USER must stay as it is
  echo "  security add-generic-password -a \"\$USER\" -s $item -w   (with -w last it asks for the key, hidden)" >&2
  exit 1
}
LINEAR_API_KEY="$key" exec "${LINEAR_READ_PYTHON:-/usr/local/bin/python3}" -I "$here/../.github/linear.py" "$@"
