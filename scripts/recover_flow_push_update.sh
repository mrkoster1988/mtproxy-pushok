#!/usr/bin/env bash
# A legacy host must run the corrected backup before it can fast-forward.
set -euo pipefail
umask 077
cd /opt/mtproxy-pushok
stable=$(git rev-parse --verify 'FETCH_HEAD^{commit}')
git merge-base --is-ancestor HEAD "$stable"
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
git archive "$stable" scripts | tar -x -C "$work"
python3 "$work/scripts/updater.py" --recover-flow-push "$stable"
