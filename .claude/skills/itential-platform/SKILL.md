---
name: itential-platform
description: Administer Itential Automation Platform (IAP) — health checks, adapter management, application management, workflow engine control, service restarts, credential rotation
allowed-tools: Bash, Read
---

You are the IAP admin skill. Follow these steps every time you are invoked.

## Step 1: Identify Target Environment

If the user specified an environment in their request (e.g. `/itential-platform production`),
use `$ARGUMENTS` as the environment name. Otherwise ask: "Which environment? (production /
development / test)"

## Step 1a: Resolve SSH Key Path

Before executing any task that requires SSH access (service restart, log viewing, process
status), you must know the path to the user's SSH private key.

1. Check your persistent memory for an entry named `ssh_key_path`.
2. If found, set `SSH_KEY_PATH` to that value and proceed.
3. If not found, ask the user:

   > "What is the path to your SSH private key? (e.g., `~/.ssh/id_rsa`)"

4. Save the provided path to persistent memory as `ssh_key_path` so future sessions do not
   need to ask again.
5. Use `ssh -i $SSH_KEY_PATH` for all SSH commands in this session.

## Step 2: Load Environment Context

Read the environment skill file:

```
environments/<env-name>/CLAUDE.md
```

From it, extract and hold in memory:

- `IAP_HOST` — load balancer hostname (use for read-only API calls)
- `IAP_PORT` — HTTPS port on the load balancer
- `IAP_NODES` — direct IAP node hostnames and ports (use for write/mutating operations that
  must target each node individually, not the load balancer)
- `VAULT_ADDR` — HashiCorp Vault address
- `SA_VAULT_PATH` — Vault path for service account credentials
- `SA_CLIENT_ID_KEY` — key name for OAuth client ID
- `SA_CLIENT_SECRET_KEY` — key name for OAuth client secret
- `IAP_SSH_USER` — SSH user (for shell-level tasks)
- `AUTH_METHOD` — default auth method (`service-account` or `user-account`)

Before any Vault call, source the credentials file to load `VAULT_TOKEN` and `VAULT_ADDR`
into the current process (Claude Code's Bash tool runs in a child process that does not
inherit the user's terminal environment):

```bash
source vault-env.sh
```

If `vault-env.sh` is not found or sourcing it leaves `$VAULT_TOKEN` empty, instruct the
user to check that the file exists in the working directory and contains a valid token.

## Step 3: Authenticate

IAP tokens are valid for **1 hour**. Cache the token in `/tmp/iap_token_<env-name>` and
reuse it across invocations rather than fetching a new one every time.

```bash
TOKEN_CACHE="/tmp/iap_token_${ENV_NAME}"
IAP_TOKEN=""

if [ -f "$TOKEN_CACHE" ]; then
  CACHE_AGE=$(( $(date +%s) - $(stat -f %m "$TOKEN_CACHE" 2>/dev/null \
                               || stat -c %Y "$TOKEN_CACHE") ))
  if [ "$CACHE_AGE" -lt 3300 ]; then   # reuse if under 55 minutes old
    IAP_TOKEN=$(cat "$TOKEN_CACHE")
    echo "Reusing cached IAP token (age: ${CACHE_AGE}s)"
  fi
fi
```

Only fetch a new token when `$IAP_TOKEN` is still empty after the cache check. Use the
auth method from the environment (default: `service-account`). The user may override by
saying "use my personal account" or "use the service account" — apply for this session only.

### Service Account (default)

The IAP Bearer token has a 60-minute TTL. Cache it to `/tmp/.iap_token` on first fetch
so subsequent Bash calls within the same session do not need to re-authenticate:

```bash
IAP_TOKEN_FILE=/tmp/.iap_token
TOKEN_TTL=3540  # 59 minutes — 1 minute buffer before the 60-minute IAP token expiry

# Expire the cached token if it is older than TOKEN_TTL seconds
if [ -f "$IAP_TOKEN_FILE" ]; then
  FILE_AGE=$(( $(date +%s) - $(stat -f %m "$IAP_TOKEN_FILE") ))
  [ "$FILE_AGE" -ge "$TOKEN_TTL" ] && rm -f "$IAP_TOKEN_FILE"
fi

if [ ! -f "$IAP_TOKEN_FILE" ]; then
  source vault-env.sh

  SA=$(curl -s -H "X-Vault-Token: $VAULT_TOKEN" \
    $VAULT_ADDR/<sa-vault-path> | jq -r '.data.data')

  IAP_CLIENT_ID=$(echo "$SA" | jq -r '."<client-id-key>"')
  IAP_CLIENT_SECRET=$(echo "$SA" | jq -r '."<client-secret-key>"')

  curl -sk --location \
    --request POST "https://$IAP_HOST:$IAP_PORT/oauth/token" \
    --header 'Content-Type: application/x-www-form-urlencoded' \
    --data-urlencode "client_id=$IAP_CLIENT_ID" \
    --data-urlencode "client_secret=$IAP_CLIENT_SECRET" \
    --data-urlencode 'grant_type=client_credentials' \
    | jq -r '.access_token' > "$IAP_TOKEN_FILE"
fi

IAP_TOKEN=$(cat "$IAP_TOKEN_FILE")
IAP_AUTH_HEADER="Authorization: Bearer $IAP_TOKEN"
```

### User Account (override)

```bash
source vault-env.sh

IAP_PASSWORD=$(curl -s -H "X-Vault-Token: $VAULT_TOKEN" \
  $VAULT_ADDR/<platform-vault-path> | jq -r '.data.data."<admin-password-key>"')

IAP_TOKEN=$(curl -sk -X POST \
  "https://$IAP_HOST:$IAP_PORT/login" \
  -H 'Content-Type: application/json' \
  -d "{\"user\": {\"username\": \"$IAP_ADMIN_USER\", \"password\": \"$IAP_PASSWORD\"}}" \
  | jq -r '.token')

echo "$IAP_TOKEN" > "$TOKEN_CACHE"
```

Confirm authentication succeeded: `IAP_TOKEN` must be non-empty and not `null`.

Set the auth header for all subsequent API calls:

```bash
IAP_AUTH_HEADER="Authorization: Bearer $IAP_TOKEN"   # service-account
# or for user-account:
IAP_AUTH_HEADER="Cookie: token=$IAP_TOKEN"
```

## Step 4: Execute Requested Task

If the user has not stated a task, present this menu and ask what they would like to do:

```
Health & Status
  1.  Health check
  2.  List adapters
  3.  List applications
  4.  Version
  5.  Get server config

Adapters
  6.  Get adapter
  7.  Get adapter health
  8.  Start adapter              [destructive — requires confirmation]
  9.  Stop adapter               [destructive — requires confirmation]
  10. Restart adapter            [destructive — requires confirmation]

Applications
  11. Get application
  12. Get application health
  13. Start application          [destructive — requires confirmation]
  14. Stop application           [destructive — requires confirmation]
  15. Restart application        [destructive — requires confirmation]

Workflow Engine
  16. Check worker status
  17. Activate task worker       [destructive — requires confirmation]
  18. Deactivate task worker     [destructive — requires confirmation]
  19. Activate job worker        [destructive — requires confirmation]
  20. Deactivate job worker      [destructive — requires confirmation]

IAP Service
  21. Restart IAP service        [destructive — requires confirmation]
  22. View IAP logs
  23. Check IAP process status
  24. Change admin password      [destructive — requires confirmation]

itential-platform Service
  25. Check itential-platform service status
  26. Start itential-platform service    [destructive — requires confirmation]
  27. Stop itential-platform service     [destructive — requires confirmation]
  28. Restart itential-platform service  [destructive — requires confirmation]

Jobs
  29. Cancel all Active Jobs [destructive — requires confirmation]
```

> **Per-node operations:** Tasks 8–10 (adapter start/stop/restart) and 13–15 (application
> start/stop/restart) must be run against each IAP node directly — NOT through the load
> balancer. Use the node hostnames and ports from `IAP_NODES` in the environment file.
> Run against each node in sequence; confirm with the user before moving to the next node.

---

### Task: Health Check

```bash
curl -sk -H "$IAP_AUTH_HEADER" \
  "https://$IAP_HOST:$IAP_PORT/health/status?exclude-services=true" | jq .
```

Report `apps` and `adapters`. Note any `degraded` or `stopped` values and offer to
investigate further. Use `?exclude-services=true` to avoid a cascading check against
MongoDB and Redis (run the full check without it only if explicitly asked).

---

### Task: List Adapters

```bash
curl -sk -H "$IAP_AUTH_HEADER" \
  "https://$IAP_HOST:$IAP_PORT/health/adapters?skip=0&limit=25&sort=name&order=1" \
  | jq '[.results[] | {id, version, state, connection_state: .connection.state}]'
```

Present results as a table with columns: `id`, `version`, `state`, `connection.state`.
Highlight any adapters where `state` is not `RUNNING` or `connection.state` is not `ONLINE`.

---

### Task: List Applications

```bash
curl -sk -H "$IAP_AUTH_HEADER" \
  "https://$IAP_HOST:$IAP_PORT/health/applications?skip=0&limit=25" \
  | jq '[.results[] | {id, version, state}]'
```

Present results as a table with columns: `id`, `version`, `state`.
Highlight any applications where `state` is not `RUNNING`.

---

### Task: Version

```bash
curl -sk -H "$IAP_AUTH_HEADER" \
  https://$IAP_HOST:$IAP_PORT/health/server \
  | jq -r '.version'
```

Report the version string.

---

### Task: Get Server Config

```bash
curl -sk -H "$IAP_AUTH_HEADER" \
  https://$IAP_HOST:$IAP_PORT/server/config | jq .
```

Display the full configuration. Note any values relevant to the user's question.

---

### Task: Get Adapter

Ask for the adapter name if not provided.

```bash
curl -sk -H "$IAP_AUTH_HEADER" \
  https://$IAP_HOST:$IAP_PORT/adapters/<adapter-name> | jq .
```

---

### Task: Get Adapter Health

Ask for the adapter name if not provided.

```bash
curl -sk -H "$IAP_AUTH_HEADER" \
  https://$IAP_HOST:$IAP_PORT/health/adapters/<adapter-name> | jq .
```

Report `id`, `version`, `state`, and `connection.state`.

---

### Task: Start Adapter

Ask for the adapter name if not provided. **Confirm before executing.**
Run against each IAP node directly (not the load balancer).

```bash
curl -sk -X PUT \
  -H "$IAP_AUTH_HEADER" \
  https://<node-host>:<node-port>/adapters/<adapter-name>/start | jq .
```

Verify state is `RUNNING` after each node using Get Adapter Health.

---

### Task: Stop Adapter

Ask for the adapter name if not provided. **Confirm before executing.**
Run against each IAP node directly (not the load balancer).

```bash
curl -sk -X PUT \
  -H "$IAP_AUTH_HEADER" \
  https://<node-host>:<node-port>/adapters/<adapter-name>/stop | jq .
```

---

### Task: Restart Adapter

Ask for the adapter name if not provided. **Confirm before executing.**
Run against each IAP node directly (not the load balancer).

```bash
curl -sk -X PUT \
  -H "$IAP_AUTH_HEADER" \
  https://<node-host>:<node-port>/adapters/<adapter-name>/restart | jq .
```

Verify state is `RUNNING` after each node using Get Adapter Health.

---

### Task: Get Application

Ask for the application name if not provided.

```bash
curl -sk -H "$IAP_AUTH_HEADER" \
  https://$IAP_HOST:$IAP_PORT/applications/<application-name> | jq .
```

---

### Task: Get Application Health

Ask for the application name if not provided.

```bash
curl -sk -H "$IAP_AUTH_HEADER" \
  https://$IAP_HOST:$IAP_PORT/health/applications/<application-name> | jq .
```

Report `id`, `version`, and `state`.

---

### Task: Start Application

Ask for the application name if not provided. **Confirm before executing.**
Run against each IAP node directly (not the load balancer).

```bash
curl -sk -X PUT \
  -H "$IAP_AUTH_HEADER" \
  https://<node-host>:<node-port>/application/<application-name>/start | jq .
```

Verify state is `RUNNING` after each node using Get Application Health.

---

### Task: Stop Application

Ask for the application name if not provided. **Confirm before executing.**
Run against each IAP node directly (not the load balancer).

```bash
curl -sk -X PUT \
  -H "$IAP_AUTH_HEADER" \
  https://<node-host>:<node-port>/application/<application-name>/stop | jq .
```

---

### Task: Restart Application

Ask for the application name if not provided. **Confirm before executing.**
Run against each IAP node directly (not the load balancer).

```bash
curl -sk -X PUT \
  -H "$IAP_AUTH_HEADER" \
  https://<node-host>:<node-port>/application/<application-name>/restart | jq .
```

Verify state is `RUNNING` after each node using Get Application Health.

---

### Task: Check Worker Status

```bash
curl -sk -H "$IAP_AUTH_HEADER" \
  https://$IAP_HOST:$IAP_PORT/workflow_engine/workers/status | jq .
```

Report the status of both job and task workers.

---

### Task: Activate Task Worker

**Confirm with user before executing.**

```bash
curl -sk -X POST \
  -H "$IAP_AUTH_HEADER" \
  https://$IAP_HOST:$IAP_PORT/workflow_engine/activate \
  -d '' | jq .
```

Verify by running Check Worker Status.

---

### Task: Deactivate Task Worker

**Confirm with user before executing.**

```bash
curl -sk -X POST \
  -H "$IAP_AUTH_HEADER" \
  https://$IAP_HOST:$IAP_PORT/workflow_engine/deactivate \
  -d '' | jq .
```

Verify by running Check Worker Status.

---

### Task: Activate Job Worker

**Confirm with user before executing.**

```bash
curl -sk -X POST \
  -H "$IAP_AUTH_HEADER" \
  https://$IAP_HOST:$IAP_PORT/workflow_engine/jobWorker/activate \
  -d '' | jq .
```

Verify by running Check Worker Status.

---

### Task: Deactivate Job Worker

**Confirm with user before executing.**

```bash
curl -sk -X POST \
  -H "$IAP_AUTH_HEADER" \
  https://$IAP_HOST:$IAP_PORT/workflow_engine/jobWorker/deactivate \
  -d '' | jq .
```

Verify by running Check Worker Status.

---

### Task: Restart IAP Service

**Confirm with user before executing.** In HA deployments, restart one node at a time.

```bash
ssh -i $SSH_KEY_PATH $IAP_SSH_USER@<node> 'sudo systemctl restart itential'
```

Poll health after restart (up to 120 seconds):

```bash
for i in $(seq 1 24); do
  status=$(curl -sk -H "$IAP_AUTH_HEADER" \
    "https://$IAP_HOST:$IAP_PORT/health/status?exclude-services=true" \
    | jq -r '.apps' 2>/dev/null)
  echo "[$i] apps: $status"
  [ "$status" = "running" ] && break
  sleep 5
done
```

In HA2: after the first node is healthy, ask the user before restarting the second.

---

### Task: View IAP Logs

```bash
ssh -i $SSH_KEY_PATH $IAP_SSH_USER@<node> 'sudo journalctl -u itential -n 200 --no-pager'
```

Ask the user which node if not specified.

---

### Task: Check IAP Process Status

```bash
ssh -i $SSH_KEY_PATH $IAP_SSH_USER@<node> 'sudo systemctl status itential'
```

---

### Task: Change Admin Password

**High-risk — confirm with user first.**

1. Retrieve current password from Vault.
2. Call the IAP user-management API to set the new password.
3. Update the secret in Vault.
4. Verify login with the new password.

TODO: Document the exact IAP API endpoint for password rotation once confirmed.

---

### Task: Check itential-platform Service Status

Ask the user which node if not specified.

```bash
ssh -i $SSH_KEY_PATH $IAP_SSH_USER@<node> 'sudo systemctl status itential-platform'
```

---

### Task: Start itential-platform Service

**Confirm with user before executing.**

Ask the user which node if not specified.

```bash
ssh -i $SSH_KEY_PATH $IAP_SSH_USER@<node> 'sudo systemctl start itential-platform'
```

Verify the service is active after starting:

```bash
ssh -i $SSH_KEY_PATH $IAP_SSH_USER@<node> 'sudo systemctl status itential-platform'
```

---

### Task: Stop itential-platform Service

**Confirm with user before executing.**

Ask the user which node if not specified.

```bash
ssh -i $SSH_KEY_PATH $IAP_SSH_USER@<node> 'sudo systemctl stop itential-platform'
```

Verify the service is inactive after stopping:

```bash
ssh -i $SSH_KEY_PATH $IAP_SSH_USER@<node> 'sudo systemctl status itential-platform'
```

---

### Task: Restart itential-platform Service

**Confirm with user before executing.** In HA deployments, restart one node at a time.

Ask the user which node if not specified.

```bash
ssh -i $SSH_KEY_PATH $IAP_SSH_USER@<node> 'sudo systemctl restart itential-platform'
```

Verify the service is active after restarting:

```bash
ssh -i $SSH_KEY_PATH $IAP_SSH_USER@<node> 'sudo systemctl status itential-platform'
```

In HA deployments: after the first node is confirmed active, ask the user before restarting
the next node.

---

### Task: Cancel all Active Jobs

**Destructive — confirm with user before executing.**

Jobs API notes (IAP P6, `app-operations_manager`):
- Job list endpoint: `GET /operations-manager/jobs` — returns `{data, metadata}` (NOT
  `/workflow_engine/jobs`, which serves the SPA frontend)
- Filter syntax uses bracket notation: `equals%5Bstatus%5D=running`
- Cancel endpoint: `POST /operations-manager/jobs/cancel` with body `{"jobIds": [...]}`
- Batch limit: **100 job IDs per request** (`BULK_JOB_UPDATE_TRANSACTION_LIMIT = 100`)
- Always fetch from `skip=0` on each iteration — canceled jobs drop off the list
  automatically, so the next page of running jobs is always at the top

**Step 1 — Count running jobs:**

```bash
TOTAL=$(curl -sk -H "$IAP_AUTH_HEADER" \
  "https://$IAP_HOST:$IAP_PORT/operations-manager/jobs?equals%5Bstatus%5D=running&limit=1&skip=0" \
  | jq -r '.metadata.total')
echo "Running jobs: $TOTAL"
```

Show the count to the user and confirm before proceeding.

**Step 2 — Cancel in batches:**

```bash
PAGE_SIZE=100
TOTAL_CANCELED=0
TOTAL_FAILED=0
PAGE=0

while true; do
  RESPONSE=$(curl -sk -H "$IAP_AUTH_HEADER" \
    "https://$IAP_HOST:$IAP_PORT/operations-manager/jobs?equals%5Bstatus%5D=running&limit=$PAGE_SIZE&skip=0")

  JOB_IDS=$(echo "$RESPONSE" | jq '[.data[]._id]')
  COUNT=$(echo "$JOB_IDS" | jq 'length')
  REMAINING=$(echo "$RESPONSE" | jq -r '.metadata.total')

  if [ "$COUNT" -eq 0 ]; then
    echo "No more running jobs. Done."
    break
  fi

  PAGE=$((PAGE + 1))

  PAYLOAD=$(jq -n --argjson ids "$JOB_IDS" '{"jobIds": $ids}')
  CANCEL_RESP=$(curl -sk -X POST \
    -H "$IAP_AUTH_HEADER" \
    -H "Content-Type: application/json" \
    "https://$IAP_HOST:$IAP_PORT/operations-manager/jobs/cancel" \
    -d "$PAYLOAD")

  MSG=$(echo "$CANCEL_RESP" | jq -r '.message // "unknown"')

  if echo "$CANCEL_RESP" | jq -e '.message | test("canceled|success"; "i")' > /dev/null 2>&1; then
    TOTAL_CANCELED=$((TOTAL_CANCELED + COUNT))
    if (( PAGE % 50 == 0 )); then
      echo "Page $PAGE: $TOTAL_CANCELED canceled so far, ~$REMAINING remaining"
    fi
  else
    TOTAL_FAILED=$((TOTAL_FAILED + COUNT))
    echo "Page $PAGE: FAILED — $MSG"
    # Re-authenticate in case token expired mid-run
    IAP_TOKEN=$(curl -sk --location \
      --request POST "https://$IAP_HOST:$IAP_PORT/oauth/token" \
      --header 'Content-Type: application/x-www-form-urlencoded' \
      --data-urlencode "client_id=$IAP_CLIENT_ID" \
      --data-urlencode "client_secret=$IAP_CLIENT_SECRET" \
      --data-urlencode 'grant_type=client_credentials' \
      | jq -r '.access_token')
    IAP_AUTH_HEADER="Authorization: Bearer $IAP_TOKEN"
  fi
done

echo "Complete. Canceled: $TOTAL_CANCELED | Failed: $TOTAL_FAILED"
```

**Step 3 — Verify:**

```bash
REMAINING=$(curl -sk -H "$IAP_AUTH_HEADER" \
  "https://$IAP_HOST:$IAP_PORT/operations-manager/jobs?equals%5Bstatus%5D=running&limit=1&skip=0" \
  | jq -r '.metadata.total')
echo "Remaining running jobs: $REMAINING"
```

Report total canceled, total failed, and remaining running jobs.
