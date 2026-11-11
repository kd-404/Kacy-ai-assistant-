#!/usr/bin/env bash
# Deploys Kacy to Azure Container Apps. Run from the repo root after `az login`.
#
#   RESOURCE_GROUP=kacy-rg LOCATION=centralindia \
#   FOUNDRY_ACCOUNT=<foundry resource name> FOUNDRY_RG=<its resource group> \
#   IMAGE=ghcr.io/kd-404/kacy:latest \
#   bash infra/deploy.sh
#
# Secrets are read from backend/.env (never committed) and sent to Azure as Container Apps secrets.
set -euo pipefail

: "${RESOURCE_GROUP:?set RESOURCE_GROUP}" "${LOCATION:?set LOCATION}"
: "${FOUNDRY_ACCOUNT:?set FOUNDRY_ACCOUNT}" "${FOUNDRY_RG:?set FOUNDRY_RG}"
: "${IMAGE:?set IMAGE}"

set -a; source backend/.env; set +a

echo "1/3 Resource group"
az group create -n "$RESOURCE_GROUP" -l "$LOCATION" -o none

echo "2/3 Hosting (Container Apps, Azure Files, logs)"
OUT=$(az deployment group create -g "$RESOURCE_GROUP" -f infra/main.bicep -o json \
  --parameters image="$IMAGE" \
    foundryProjectEndpoint="$FOUNDRY_PROJECT_ENDPOINT" foundryModelDeployment="${FOUNDRY_MODEL_DEPLOYMENT:-gpt-4.1-mini}" \
    foundryAgentName="${FOUNDRY_AGENT_NAME:-kacy}" \
    searchEndpoint="$SEARCH_ENDPOINT" searchIndexName="${SEARCH_INDEX_NAME:-kacy-handbook}" searchApiKey="$SEARCH_API_KEY" \
    contentSafetyEndpoint="$CONTENT_SAFETY_ENDPOINT" contentSafetyKey="$CONTENT_SAFETY_KEY" \
    languageEndpoint="$LANGUAGE_ENDPOINT" languageKey="$LANGUAGE_KEY" \
    googleClientId="$GOOGLE_CLIENT_ID" googleClientSecret="$GOOGLE_CLIENT_SECRET" \
    userRoles="$KACY_USER_ROLES" timezone="${KACY_TZ:-Asia/Kolkata}")
APP_URL=$(echo "$OUT" | python3 -c "import sys,json;print(json.load(sys.stdin)['properties']['outputs']['appUrl']['value'])")
PRINCIPAL=$(echo "$OUT" | python3 -c "import sys,json;print(json.load(sys.stdin)['properties']['outputs']['principalId']['value'])")

echo "3/3 Let the app call Foundry as itself (managed identity, no key)"
FOUNDRY_ID=$(az cognitiveservices account show -n "$FOUNDRY_ACCOUNT" -g "$FOUNDRY_RG" --query id -o tsv)
az role assignment create --assignee-object-id "$PRINCIPAL" --assignee-principal-type ServicePrincipal \
  --role "Azure AI User" --scope "$FOUNDRY_ID" -o none || echo "(role may already exist)"

echo
echo "Live at: $APP_URL"
echo "Add this to your Google OAuth client:"
echo "  Authorized redirect URI:        $APP_URL/auth/google/callback"
echo "  Authorized JavaScript origin:   $APP_URL"
