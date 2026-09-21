# Troubleshooting Reference

---

## Provisioning (Step 3)

### `tofu: command not found`
OpenTofu not in PATH. Try `terraform` instead. If neither works, install OpenTofu:
https://opentofu.org/docs/intro/install/

### AWS credentials error
Profile expired or missing.
```bash
aws sso login --profile pe-team-sbx
aws sts get-caller-identity --profile pe-team-sbx   # verify
```

### `Error: No valid credential sources found`
`~/.aws/credentials` or `~/.aws/config` missing the `pe-team-sbx` profile. Check both files.

### Partial provisioning failure
If `tofu apply` fails mid-way, orphaned EC2 resources may exist. Destroy before retrying:
```bash
cd <themis-root>
ansible-playbook iag5-ansible/destroy.yml -e "workspace=/tmp/tofu-workspaces/<timestamp>" -v
```

### Subnet mapping error at plan time
Subnets in `terraform.tfvars` are AZ-specific. Verify the `subnet_map` entries match the target region's AZs (`us-east-1a`, `us-east-1b`, `us-east-1c`).

---

## Inventory Generation (Step 4)

### `ValueError: Terraform/OpenTofu state does not correspond to a deployed validated design`
Instance names in Terraform state don't match expected prefixes. Check that the `name` fields in your `tfvars/<design>.tfvars` match exactly:
- AIO: `all`
- Minimal: `redis`, `mongo`, `platform`, `gateway`
- HA2: `redis01–03`, `mongo01–03`, `platform01–02`, `gateway`
- ASA: `redis01–06`, `mongo01–05`, `platform01–04`, `gateway01–02`

### `FileNotFoundError: Command not found: tofu`
Pass `--tf-binary terraform` to `generate_inventory.py` if using Terraform instead of OpenTofu:
```bash
python3 scripts/generate_inventory.py --tf-binary terraform --working-dir ...
```

### `KeyError` on inventory generation
Terraform outputs are missing expected keys (`public_ips`, `hostnames`, `ami_name_used`). Re-run `tofu apply` to ensure state is complete.

---

## Deployment (Step 5)

### SSH timeout / unreachable hosts
`deploy.yml` waits up to 300s for SSH. If exceeded:
- Verify security group allows inbound SSH (port 22) from your IP
- Confirm `associate_public_ip = true` in `terraform.tfvars`
- Check EC2 instance state in AWS console

### `itential.deployer collection not found`
```bash
ansible-galaxy collection install -r iag5-ansible/requirements.yml
```

### `gateway_whl_file is undefined`
Required variable missing. Set it in `inventories/<design>/group_vars/gateway.yml`:
```yaml
gateway_whl_file: automation_gateway-4.3.0-py3-none-any.whl
gateway_release: "4.3"
```

### `platform_release is undefined`
Set in `inventories/<design>/group_vars/platform.yml` before deploying.

### Platform service fails to start
Redis and MongoDB must be running before Platform starts. Check:
```bash
# On redis host
systemctl status redis

# On mongo host
systemctl status mongod
```
If either is down, fix it before retrying Platform deployment.

### TLS errors on deploy
If `*_tls_enabled: true` is set, `*_pki_src_dir` must also be set and the cert files must exist at that path. All three must align: `tls_enabled`, `pki_src_dir`, and `copy_certs: true`.

### Weak default passwords warning
Default credentials (`admin`/`itential`/`sentineluser`) are fine for dev/POC. Override in group_vars before any production use.

---

## Vault (deploy-with-vault.yml only)

### `vault: connection refused`
Vault is at `http://172.85.0.30:8200`. Confirm you have network access to that host.

### `permission denied` on Vault secret
Your Vault token may lack access to `platform-engineering/nexus-pe-service-account`. Check with your Vault admin.

### Wrong mount point or secret path
Double-check the `-e` vars passed to `deploy-with-vault.yml`:
```bash
-e "vault_mount_point=platform-engineering"
-e "vault_secret_path=nexus-pe-service-account"
```
