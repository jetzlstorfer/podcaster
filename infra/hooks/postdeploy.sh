#!/usr/bin/env bash
# Post-deploy hook: grant hosted narrator agent identity write access to audio blobs.
#
# Why post-deploy (not post-provision): the hosted agent identity does not exist
# until the agent is deployed.

set -euo pipefail

if [[ -z "${AZURE_STORAGE_ACCOUNT_URL:-}" ]]; then
  echo "AZURE_STORAGE_ACCOUNT_URL is not set; skipping narrator storage RBAC."
  exit 0
fi

if [[ -z "${AGENT_NARRATOR_NAME:-}" ]]; then
  echo "AGENT_NARRATOR_NAME is not set; skipping narrator storage RBAC."
  exit 0
fi

storage_host="${AZURE_STORAGE_ACCOUNT_URL#https://}"
storage_account="${storage_host%%.*}"

if [[ -z "$storage_account" ]]; then
  echo "Could not parse storage account name from AZURE_STORAGE_ACCOUNT_URL='${AZURE_STORAGE_ACCOUNT_URL}'."
  exit 0
fi

storage_id=$(az storage account show \
  --name "$storage_account" \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --query id -o tsv 2>/dev/null || true)

if [[ -z "$storage_id" ]]; then
  echo "Could not resolve storage account id for '$storage_account'; skipping narrator storage RBAC."
  exit 0
fi

echo "Resolving hosted narrator identity for service 'narrator'..."
agent_json=$(azd ai agent show narrator --output json 2>/dev/null || true)
if [[ -z "$agent_json" ]]; then
  echo "Could not read narrator agent details; skipping narrator storage RBAC."
  exit 0
fi

instance_pid=$(echo "$agent_json" | python3 -c "import json,sys; d=json.load(sys.stdin); print((d.get('instance_identity') or {}).get('principal_id') or '')")
blueprint_pid=$(echo "$agent_json" | python3 -c "import json,sys; d=json.load(sys.stdin); print((d.get('blueprint') or {}).get('principal_id') or '')")

if [[ -z "$instance_pid" && -z "$blueprint_pid" ]]; then
  echo "No narrator principal IDs found in azd output; skipping narrator storage RBAC."
  exit 0
fi

for pid in "$instance_pid" "$blueprint_pid"; do
  if [[ -z "$pid" ]]; then
    continue
  fi

  echo "Assigning Storage Blob Data Contributor to principal '$pid' on '$storage_id'"
  az role assignment create \
    --assignee-object-id "$pid" \
    --assignee-principal-type ServicePrincipal \
    --role "Storage Blob Data Contributor" \
    --scope "$storage_id" \
    --output none \
  && echo "  Done." \
  || echo "  Role may already exist or assignment failed (check permissions)."
done

echo "Post-deploy narrator storage RBAC complete."
