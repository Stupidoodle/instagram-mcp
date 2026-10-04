#!/usr/bin/env bash
# Deploys a commit to nexi through the deployer at deploy.skoggi.ch and waits for the
# result. Source: skoggi-ch infrastructure/platform/release/deploy.sh, copied into each
# repo's .github/release/ by onboard.sh; edit it there. Run by a GitHub Actions job with
# `environment: nexi` and `permissions: id-token: write`, from a checkout of
# .github/release at the commit being deployed.
#
# Env: SHA (40 hex), VERSION and NOTES (empty when nothing was released), RUN_URL
# (exactly <server>/<repo>/actions/runs/<run_id>), CF_ACCESS_CLIENT_ID and
# CF_ACCESS_CLIENT_SECRET (the repo's Access service token), plus what Actions provides:
# ACTIONS_ID_TOKEN_REQUEST_URL/TOKEN, RUNNER_TEMP, GITHUB_OUTPUT, GITHUB_STEP_SUMMARY.
# Writes `service` (the manifest name the deployer reports) to GITHUB_OUTPUT.
set -euo pipefail

: "${SHA:?SHA is not set}" "${RUN_URL:?RUN_URL is not set}"
VERSION=${VERSION:-}
NOTES=${NOTES:-}
CF_ACCESS_CLIENT_ID=${CF_ACCESS_CLIENT_ID:-}
CF_ACCESS_CLIENT_SECRET=${CF_ACCESS_CLIENT_SECRET:-}
export SHA VERSION NOTES RUN_URL # jq reads them from the environment

api=https://deploy.skoggi.ch
if [ -z "${ACTIONS_ID_TOKEN_REQUEST_URL:-}" ] || [ -z "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:-}" ]; then
  echo "::error::No OIDC token request in this job: it needs permissions: id-token: write"
  exit 1
fi
if [ -z "$CF_ACCESS_CLIENT_ID" ] || [ -z "$CF_ACCESS_CLIENT_SECRET" ]; then
  echo "::error::CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET are not set for nexi"
  exit 1
fi

umask 077
token_req="$RUNNER_TEMP/oidc-request.hdr"
hdr="$RUNNER_TEMP/deploy.hdr"
body="$RUNNER_TEMP/deploy-body.json"
resp="$RUNNER_TEMP/deploy-response.json"
trap 'rm -f "$token_req" "$hdr"' EXIT
printf 'Authorization: bearer %s\n' "$ACTIONS_ID_TOKEN_REQUEST_TOKEN" > "$token_req"

# A fresh OIDC token for every request: each one lives 5 minutes and the
# deployer accepts it once. Headers go through a file, never argv or the log.
new_headers() {
  local token
  token=$(curl -sSf --max-time 30 -H @"$token_req" \
    "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=https%3A%2F%2Fdeploy.skoggi.ch" \
    | jq -r .value)
  if [ -z "$token" ] || [ "$token" = null ]; then
    echo "::error::No OIDC token from GitHub"
    return 1
  fi
  echo "::add-mask::$token"
  {
    printf 'Authorization: Bearer %s\n' "$token"
    printf 'CF-Access-Client-Id: %s\n' "$CF_ACCESS_CLIENT_ID"
    printf 'CF-Access-Client-Secret: %s\n' "$CF_ACCESS_CLIENT_SECRET"
  } > "$hdr"
}

print_log_tail() {
  jq -r 'if (.log_tail | type) == "array" then .log_tail[] else (.log_tail // "") end' \
    "$resp" || true
}

jq -n '{
  sha: env.SHA,
  version: (env.VERSION | if . == "" then null else . end),
  notes: (env.NOTES | if . == "" then null else . end),
  run_url: env.RUN_URL
}' > "$body"

# Retried only when the request may not have arrived (no answer, 5xx).
for attempt in 1 2 3; do
  new_headers
  code=$(curl -sS --max-time 30 -o "$resp" -w '%{http_code}' -X POST \
    -H @"$hdr" -H 'Content-Type: application/json' --data-binary @"$body" \
    "$api/v1/deploy") || code=000
  [ "$code" = 202 ] && break
  echo "POST /v1/deploy: HTTP $code (attempt $attempt)"
  case $code in
    000 | 5??) sleep 5 ;;
    *) cat "$resp" || true; exit 1 ;;
  esac
done
if [ "$code" != 202 ]; then
  echo "::error::The deployer did not accept the deploy"
  exit 1
fi

id=$(jq -r .id "$resp")
if ! [[ $id =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
  echo "::error::Unexpected deploy id in the deployer's answer"
  exit 1
fi
echo "Deploy $id queued for ${SHA:0:12}${VERSION:+ (v$VERSION)}"

deadline=$((SECONDS + 1200))
errors=0
last=""
service=""
while :; do
  sleep 5
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "::error::Deploy $id did not finish within 20 minutes (last status: ${last:-none})"
    exit 1
  fi
  new_headers
  code=$(curl -sS --max-time 30 -o "$resp" -w '%{http_code}' -H @"$hdr" \
    "$api/v1/deploys/$id") || code=000
  if [ "$code" != 200 ]; then
    errors=$((errors + 1))
    echo "GET /v1/deploys/$id: HTTP $code ($errors in a row)"
    if [ "$errors" -ge 12 ]; then
      echo "::error::The deployer stopped answering"
      exit 1
    fi
    continue
  fi
  errors=0
  if [ -z "$service" ]; then
    service=$(jq -r '.service // empty' "$resp")
    if [[ $service =~ ^[a-z0-9-]+$ ]]; then
      echo "service=$service" >> "$GITHUB_OUTPUT"
    else
      service=""
    fi
  fi
  status=$(jq -r .status "$resp")
  if [ "$status" != "$last" ]; then
    echo "status: $status"
    last=$status
  fi
  case $status in
    queued | running) ;;
    succeeded) break ;;
    *)
      echo "::error::Deploy $id $status"
      print_log_tail
      exit 1
      ;;
  esac
done

echo "Deployed \`${SHA:0:12}\`${VERSION:+ as v$VERSION} to nexi (deploy $id)" \
  >> "$GITHUB_STEP_SUMMARY"
