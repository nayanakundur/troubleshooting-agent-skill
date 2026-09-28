#!/bin/bash
set -euo pipefail

if [ $# -ne 3 ]; then
  echo "Usage: $0 <TestID> <Environment> <Date>" >&2
  exit 1
fi

TESTID="$1"
ENVIRONMENT="$2"
DATE="$3"

VAULT_ADDR="https://172.85.0.30:8200"
ROLE_ID=$(cat /home/pet-user/.vault-approle/role_id)
SECRET_ID=$(cat /home/pet-user/.vault-approle/secret_id)

LOGIN_RESP=$(curl -sk -X POST "$VAULT_ADDR/v1/auth/approle/login" \
  -d "{\"role_id\":\"$ROLE_ID\",\"secret_id\":\"$SECRET_ID\"}")
VAULT_TOKEN=$(echo "$LOGIN_RESP" | jq -r '.auth.client_token')

if [ "$VAULT_TOKEN" = "null" ] || [ -z "$VAULT_TOKEN" ]; then
  echo "Vault login failed" >&2
  exit 1
fi

SECRET_RESP=$(curl -sk -H "X-Vault-Token: $VAULT_TOKEN" "$VAULT_ADDR/v1/platform-engineering/data/perflab-test-analysis")
ANTHROPIC_API_KEY=$(echo "$SECRET_RESP" | jq -r '.data.data.ANTHROPIC_KEY')
ANTHROPIC_WORKSPACE_ID=$(echo "$SECRET_RESP" | jq -r '.data.data.ANTHROPIC_WORKSPACE_ID')
export ANTHROPIC_API_KEY
export ANTHROPIC_WORKSPACE_ID

if [ "$ANTHROPIC_API_KEY" = "null" ] || [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "Failed to fetch ANTHROPIC_KEY from Vault" >&2
  exit 1
fi

if [ "$ANTHROPIC_WORKSPACE_ID" = "null" ] || [ -z "$ANTHROPIC_WORKSPACE_ID" ]; then
  echo "Failed to fetch ANTHROPIC_WORKSPACE_ID from Vault" >&2
  exit 1
fi

cd /home/pet-user/Performance-Tests/loadgen/reports/

"$HOME/.npm-global/bin/claude" -p "/perf-test-analysis $TESTID $ENVIRONMENT $DATE" \
  --allowedTools "Read,Glob,Bash,Write"

unset ANTHROPIC_API_KEY ANTHROPIC_WORKSPACE_ID VAULT_TOKEN SECRET_ID ROLE_ID LOGIN_RESP SECRET_RESP
