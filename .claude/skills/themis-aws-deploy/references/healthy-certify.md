# Healthy Certification Reference

After running certify playbooks, read the fetched report files from `/tmp/itential-reports/<component>/` and evaluate them against these baselines. Report a clear PASSED / FAILED / WARNING per component and call out every deviation.

---

## How to Read the Reports

Report files land at:
- `/tmp/itential-reports/redis/redis-report-<hostname>.md`
- `/tmp/itential-reports/mongodb/mongodb-report-<hostname>.md`
- `/tmp/itential-reports/platform/platform-report-<hostname>.md`

Read every report. In multi-host architectures (HA2, ASA) there is one file per host — read all of them.

---

## Redis — Healthy Baseline

### Validation Summary (bottom of report)

| Check | Healthy value |
|-------|--------------|
| Overall Status | `PASSED ✓` |
| Redis Service Active | `ACTIVE ✓` |
| Redis Process Running | `YES ✓` |
| Redis Responding | `YES ✓` |
| Redis Config File Present | `YES ✓` |
| Sentinel Service Active _(if HA)_ | `ACTIVE ✓` |
| Sentinel Process Running _(if HA)_ | `YES ✓` |
| Sentinel Responding _(if HA)_ | `YES ✓` |
| Sentinel Quorum _(if HA)_ | `OK ✓` |
| Sentinel Monitoring _(if HA)_ | `1 master(s) ✓` |

### Connectivity

- Ping Response: `PONG`
- Connection Status: `SUCCESS ✓`

### User Auth Tests

All users listed must show `PASSED ✓`:
- `admin: PASSED ✓`
- `itential: PASSED ✓`
- `repluser: PASSED ✓` _(present only when replicas exist)_
- `sentineluser: PASSED ✓` _(present only when sentinels exist)_
- `monitor: PASSED ✓` _(present only when monitor user is enabled)_

Any `FAILED ✗` on a user auth test is a hard failure.

### TLS Certificates (when TLS enabled)

| Check | Healthy value |
|-------|--------------|
| TLS Status | `ENABLED` |
| Certificate file | `YES` |
| Private Key file | `YES` |
| CA Bundle file | `YES` |
| Cert-Key Match | `YES` |
| Chain Valid | `YES` |
| Expiry Warning | `OK` (not `EXPIRES WITHIN 30 DAYS`) |
| inventory_hostname in SANs | `YES` |
| ansible_host in SANs | `YES` |
| Live TLS Handshake | contains `Verify return code: 0 (ok)` |

### Red Flags

- `Overall Status: FAILED` — stop, do not proceed
- Any user auth `FAILED ✗`
- `Cert-Key Match: FAILED`
- `Chain Valid: FAILED`
- `EXPIRES WITHIN 30 DAYS` on cert or CA
- `inventory_hostname in SANs: NO` or `ansible_host in SANs: NO`
- Handshake verify code anything other than `0`
- Sentinel quorum `FAILED` or `No quorum was found!`
- `No masters are being monitored` (sentinel)

---

## MongoDB — Healthy Baseline

### Validation Summary (bottom of report)

| Check | Healthy value |
|-------|--------------|
| Overall Status | `PASSED ✓` |
| MongoDB Service Active | `ACTIVE ✓` |
| MongoDB Process Running | `YES ✓` |
| MongoDB Ping | `YES ✓` |
| Config File Present | `YES ✓` |
| Authentication Enabled | `YES ✓` |
| TLS Configured | `YES ✓` |
| Replica Set Configured _(if HA)_ | `YES ✓` |
| Replica Set Status _(if HA)_ | `OK ✓` |

### Replica Set Members (HA2 / ASA)

Each member must show:
- `State: PRIMARY` (exactly one) or `State: SECONDARY`
- `Health: 1`

Any member showing `RECOVERING`, `DOWN`, `STARTUP`, `UNKNOWN`, or `Health: 0` is a failure.

### User Auth Tests

- `admin: PASSED ✓`
- `itential: PASSED ✓`
- `monitor: PASSED ✓` _(when monitor user enabled)_

### TLS Certificates (when TLS enabled)

| Check | Healthy value |
|-------|--------------|
| TLS Mode | `requireTLS` in config |
| Server Cert (PEM) | `YES` |
| CA Bundle | `YES` |
| Replica Keyfile | `YES` |
| Chain Valid | `YES` |
| Expiry Warning | `OK` |
| inventory_hostname in SANs | `YES` |
| ansible_host in SANs | `YES` |
| Live TLS Handshake | contains `Verify return code: 0 (ok)` |

### Red Flags

- `Overall Status: FAILED ✗`
- `Authentication Enabled: NO ✗`
- `TLS Configured: NO ✗`
- Any replica member not in PRIMARY or SECONDARY state
- Any member with `Health: 0`
- `Replica Set Status: FAILED ✗`
- Any user auth `FAILED ✗`
- `Chain Valid: FAILED`
- `EXPIRES WITHIN 30 DAYS`
- Handshake verify code not `0`

---

## Platform — Healthy Baseline

Platform has no single "Overall Status" line — evaluate each section.

### Service Status

| Check | Healthy value |
|-------|--------------|
| Service State | `active` |
| Service SubState | `running` |
| Process Running | `YES ✓` |

### Configuration Files

| Check | Healthy value |
|-------|--------------|
| Config File Exists | `YES ✓` |
| Systemd Unit File | `YES ✓` |

### Connectivity (bottom of report)

| Check | Healthy value |
|-------|--------------|
| HTTP health check | `YES ✓` (port 3000) OR |
| HTTPS health check | `YES ✓` (port 3443) |
| MongoDB connectivity | `YES ✓` |
| Redis connectivity | `YES ✓` |

At least one of HTTP or HTTPS health check must pass. Both MongoDB and Redis connectivity must be `YES ✓`.

### HTTPS TLS Certificates

| Check | Healthy value |
|-------|--------------|
| Certificate | `YES` |
| Private Key | `YES` |
| Cert-Key Match | `YES` |
| Chain Valid | `YES — <hostname>.crt: OK` |
| Expiry Warning | `OK` |
| inventory_hostname in SANs | `YES` |
| ansible_host in SANs | `YES` |
| Live TLS Handshake | contains `Verify return code: 0 (ok)` |

### MongoDB Client TLS (when mongo_tls_enabled = true)

| Check | Healthy value |
|-------|--------------|
| CA Bundle | `YES` |
| Expiry Warning | `OK` |
| Live MongoDB TLS Handshake | contains `Verify return code: 0 (ok)` |

### Red Flags

- `Service State` not `active` or `SubState` not `running`
- `Process Running: NO ✗`
- `Config File Exists: NO ✗`
- Both HTTP and HTTPS health checks `NO ✗`
- `MongoDB connectivity: NO ✗`
- `Redis connectivity: NO ✗`
- `Cert-Key Match: NO`
- `Chain Valid: NO`
- `EXPIRING WITHIN 30 DAYS`
- Handshake verify code not `0`
- MongoDB TLS handshake failing when `mongo_tls_enabled = true`

---

## Evaluation Protocol

After reading all reports for a component:

1. Check the Validation Summary (Redis/MongoDB) or Service + Connectivity sections (Platform) first.
2. If Overall Status is `FAILED` or service is not running — report immediately, do not continue evaluating that host.
3. Check TLS section for each host — report any cert, chain, SAN, or handshake failure.
4. Check user auth — any `FAILED ✗` is a hard failure.
5. For HA architectures — check every host. A failure on one member is a cluster risk even if others pass.
6. Summarize per component: `PASSED`, `FAILED`, or `WARNING` (passed but has non-critical issues like near-expiry).
7. List every deviation found, with the hostname and section it came from.
