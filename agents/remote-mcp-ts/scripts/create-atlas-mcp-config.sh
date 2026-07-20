#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Create or delete a cloud-dev Atlas Remote MCP group-level service account config.

Usage:
  scripts/create-atlas-mcp-config.sh create --group-id GROUP_ID \
    --gsa-client-id GSA_CLIENT_ID --gsa-client-secret GSA_CLIENT_SECRET \
    [--env-file .env.atlas]

  scripts/create-atlas-mcp-config.sh delete --group-id GROUP_ID --config-id CONFIG_ID \
    --gsa-client-id GSA_CLIENT_ID --gsa-client-secret GSA_CLIENT_SECRET

Environment fallbacks:
  ATLAS_GROUP_ID
  ATLAS_DEV_GSA_CLIENT_ID
  ATLAS_DEV_GSA_CLIENT_SECRET
  ATLAS_MCP_CONFIG_ID_DEV
  ATLAS_BASE_URL                         default: https://cloud-dev.mongodb.com
  ATLAS_MCP_SECRET_EXPIRES_AFTER_HOURS   default: 720
  ATLAS_MCP_REFRESH_INTERVAL_HOURS       default: 336
  ATLAS_MCP_INGRESS_MANAGED_BY           default: USER
  ATLAS_MCP_ROLES                        default: GROUP_OWNER

The create command prints exports for the remote MCP agent:
  MCP_GROUP_SA_ID_DEV
  MCP_GROUP_SA_SECRET_DEV
  ATLAS_MCP_CONFIG_ID_DEV
USAGE
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

shell_quote() {
  local value=${1//\'/\'\\\'\'}
  printf "'%s'" "$value"
}

write_env_assignment() {
  local key=$1
  local value=$2
  printf '%s=%s\n' "$key" "$(shell_quote "$value")"
}

write_env_file() {
  local path=$1
  local content=$2
  local tmp_path
  tmp_path=$(mktemp "${path}.tmp.XXXXXX")
  chmod 600 "$tmp_path"
  printf '%s\n' "$content" >"$tmp_path"
  mv "$tmp_path" "$path"
  chmod 600 "$path"
}

require_non_empty() {
  local name=$1
  local value=$2
  [[ -n "$value" ]] || die "$name is required"
}

require_integer() {
  local name=$1
  local value=$2
  [[ "$value" =~ ^[0-9]+$ ]] || die "$name must be an integer"
}

fetch_access_token() {
  curl -fsS -X POST "${base_url}/api/oauth/token" \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    -H 'Accept: application/json' \
    -u "${gsa_client_id}:${gsa_client_secret}" \
    -d 'grant_type=client_credentials' |
    jq -er '.access_token'
}

roles_json() {
  jq -R -c 'split(",") | map(gsub("^\\s+|\\s+$"; "")) | map(select(length > 0))' \
    <<<"$atlas_mcp_roles"
}

create_config() {
  local access_token roles body response client_id client_secret config_id output
  access_token=$(fetch_access_token)
  roles=$(roles_json)
  body=$(
    jq -n \
      --argjson secretExpiresAfterHours "$secret_expires_after_hours" \
      --argjson refreshIntervalHours "$refresh_interval_hours" \
      --arg ingressManagedBy "$ingress_managed_by" \
      --argjson roles "$roles" \
      '{
        secretExpiresAfterHours: $secretExpiresAfterHours,
        refreshIntervalHours: $refreshIntervalHours,
        ingressManagedBy: $ingressManagedBy,
        roles: $roles
      }'
  )

  response=$(
    curl -fsS -X POST "${base_url}/api/private/groups/${group_id}/mcpConfig" \
      -H 'Content-Type: application/json' \
      -H "Authorization: Bearer ${access_token}" \
      -d "$body"
  )

  client_id=$(jq -er '.clientId' <<<"$response")
  client_secret=$(jq -er '.clientSecret' <<<"$response")
  config_id=$(jq -er '.configId' <<<"$response")

  output=$(
    {
      write_env_assignment MCP_GROUP_SA_ID_DEV "$client_id"
      write_env_assignment MCP_GROUP_SA_SECRET_DEV "$client_secret"
      write_env_assignment ATLAS_MCP_CONFIG_ID_DEV "$config_id"
      write_env_assignment ATLAS_GROUP_ID "$group_id"
    }
  )

  if [[ -n "$env_file" ]]; then
    write_env_file "$env_file" "$output"
    printf 'Wrote Atlas Remote MCP credentials to %s\n' "$env_file" >&2
  else
    printf '%s\n' "$output"
  fi

  printf '\nCleanup commands:\n' >&2
  if [[ -n "$env_file" ]]; then
    printf '  set -a; source .env; source %s; set +a\n' "$(shell_quote "$env_file")" >&2
    printf '  %s delete --group-id "$ATLAS_GROUP_ID" --config-id "$ATLAS_MCP_CONFIG_ID_DEV"\n' "$(shell_quote "$0")" >&2
  else
    printf '  %s delete --group-id %s --config-id %s --gsa-client-id "$ATLAS_DEV_GSA_CLIENT_ID" --gsa-client-secret "$ATLAS_DEV_GSA_CLIENT_SECRET"\n' \
      "$(shell_quote "$0")" \
      "$(shell_quote "$group_id")" \
      "$(shell_quote "$config_id")" >&2
  fi
}

delete_config() {
  local access_token status
  require_non_empty "config id" "$config_id"
  access_token=$(fetch_access_token)
  status=$(
    curl -sS -o /dev/null -w '%{http_code}' -X DELETE \
      "${base_url}/api/private/groups/${group_id}/mcpConfig/${config_id}" \
      -H 'Content-Type: application/json' \
      -H "Authorization: Bearer ${access_token}"
  )
  case "$status" in
    200 | 202 | 204)
      printf 'Deleted Atlas Remote MCP config %s for group %s\n' "$config_id" "$group_id"
      ;;
    *)
      die "delete failed with HTTP status ${status}"
      ;;
  esac
}

if [[ $# -eq 0 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

action=$1
shift

group_id=${ATLAS_GROUP_ID:-}
gsa_client_id=${ATLAS_DEV_GSA_CLIENT_ID:-}
gsa_client_secret=${ATLAS_DEV_GSA_CLIENT_SECRET:-}
config_id=${ATLAS_MCP_CONFIG_ID_DEV:-}
base_url=${ATLAS_BASE_URL:-https://cloud-dev.mongodb.com}
secret_expires_after_hours=${ATLAS_MCP_SECRET_EXPIRES_AFTER_HOURS:-720}
refresh_interval_hours=${ATLAS_MCP_REFRESH_INTERVAL_HOURS:-336}
ingress_managed_by=${ATLAS_MCP_INGRESS_MANAGED_BY:-USER}
atlas_mcp_roles=${ATLAS_MCP_ROLES:-GROUP_OWNER}
env_file=

while [[ $# -gt 0 ]]; do
  case "$1" in
    --group-id)
      group_id=${2:-}
      shift 2
      ;;
    --gsa-client-id)
      gsa_client_id=${2:-}
      shift 2
      ;;
    --gsa-client-secret)
      gsa_client_secret=${2:-}
      shift 2
      ;;
    --config-id)
      config_id=${2:-}
      shift 2
      ;;
    --base-url)
      base_url=${2:-}
      shift 2
      ;;
    --secret-expires-after-hours)
      secret_expires_after_hours=${2:-}
      shift 2
      ;;
    --refresh-interval-hours)
      refresh_interval_hours=${2:-}
      shift 2
      ;;
    --ingress-managed-by)
      ingress_managed_by=${2:-}
      shift 2
      ;;
    --roles)
      atlas_mcp_roles=${2:-}
      shift 2
      ;;
    --env-file)
      env_file=${2:-}
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

require_command curl
require_command jq
require_non_empty "group id" "$group_id"
require_non_empty "GSA client id" "$gsa_client_id"
require_non_empty "GSA client secret" "$gsa_client_secret"
require_integer "secret expiry hours" "$secret_expires_after_hours"
require_integer "refresh interval hours" "$refresh_interval_hours"
base_url=${base_url%/}

case "$action" in
  create)
    create_config
    ;;
  delete)
    delete_config
    ;;
  *)
    die "unknown action: $action"
    ;;
esac
