---
name: deployer-inventory
description: Generate a combined itential.deployer + itential.iag5 Ansible inventory YAML file. Supports all four deployer topologies (aio, minimal, ha2, asa) with TLS on/off and default/custom passwords. IAG5 gateway replaces the deprecated gateway v4 role; supports single-server, HA Active/Standby, and distributed-execution topologies with etcd or DynamoDB backends. Accepts a YAML input file or prompts interactively. Prints the result and writes it to the specified output file.
argument-hint: [<path/to/input.yaml>]
---

Generate a combined `itential.deployer` + `itential.iag5` Ansible inventory file. Follow the steps below exactly.

## Step 1 — Determine input mode

If `$ARGUMENTS` is non-empty and looks like a file path, use **file mode**: read the file at that path and parse it as YAML. If `$ARGUMENTS` is empty or the file cannot be read, use **interactive mode**.

## Step 2 — Collect inputs

### File mode input schema

```yaml
topology: ha2                  # Required: aio | minimal | ha2 | asa
output_file: ./inventory.yaml  # Required

platform_release: 6            # Optional, default: 6
tls: false                     # Optional, default: false
custom_passwords: false        # Optional, default: false

# Platform RPM packages — always include at minimum these three
platform_packages:
  - itential-platform-<version>.noarch.rpm
  - itential-gateway_manager-<version>.noarch.rpm
  - itential-inventory-manager-<version>.noarch.rpm

# IAG5 gateway configuration
iag5:
  topology: ha_standby         # single | ha_standby | distributed
  backend: dynamodb            # local (single only) | etcd | dynamodb
  cluster_id: cluster_1        # identifies this cluster; default: cluster_1
  server_connect_hosts: gwm.example.com:8080
  server_packages:
    - <IAGCTL-RPM>
  client_packages:
    - <IAGCTL-TARBALL>
  # etcd hosts — required when backend: etcd (space-separated host:port pairs)
  etcd_hosts: etcd01.example.com:2379 etcd02.example.com:2379 etcd03.example.com:2379

hosts:
  # deployer host keys (vary by topology — see schemas below)
  # iag5 host keys (always present):
  iag5_servers:                # list; 1 for single, 2 for ha_standby/distributed
    - gateway-server01.example.com
    - gateway-server02.example.com
  iag5_runners:                # list; only for distributed topology
    - gateway-runner01.example.com
  iag5_clients:                # list
    - gateway-client01.example.com
  iag5_client_connect_host: gateway-server01.example.com  # server each client connects to
```

**Per-topology deployer `hosts` keys:**

*aio:*
```yaml
hosts:
  aio_host: host01.example.com
```

*minimal:*
```yaml
hosts:
  redis_master: redis01.example.com
  mongodb_primary: mongodb01.example.com
  platform: platform01.example.com
```

*ha2:*
```yaml
hosts:
  redis_master: redis01.example.com
  redis_replicas: [redis02.example.com, redis03.example.com]
  mongodb_primary: mongodb01.example.com
  mongodb_replicas: [mongodb02.example.com, mongodb03.example.com]
  platform: [platform01.example.com, platform02.example.com]
```

*asa:*
```yaml
hosts:
  redis_master: redis-data01.dc1.example.com
  redis_replicas: [redis-data02.dc1.example.com, redis-data01.dc2.example.com, redis-data02.dc2.example.com]
  redis_sentinels: [redis-sentinel01.dc1.example.com, redis-sentinel01.dc2.example.com, redis-sentinel01.dc3.example.com]
  mongodb_primary: mongodb-data01.dc1.example.com
  mongodb_replicas: [mongodb-data02.dc1.example.com, mongodb-data01.dc2.example.com, mongodb-data02.dc2.example.com]
  mongodb_arbiter: mongodb-arbiter01.dc3.example.com
  platform: [platform01.dc1.example.com, platform02.dc1.example.com, platform01.dc2.example.com, platform02.dc2.example.com]
```

### Interactive mode

Use `AskUserQuestion` for multiple-choice questions (max 4 options per question). Ask for hostnames as free text. Collect all answers before generating. Ask in this order:

**Deployer questions:**
1. Topology — aio / minimal / ha2 / asa
2. TLS — on or off
3. Passwords — default or custom
4. Deployer hostnames per topology schema above

**IAG5 questions:**
5. IAG5 topology — single-server (local backend) / HA Active/Standby / distributed execution
6. Backend — local (single only) / etcd / DynamoDB  *(skip if single-server; local is implied)*
7. Cluster ID — default `cluster_1` or custom
8. IAG5 server hosts (1 for single, 2 for HA/distributed)
9. IAG5 runner hosts (only for distributed)
10. IAG5 client hosts
11. Gateway Manager connect host (format: `hostname:port`)
12. IAG5 server RPM package
13. IAG5 client tarball package
14. etcd hosts (space-separated `host:port` list) — only if backend is etcd

**Final questions:**
15. Platform RPM packages — remind user the minimum baseline is always these three:
    - `itential-platform-<version>.noarch.rpm`
    - `itential-gateway_manager-<version>.noarch.rpm`
    - `itential-inventory-manager-<version>.noarch.rpm`
    Ask if they have additional packages to add beyond the baseline.
16. Output file path

## Step 3 — Generate the inventory YAML

### Universal rules

- Top-level key is `all:` with `vars:` and `children:`.
- `platform_release` goes in `all.vars`.
- The following iag5 global vars always go in `all.vars`:
  - `ansible_user: <ANSIBLE-USER>`
  - `repository_username: ""` (leave blank when using JFrog token auth) and `repository_password: <JFROG_TOKEN>` — JFrog Identity Token from `.env`; previously Nexus credentials, now JFrog (`itential.jfrog.io`). Add a vault note.
  - `gateway_secrets_encrypt_key: <ENCRYPT-KEY>` with comment: `# IAG5 secrets encryption key — 64-char hex string. / #  - example command to generate key: 'openssl rand -hex 32'`
  - `gateway_pki_src_dir: <PKI-DIR>` with comment: `# Local directory on the control node containing IAG5 TLS certificate files.`
- Do NOT include `mongodb_replication_enabled: true` — replication is inferred from the presence of the `mongodb_replica` group.
- Do NOT include a `gateway:` group — gateway v4 is deprecated. Use iag5 groups only.
- The leading comment block describes both deployer and iag5 components:
  ```yaml
  # In this example, the Deployer will install and configure Redis, MongoDB, and Itential Platform
  # in a <Topology Name> environment. Itential Automation Gateway 5 will be installed in
  # <IAG5 topology description> mode using <backend> as the shared backend.
  # <Default|Custom> Redis and MongoDB passwords will be used.
  # TLS will be configured <ON (default)|OFF>.
  ```
  For single-server IAG5 (local backend), omit the "using X as the shared backend" clause.

### Deployer group rules by topology

#### aio
```
redis_master:        hosts: [aio_host]
mongodb_primary:     hosts: [aio_host]  vars: TLS settings
platform:            hosts: [aio_host]  vars: encryption key, packages, mongo/redis connection
```
platform_mongo_url: `mongodb://localhost:27017`
platform_redis_host: `localhost`

#### minimal
```
redis_master:        hosts: [redis_master]
mongodb_primary:     hosts: [mongodb_primary]  vars: TLS settings
platform:            hosts: [platform]  vars: encryption key, packages, mongo URL, redis host
```
platform_mongo_url: `mongodb://<mongodb_primary>:27017/itential`
platform_redis_host: `<redis_master>`

#### ha2
```
redis_master:        hosts: [redis_master]
redis_replica:       hosts: redis_replicas
redis_sentinel:      hosts: [redis_master] + redis_replicas  (auto-derived)
redis_nodes:         hosts: [redis_master] + redis_replicas  (auto-derived; password vars here if custom)
mongodb_node:
  children:
    mongodb_primary: hosts: [mongodb_primary]
    mongodb_replica: hosts: mongodb_replicas
  vars: TLS settings [+ password vars if custom]
platform:            hosts: platform list  vars: encryption key, packages, mongo URL, sentinel list
```
platform_mongo_url: `mongodb://<primary>:27017,<replica1>:27017,<replica2>:27017/itential?replicaSet=rs0`
platform_redis_sentinels: [redis_master] + redis_replicas at port 26379

#### asa
```
redis_master:        hosts: [redis_master]
redis_replica:       hosts: redis_replicas
redis_sentinel:      hosts: redis_sentinels  (explicitly provided — NOT derived from data nodes)
redis_nodes:         hosts: [redis_master] + redis_replicas + redis_sentinels  (auto-derived; password vars here if custom)
mongodb_node:
  children:
    mongodb_primary: hosts: [mongodb_primary]
    mongodb_replica: hosts: mongodb_replicas
  vars: TLS settings [+ password vars if custom]
mongodb_arbiter:     hosts: [mongodb_arbiter]
platform:            hosts: platform list  vars: encryption key, packages, mongo URL, sentinel list
```
platform_mongo_url: data nodes only — `mongodb://<primary>:27017,<replica1>:27017,.../itential?replicaSet=rs0` — do NOT include the arbiter
platform_redis_sentinels: redis_sentinels list at port 26379

### IAG5 group rules by topology

All iag5 server and runner hosts use `ansible_host: <HOSTNAME-IP>` as a host variable.
All iag5 client hosts use `ansible_host: <HOSTNAME-IP>` and `gateway_client_host: <server-hostname>` as host variables (pointing to the first iag5 server or a load-balancer).

#### single (local backend)
```
iag5_servers:
  hosts: [server1]  (with ansible_host per host)
  vars:
    gateway_cluster_id: <cluster_id>
    gateway_server_connect_hosts: <gwm_host>
    gateway_server_packages: [<rpm>]

iag5_clients:
  hosts: [client1, ...]  (with ansible_host + gateway_client_host per host)
  vars:
    gateway_client_packages: [<tarball>]
```

#### ha_standby (etcd or DynamoDB)
```
iag5_servers:
  hosts: [server1 (# active), server2 (# standby)]  (with ansible_host per host)
  vars:
    gateway_cluster_id: <cluster_id>
    gateway_server_connect_hosts: <gwm_host>
    gateway_server_store_backend: etcd|dynamodb
    # if etcd: gateway_server_store_etcd_hosts: <host1:port> <host2:port> <host3:port>
    # if dynamodb: add comment "# NOTE: DynamoDB AWS configuration vars must also be set."
    gateway_server_packages: [<rpm>]

iag5_clients: (same as single)
```

#### distributed (etcd or DynamoDB)
```
iag5_servers:
  hosts: [server1 (# active), server2 (# standby)]  (with ansible_host per host)
  vars:
    gateway_cluster_id: <cluster_id>
    gateway_server_connect_hosts: <gwm_host>
    gateway_server_distributed_execution: true

iag5_runners:
  hosts: [runner1, runner2, ...]  (with ansible_host per host)

iag5_servers_runners:
  hosts: [all servers + all runners]
  vars:
    gateway_server_store_backend: etcd|dynamodb
    # if etcd: gateway_server_store_etcd_hosts: <host1:port> <host2:port> <host3:port>
    # if dynamodb: add comment "# NOTE: DynamoDB AWS configuration vars must also be set."
    gateway_server_packages: [<rpm>]

iag5_clients: (same as single)
```

### TLS vars

**TLS off:**
```yaml
# on mongodb_primary.vars / mongodb_node.vars:
mongodb_tls_enabled: false
mongodb_tls_copy_certs: false
# on platform.vars:
platform_mongo_tls_enabled: false
```

**TLS on** — omit `mongodb_tls_enabled` and `mongodb_tls_copy_certs` (default true); include:
```yaml
# on mongodb_primary.vars / mongodb_node.vars:
mongodb_pki_src_dir: </path/to/certs/on/control/node>
# commented-out overridable TLS vars (see ha2_tls / asa_tls examples for full list)

# on platform.vars:
platform_https_pki_src_dir: </path/to/platform/https/certs/on/control/node>
platform_mongodb_pki_src_dir: </path/to/platform/mongodb/certs/on/control/node>
# platform_mongo_tls_enabled: true  (commented — it's the default)
```

### Password vars

**Default passwords** — comment out with defaults shown:
```yaml
# platform_mongo_user: itential
# platform_mongo_password: itential
# platform_mongo_db_name: itential
# platform_redis_username: itential
# platform_redis_password: itential
# platform_redis_sentinel_username: sentineluser  # ha2/asa only
# platform_redis_sentinel_password: sentineluser  # ha2/asa only
```

**Custom passwords** — placeholder tokens with vault note:
```yaml
# NOTE: Using Ansible Vault or Hashicorp Vault for passwords is highly recommended.
redis_user_admin_password: <redis-admin-password>
redis_user_itential_password: <redis-itential-password>
redis_user_repluser_password: <redis-repluser-password>
redis_user_sentineladmin_password: <redis-sentineladmin-password>
redis_user_sentineluser_password: <redis-sentineluser-password>
redis_user_monitor_password: <redis-monitor-password>

mongodb_user_admin_password: <mongodb-admin-password>
mongodb_user_itential_password: <mongodb-itential-password>

platform_mongo_password: <mongodb-itential-password>
platform_redis_password: <redis-itential-password>
platform_redis_sentinel_password: <redis-sentineluser-password>  # ha2/asa only
```

For **aio/minimal**: Redis passwords on `redis_master.vars`, MongoDB passwords on `mongodb_primary.vars`, Platform passwords on `platform.vars`.
For **ha2/asa**: Redis passwords on `redis_nodes.vars`, MongoDB passwords on `mongodb_node.vars`, Platform passwords on `platform.vars` (including `platform_redis_sentinel_password`).

### Required platform vars (always include)

```yaml
# The Platform encryption key must be defined.
# It is an open64-length hex string, representing a 256-bit AES encryption key.
#  - example command to generate key: 'openssl rand -hex 32'
platform_encryption_key: <key>

# The Platform packages to install must be defined.
# NOTE: The Platform packages list varies depending on entitlement.
platform_packages:
  - itential-platform-<version>.noarch.rpm
  - itential-gateway_manager-<version>.noarch.rpm
  - itential-inventory-manager-<version>.noarch.rpm
  # additional entitlement packages may follow
```

## Step 4 — Output

1. Print the complete inventory YAML inside a fenced `yaml` code block.
2. Write the identical content to `output_file` using the Write tool.
3. Report the output file path.
