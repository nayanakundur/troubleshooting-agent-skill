# Itential Product Capability Reference
**Source:** docs.itential.com | **Current GA:** Platform 6.4.0 + IAG 5.4 | **Updated:** 2026-06-09

---

## 1. Itential Platform — Product Overview

Itential Platform is a low-code network and infrastructure automation orchestration platform. It provides a visual workflow designer, execution engine, integration ecosystem, configuration management, and an operator-facing job management interface.

**GA Release Timeline:**
- Platform 6.0.0 — initial P6 feature release
- Platform 6.1.x — maintenance + WFE HA fixes
- Platform 6.2.0 — Config Mgr + inventory enhancements
- Platform 6.3.0 — project JST fixes, SSO improvements
- Platform 6.4.0 — **GA 2026-05-13 (on-prem), 2026-05-21 (cloud)**

---

## 1b. Platform Version History

> **docs.itential.com access note:** The site returns HTTP 303 for all page fetches (Fern framework session requirement). Use WebSearch with `site:docs.itential.com` or `site:itential.com` to retrieve content. Sitemap at `https://docs.itential.com/sitemap.xml` is accessible.

### Platform 6.x (Semantic versioning, current major line)

| Version | Release Date | Type | Key Features |
|---------|-------------|------|-------------|
| **6.4.0** | 2026-05-13 (on-prem) / 2026-05-21 (cloud) | Feature | **Run Code task** (Python on Canvas via IAG 5), **Compliance Reporting Dashboard** (plan scores, coverage, trends, drill-down), **Plan Creation Wizard**, **Proxy support** for integrations (centralized HTTPS proxy + per-integration override), Task auto-center on canvas, IAG 5.4 co-release |
| **6.3.x** | 2025 (exact date unconfirmed) | Feature | ENG fix versions reference 6.3.0 — JST/project fixes (ENG-9553: JSTs in childJob task), device group enhancements; exact feature list not publicly indexed |
| **6.2.x** | 2025 (exact date unconfirmed) | Feature | Referenced in fix versions; exact feature list not publicly indexed |
| **6.1.1** | 2025 (maintenance) | Maintenance | Bug fixes, security patches |
| **6.1.0** | 2025 | Feature | **Canvas Debug Mode** (simulate workflow execution at design time without live systems), Canvas Data Mocking |
| **6.0.8** | 2025 (maintenance) | Maintenance | Bug fixes including CyberArk CCP support |
| **6.0.0** | April 2025 | Feature | **New major version** (semantic versioning debut): OpenAPI 3.1 auth (mTLS, OAuth2.0, OIDC), Dynamic API Keys, LCM Instance Groups, LCM audit trail, Project reference auto-discovery and remapping, External reference validation on import |

### Prior Versioning Scheme (Date-Based)

| Version | Notes |
|---------|-------|
| 2023.2.x | Last date-based release series before P6; compatibility baseline for IAG 5.1 |
| 2023.1.x | End of support: Aug 19, 2025 |
| 2022.1.x | Legacy |

---

## 1c. IAG Version History

### IAG 5.x (Current line — co-released with Platform 6.x)

| Version | Release Date | Co-released with | Key Features |
|---------|-------------|-----------------|-------------|
| **5.4** | 2026-05-13 (on-prem) / 2026-05-21 (cloud) | Platform 6.4.0 | Cluster management improvements, AI readiness enhancements, Run Code task execution backend, FlowMCP Gateway enhancements |
| **5.3** | 2025 (unconfirmed) | Platform 6.3 | Details not publicly indexed |
| **5.2** | 2025 (unconfirmed) | Platform 6.2 | Details not publicly indexed |
| **5.1** | July 7, 2025 (published) | Platform 6.1 | **First IAG 5 GA release**: Git-native content, auto-built environments, cluster + runners, mTLS, native secret store, **Service Discovery**, **Kickstart Service Creation**, **pyproject.toml support**, **External DB** (etcd/DynamoDB), **Dependency registries** (PyPI/Ansible Galaxy) |
| **5.0** | July 2025 | Platform 6.0 | IAG 5 architectural debut: CLI management (no web UI), Git-based content, OpenTofu support |

**IAG 5 compatibility:** IAG 5.1+ requires Platform 6 or 2023.2 minimum.

**IAG 5 current limitations (as of 5.1):**
- ❌ HashiCorp Vault integration
- ❌ CyberArk integration
- ❌ Configuration Manager support

### IAG 4.x (Legacy line — maintenance only)

| Version | Release Date | Notes |
|---------|-------------|-------|
| **4.3.8** | Sep 5, 2025 | **Latest IAG 4 release** — bug fixes for issues reported to Product Support |
| **4.3.5** | Jun 13, 2025 | Bug fixes and security patches |
| **4.3.4** | May 7, 2025 | Bug fixes |
| **4.3.x** | 2024-2025 | Maintenance series aligned with IAP 2023.x and Platform 6 compatibility |
| **4.2.x** | 2023-2024 | Legacy |
| **4.1.x / 4.0.x** | Legacy | End of support |

**IAG 4 features (retained in 4.x, absent in IAG 5.1):**
- ✅ Web-based admin UI
- ✅ HashiCorp Vault integration
- ✅ CyberArk integration
- ✅ Configuration Manager support
- Supports Platform 6, 2023.2, 2023.1, 2022.1

**IAG 4 end of support:** Follows corresponding Platform version EOL dates.

---

## 2. Platform Components

### 2a. Automation Studio (Workflow Builder)
Low-code drag-and-drop visual workflow design environment.

| Feature | Description |
|---------|-------------|
| **Canvas** | Visual drag-and-drop interface; tasks connected by transitions |
| **Task Library** | 100+ built-in task types: adapters, applications, utility tasks |
| **childJob task** | Starts a sub-workflow (child job) inside the parent; parent waits for completion |
| **Query task** | Reshapes data using JSON-path expressions; extracts fields from task output |
| **Enable Query (Inline Query)** | Per-input query editor on any task input field — allows inline data transformation without a separate Query task. **Bug in 6.4.0: crashes on childJob tasks** (ISD-9261 / `childJobLoopIndex` TypeError) |
| **Transformation (JST)** | JavaScript Transformation scripts; incoming/outgoing scripts; sandboxed Node.js |
| **JSON Forms** | User input forms rendered in the UI at trigger time |
| **Command Templates** | CLI/device command templates with `<!var!>` variable syntax |
| **Jinja2 Templates** | Jinja2-based text templates for device configuration rendering |
| **Projects** | Group assets (workflows, templates, transformations, JSON forms) into versioned projects |
| **Canvas Debug Mode** | Isolated workflow simulation environment — test task behavior with mock inputs at design time without executing against live systems |
| **Run Code Task** *(6.4.0 NEW)* | Write and execute Python directly on the Canvas; code stored in task; testable at design time with live I/O; executes via IAG 5's isolated runtime |
| **Task Center** | Inspect any task on the canvas — auto-centers and highlights it (6.4.0) |
| **Variable wiring** | `$var.job.x` references; childJob variables array; merge/makeData/evalResult tasks |
| **Error transitions** | Mandatory on adapter/external tasks to prevent stuck jobs |

### 2b. Operations Manager
Job management and automation execution hub.

| Feature | Description |
|---------|-------------|
| **Jobs** | Every workflow run creates a Job instance; states: running, paused, cancelled, completed, error |
| **Job Metrics** | Job execution stats, duration, success/error rates |
| **Actionable Tasks** | Manual intervention tasks surfaced to operators; workable tasks (complete manually) + retryable tasks (failed, needs restart) |
| **Work Center** | Purpose-built operator UI for managing jobs awaiting human intervention; dedicated queue, decision context, audit trail |
| **Triggers** | Four trigger types: API endpoint, manual/form, scheduled (cron), event-based |
| **Automations** | Named, deployable workflow definitions with associated triggers |
| **Pause / Cancel / Delete** | Operators can pause, watch, cancel, or delete jobs from the dashboard |

### 2c. Configuration Manager (Golden Config)
Continuous configuration compliance and drift enforcement for CLI and API-managed devices.

| Feature | Description |
|---------|-------------|
| **Golden Configuration (CLI)** | Baseline configuration templates for network devices; compliance measured against running config |
| **Golden Configuration (JSON/API)** | Adapter-based compliance for API-managed systems |
| **Compliance Plans** | Group devices + golden config rules into plans; run compliance on schedule or demand |
| **Compliance Reporting Dashboard** *(6.4.0 NEW)* | Plan-level scores, connection success rates, device coverage, trend data across configurable time windows; drill from fleet view to individual device results |
| **Plan Creation Wizard** *(6.4.0 NEW)* | Redesigned step-by-step wizard for compliance plan creation |
| **Configuration Parsers** | TextFSM and Jinja2 parsers for extracting structured data from device CLI output |
| **Remediation** | Workflows triggered on compliance failure to push corrective config |
| **Device Groups** | Apply compliance plans to groups (same as individual devices) |

### 2d. Lifecycle Manager
State management for infrastructure resources across their lifecycle.

| Feature | Description |
|---------|-------------|
| **Resource Models** | JSON Schema definitions of manageable infrastructure resources (devices, services, VLANs, etc.) |
| **Instances** | Discrete occurrences of a resource model (e.g., a specific firewall, a specific VLAN) |
| **Lifecycle Actions** | Workflows bound to a resource type (create, update, delete, validate); update instance properties at runtime |
| **Instance tracking** | Persistent state record of each instance as it changes over time |

### 2e. Inventory Manager
Device and infrastructure inventory management.

| Feature | Description |
|---------|-------------|
| **Devices** | Register, list, and manage network devices; store device metadata |
| **Device Groups** | Logical groupings of devices for batch compliance, workflows, and golden config |
| **Nodes** | Golden config tree nodes associating devices with baseline templates |
| **Backups** | Device configuration backup and diff comparison |
| **Tags** | Device tagging for filtering and targeting |

### 2f. Adapters & Integrations
Integration ecosystem connecting Platform to external systems.

| Feature | Description |
|---------|-------------|
| **300+ pre-built adapters** | ITSM (ServiceNow, Jira), IaC (Ansible, Terraform), CI/CD (GitHub, Jenkins), monitoring (Splunk, Datadog), network (Cisco, Juniper, Nokia, Palo Alto, Arista...) |
| **Adapter Builder** | Upload Swagger/OpenAPI/Postman Collection → guided wizard generates custom adapter |
| **Integrations (Virtual)** | Lightweight integration objects referencing adapter instances; used in workflows |
| **genericAdapterRequest** | Generic REST call via any adapter; prepends adapter `base_path` to `uriPath` |
| **genericAdapterRequestNoBasePath** | Same but uses full path — bypasses base_path prepend |
| **Adapter type vs instance** | `app` field = type name from `apps.json`; `adapter_id` = instance name. Mixing them causes "No config found" errors |
| **Proxy Support** *(6.4.0 NEW)* | Centralized HTTPS proxy config at feature level; integrations inherit by default; per-integration overrides available |

### 2g. Authentication & Authorization

| Feature | Description |
|---------|-------------|
| **Local AAA** | Username/password with local user store |
| **LDAP** | Directory authentication against Active Directory/LDAP |
| **RADIUS** | RADIUS-based authentication |
| **SAML / SSO** | Azure Entra ID (Azure AD), PingID integration; browser-based SSO |
| **RBAC** | Role-based access control; users, groups, roles, permissions |
| **OAuth / JWT** | Token-based API auth; Bearer tokens for API calls |

### 2h. Secrets Management

| Feature | Description |
|---------|-------------|
| **HashiCorp Vault** | Secrets fetched at runtime by IAG; supports token revocation, rolling, and auditing |
| **CyberArk** | Enterprise PAM integration; password rotation and retrieval |
| **Migration note** | Platform 6+ requires migration away from `$ENC` encrypted secrets to Vault/CyberArk |

### 2i. FlowAI (Agentic Framework — GA Dec 2025)

| Feature | Description |
|---------|-------------|
| **FlowAgent Builder** | Application in Platform for creating role-based AI agents; define purpose, reasoning style, toolset |
| **FlowAgents** | Intelligent agents that reason through goals; execute via deterministic Itential workflows |
| **Autonomy thresholds** | Fully autonomous (low blast radius) / human-in-the-loop (approval required) / human-on-the-loop (monitored, no per-step approval) |
| **Work Center integration** | AI-initiated manual tasks route to human operators via Work Center |
| **FlowMCP Gateway** | Governance + auth + policy enforcement layer for external MCP tools (NetBox, Selector, Forward Networks) |
| **Itential MCP Server** | Exposes Platform workflows and assets as MCP tools consumable by AI agents |

---

## 3. Itential Automation Gateway (IAG)

IAG is a secure, governed execution environment for automation content — Python scripts, Ansible playbooks, and OpenTofu plans — organized as versioned, reusable services discoverable through Platform.

**Current versions:** IAG 5.4 (current GA, co-released with Platform 6.4)

---

### 3a. IAG 4 (Legacy)

| Feature | Description |
|---------|-------------|
| **Web UI** | Browser-based administration UI |
| **Python scripts** | Execute Python scripts as services |
| **Ansible playbooks** | Execute Ansible playbooks |
| **Terraform** | Execute Terraform plans |
| **HashiCorp Vault** | Secrets integration supported |
| **CyberArk** | Secrets integration supported |
| **Config Manager** | Supported as execution backend |

**Status:** Supported but deprecated path; IAG 5 is the strategic direction. Both versions can coexist during migration.

---

### 3b. IAG 5 (Current)

IAG 5 is a ground-up redesign with Git-native workflows, CLI management, and cluster-based scaling.

#### Core Architecture Changes vs IAG 4
| Area | IAG 4 | IAG 5 |
|------|-------|-------|
| Admin interface | Web UI | CLI only |
| Content management | Upload to server | Git repository at runtime |
| Environment management | Manual | Auto-built from requirements files |
| Scaling | Single node | Multi-node clusters + runner nodes |
| Vault/CyberArk | ✅ Supported | ❌ Not yet supported (as of 5.1) |
| Config Manager | ✅ Supported | ❌ Not yet supported (as of 5.1) |

#### IAG 5 Feature Set

| Feature | Description |
|---------|-------------|
| **Git-native content** | All automation content (scripts, playbooks, plans) lives in Git repos; fetched at runtime — no pre-installation required |
| **Service model** | Automations exposed as named services with defined inputs; discoverable via Platform without knowledge of underlying tooling |
| **Python execution** | Isolated Python environments; auto-built from `requirements.txt` or `pyproject.toml` |
| **Ansible execution** | Playbook execution with auto-managed Ansible environments and Galaxy dependencies |
| **OpenTofu plans** | Infrastructure-as-code execution (OpenTofu = open-source Terraform fork) |
| **Cluster + runners** | Add runner nodes by placing IAG binary on a Linux server and pointing at the cluster; horizontal scale-out |
| **5 deployment models** | Single all-in-one → multi-cluster spanning geographic regions or network segments |
| **mTLS connectivity** | IAG initiates outbound WebSocket + mutual TLS to Platform; no VPN required; no inbound firewall rules needed |
| **Native secret store** | Encrypted secret store built in; per-service secrets |
| **RBAC + audit** | Role-based access, encryption, auditing, policy enforcement on every execution |
| **Service Discovery** *(5.1)* | Automatic discovery and registration of available gateway services |
| **Kickstart Service Creation** *(5.1)* | Import all automation scripts from a repo in bulk |
| **pyproject.toml support** *(5.1)* | Modern Python project configuration support |
| **External DB** *(5.1)* | etcd or Amazon DynamoDB as external database backend |
| **Dependency registries** *(5.1)* | PyPI and Ansible Galaxy private registry support |
| **FlowMCP Gateway** *(5.x)* | Proxy + govern external MCP tool calls from AI agents |
| **Run Code (Platform 6.4)** | Python on the Canvas executes through IAG 5's isolated runtime |

---

## 4. Platform 6.4 — What's New Summary

Released: **2026-05-13** (on-prem) / **2026-05-21** (cloud)

| Feature | Area | Description |
|---------|------|-------------|
| **Run Code task** | Automation Studio | Write Python directly on the Canvas; stored in task; tested at design time with live I/O; runs via IAG 5 isolated runtime |
| **Task auto-center** | Automation Studio | Inspecting a task auto-centers and highlights it on the Canvas |
| **Compliance reporting dashboard** | Configuration Manager | Plan-level scores, connection rates, device coverage, trend data; fleet → device drill-down |
| **Plan creation wizard** | Configuration Manager | Redesigned step-by-step compliance plan creation |
| **Proxy support** | Integrations | Centralized HTTPS proxy at feature level; per-integration override |
| **IAG 5.4 co-release** | Gateway | Cluster management improvements; AI readiness enhancements |

---

## 5. Known Open Bugs (as of 2026-06-09)

| Ticket | Component | Description | Status |
|--------|-----------|-------------|--------|
| **ENG-16911** | Automation Studio UI | Child job / transformation opens in new browser window instead of Studio tab | Backlog, no fix version |
| **ISD-9261** | Automation Studio UI — childJob task panel | "Enable query" throws `TypeError: Cannot set properties of undefined (setting 'childJobLoopIndex')` on childJob tasks only; all other task types unaffected | No ENG bug filed yet; affects Platform 6.4.0 GA; confirmed repro by Ahmed Al-Zubidy |
| **ENG-27979** | Adapter — `@itentialopensource/adapter-utils` (`scrubSensitiveInfo`, shared lib) | Debug-log `FULL BODY` line silently drops `&` separators between form-encoded fields when none of the field names match the hardcoded sensitive-word list (e.g., adapters that rename fields via `external_name` to values like `credential_0`/`credential_1`). Confirmed via source tracing (adapter-utils v7.0.6) to be a **logging-only artifact** — does not affect the actual outbound wire request. Was initially suspected as the root cause of an AirWave adapter HTTP 403 (ISD-9593) but **confirmed not to be** — that 403 was actually caused by an AirWave account/role misconfiguration plus a blank `auth_request_datatype` sending an empty request body; see known-resolutions.md. | Backlog, no fix version — confirmed logging-only defect (surfaced via ISD-9593) |

---

## 6. Key Support Gotchas (for Troubleshooting)

| Pattern | Issue | Resolution |
|---------|-------|------------|
| `No config found for Adapter: {name}` | `app` field set to instance name not type name | Set `app` = type from `apps.json`, `adapter_id` = instance name |
| `$var` references not resolving | Task ID contains non-hex characters (e.g., `apush`) | Task IDs must be `[0-9a-f]{1,4}` — regenerate task |
| `Job has no available transitions` | No error transition on adapter/external task | Add `"state": "error"` transition to all adapter tasks |
| JST output is `null` | Missing `return` statement in outgoing/incoming script | Add `return result;` |
| Workflow won't start — "validation errors" | Workflow in draft state | Fix all validation errors listed in workflow definition |
| Task dialog won't open (22.x → P6 migration) | Missing `location` field on hand-authored tasks | Add `"location": "Application"` to affected tasks; platform fix: v8 migration (ENG-22105) |
| childJob parent stuck waiting forever | WFE memory spike (ENG-22494) | Fixed in Platform 6.4.0 |
| Enable query crashes on childJob | `childJobLoopIndex` TypeError (ISD-9261) | Workaround: use `makeData`/`merge`/JST upstream instead |

---

## 7. Version-Specific Behavioral Differences

**How to use:** Read this section in Phase 1 Step 1c after determining support status. For each row matching the customer's IAP version, extract the "What Does NOT Apply / Exist" and "Diagnostic Impact" and append them to `ticket_context.md` under a `## Version-Specific Behavioral Notes` block. Include these notes in the pre-investigation summary (Step 1h) and pass them to any sub-skill invocations in Phase 2.

**Adding entries:** Append a row per behavioral difference discovered during any investigation. Version range uses exact version (`23.2.x`), a bound (`< 6.0`, `6.x+`), or a release window (`23.2.x – 2023.1.x`). One row = one behavioral fact. These are proactive facts — not symptom-driven gotchas (those go in Section 6).

| Version Range | Component | What Applies | What Does NOT Apply / Exist | Diagnostic Impact |
|---|---|---|---|---|
| 23.2.x | Platform configuration | `services` array per profile in platform config controls which services start for that profile | `/etc/platform/properties` — this file does not exist in 23.2 deployments | Do not look for, reference, or attempt to read `/etc/platform/properties` when diagnosing 23.2 systems; use the platform config API (`GET /api/v2.0/platform/config`) to inspect service and profile settings instead |
| 6.5.1 (range unconfirmed — needs broader verification) | Adapter settings — `auth_request_datatype` (request_token / two-step-token auth) | Setting `auth_request_datatype` explicitly (e.g., `URLENCODE`) correctly builds and sends the token request body | Leaving `auth_request_datatype` blank does NOT reliably fall back to the adapter's `action.json`-level `requestDatatype` for the actual outbound body — confirmed via ISD-9593, where a blank value resulted in an empty token-request body (`Content-Length: 0`) despite `action.json` declaring `requestDatatype: URLENCODE` | Do not assume a blank `auth_request_datatype` is harmless because a fallback exists elsewhere in the adapter's config — verify the actual outbound request body (packet capture or target-system access log) rather than relying on source-level fallback logic. Set `auth_request_datatype` explicitly for `request_token`/two-step-token auth. |

## 8. Cloud Deployment Topology (itential-saas)

**Detection:** `project_type = itential-saas` in Jira, OR platform URL = `*.itential.io`.

### Architecture

| Layer | Who manages it | Access for support engineers |
|---|---|---|
| IAP application nodes | **Itential** (cloud-managed) | No direct SSH — use platform API only |
| IAG (IG4 or IG5) | **Customer** (on-premises) | SSH possible via `.env` SSH_HOST_N targets |
| MongoDB / Redis | **Itential** (cloud-managed) | Via Mongo URL in `.env` if Itential ops provides it |
| Adapters / integrations | Itential NATs customer IPs | See NAT section below |

### NAT and IP Whitelisting

Itential operates a NAT layer for all cloud customer adapter and integration traffic. The target system (ServiceNow, NetBox, Cisco NSO, IPAM, ITSM, etc.) sees a **NAT IP assigned by Itential**, not the customer's on-prem IP. Key implications:

- **Adapter OFFLINE for a cloud customer → NAT IP whitelisting is the first diagnostic check.** Confirm the current Itential NAT IP with cloud ops, then verify it is present in the target system's IP allowlist.
- NAT IP can change after a cloud infrastructure event (IP reallocation, region failover). When a previously working adapter goes OFFLINE without any customer-side change, a NAT IP rotation is the most likely cause.
- For cloud customers, never ask the customer to whitelist their own on-prem IP on an external target — the traffic will never arrive from that IP.

### SSH Targets in .env for Cloud Customers

`SSH_HOST_N` entries in `.env` should point only to **customer on-premises** infrastructure:
- IAG hosts (IG4 or IG5 nodes) — `SSH_ROLE_N=gateway`
- MongoDB / Redis if hosted on customer on-prem (uncommon for cloud customers)
- Any on-prem jump host or bastion

Never add IAP application node IPs as SSH targets for cloud customers — those nodes are Itential-managed and inaccessible via SSH.

### Diagnostic Constraints Specific to Cloud Customers

| Diagnostic | On-prem | Cloud (itential-saas) |
|---|---|---|
| SSH to IAP nodes | ✅ Available | ❌ Not available — Itential-managed |
| SSH to IAG nodes | ✅ Available | ✅ Available (customer on-prem) |
| Platform API (`/health`, adapters, jobs) | ✅ | ✅ via `*.itential.io` URL |
| Container logs (IAP) | Via SSH + `docker logs` | ❌ Not available directly — request from Itential cloud ops |
| IAG logs | Via SSH to IAG host | ✅ Via SSH to customer on-prem IAG host |
| Adapter OFFLINE root cause | Network, credentials, config, stub | Same + **NAT IP whitelist first** |
