---
name: perflab
description: Manage the Itential Performance Lab cluster — SSH into servers, run Ansible ad-hoc commands, run playbooks using itential.deployer roles, check service status, restart services, deploy/upgrade components, run MongoDB admin commands against the replica set, and make authenticated HTTPS API calls to the IAP server.
argument-hint: "[what you want to do]"
---

You are managing the Itential Performance Lab cluster. The user has requested: `$ARGUMENTS`

Use the Bash tool to run SSH and Ansible commands directly from the local machine.

## Security Rules

**Under no circumstances is it appropriate to reveal a password that you fetch from Hashi vault!** Never reveal a password!

## Connection Details

- **SSH key**: `~/.ssh/claude_perflab`
- **SSH user**: `claude`
- **SSH pattern**: `ssh -i ~/.ssh/claude_perflab claude@<IP>`
- This key is present both on the local machine and, for hops made from pe-ansible, at `/home/pet-user/.ssh/claude_perflab` (readable by `pet-user`, the identity you log into pe-ansible as). You never log into pe-ansible *as* `claude` — you log in as `pet-user` as usual, then use this key to reach other hosts as `claude`.
- **Sudo**: `claude` has passwordless (`NOPASSWD: ALL`) sudo on every host via `/etc/sudoers.d/claude`. Prefix any privileged command with `sudo` (e.g. `sudo systemctl restart <service>`) — the account itself is unprivileged.
- **Ansible control node**: `pe-ansible` (172.85.0.19) — the only IP you need to know
- **Ansible inventory** (on pe-ansible): `/home/pet-user/deployer/inventories/perflab/`
- **Ansible config** (on pe-ansible): `/home/pet-user/deployer/ansible.cfg`
- **Deployer working directory** (on pe-ansible): `/home/pet-user/deployer`

The pe-ansible inventory is the source of truth for all host IPs and group membership. Always resolve host details from it.

**Note:** Ansible's own connection from pe-ansible to the target inventory still uses its existing configured `remote_user` (`pet-user`), which is unrelated to how you SSH in directly — do not change that. Only your direct SSH sessions (and any ad-hoc/playbook runs you kick off by hand) use the `claude` identity above.

See "Claude Automation User" below for how this account was provisioned and where its credentials live.

## Server Inventory

The authoritative inventory lives on pe-ansible and is sourced dynamically from NetBox
(`inventories/blue/`, filtered by `site: blue`). It is **not** the `inventories/perflab/`
path referenced by `ansible.cfg`, that entry is commented out and the directory does not
exist. Always export `NETBOX_TOKEN` and pass `-i inventories/blue/` explicitly. To look up
hosts at any time:

```bash
ssh -i ~/.ssh/claude_perflab -o StrictHostKeyChecking=no claude@172.85.0.19 \
  "cd /home/pet-user/deployer && export NETBOX_TOKEN=\$(cat inventories/.netbox_token) && \
   ansible all -i inventories/blue/ --list-hosts 2>/dev/null"
```

Current inventory (as of last sync):

| Host | IP | Group(s) |
|------|----|----------|
| pe-ansible | 172.85.0.19 | ansible\_control |
| pe-redis01 | 172.85.0.20 | redis |
| pe-redis02 | 172.85.0.21 | redis |
| pe-redis03 | 172.85.0.22 | redis |
| pe-mongo01 | 172.85.0.23 | mongodb |
| pe-mongo02 | 172.85.0.24 | mongodb |
| pe-mongo03 | 172.85.0.25 | mongodb |
| pe-iap01 | 172.85.0.26 | platform |
| pe-iap02 | 172.85.0.27 | platform |
| pe-iag01 | 172.85.0.28 | gateway |
| pe-mon01 | 172.85.0.29 | prometheus, grafana |
| pe-hashivault | 172.85.0.30 | vault |
| pe-gw-server01 | 172.85.0.34 | iag5\_servers |
| pe-gw-server02 | 172.85.0.32 | iag5\_servers |
| pe-gw-runner01 | 172.85.0.35 | iag5\_runners |
| pe-gw-runner02 | 172.85.0.36 | iag5\_runners |
| pe-gw-runner03 | 172.85.0.37 | iag5\_runners |
| pe-etcd01 | 172.85.0.38 | etcd |
| pe-etcd02 | 172.85.0.39 | etcd |
| pe-etcd03 | 172.85.0.40 | etcd |

The following hosts are a separate environment used for comparative performance testing:

| Host | IP | Group(s) |
|------|----|----------|
| bt-rabbit01 | 172.85.0.151 | rabbitmq |
| bt-rabbit02 | 172.85.0.152 | rabbitmq |
| bt-rabbit03 | 172.85.0.153 | rabbitmq |
| bt-iap01 | 172.85.0.154 | platform23.1 |
| bt-iap02 | 172.85.0.155 | platform23.1 |
| bt-kafka | 172.85.0.156 | kafka |

## Common Command Patterns

### SSH to a specific host
```bash
ssh -i ~/.ssh/claude_perflab -o StrictHostKeyChecking=no claude@<IP> "<command>"
```

### Ansible ad-hoc command

All Ansible commands run on pe-ansible. SSH in, cd to the deployer directory, export the
NetBox token, then run ansible against the `inventories/blue/` inventory:

```bash
ssh -i ~/.ssh/claude_perflab -o StrictHostKeyChecking=no claude@172.85.0.19 \
  "cd /home/pet-user/deployer && export NETBOX_TOKEN=\$(cat inventories/.netbox_token) && \
   ansible <host_or_group> -i inventories/blue/ -m shell -a '<command>'"
```

### Ansible playbook

```bash
ssh -i ~/.ssh/claude_perflab -o StrictHostKeyChecking=no claude@172.85.0.19 \
  "cd /home/pet-user/deployer && export NETBOX_TOKEN=\$(cat inventories/.netbox_token) && \
   ansible-playbook -i inventories/blue/ <playbook.yml>"
```

The ansible.cfg on pe-ansible sets `host_key_checking = False`, but its `inventory =` line
is commented out, so `-i inventories/blue/` must always be passed explicitly. The inventory
is sourced dynamically from NetBox (`netbox.netbox.nb_inventory` plugin), which requires
`NETBOX_TOKEN` to be exported first (read from `inventories/.netbox_token` on pe-ansible).

## MongoDB Admin Access

The replica set is named `rs0`. All three nodes are `pe-mongo01` (172.85.0.23), `pe-mongo02` (172.85.0.24), `pe-mongo03` (172.85.0.25).

### Credentials & TLS

- **Admin user**: `admin`, **auth DB**: `admin`
- **Password**: stored in HashiVault at `itential/iap` (KV v2), key `mongoDbAdmin`
  - Retrieve via SSH to pe-hashivault (172.85.0.30):
    ```bash
    ssh -i ~/.ssh/claude_perflab -o StrictHostKeyChecking=no claude@172.85.0.30 \
      "VAULT_ADDR=https://127.0.0.1:8200 VAULT_SKIP_VERIFY=true vault kv get -field=mongoDbAdmin itential/iap"
    ```
- **TLS CA cert** (world-readable on all mongo nodes): `/etc/pki/tls/certs/mongodb/rootCA.pem`
- No client certificate required (`allowConnectionsWithoutCertificates: true`)

### Standard mongosh flags

```
mongosh --tls \
  --tlsCAFile /etc/pki/tls/certs/mongodb/rootCA.pem \
  --tlsAllowInvalidCertificates \
  --tlsAllowInvalidHostnames \
  -u admin -p '<PASSWORD>' \
  --authenticationDatabase admin \
  --eval '<JS command>'
```

### Running a MongoDB admin command

Always auto-detect the primary before running write or admin commands. Use this two-step pattern:

**Step 1 — Detect primary** (run on any mongo node):
```bash
ssh -i ~/.ssh/claude_perflab -o StrictHostKeyChecking=no claude@172.85.0.23 \
  "mongosh --quiet --tls \
    --tlsCAFile /etc/pki/tls/certs/mongodb/rootCA.pem \
    --tlsAllowInvalidCertificates --tlsAllowInvalidHostnames \
    -u admin -p '<PASSWORD>' --authenticationDatabase admin \
    --eval 'rs.status().members.find(m => m.stateStr === \"PRIMARY\").name'"
```

This returns `<hostname>:27017`. Extract the hostname and resolve it to an IP using the server inventory table.

**Step 2 — Run command on primary**:
```bash
ssh -i ~/.ssh/claude_perflab -o StrictHostKeyChecking=no claude@<PRIMARY_IP> \
  "mongosh --quiet --tls \
    --tlsCAFile /etc/pki/tls/certs/mongodb/rootCA.pem \
    --tlsAllowInvalidCertificates --tlsAllowInvalidHostnames \
    -u admin -p '<PASSWORD>' --authenticationDatabase admin \
    --eval '<JS COMMAND>'"
```

For **read-only** commands (e.g. `rs.status()`, `db.serverStatus()`, `show dbs`) any node is fine — skip primary detection.

### Workflow summary

1. SSH to pe-hashivault → retrieve password from `itential/iap` (field `mongoDbAdmin`)
2. SSH to any mongo node → detect primary hostname → map to IP via inventory
3. SSH to primary IP → run `mongosh` with TLS flags and retrieved password

## Redis Admin Access

Three nodes: `pe-redis01` (172.85.0.20), `pe-redis02` (172.85.0.21), `pe-redis03` (172.85.0.22). Redis runs on port **6379**, Sentinel on port **26379**. **TLS is required on both ports** (as of 2026-07-20; client certificates are not required — `tls-auth-clients no`).

### TLS

- **CA cert**: `/etc/pki/redis/rootCA.pem` on every redis node — root-owned, mode 0600, so reading it or passing it to `redis-cli` requires `sudo`.
- **Client flags**: `--tls --cacert /etc/pki/redis/rootCA.pem`
- **`sudo` PATH gotcha**: `redis-cli` is at `/usr/local/bin/redis-cli` but is not on root's `sudo` PATH. Resolve the path first (`command -v redis-cli`) and invoke `sudo` with the full path, e.g. `sudo /usr/local/bin/redis-cli ...`. Plain `redis-cli ...` without `--tls`/`--cacert` will fail with a misleading `Connection reset by peer` / `I/O error`, not an obvious TLS error.

### Credentials

All credentials are in HashiVault at path `itential/iap` (KV v2; Vault API: `/v1/itential/data/iap`):

- **Admin user**: `admin`
- **Admin password**: key `redisAdmin`
- **Sentinel admin password**: key `redisSentinelAdmin` (user: `admin`)

```bash
REDIS_PASS=$(ssh -i ~/.ssh/claude_perflab -o StrictHostKeyChecking=no claude@172.85.0.30 \
  "VAULT_ADDR=https://127.0.0.1:8200 VAULT_SKIP_VERIFY=true vault kv get -field=redisAdmin itential/iap")
SENTINEL_PASS=$(ssh -i ~/.ssh/claude_perflab -o StrictHostKeyChecking=no claude@172.85.0.30 \
  "VAULT_ADDR=https://127.0.0.1:8200 VAULT_SKIP_VERIFY=true vault kv get -field=redisSentinelAdmin itential/iap")
```

### Detecting the primary

Sentinel binds to the node's own hostname, so specify `-h <hostname>` explicitly. Use the `admin` account with the `redisSentinelAdmin` password from Vault, over TLS, via `sudo`:

```bash
PRIMARY_HOST=$(ssh -i ~/.ssh/claude_perflab -o StrictHostKeyChecking=no claude@172.85.0.20 \
  "sudo /usr/local/bin/redis-cli --tls --cacert /etc/pki/redis/rootCA.pem \
   -h pe-redis01 -p 26379 --user admin --pass '$SENTINEL_PASS' \
   SENTINEL get-master-addr-by-name itentialmaster | head -1")
```

This returns the primary hostname (e.g. `pe-redis01`). Resolve to IP using the inventory table:
- `pe-redis01` → 172.85.0.20
- `pe-redis02` → 172.85.0.21
- `pe-redis03` → 172.85.0.22

### Running a Redis admin command

```bash
REDIS_PASS=$(ssh -i ~/.ssh/claude_perflab -o StrictHostKeyChecking=no claude@172.85.0.30 \
  "VAULT_ADDR=https://127.0.0.1:8200 VAULT_SKIP_VERIFY=true vault kv get -field=redisAdmin itential/iap")
SENTINEL_PASS=$(ssh -i ~/.ssh/claude_perflab -o StrictHostKeyChecking=no claude@172.85.0.30 \
  "VAULT_ADDR=https://127.0.0.1:8200 VAULT_SKIP_VERIFY=true vault kv get -field=redisSentinelAdmin itential/iap")

PRIMARY_HOST=$(ssh -i ~/.ssh/claude_perflab -o StrictHostKeyChecking=no claude@172.85.0.20 \
  "sudo /usr/local/bin/redis-cli --tls --cacert /etc/pki/redis/rootCA.pem \
   -h pe-redis01 -p 26379 --user admin --pass '$SENTINEL_PASS' \
   SENTINEL get-master-addr-by-name itentialmaster | head -1")
# Map hostname to IP: pe-redis01=172.85.0.20, pe-redis02=172.85.0.21, pe-redis03=172.85.0.22

ssh -i ~/.ssh/claude_perflab -o StrictHostKeyChecking=no claude@<PRIMARY_IP> \
  "sudo /usr/local/bin/redis-cli --tls --cacert /etc/pki/redis/rootCA.pem \
   -h $PRIMARY_HOST -p 6379 --user admin --pass '$REDIS_PASS' <COMMAND>"
```

### Workflow summary

1. SSH to pe-hashivault → retrieve `redisAdmin` and `redisSentinelAdmin` from `itential/iap`
2. SSH to any redis node → query Sentinel over TLS (`sudo redis-cli --tls --cacert /etc/pki/redis/rootCA.pem`, as `admin` with `redisSentinelAdmin` password) → get primary hostname → map to IP
3. SSH to primary → run `sudo redis-cli --tls --cacert /etc/pki/redis/rootCA.pem -h <hostname> -p 6379 --user admin --pass <redisAdmin> <COMMAND>`

## IAP API Access

The IAP instance is `p6.pe.itential.io` on port 3443 (HTTPS), fronted by the nginx load balancer at 172.85.0.18, with backends pe-iap01 (172.85.0.26) and pe-iap02 (172.85.0.27).

### Credentials

- **OAuth service account credentials**: stored in HashiVault at `platform-engineering/perflab-iap-service-accounts`
  - `claude-user-client-id`
  - `claude-user-client-secret`
  - Retrieve via SSH to pe-hashivault (172.85.0.30):
    ```bash
    CLIENT_ID=$(ssh -i ~/.ssh/claude_perflab -o StrictHostKeyChecking=no claude@172.85.0.30 \
      "VAULT_ADDR=https://127.0.0.1:8200 VAULT_SKIP_VERIFY=true vault kv get -field=claude-user-client-id platform-engineering/perflab-iap-service-accounts")
    CLIENT_SECRET=$(ssh -i ~/.ssh/claude_perflab -o StrictHostKeyChecking=no claude@172.85.0.30 \
      "VAULT_ADDR=https://127.0.0.1:8200 VAULT_SKIP_VERIFY=true vault kv get -field=claude-user-client-secret platform-engineering/perflab-iap-service-accounts")
    ```

### Authentication — OAuth2 Client Credentials

Token endpoint: `POST https://p6.pe.itential.io:3443/oauth/token`
- Content-Type: `application/x-www-form-urlencoded`
- Body fields: `grant_type=client_credentials`, `client_id`, `client_secret`
- Returns: `access_token` (bearer JWT), valid for 3600 seconds

```bash
TOKEN=$(/usr/bin/curl -sk \
  -X POST "https://p6.pe.itential.io:3443/oauth/token" \
  --resolve "p6.pe.itential.io:3443:172.85.0.18" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "grant_type=client_credentials" \
  --data-urlencode "client_id=$CLIENT_ID" \
  --data-urlencode "client_secret=$CLIENT_SECRET" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

### API Reference

The full IAP REST API documentation is available at:
`https://p6.pe.itential.io:3443/rest-api`

The raw OpenAPI spec (useful for programmatic path lookups) is at:
`GET /help/openapi?url=https://p6.pe.itential.io:3443` (requires auth token)

When the correct API path is unknown, fetch the OpenAPI spec and search it:
```bash
/usr/bin/curl -sk \
  "https://p6.pe.itential.io:3443/help/openapi?url=https://p6.pe.itential.io:3443" \
  --resolve "p6.pe.itential.io:3443:172.85.0.18" \
  -H "Authorization: Bearer $TOKEN" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for path, methods in d.get('paths', {}).items():
    if 'keyword' in path.lower():
        for m in methods: print(m.upper(), path)
"
```

**Known adapter management endpoints:**
- `GET /health/adapters` — list all adapters with status
- `PUT /adapters/{name}/restart` — restart a named adapter
- `PUT /adapters/{name}/start` — start a named adapter
- `PUT /adapters/{name}/stop` — stop a named adapter

**IMPORTANT — Adapter actions must target every IAP node directly:**
Start, stop, and restart operations must be sent to **each platform host individually** (not through the load balancer), because the LB will only route to one node. Target pe-iap01 (172.85.0.26) and pe-iap02 (172.85.0.27) directly:

```bash
for IAP_IP in 172.85.0.26 172.85.0.27; do
  echo "=== $IAP_IP ==="
  /usr/bin/curl -sk \
    -X PUT "https://$IAP_IP:3443/adapters/{name}/restart" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -w "\n[HTTP %{http_code}]\n"
done
```

### Making API calls

Use the bearer token in the `Authorization` header for all subsequent requests:

```bash
/usr/bin/curl -sk \
  -X GET "https://p6.pe.itential.io:3443/api/v1/<endpoint>" \
  --resolve "p6.pe.itential.io:3443:172.85.0.18" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json"
```

### Workflow summary

1. SSH to pe-hashivault → retrieve `claude-user-client-id` and `claude-user-client-secret` from `platform-engineering/perflab-iap-service-accounts`
2. POST to `/oauth/token` with form-encoded client credentials → get bearer token
3. Make API calls to `https://p6.pe.itential.io:3443` with `Authorization: Bearer <token>`

## Claude Automation User

Every perflab guest VM (all hosts in the inventory table above, including pe-ansible, pe-hashivault, and pe-mon01) has a dedicated `claude` OS user:

- **Login**: SSH key only (`~/.ssh/claude_perflab` / `~/.ssh/claude_perflab.pub`). The account has no password — its password is locked (`passwd -l`) on every host, so password-based login is not possible at all.
- **Sudo**: passwordless (`NOPASSWD: ALL`) via `/etc/sudoers.d/claude`. Every privileged command must still be prefixed with `sudo` — the account itself has no elevated rights until you invoke it.
- **Vault record**: `platform-engineering/perflab-ssh-service-account` holds `username` and `ssh_public_key` only, for reference. There is no password field or credential to fetch here.
- **Provisioning**: created via an Ansible playbook run against `inventories/blue/` (for the 17 hosts in that dynamic inventory) plus direct SSH for the three hosts outside it (pe-ansible, pe-hashivault, pe-mon01). This does **not** cover the Proxmox VM template — that is maintained manually through the Proxmox web UI, so brand-new VMs cloned from the template won't have this user until the template itself is updated.
- Ansible's own `remote_user` for connections it makes from pe-ansible to inventory targets is unaffected and still uses `pet-user` — see the note under Connection Details.

## Output Rules

- **Adapter/application listings**: Always include the version alongside the name. Never return a name-only list.

## Guidelines

- **Interpret group names loosely**: "IAP nodes", "mongo cluster", "redis", "IAG5" should map to the correct inventory groups.
- **For status/read operations**: run immediately without confirmation.
- **For destructive or service-impacting operations** (restarts, deployments, config changes): state what you are about to do and ask for confirmation before running.
- **For multi-host operations**: show which hosts will be targeted before executing.
- **Prefer Ansible** over raw SSH loops when targeting multiple hosts.
- **Show command output** clearly, especially errors.
- If the request is ambiguous, ask one clarifying question before proceeding.
