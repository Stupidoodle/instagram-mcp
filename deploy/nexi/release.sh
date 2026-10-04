#!/usr/bin/env bash
# Build and restart for the tree the deployer has checked out: sync deps, reload units,
# restart the bridge. The deployer did the git part (fetch, verify, reset to DEPLOY_SHA);
# this does none. Untracked files (.env, sessions) are kept.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")/../.."
"$HOME/.local/bin/uv" sync -q --frozen
systemctl --user daemon-reload
systemctl --user restart instagram-bridge.service
sha=${DEPLOY_SHA:-unknown}
echo "deployed ${sha:0:12}${DEPLOY_VERSION:+ as v$DEPLOY_VERSION}"
