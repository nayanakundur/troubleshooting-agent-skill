# sample-env — .env Templates for /themis-aws-deploy

Copy the appropriate file to `.env` in the repo root before invoking `/themis-aws-deploy`.

| File | Architecture | VMs | Approx. Cost/hr |
|---|---|---|---|
| `env.aio.example` | AIO (All-in-One) | 1 | ~$0.15–0.20 |
| `env.minimal.example` | Minimal | 4 | ~$0.35–0.45 |
| `env.ha2.example` | HA2 (High Availability) | 9 | ~$0.80–1.00 |
| `env.asa.example` | ASA (Active-Standby) | 17 | ~$1.60–1.80 |

## Quick Start

```bash
# 1. Copy the template for your target architecture
cp sample-env/env.ha2.example .env

# 2. Fill in your values (search for fields that need values)
nano .env

# 3. Set the architecture in run-vars.yml
nano .claude/skills/themis-aws-deploy/run-vars.yml
# architecture: ha2

# 4. Invoke the skill
# /themis-aws-deploy
```

## What Goes in .env vs run-vars.yml

| Setting | Where |
|---|---|
| `architecture` (aio/minimal/ha2/asa) | `run-vars.yml` |
| `os`, `owner`, `platform_release` | `run-vars.yml` |
| `themis_root`, `deployer_repo`, `tls_repo` | `run-vars.yml` |
| AWS region, key pair, SG IDs, subnet IDs | `.env` |
| Instance types per role | `.env` |
| `JFROG_TOKEN`, `GITLAB_TOKEN` | `.env` |

## Notes

- `.env` is gitignored — never commit it.
- `generate_account_tfvars.py` (run at Step 1b) converts `.env` AWS_ fields into
  `tfvars-overrides/auto-account.tfvars` automatically — no manual tfvars editing needed.
- All 3 subnet IDs are required for every architecture (even AIO which only uses one).
  Themis always declares all three keys in the `subnet_map` HCL block.
- For `pe-team-sbx`: leave all `AWS_` fields blank. Themis defaults apply.
