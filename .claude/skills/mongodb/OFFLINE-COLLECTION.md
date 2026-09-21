# Offline Life-Report Collection

Two shell scripts gather the same data as the `/mongodb` full life report without needing
Claude, a network path back to your workstation, or interactive access. Use them for
air-gapped environments, unattended captures, or when you just want to hand off a single
text bundle for later analysis.

The scripts are a **pair**, split by scope:

| Script | Where to run | Scope | Output |
| --- | --- | --- | --- |
| `offline-collect-cluster.sh` | **Once**, from any node with `mongosh` + credentials | Database layer (mongosh) — replica set, `serverStatus`, `currentOp`, profiler, jobs/tasks indexes | `/tmp/mongo-life-cluster.txt` |
| `offline-collect-node.sh` | **Each** replica member | Guest-VM layer — CPU/mem/disk, THP, kernel/OOM, mongod service + logs | `/tmp/mongo-life-<host>.txt` |

> Scope note: `offline-collect-node.sh` collects **guest-VM signals only**. Physical disk
> SMART / ECC / RAID health must be collected on the hypervisor host separately (see the
> `proxmox_hypervisor` context — production VMs run on Proxmox and the host has OOM-killed VMs).

---

## Prerequisites

- **`mongosh`** installed on the node running the cluster script.
- **Admin or monitoring credentials** for the replica set (see below).
- **`sysstat`** for `iostat` on each member (`dnf install -y sysstat`). The node script degrades
  gracefully if it's missing.
- **`sudo`** on each member — the node script uses it for `du`, `dmesg`, `journalctl`,
  reading `/etc/mongod.conf`, and grepping `/var/log/mongodb/mongod.log`.

---

## Creating a dedicated monitoring user (recommended)

Don't run the collectors as the root `admin` user. Create a least-privilege monitoring user
instead. It needs two roles:

- `clusterMonitor` on `admin` — read-only access to `serverStatus`, `rs.status()`,
  `rs.conf()`, `currentOp`, `listDatabases`, etc.
- `read` on the application database (`itential`) — needed so the cluster script can read
  `system.profile`, collection counts, and index definitions on `jobs`/`tasks`.

Create it once, connected as `admin`. `passwordPrompt()` reads the password without echoing it
or leaving it in shell history:

```bash
mongosh "mongodb://admin:$ADMIN_PASS@<any-node>:27017/?authSource=admin" \
  --tls --tlsCAFile /etc/ssl/mongo-ca.pem --quiet --eval '
  db.getSiblingDB("admin").createUser({
    user: "monitor",
    pwd: passwordPrompt(),
    roles: [
      { role: "clusterMonitor", db: "admin" },
      { role: "read",           db: "itential" }
    ]
  })
'
```

(Drop the `--tls --tlsCAFile ...` flags if TLS is disabled.)

To rotate the monitoring password later:

```bash
mongosh "$MONGODB_URI" --quiet --eval '
  db.getSiblingDB("admin").updateUser("monitor", { pwd: passwordPrompt() })
'
```

---

## Configuring credentials via environment variables

The **cluster** script is driven entirely by env vars — nothing is hardcoded:

| Variable | Required | Meaning | Example |
| --- | --- | --- | --- |
| `MONGO_URI` | yes | Full `mongodb://` connection string incl. user, password, and `replicaSet` | see below |
| `MONGO_TLS` | yes | TLS flags, **or an empty string** if TLS is disabled | `--tls --tlsCAFile /etc/ssl/mongo-ca.pem` |
| `MONGO_DB` | no | Application database (default `itential`) | `itential` |

Set them without leaking the password into your shell history — prompt for it with `read -s`
and build the URI from the parts:

```bash
export MONGO_DB=itential

read -rsp "monitor password: " MPW; echo                 # -s = no echo
export MONGO_URI="mongodb://monitor:${MPW}@node1:27017,node2:27017,node3:27017/?authSource=admin&replicaSet=rs0"

# TLS on:
export MONGO_TLS="--tls --tlsCAFile /etc/ssl/mongo-ca.pem"
# TLS off (must still be set, to an empty string):
# export MONGO_TLS=""
```

**Password with special characters** (`@ : / ? # % &` etc.) must be percent-encoded in the URI,
otherwise it corrupts the connection string. Encode it first:

```bash
MPW_ENC=$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$MPW")
export MONGO_URI="mongodb://monitor:${MPW_ENC}@node1:27017,node2:27017,node3:27017/?authSource=admin&replicaSet=rs0"
```

The **node** script takes no credentials — it reads local OS/service state only, so it needs no
`MONGO_URI`, just `sudo`.

---

## Running

**1. Cluster data — run once** from any node with `mongosh` and the env vars set:

```bash
./offline-collect-cluster.sh
# wrote /tmp/mongo-life-cluster.txt (NNN lines)
```

**2. Per-node data — run on every replica member:**

```bash
./offline-collect-node.sh
# wrote /tmp/mongo-life-<host>.txt (NNN lines)
```

Copy the script to each member first (e.g. `scp`), or run it over SSH per host.

**3. Bundle all outputs** into one archive to hand off:

```bash
tar czf mongo-life-$(date -u +%Y%m%d-%H%M).tgz /tmp/mongo-life-*.txt
```

Gather the `/tmp/mongo-life-*.txt` files from every member into one place before running `tar`
(they're written locally on each node).

---

## What maps to the interactive skill

| Collector output | `SKILL.md` equivalent |
| --- | --- |
| `rs.status`, `rs.conf` | Sections 1–2 |
| `mem`, `wiredTiger.cache` | Section 4 |
| `writeConflicts`, `locks` | Section 5 |
| `currentOp` (total / slow / aggregate) | Section 6 |
| `profilingStatus`, `system.profile` | Section 7 |
| `listDatabases`, `connections` | Section 8 |
| `jobs` / `tasks` indexes + counts | Section 9 |
| vmstat / free / iostat / df (node) | Section 3 |
| THP (node) | Section 10 |
| dmesg / journal / OOM / mongod log (node) | Section 11 |

Feed the collected `.tgz` back into `/mongodb` and ask for the scored summary (Step 5) to get the
same HEALTHY / DEGRADED / CRITICAL assessment from the offline capture.

---

## Security notes

- Prefer the least-privilege `monitor` user over `admin` for collection.
- Never pass the password as a plain command-line argument (it lands in `ps` and shell history);
  use `read -s` / `passwordPrompt()`.
- The output files under `/tmp` contain configuration and topology detail. Delete them after
  handoff: `rm -f /tmp/mongo-life-*.txt`.
- The collectors are **read-only** — they never modify database or job state.
