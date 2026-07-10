#!/usr/bin/env bash
# Post-provision hook: grant the web app's managed identity the RBAC roles it
# needs to call Foundry AI models and Azure Speech TTS via Entra ID auth.
#
# azd sets these env vars from Bicep outputs before running this script:
#   WEB_IDENTITY_PRINCIPAL_ID  — principal ID of the user-assigned managed identity
#   FOUNDRY_PROJECT_ENDPOINT   — https://<account>.services.ai.azure.com/api/projects/<proj>
#   AZURE_SPEECH_RESOURCE_ID   — ARM ID of the Speech account (optional)
#
# Role GUIDs (built-in):
#   Azure AI User                5e0bd9bd-7b93-4f28-af87-19fc36ad61bd
#   Cognitive Services User      a97b65f3-24c7-4388-baec-2e87135dc908

set -euo pipefail

# ---------------------------------------------------------------------------
# 1. Azure AI User on the Foundry / AI Services account
# ---------------------------------------------------------------------------
# Derive the AI Services account scope from FOUNDRY_PROJECT_ENDPOINT.
# Endpoint format: https://<account>.services.ai.azure.com/api/projects/<project>
# We need: /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<account>

if [[ -z "${FOUNDRY_PROJECT_ENDPOINT:-}" ]]; then
  echo "FOUNDRY_PROJECT_ENDPOINT is not set — skipping Foundry RBAC."
else
  # Extract the account hostname prefix (e.g. "my-resource")
  account_host="${FOUNDRY_PROJECT_ENDPOINT#https://}"   # strip scheme
  account_name="${account_host%%.*}"                    # take up to first dot

  echo "Looking up AI Services account: ${account_name}"
  account_id=$(az cognitiveservices account list \
    --query "[?name=='${account_name}'].id | [0]" \
    -o tsv 2>/dev/null || true)

  if [[ -z "${account_id}" ]]; then
    echo "WARNING: Could not find AI Services account '${account_name}' in accessible subscriptions."
    echo "         Grant the 'Azure AI User' role on the AI Services resource manually:"
    echo "         Principal ID: ${WEB_IDENTITY_PRINCIPAL_ID}"
  else
    echo "Assigning Azure AI User to ${WEB_IDENTITY_PRINCIPAL_ID} on ${account_id}"
    az role assignment create \
      --role "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd" \
      --assignee-object-id "${WEB_IDENTITY_PRINCIPAL_ID}" \
      --assignee-principal-type ServicePrincipal \
      --scope "${account_id}" \
      --output none \
    && echo "  Done." \
    || echo "  Role may already exist or assignment failed (check permissions)."
  fi
fi

# ---------------------------------------------------------------------------
# 2. Cognitive Services User on the Speech account (Entra TTS auth)
# ---------------------------------------------------------------------------
if [[ -z "${AZURE_SPEECH_RESOURCE_ID:-}" ]]; then
  echo "AZURE_SPEECH_RESOURCE_ID is not set — skipping Speech RBAC."
else
  echo "Assigning Cognitive Services User to ${WEB_IDENTITY_PRINCIPAL_ID} on Speech resource."
  az role assignment create \
    --role "a97b65f3-24c7-4388-baec-2e87135dc908" \
    --assignee-object-id "${WEB_IDENTITY_PRINCIPAL_ID}" \
    --assignee-principal-type ServicePrincipal \
    --scope "${AZURE_SPEECH_RESOURCE_ID}" \
    --output none \
  && echo "  Done." \
  || echo "  Role may already exist or assignment failed (check permissions)."
fi

echo "Post-provision RBAC complete."
