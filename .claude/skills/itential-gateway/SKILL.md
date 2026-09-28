---
name: itential-gateway
description: Administer Itential Automation Gateway (IAG) — health checks, service restarts, script listing, backend operations
allowed-tools: Bash, Read
---

You are the IAG admin skill. Follow these steps every time you are invoked.

## Step 1: Identify Target Environment

If the user specified an environment in their request (e.g. `/itential-gateway production`),
use `$ARGUMENTS` as the environment name. Otherwise ask: "Which environment? (production /
development / test)"

## Step 1a: Resolve SSH Key Path

Before executing any task that requires SSH access (service restart, log viewing, process
status, etcd operations), you must know the path to the user's SSH private key.

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

- `IAG_HOST` — IAG hostname
- `IAG_PORT` — HTTPS port (typically `8083`)
- `VAULT_ADDR` — HashiCorp Vault address
- `PLATFORM_VAULT_PATH` — Vault path for platform credentials
- `IAG_ADMIN_USER` — IAG admin username key in Vault
- `IAG_ADMIN_PASSWORD_KEY` — key name for IAG admin password in Vault
- `IAG_SSH_USER` — SSH user for direct node access
- `IAG_BACKEND` — backend type: `etcd` or `dynamodb`

Before any Vault call, source the credentials file to load `VAULT_TOKEN` and `VAULT_ADDR`
into the current process (Claude Code's Bash tool runs in a child process that does not
inherit the user's terminal environment):

```bash
source vault-env.sh
```

If `vault-env.sh` is not found or sourcing it leaves `$VAULT_TOKEN` empty, instruct the
user to check that the file exists in the working directory and contains a valid token.

## Step 3: Authenticate

Source vault-env.sh then retrieve the IAG admin password from Vault:

```bash
source vault-env.sh

IAG_PASSWORD=$(curl -s -H "X-Vault-Token: $VAULT_TOKEN" \
  $VAULT_ADDR/<platform-vault-path> \
  | jq -r '.data.data."<iag-admin-password-key>"')
```

IAG uses HTTP Basic auth. Verify connectivity:

```bash
curl -sk -u "$IAG_ADMIN_USER:$IAG_PASSWORD" \
  https://$IAG_HOST:$IAG_PORT/api/v2.0/profile | jq .
```

## Step 4: Execute Requested Task

If the user has not stated a task, present this menu and ask what they would like to do:

```
Available tasks:
  1. Health check
  2. List installed scripts / modules
  3. Restart IAG service        [destructive — requires confirmation]
  4. Check IAG process status
  5. View IAG logs
  6. etcd cluster health        [etcd backend only]
  7. List etcd members          [etcd backend only]
  8. Change IAG admin password  [destructive — requires confirmation]
```

---

### Task: Health Check

```bash
curl -sk -u "$IAG_ADMIN_USER:$IAG_PASSWORD" \
  https://$IAG_HOST:$IAG_PORT/api/v2.0/profile | jq .
```

Report the response status. Note any error fields.

---

### Task: List Installed Scripts / Modules

```bash
curl -sk -u "$IAG_ADMIN_USER:$IAG_PASSWORD" \
  https://$IAG_HOST:$IAG_PORT/api/v2.0/scripts \
  | jq '[.[] | {name, type}]'
```

---

### Task: Restart IAG Service

**Confirm with user before executing.** IAG downtime interrupts all network automation
workflows.

```bash
ssh -i $SSH_KEY_PATH $IAG_SSH_USER@$IAG_HOST 'sudo systemctl restart itential-gateway'
```

Poll until healthy (5 attempts × 5s = 25s total):

```bash
for i in $(seq 1 5); do
  status=$(curl -sk -u "$IAG_ADMIN_USER:$IAG_PASSWORD" \
    https://$IAG_HOST:$IAG_PORT/api/v2.0/profile \
    | jq -r '.status' 2>/dev/null)
  echo "[$i/5] status: $status"
  [ -n "$status" ] && [ "$status" != "null" ] && break
  sleep 5
done
```

---

### Task: Check IAG Process Status

```bash
ssh -i $SSH_KEY_PATH $IAG_SSH_USER@$IAG_HOST 'sudo systemctl status itential-gateway'
```

---

### Task: View IAG Logs

```bash
ssh -i $SSH_KEY_PATH $IAG_SSH_USER@$IAG_HOST \
  'sudo journalctl -u itential-gateway -n 200 --no-pager'
```

---

### Task: etcd Cluster Health

Applies only when `IAG_BACKEND` is `etcd`.

```bash
ssh -i $SSH_KEY_PATH $IAG_SSH_USER@$IAG_HOST \
  'ETCDCTL_API=3 etcdctl endpoint health --cluster'
```

---

### Task: List etcd Members

Applies only when `IAG_BACKEND` is `etcd`.

```bash
ssh -i $SSH_KEY_PATH $IAG_SSH_USER@$IAG_HOST \
  'ETCDCTL_API=3 etcdctl member list'
```

---

### Task: Change IAG Admin Password

**High-risk — confirm with user first.**

1. Retrieve current password from Vault.
2. Update via IAG API or config file (TODO: confirm method for this deployment).
3. Restart IAG if a config file change was required.
4. Update the secret in Vault.
5. Verify login with the new password.
