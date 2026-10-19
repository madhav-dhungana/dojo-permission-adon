#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
LIBRARY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
source "$SCRIPT_DIR/local-stack-common.sh"

CONFIG_FILE="$SCRIPT_DIR/install-existing.conf"
PLAN=0
LOCK_DIR=""
PACKAGE_VERSION="0.3.0"
MANAGED_UPGRADE=0

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"; }
canonical_path() { python3 -c 'import os,sys; print(os.path.realpath(os.path.abspath(sys.argv[1])))' "$1"; }
cleanup() { if [[ -n "$LOCK_DIR" ]]; then rmdir "$LOCK_DIR" 2>/dev/null || true; fi; }
trap cleanup EXIT

usage() {
    cat <<'EOF'
Install the library on an existing DefectDojo 2.58.3 Compose deployment.

Prepare once:
  cp deploy/install-existing.conf.example deploy/install-existing.conf
  # Edit deploy/install-existing.conf with the existing deployment's details.
  bash deploy/install-existing.sh

Options:
  --plan          Validate and show the plan without building or changing files.
  --config FILE   Use another trusted Bash configuration file.
  -h, --help      Show this help.

The installer extends the exact running Django image, backs up the database
and configuration, applies only library migrations, and restarts application
services. It preserves the existing ports, nginx image, settings, and volumes.
The generated private Compose launcher includes the persistent image override.
Rerunning after git pull updates a managed 0.1.0, 0.2.0, or 0.3.0 installation while
retaining the previous enabled image for rollback.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --plan) PLAN=1; shift ;;
        --config) [[ $# -ge 2 ]] || die "--config requires a file"; CONFIG_FILE="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
done

need python3
need git
need curl
local_require_usable_checkout_path "$LIBRARY_ROOT"
local_add_docker_bin_to_path /Applications/Docker.app/Contents/Resources/bin
need docker
[[ -f "$CONFIG_FILE" && -r "$CONFIG_FILE" ]] || die "Configuration not found: $CONFIG_FILE. Copy deploy/install-existing.conf.example to deploy/install-existing.conf and edit it first."
CONFIG_FILE="$(canonical_path "$CONFIG_FILE")"

PROJECT_DIRECTORY=""
PROJECT_NAME=""
COMPOSE_FILES=()
COMPOSE_ENV=()
ENV_FILE=""
SETTINGS_FILE=""
HEALTH_URL=""
BACKUP_EVIDENCE=""
POSTGRES_SERVICE="postgres"
WEB_SERVICE="uwsgi"
INITIALIZER_SERVICE="initializer"
WORKER_SERVICE="celeryworker"
BEAT_SERVICE="celerybeat"
PROXY_SERVICE="nginx"
RESTART_PROXY=true
RELEASE_OVERRIDE=""
STATE_DIR=""
# The operator owns this trusted, private Bash configuration.
source "$CONFIG_FILE"

[[ -d "$PROJECT_DIRECTORY" ]] || die "PROJECT_DIRECTORY must name the existing deployment directory"
PROJECT_DIRECTORY="$(canonical_path "$PROJECT_DIRECTORY")"
local_require_usable_checkout_path "$PROJECT_DIRECTORY"
[[ "$PROJECT_NAME" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "Set PROJECT_NAME to the existing Compose project name"
[[ ${#COMPOSE_FILES[@]} -gt 0 ]] || die "COMPOSE_FILES must list the existing Compose files in order"
[[ -f "$SETTINGS_FILE" && -r "$SETTINGS_FILE" ]] || die "SETTINGS_FILE must be an existing readable local_settings.py"
SETTINGS_FILE="$(canonical_path "$SETTINGS_FILE")"
[[ "$SETTINGS_FILE" == */local_settings.py ]] || die "SETTINGS_FILE must name local_settings.py"
[[ "$HEALTH_URL" =~ ^https?://[^[:space:]]+$ ]] || die "HEALTH_URL must be an http:// or https:// URL"
[[ "$RESTART_PROXY" == true || "$RESTART_PROXY" == false ]] || die "RESTART_PROXY must be true or false"
for service in "$WEB_SERVICE" "$INITIALIZER_SERVICE" "$WORKER_SERVICE" "$BEAT_SERVICE" "$POSTGRES_SERVICE"; do
    [[ "$service" =~ ^[A-Za-z0-9_.-]+$ ]] || die "Invalid Compose service name: $service"
done
if [[ "$RESTART_PROXY" == true ]]; then
    [[ "$PROXY_SERVICE" =~ ^[A-Za-z0-9_.-]+$ ]] || die "Invalid PROXY_SERVICE"
fi
RELEASE_OVERRIDE="${RELEASE_OVERRIDE:-$PROJECT_DIRECTORY/compose.dojo-permission.release.yml}"
STATE_DIR="${STATE_DIR:-$PROJECT_DIRECTORY/.dojo-permission-existing}"
[[ ! -L "$STATE_DIR" && ! -L "$RELEASE_OVERRIDE" ]] || die "State and release paths must not be symbolic links"
RELEASE_OVERRIDE="$(canonical_path "$RELEASE_OVERRIDE")"
STATE_DIR="$(canonical_path "$STATE_DIR")"
[[ "$STATE_DIR" != / && "$STATE_DIR" != "$PROJECT_DIRECTORY" && "$STATE_DIR" != "$LIBRARY_ROOT" ]] || die "STATE_DIR must be a dedicated state directory"
[[ "$RELEASE_OVERRIDE" != "$SETTINGS_FILE" && "$RELEASE_OVERRIDE" != "$CONFIG_FILE" ]] || die "RELEASE_OVERRIDE must not overwrite configuration or settings"
for index in "${!COMPOSE_FILES[@]}"; do
    [[ -f "${COMPOSE_FILES[$index]}" && -r "${COMPOSE_FILES[$index]}" ]] || die "Compose file not found: ${COMPOSE_FILES[$index]}"
    COMPOSE_FILES[$index]="$(canonical_path "${COMPOSE_FILES[$index]}")"
    local_require_usable_checkout_path "${COMPOSE_FILES[$index]}"
    [[ "${COMPOSE_FILES[$index]}" != "$RELEASE_OVERRIDE" ]] || die "COMPOSE_FILES must exclude the generated RELEASE_OVERRIDE"
done
if [[ -n "$ENV_FILE" ]]; then
    [[ -f "$ENV_FILE" && -r "$ENV_FILE" ]] || die "ENV_FILE is not readable: $ENV_FILE"
    ENV_FILE="$(canonical_path "$ENV_FILE")"
    [[ "$RELEASE_OVERRIDE" != "$ENV_FILE" ]] || die "RELEASE_OVERRIDE must not overwrite ENV_FILE"
elif [[ -e "$PROJECT_DIRECTORY/.env" ]]; then
    [[ "$RELEASE_OVERRIDE" != "$(canonical_path "$PROJECT_DIRECTORY/.env")" ]] || die "RELEASE_OVERRIDE must not overwrite the deployment .env file"
fi
for assignment in ${COMPOSE_ENV[@]+"${COMPOSE_ENV[@]}"}; do
    [[ "$assignment" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || die "COMPOSE_ENV entries must be NAME=value"
    variable_name="${assignment%%=*}"
    case "$variable_name" in
        HOME|home|CODEX_HOME|PATH|BASH_ENV|ENV|SHELLOPTS|BASHOPTS|IFS|CDPATH|LD_*|DYLD_*|SCRIPT_DIR|LIBRARY_ROOT|CONFIG_FILE|PLAN|LOCK_DIR|PACKAGE_VERSION|MANAGED_UPGRADE|PROJECT_DIRECTORY|PROJECT_NAME|COMPOSE_FILES|COMPOSE_ENV|ENV_FILE|SETTINGS_FILE|HEALTH_URL|BACKUP_EVIDENCE|POSTGRES_SERVICE|WEB_SERVICE|INITIALIZER_SERVICE|WORKER_SERVICE|BEAT_SERVICE|PROXY_SERVICE|RESTART_PROXY|RELEASE_OVERRIDE|STATE_DIR) die "COMPOSE_ENV cannot override installer or shell/runtime variable $variable_name" ;;
    esac
    export "$assignment"
done
unset assignment variable_name

COMPOSE=(docker compose --project-directory "$PROJECT_DIRECTORY")
if [[ -n "$ENV_FILE" ]]; then COMPOSE+=(--env-file "$ENV_FILE"); fi
COMPOSE+=(--project-name "$PROJECT_NAME")
for compose_file in "${COMPOSE_FILES[@]}"; do COMPOSE+=(--file "$compose_file"); done
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"
docker info >/dev/null 2>&1 || die "Docker is not running or accessible"
COMPOSE_JSON="$("${COMPOSE[@]}" config --format json)"

# Inspect the resolved model privately: it may contain deployment secrets.
printf '%s' "$COMPOSE_JSON" | python3 -c '
import json, os, sys
model = json.load(sys.stdin)
settings, web, initializer, worker, beat, proxy, restart, postgres, evidence = sys.argv[1:]
services = model.get("services", {})
required = [web, initializer, worker, beat] + ([proxy] if restart == "true" else [])
if len(set([web, initializer, worker, beat])) != 4:
    sys.exit("ERROR: The four application service names must be distinct")
for name in required:
    if name not in services:
        sys.exit("ERROR: Compose service not found: " + name)
for name in [web, initializer, worker, beat]:
    found = False
    for mount in services[name].get("volumes", []):
        if not isinstance(mount, dict) or mount.get("type") != "bind":
            continue
        source, target = mount.get("source", ""), mount.get("target", "").rstrip("/")
        if target in ("/app/docker/extra_settings", "/app/dojo/settings"):
            source = os.path.join(source, "local_settings.py")
        elif target not in ("/app/docker/extra_settings/local_settings.py", "/app/dojo/settings/local_settings.py"):
            continue
        if os.path.realpath(source) == settings:
            found = True
    if not found:
        sys.exit("ERROR: SETTINGS_FILE is not bind-mounted as local_settings.py for " + name)
if not evidence and postgres not in services:
    sys.exit("ERROR: No local PostgreSQL service found. Set BACKUP_EVIDENCE for an external database")
' "$SETTINGS_FILE" "$WEB_SERVICE" "$INITIALIZER_SERVICE" "$WORKER_SERVICE" "$BEAT_SERVICE" "$PROXY_SERVICE" "$RESTART_PROXY" "$POSTGRES_SERVICE" "$BACKUP_EVIDENCE"

BASE_IMAGE_ID=""
RUNTIME_USER=""
WEB_CONTAINER=""
for service in "$WEB_SERVICE" "$WORKER_SERVICE" "$BEAT_SERVICE"; do
    container_id="$("${COMPOSE[@]}" ps -q "$service")"
    [[ -n "$container_id" && "$(printf '%s\n' "$container_id" | wc -w | tr -d ' ')" == 1 ]] || die "Exactly one running $service container is required"
    INSPECT_JSON="$(docker inspect "$container_id")"
    image_id="$(printf '%s' "$INSPECT_JSON" | python3 -c '
import json,os,sys
items=json.load(sys.stdin)
if len(items)!=1: sys.exit("ERROR: Expected one container")
item=items[0]; labels=item.get("Config",{}).get("Labels",{})
project, service, directory, settings=sys.argv[1:]
if not item.get("State",{}).get("Running"):
    sys.exit("ERROR: Service is not running: "+service)
if labels.get("com.docker.compose.project") != project or labels.get("com.docker.compose.service") != service:
    sys.exit("ERROR: Container project/service labels do not match: "+service)
working=labels.get("com.docker.compose.project.working_dir","")
if not working or os.path.realpath(working)!=directory:
    sys.exit("ERROR: Container working directory does not match PROJECT_DIRECTORY: "+service)
found=False
for mount in item.get("Mounts",[]):
    if mount.get("Type") != "bind": continue
    source=mount.get("Source", "")
    target=mount.get("Destination", "").rstrip("/")
    if target in ("/app/docker/extra_settings", "/app/dojo/settings"):
        source=os.path.join(source,"local_settings.py")
    elif target not in ("/app/docker/extra_settings/local_settings.py", "/app/dojo/settings/local_settings.py"):
        continue
    if os.path.realpath(source)==settings: found=True
if not found:
    sys.exit("ERROR: Running service does not mount SETTINGS_FILE: "+service)
image=item.get("Image","")
if not image.startswith("sha256:") or len(image)!=71:
    sys.exit("ERROR: Could not resolve the immutable running image for "+service)
print(image)
' "$PROJECT_NAME" "$service" "$PROJECT_DIRECTORY" "$SETTINGS_FILE")"
    if [[ -z "$BASE_IMAGE_ID" ]]; then
        BASE_IMAGE_ID="$image_id"
        WEB_CONTAINER="$container_id"
        RUNTIME_USER="$(printf '%s' "$INSPECT_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0].get("Config",{}).get("User", ""))')"
        RUNTIME_USER="${RUNTIME_USER:-0}"
    else
        [[ "$image_id" == "$BASE_IMAGE_ID" ]] || die "Web, worker, and beat must use the same exact Django image"
    fi
done
unset INSPECT_JSON image_id container_id

PROBE="$("${COMPOSE[@]}" exec -T "$WEB_SERVICE" python manage.py shell --verbosity 0 -c '
import json, sys, dojo, django
from django.apps import apps
from importlib import metadata
try:
    version = metadata.version("dojo-permission")
except metadata.PackageNotFoundError:
    version = None
print(json.dumps({"app_enabled": apps.is_installed("dojo_permission"), "package_version": version, "defectdojo_version": dojo.__version__, "django_version": django.get_version(), "python_version": list(sys.version_info[:2])}))
')"
APP_ENABLED="$(printf '%s' "$PROBE" | python3 -c '
import json,sys
p=json.load(sys.stdin)
if p.get("defectdojo_version")!="2.58.3" or p.get("django_version")!="5.2.14" or p.get("python_version")!=[3,13]:
    sys.exit("ERROR: Requires DefectDojo 2.58.3, Django 5.2.14, and Python 3.13")
if p.get("app_enabled") and p.get("package_version") not in {"0.1.0", "0.2.0", "0.3.0"}:
    sys.exit("ERROR: Existing library version requires a separate upgrade procedure")
print("true" if p.get("app_enabled") else "false")
')"
CURRENT_PACKAGE_VERSION="$(printf '%s' "$PROBE" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("package_version") or "")')"
REVISION="$(git -C "$LIBRARY_ROOT" rev-parse HEAD)"
if [[ "$PLAN" -ne 1 ]]; then
    [[ -z "$(git -C "$LIBRARY_ROOT" status --porcelain)" ]] || die "The library checkout must be clean and committed before installation"
fi
curl --fail --silent --show-error --max-time 15 -- "$HEALTH_URL" >/dev/null || die "The existing deployment does not pass HEALTH_URL"
"${COMPOSE[@]}" exec -T "$WEB_SERVICE" python manage.py check
"${COMPOSE[@]}" exec -T "$WEB_SERVICE" python manage.py migrate --check || die "Resolve the existing deployment's pending migrations first"

if [[ "$APP_ENABLED" == true ]]; then
    [[ -f "$RELEASE_OVERRIDE" && "$(head -n 1 "$RELEASE_OVERRIDE")" == '# Generated by dojo-permission deploy-existing-compose.sh' ]] || die "The library is already enabled outside this installer. Convert its existing integration to a reviewed managed deployment before installing or upgrading."
    ACTIVE_JSON="$("${COMPOSE[@]}" --file "$RELEASE_OVERRIDE" config --format json)"
    RELEASE_IMAGE="$(printf '%s' "$ACTIVE_JSON" | python3 -c '
import json,sys
services=json.load(sys.stdin).get("services",{})
images=[]
for service in sys.argv[1:]:
    cfg=services.get(service,{})
    enabled=str(cfg.get("environment",{}).get("DD_DOJO_PERMISSION_ENABLED", "")).lower()
    if enabled not in ("1","true","yes","on"): sys.exit("ERROR: Managed override does not enable every application service")
    images.append(cfg.get("image", ""))
if not images[0] or len(set(images))!=1: sys.exit("ERROR: Managed override application images do not match")
print(images[0])
' "$WEB_SERVICE" "$INITIALIZER_SERVICE" "$WORKER_SERVICE" "$BEAT_SERVICE")"
    RELEASE_IMAGE_ID="$(docker image inspect "$RELEASE_IMAGE" --format '{{.Id}}')"
    [[ "$RELEASE_IMAGE_ID" == "$BASE_IMAGE_ID" ]] || die "The managed release image does not match the running application images"
    MIGRATIONS="$("${COMPOSE[@]}" exec -T "$WEB_SERVICE" python manage.py showmigrations dojo_permission)"
    grep -F '[X] 0001_initial' <<<"$MIGRATIONS" >/dev/null || die "The managed library migration is not applied"
    CURRENT_SOURCE_REVISION="$(docker image inspect "$BASE_IMAGE_ID" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
    if [[ "$CURRENT_PACKAGE_VERSION" == "$PACKAGE_VERSION" && "$CURRENT_SOURCE_REVISION" == "$REVISION" ]]; then
        printf '\nThe managed library is already installed and healthy at this revision. No build or restart is needed.\n'
        printf 'Release override: %s\n' "$RELEASE_OVERRIDE"
        exit 0
    fi
    MANAGED_UPGRADE=1
fi

if [[ -e "$RELEASE_OVERRIDE" ]]; then
    [[ -f "$RELEASE_OVERRIDE" && "$(head -n 1 "$RELEASE_OVERRIDE")" == '# Generated by dojo-permission deploy-existing-compose.sh' ]] || die "RELEASE_OVERRIDE already exists and is not managed by this installer"
    if [[ "$MANAGED_UPGRADE" -ne 1 ]] && grep -Eq '^[[:space:]]*DD_DOJO_PERMISSION_ENABLED:[[:space:]]*"?true"?[[:space:]]*$' "$RELEASE_OVERRIDE"; then
        die "The release override remains enabled while the running library is disabled. Complete the managed rollback before reinstalling."
    fi
fi
if [[ -n "$BACKUP_EVIDENCE" ]]; then
    [[ -e "$BACKUP_EVIDENCE" && -r "$BACKUP_EVIDENCE" ]] || die "BACKUP_EVIDENCE is not readable"
    BACKUP_EVIDENCE="$(canonical_path "$BACKUP_EVIDENCE")"
    if [[ -f "$BACKUP_EVIDENCE" ]]; then
        [[ -s "$BACKUP_EVIDENCE" ]] || die "BACKUP_EVIDENCE is empty"
    elif [[ -d "$BACKUP_EVIDENCE" ]]; then
        [[ -n "$(find "$BACKUP_EVIDENCE" -mindepth 1 -print -quit 2>/dev/null)" ]] || die "BACKUP_EVIDENCE directory is empty"
    else
        die "BACKUP_EVIDENCE must be a file or directory"
    fi
else
    postgres_id="$("${COMPOSE[@]}" ps -q "$POSTGRES_SERVICE")"
    [[ -n "$postgres_id" && "$(printf '%s\n' "$postgres_id" | wc -w | tr -d ' ')" == 1 ]] || die "A single local PostgreSQL container is required for the automatic backup; otherwise set BACKUP_EVIDENCE"
fi

printf '\nEXISTING DEPLOYMENT INSTALLATION\n'
printf 'Compose project: %s\nDeployment:      %s\nBase image:      %s\n' "$PROJECT_NAME" "$PROJECT_DIRECTORY" "$BASE_IMAGE_ID"
printf 'Settings:        %s\nRelease override: %s\n' "$SETTINGS_FILE" "$RELEASE_OVERRIDE"
if [[ "$MANAGED_UPGRADE" -eq 1 ]]; then printf 'Library update:  %s -> %s (previous image stays enabled on rollback)\n' "$CURRENT_PACKAGE_VERSION" "$PACKAGE_VERSION"; fi
if [[ -n "$BACKUP_EVIDENCE" ]]; then printf 'Backup:          supplied backup evidence\n'; else printf 'Backup:          automatic PostgreSQL dump and configuration snapshot\n'; fi
printf 'Actions: build the library layer, migrate dojo_permission, restart application services'
if [[ "$RESTART_PROXY" == true ]]; then printf ', restart the existing proxy'; fi
printf '.\n'
if [[ "$PLAN" -eq 1 ]]; then
    printf '\nPlan validated. No files, images, containers, or database state were changed.\n'
    exit 0
fi

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RELEASE_ID="$PACKAGE_VERSION-${REVISION:0:12}-$TIMESTAMP-$$"
TARGET_IMAGE="local/dojo-permission-existing:$RELEASE_ID"
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"
LOCK_DIR="$STATE_DIR/lock"
mkdir "$LOCK_DIR" 2>/dev/null || { LOCK_DIR=""; die "Another installation appears to be running in $STATE_DIR/lock"; }
RUN_DIR="$(mktemp -d "$STATE_DIR/run.$TIMESTAMP.XXXXXX")"
chmod 700 "$RUN_DIR"
printf '%s\n' "$COMPOSE_JSON" > "$RUN_DIR/compose.before.json"
cp -p "$CONFIG_FILE" "$RUN_DIR/install-existing.conf"
cp -p "$SETTINGS_FILE" "$RUN_DIR/local_settings.py.before"
for index in "${!COMPOSE_FILES[@]}"; do cp -p "${COMPOSE_FILES[$index]}" "$RUN_DIR/compose.$index.yml"; done
if [[ -n "$ENV_FILE" ]]; then
    cp -p "$ENV_FILE" "$RUN_DIR/compose.env"
elif [[ -f "$PROJECT_DIRECTORY/.env" ]]; then
    cp -p "$PROJECT_DIRECTORY/.env" "$RUN_DIR/compose.env"
fi
if [[ -f "$RELEASE_OVERRIDE" ]]; then cp -p "$RELEASE_OVERRIDE" "$RUN_DIR/compose.release.before.yml"; fi
if [[ -z "$BACKUP_EVIDENCE" ]]; then
    printf '\nCreating and validating the database backup...\n'
    if ! "${COMPOSE[@]}" exec -T "$POSTGRES_SERVICE" sh -c 'exec pg_dump -U "${POSTGRES_USER:-defectdojo}" -d "${POSTGRES_DB:-defectdojo}" -Fc' > "$RUN_DIR/database.dump"; then
        die "Database backup failed; application services were not changed"
    fi
    [[ -s "$RUN_DIR/database.dump" ]] || die "The database backup is empty"
    if ! "${COMPOSE[@]}" exec -T "$POSTGRES_SERVICE" pg_restore --list < "$RUN_DIR/database.dump" > "$RUN_DIR/database.contents"; then
        die "Database backup validation failed; application services were not changed"
    fi
    BACKUP_EVIDENCE="$RUN_DIR/database.dump"
fi

# Retain the exact previous image after its application containers are replaced.
# The builder's own temporary base tag is deliberately removed when it exits.
ROLLBACK_TAG="local/dojo-permission-rollback:$RELEASE_ID"
docker image tag "$BASE_IMAGE_ID" "$ROLLBACK_TAG"

METADATA_FILE="$LIBRARY_ROOT/artifacts/existing-$RELEASE_ID.env"
BUILD_COMMAND=(bash "$SCRIPT_DIR/build-derived-image.sh" --base-image "$BASE_IMAGE_ID" --target-image "$TARGET_IMAGE" --output "$METADATA_FILE")
if [[ -n "$RUNTIME_USER" ]]; then BUILD_COMMAND+=(--runtime-user "$RUNTIME_USER"); fi
"${BUILD_COMMAND[@]}"
[[ -f "$METADATA_FILE" && "$(head -n 1 "$METADATA_FILE")" == '# Generated by dojo-permission build-derived-image.sh' ]] || die "The builder did not produce managed image metadata"
source "$METADATA_FILE"
[[ "${DOJO_PERMISSION_IMAGE:-}" =~ ^sha256:[0-9a-f]{64}$ ]] || die "The builder did not return a local immutable image ID"
[[ "${DOJO_PERMISSION_BASE_IMAGE:-}" == "$BASE_IMAGE_ID" ]] || die "The builder metadata does not match the running base image"

DEPLOY_COMMAND=(bash "$SCRIPT_DIR/deploy-existing-compose.sh")
for compose_file in "${COMPOSE_FILES[@]}"; do DEPLOY_COMMAND+=(--compose-file "$compose_file"); done
DEPLOY_COMMAND+=(--image "$DOJO_PERMISSION_IMAGE" --rollback-image "$BASE_IMAGE_ID" --settings-file "$SETTINGS_FILE" --release-override "$RELEASE_OVERRIDE" --health-url "$HEALTH_URL" --project-name "$PROJECT_NAME" --project-directory "$PROJECT_DIRECTORY" --backup-evidence "$BACKUP_EVIDENCE" --web-service "$WEB_SERVICE" --initializer-service "$INITIALIZER_SERVICE" --worker-service "$WORKER_SERVICE" --beat-service "$BEAT_SERVICE")
if [[ -n "$ENV_FILE" ]]; then DEPLOY_COMMAND+=(--env-file "$ENV_FILE"); fi
if [[ "$RESTART_PROXY" == true ]]; then DEPLOY_COMMAND+=(--proxy-service "$PROXY_SERVICE"); else DEPLOY_COMMAND+=(--no-proxy-restart); fi
DEPLOY_COMMAND+=(--library-only --apply)
if [[ "$MANAGED_UPGRADE" -eq 1 ]]; then DEPLOY_COMMAND+=(--managed-upgrade); fi

# Retain a complete, private invocation before cutover for recovery and audit.
{
    printf '# Generated by dojo-permission install-existing.sh\n'
    printf 'PROJECT_NAME=%q\nBASE_IMAGE_ID=%q\nDERIVED_IMAGE_ID=%q\n' "$PROJECT_NAME" "$BASE_IMAGE_ID" "$DOJO_PERMISSION_IMAGE"
    printf 'RELEASE_OVERRIDE=%q\nSETTINGS_FILE=%q\nBACKUP_EVIDENCE=%q\n' "$RELEASE_OVERRIDE" "$SETTINGS_FILE" "$BACKUP_EVIDENCE"
    printf 'ROLLBACK_TAG=%q\n' "$ROLLBACK_TAG"
    printf 'ROLLBACK_LIBRARY_ENABLED=%q\nPREVIOUS_PACKAGE_VERSION=%q\n' "$APP_ENABLED" "$CURRENT_PACKAGE_VERSION"
    printf 'SOURCE_REVISION=%q\nMETADATA_FILE=%q\n' "$REVISION" "$METADATA_FILE"
} > "$RUN_DIR/install.receipt"
LAUNCHER_CANDIDATE="$RUN_DIR/compose.sh"
{
    printf '#!/usr/bin/env bash\nset -Eeuo pipefail\n'
    printf '# Generated by dojo-permission install-existing.sh; contains private deployment configuration.\n'
    printf 'source %q\n' "$SCRIPT_DIR/local-stack-common.sh"
    printf 'local_add_docker_bin_to_path /Applications/Docker.app/Contents/Resources/bin\n'
    for assignment in ${COMPOSE_ENV[@]+"${COMPOSE_ENV[@]}"}; do printf 'export %q\n' "$assignment"; done
    if [[ -n "${CURL_CA_BUNDLE:-}" ]]; then printf 'export CURL_CA_BUNDLE=%q\n' "$CURL_CA_BUNDLE"; fi
    printf 'exec '
    printf '%q ' "${COMPOSE[@]}" --file "$RELEASE_OVERRIDE"
    printf '"$@"\n'
} > "$LAUNCHER_CANDIDATE"
chmod 700 "$LAUNCHER_CANDIDATE"
if "${DEPLOY_COMMAND[@]}"; then
    :
else
    deploy_status=$?
    printf 'status=failed\nexit_status=%s\n' "$deploy_status" >> "$RUN_DIR/install.receipt"
    printf 'Installation did not complete. Review the deployment output and private receipt: %s\n' "$RUN_DIR/install.receipt" >&2
    exit "$deploy_status"
fi
printf 'status=installed\n' >> "$RUN_DIR/install.receipt"
LAUNCHER_TMP="$(mktemp "$STATE_DIR/.compose.XXXXXX")"
cp -p "$LAUNCHER_CANDIDATE" "$LAUNCHER_TMP"
mv "$LAUNCHER_TMP" "$STATE_DIR/compose.sh"
printf '\nLibrary installation verified.\nBackup and receipt: %s\n' "$RUN_DIR"
printf 'Use the managed Compose launcher for future operations:\n  bash %q ps\n' "$STATE_DIR/compose.sh"
