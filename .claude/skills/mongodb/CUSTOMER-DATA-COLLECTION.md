# MongoDB Health Data Collection — Runbook

This runbook explains how to capture a **read-only diagnostic snapshot** of your MongoDB replica
set so it can be analyzed and turned into a MongoDB health ("life") report. It is designed for
**air-gapped or remote environments** — everything runs locally on your servers and produces plain
text files you review and send back. No live connection to Itential is required.

## What you received

- `offline-collect-cluster.sh` — collects database-level information (run once).
- `offline-collect-node.sh` — collects server/OS-level information (run on each database server).
- This runbook.

## Is this safe to run?

Yes. Both scripts are **strictly read-only**:

- They **never modify** database contents, job state, users, or configuration.
- They only *read* status and diagnostic information already exposed by MongoDB and the operating
  system.
- All output is written to plain text files on your own servers (`/tmp/mongo-life-*.txt`). Nothing
  is transmitted anywhere — **you** review the files and decide what to send back.

You are welcome to open the scripts (they are short, commented shell scripts) and review the exact
commands before running them.

---

## Prerequisites

- **`mongosh`** (the MongoDB Shell) installed on at least one database server.
- **MongoDB credentials** with permission to read status information (see *Recommended: monitoring
  user* below).
- **`sudo`** access on each database server (needed to read logs, service status, and OS metrics).
- **`sysstat`** for disk I/O metrics (`iostat`). Optional — the script continues without it.
  Install with `sudo dnf install -y sysstat` (RHEL/Rocky) or `sudo apt install -y sysstat`
  (Debian/Ubuntu) if you want disk I/O data included.

---

## Recommended: create a monitoring user

Rather than using your MongoDB administrator account, we recommend creating a dedicated,
least-privilege read-only user for the collection. It needs exactly two roles:

- `clusterMonitor` — read-only access to server and replica-set status.
- `read` on your application database (commonly `itential`) — so collection sizes and index
  definitions can be read.

Run this once, connected as an administrator. You will be prompted for the new password (it is not
shown on screen or saved to your command history):

```bash
mongosh "mongodb://<admin-user>:<admin-password>@<any-server>:27017/?authSource=admin" \
  --tls --tlsCAFile /path/to/ca.pem --quiet --eval '
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

If TLS/SSL is **not** enabled in your environment, omit the `--tls --tlsCAFile /path/to/ca.pem`
portion. If your application database is not named `itential`, change it in the `read` role above.

You can remove this user after the collection is complete:

```bash
mongosh "mongodb://<admin-user>:<admin-password>@<any-server>:27017/?authSource=admin" \
  --quiet --eval 'db.getSiblingDB("admin").dropUser("monitor")'
```

---

## Step 1 — Set the connection details (database server)

On the server where you will run the cluster script, set three environment variables. Building the
connection string with a password prompt keeps the password out of your command history:

```bash
# Application database name (default is "itential")
export MONGO_DB=itential

# Prompt for the password without displaying it, then build the connection string.
# List all replica set members, comma-separated, and set the correct replicaSet name.
read -rsp "monitoring user password: " MPW; echo
export MONGO_URI="mongodb://monitor:${MPW}@server1:27017,server2:27017,server3:27017/?authSource=admin&replicaSet=rs0"

# TLS/SSL flags. If TLS is ENABLED, point to your CA file:
export MONGO_TLS="--tls --tlsCAFile /path/to/ca.pem"
# If TLS is DISABLED, set it to an empty string instead:
# export MONGO_TLS=""
```

> **If your password contains special characters** (`@ : / ? # % &`), they must be percent-encoded
> in the connection string:
> ```bash
> MPW_ENC=$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$MPW")
> export MONGO_URI="mongodb://monitor:${MPW_ENC}@server1:27017,server2:27017,server3:27017/?authSource=admin&replicaSet=rs0"
> ```

Replace `server1/2/3`, `27017`, and `rs0` with your actual hostnames, port, and replica set name.

---

## Step 2 — Run the cluster collector (once)

From that same server, with the variables from Step 1 set:

```bash
chmod +x offline-collect-cluster.sh          # first time only
./offline-collect-cluster.sh
```

It prints a confirmation such as `wrote /tmp/mongo-life-cluster.txt (NNN lines)`.

---

## Step 3 — Run the node collector (on every server)

Copy `offline-collect-node.sh` to **each** database server and run it there. It needs no connection
details — it reads local operating-system and service information only:

```bash
chmod +x offline-collect-node.sh             # first time only
./offline-collect-node.sh
```

Each run produces `/tmp/mongo-life-<hostname>.txt` on that server.

---

## Step 4 — Gather and bundle the output

Collect all the `.txt` files into one place, then create a single archive:

```bash
# Copy each server's file to one machine first if needed, e.g.:
#   scp server2:/tmp/mongo-life-*.txt /tmp/
# Then bundle everything:
tar czf mongo-life-$(date -u +%Y%m%d-%H%M).tgz /tmp/mongo-life-*.txt

ls -lh mongo-life-*.tgz
```

---

## Step 5 — Review and return the archive

1. **Review the files before sending.** They contain configuration, topology, and diagnostic
   detail (see the tables below). Open them and confirm you are comfortable sharing the contents.
2. Send the `.tgz` archive back to your Itential contact through your agreed secure channel
   (support case attachment, secure file transfer, etc.).

That archive is everything needed to produce the MongoDB life report.

---

## What is collected

**Database level** (`offline-collect-cluster.sh`):

| Area | Details |
| --- | --- |
| Replica set | `rs.status()`, `rs.conf()` — members, health, roles, priorities |
| Server status | version, uptime, memory, WiredTiger cache, write conflicts, locks, connections |
| Active operations | current operations, slow operations (>1s), running aggregations |
| Databases | list of databases and their sizes on disk |
| Query profiling | profiler status and the 20 slowest recorded queries (if profiling is enabled) |
| Collections | document counts and index definitions for `jobs` and `tasks` |

**Server / OS level** (`offline-collect-node.sh`, per server):

| Area | Details |
| --- | --- |
| System | OS version, uptime |
| CPU & memory | `vmstat`, `free` |
| Disk | `iostat` (if installed), capacity (`df`), data directory size, block devices |
| Kernel | Transparent Huge Pages setting, kernel errors/warnings, out-of-memory events |
| MongoDB service | service state, restart count, `mongod.conf`, recent log warnings/errors |

## What is **not** collected

- **No passwords, keys, or certificates.** Credentials you type are used only to connect; they are
  not written to the output. Certificate/keyfile *paths* may appear in `mongod.conf`, but the files
  themselves are not read.
- **No document/record contents.** The collectors read counts, sizes, and index definitions — not
  the data stored in your collections.
- **No changes of any kind** to data, jobs, users, or configuration.

> Note: if MongoDB query profiling is enabled, the "slowest queries" section can include query
> *filter values* from recorded operations. Review that section during Step 5 if your queries may
> reference sensitive values.

---

## Cleanup

The output files are just diagnostics. After you have sent the archive you can remove them:

```bash
rm -f /tmp/mongo-life-*.txt
# and on each server where you ran the node collector
```

If you created the `monitor` user for this collection, drop it (see *Recommended: monitoring user*).

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `set MONGO_URI ...` error | The environment variables from Step 1 are not set in the current shell. Set them and re-run. |
| Authentication failed | Check the username/password and that `authSource=admin` matches where the user was created. |
| TLS/SSL handshake error | Confirm `MONGO_TLS` matches your environment — CA file path if TLS is on, empty string if off. |
| `iostat: command not found` | Install `sysstat` (optional), or ignore — the rest of the collection still runs. |
| Cannot connect from one server | Run the cluster collector from any *other* member that can reach the replica set. |
