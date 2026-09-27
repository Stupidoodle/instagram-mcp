#!/usr/bin/env bash
# Deploy origin/main when it moved: sync deps, reload units, restart the bridge.
# Run by instagram-mcp-deploy.timer. Untracked files (.env, sessions) are kept.
set -euo pipefail

# Wrapped in a function so bash has read all of it before git replaces this file.
main() {
  cd "$(dirname "$(readlink -f "$0")")/../.."
  git fetch -q origin main
  if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ]; then
    return 0
  fi
  git reset -q --hard origin/main
  "$HOME/.local/bin/uv" sync -q --frozen
  systemctl --user daemon-reload
  systemctl --user restart instagram-bridge.service
  echo "deployed $(git log -1 --format='%h %s')"
}

main "$@"
