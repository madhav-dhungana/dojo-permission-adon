#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

APPLY=0
DERIVED_IMAGE=""
ROLLBACK_IMAGE=""
SETTINGS_FILE=""
BACKUP_EVIDENCE=""
RELEASE_OVERRIDE=""
HEALTH_URL=""
PROJECT_NAME=""
PROJECT_DIRECTORY=""
ENV_FILE=""
WEB_SERVICE="uwsgi"
INITIALIZER_SERVICE="initializer"
WORKER_SERVICE="celeryworker"
BEAT_SERVICE="celerybeat"
PROXY_SERVICE="nginx"
RESTART_PROXY=1
LIBRARY_ONLY=0
MANAGED_UPGRADE=0
OVERRIDE_ENABLED=0
ROLLBACK_ENABLED="false"
COMPOSE_FILES=()

usage() {
    cat <<'EOF'
Plan or apply an installation of the permission image to a single-host
customized DefectDojo Docker Compose deployment. With no --apply flag this
script is read-only.

Example:
  deploy-existing-compose.sh \
    --compose-file /opt/dojo/docker-compose.yml \
    --image registry.example/custom-dojo-permission@sha256:<new-digest> \
    --rollback-image registry.example/custom-dojo@sha256:<old-digest> \
    --settings-file /opt/dojo/docker/extra_settings/local_settings.py \
    --release-override /opt/dojo/compose.dojo-permission.release.yml \
    --backup-evidence /secure/backups/dojo-before-permission.dump \
    --health-url https://dojo.example/login \
    --project-name defectdojo \
    --apply

Required:
  --compose-file FILE    Existing customized Compose file; repeat as needed.
  --image REF            Derived immutable digest or local sha256 image ID.
  --rollback-image REF   Current immutable Django image reference/ID.
  --settings-file FILE   Existing host local_settings.py; its content is
                         preserved and a managed hook is appended.
  --release-override FILE Persistent generated image-only Compose override.
  --health-url URL       Login/health URL checked after cutover.

Required with --apply:
  --backup-evidence PATH Existing DB/config backup file or directory.

Optional:
  --project-name NAME
  --project-directory DIR       default: directory of first Compose file
  --env-file FILE               explicit Compose environment file
  --web-service NAME            default: uwsgi
  --initializer-service NAME    default: initializer
  --worker-service NAME         default: celeryworker
  --beat-service NAME           default: celerybeat
  --proxy-service NAME          default: nginx
  --no-proxy-restart            do not restart the unchanged proxy container
  --library-only                apply only dojo_permission migrations; do not
                                run the deployment's full initializer
  --managed-upgrade             update a verified managed 0.1.0/0.2.0/0.3.0 release;
                                requires --library-only
  --apply                       perform migration and cutover
  -h, --help

This script never runs `compose down`, deletes volumes, changes nginx images,
or reverses database migrations. Clustered/multi-host deployments require a
separate blue/green procedure. The managed upgrade retains the previous enabled
library image and additive migrations for rollback.
EOF
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

need() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

is_immutable_image() {
    [[ "$1" =~ ^sha256:[0-9a-f]{64}$ || "$1" =~ @sha256:[0-9a-f]{64}$ ]]
}

valid_service_name() {
    [[ "$1" =~ ^[A-Za-z0-9_.-]+$ ]]
}

canonical_path() {
    python3 -c 'import os, sys; print(os.path.realpath(os.path.abspath(sys.argv[1])))' "$1"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --compose-file)
            [[ $# -ge 2 ]] || die "--compose-file requires a value"
            COMPOSE_FILES+=("$2")
            shift 2
            ;;
        --image)
            [[ $# -ge 2 ]] || die "--image requires a value"
            DERIVED_IMAGE="$2"
            shift 2
            ;;
        --rollback-image)
            [[ $# -ge 2 ]] || die "--rollback-image requires a value"
            ROLLBACK_IMAGE="$2"
            shift 2
            ;;
        --settings-file)
            [[ $# -ge 2 ]] || die "--settings-file requires a value"
            SETTINGS_FILE="$2"
            shift 2
            ;;
        --release-override)
            [[ $# -ge 2 ]] || die "--release-override requires a value"
            RELEASE_OVERRIDE="$2"
            shift 2
            ;;
        --backup-evidence)
            [[ $# -ge 2 ]] || die "--backup-evidence requires a value"
            BACKUP_EVIDENCE="$2"
            shift 2
            ;;
        --health-url)
            [[ $# -ge 2 ]] || die "--health-url requires a value"
            HEALTH_URL="$2"
            shift 2
            ;;
        --project-name)
            [[ $# -ge 2 ]] || die "--project-name requires a value"
            PROJECT_NAME="$2"
            shift 2
            ;;
        --project-directory)
            [[ $# -ge 2 ]] || die "--project-directory requires a value"
            PROJECT_DIRECTORY="$2"
            shift 2
            ;;
        --env-file)
            [[ $# -ge 2 ]] || die "--env-file requires a value"
            ENV_FILE="$2"
            shift 2
            ;;
        --web-service)
            [[ $# -ge 2 ]] || die "--web-service requires a value"
            WEB_SERVICE="$2"
            shift 2
            ;;
        --initializer-service)
            [[ $# -ge 2 ]] || die "--initializer-service requires a value"
            INITIALIZER_SERVICE="$2"
            shift 2
            ;;
        --worker-service)
            [[ $# -ge 2 ]] || die "--worker-service requires a value"
            WORKER_SERVICE="$2"
            shift 2
            ;;
        --beat-service)
            [[ $# -ge 2 ]] || die "--beat-service requires a value"
            BEAT_SERVICE="$2"
            shift 2
            ;;
        --proxy-service)
            [[ $# -ge 2 ]] || die "--proxy-service requires a value"
            PROXY_SERVICE="$2"
            shift 2
            ;;
        --no-proxy-restart)
            RESTART_PROXY=0
            shift
            ;;
        --library-only)
            LIBRARY_ONLY=1
            shift
            ;;
        --managed-upgrade)
            MANAGED_UPGRADE=1
            shift
            ;;
        --apply)
            APPLY=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "Unknown option: $1"
            ;;
    esac
done

need docker
need curl
need python3
[[ "$MANAGED_UPGRADE" -ne 1 || "$LIBRARY_ONLY" -eq 1 ]] || die "--managed-upgrade requires --library-only"

[[ ${#COMPOSE_FILES[@]} -gt 0 ]] || die "At least one --compose-file is required"
[[ -n "$DERIVED_IMAGE" ]] || die "--image is required"
[[ -n "$ROLLBACK_IMAGE" ]] || die "--rollback-image is required"
[[ -n "$SETTINGS_FILE" ]] || die "--settings-file is required"
[[ -n "$RELEASE_OVERRIDE" ]] || die "--release-override is required"
[[ -n "$HEALTH_URL" ]] || die "--health-url is required"
[[ "$HEALTH_URL" =~ ^https?://[^[:space:]]+$ ]] || die "--health-url must be an http:// or https:// URL without whitespace"
is_immutable_image "$DERIVED_IMAGE" || die "--image must be immutable"
is_immutable_image "$ROLLBACK_IMAGE" || die "--rollback-image must be immutable"
[[ "$DERIVED_IMAGE" != *[[:space:]\"\']* ]] || die "Invalid derived image reference"
[[ "$ROLLBACK_IMAGE" != *[[:space:]\"\']* ]] || die "Invalid rollback image reference"
[[ -f "$SETTINGS_FILE" ]] || die "Settings file not found: $SETTINGS_FILE"
SETTINGS_FILE="$(canonical_path "$SETTINGS_FILE")"
RELEASE_OVERRIDE="$(canonical_path "$RELEASE_OVERRIDE")"

NORMALIZED_COMPOSE_FILES=()
for compose_file in "${COMPOSE_FILES[@]}"; do
    [[ -f "$compose_file" ]] || die "Compose file not found: $compose_file"
    NORMALIZED_COMPOSE_FILES+=("$(canonical_path "$compose_file")")
done
COMPOSE_FILES=("${NORMALIZED_COMPOSE_FILES[@]}")
if [[ -z "$PROJECT_DIRECTORY" ]]; then
    PROJECT_DIRECTORY="$(dirname "${COMPOSE_FILES[0]}")"
else
    [[ -d "$PROJECT_DIRECTORY" ]] || die "Project directory not found: $PROJECT_DIRECTORY"
    PROJECT_DIRECTORY="$(cd "$PROJECT_DIRECTORY" && pwd -P)"
fi
if [[ -n "$ENV_FILE" ]]; then
    [[ -f "$ENV_FILE" ]] || die "Compose environment file not found: $ENV_FILE"
    ENV_FILE="$(canonical_path "$ENV_FILE")"
fi

for protected_path in "${COMPOSE_FILES[@]}" "$SETTINGS_FILE"; do
    [[ "$RELEASE_OVERRIDE" != "$protected_path" ]] || die "--release-override must not overwrite an input file: $protected_path"
done
if [[ -n "$ENV_FILE" ]]; then
    [[ "$RELEASE_OVERRIDE" != "$ENV_FILE" ]] || die "--release-override must not overwrite --env-file"
elif [[ -e "$PROJECT_DIRECTORY/.env" ]]; then
    DEFAULT_ENV_FILE="$(canonical_path "$PROJECT_DIRECTORY/.env")"
    [[ "$RELEASE_OVERRIDE" != "$DEFAULT_ENV_FILE" ]] || die "--release-override must not overwrite the project's .env file"
fi
if [[ -e "$RELEASE_OVERRIDE" ]]; then
    [[ -f "$RELEASE_OVERRIDE" ]] || die "Existing --release-override is not a regular file"
    [[ "$(head -n 1 "$RELEASE_OVERRIDE")" == '# Generated by dojo-permission deploy-existing-compose.sh' ]] || die "Existing --release-override is not managed by this script"
    if grep -Eq '^[[:space:]]*DD_DOJO_PERMISSION_ENABLED:[[:space:]]*"?true"?[[:space:]]*$' "$RELEASE_OVERRIDE"; then
        OVERRIDE_ENABLED=1
        [[ "$MANAGED_UPGRADE" -eq 1 ]] || die "The managed release is already enabled; use install-existing.sh to update it"
    fi
fi
[[ "$MANAGED_UPGRADE" -ne 1 || "$OVERRIDE_ENABLED" -eq 1 ]] || die "A managed upgrade requires the existing enabled generated release override"
for service in "$WEB_SERVICE" "$INITIALIZER_SERVICE" "$WORKER_SERVICE" "$BEAT_SERVICE"; do
    valid_service_name "$service" || die "Invalid service name: $service"
done
if [[ "$RESTART_PROXY" -eq 1 ]]; then
    valid_service_name "$PROXY_SERVICE" || die "Invalid service name: $PROXY_SERVICE"
fi

COMPOSE=(docker compose --project-directory "$PROJECT_DIRECTORY")
if [[ -n "$ENV_FILE" ]]; then
    COMPOSE+=(--env-file "$ENV_FILE")
fi
if [[ -n "$PROJECT_NAME" ]]; then
    COMPOSE+=(-p "$PROJECT_NAME")
fi
for compose_file in "${COMPOSE_FILES[@]}"; do
    COMPOSE+=(-f "$compose_file")
done

"${COMPOSE[@]}" config -q
AVAILABLE_SERVICES="$("${COMPOSE[@]}" config --services)"
for service in "$WEB_SERVICE" "$INITIALIZER_SERVICE" "$WORKER_SERVICE" "$BEAT_SERVICE"; do
    grep -Fx "$service" <<<"$AVAILABLE_SERVICES" >/dev/null || die "Compose service not found: $service"
done
if [[ "$RESTART_PROXY" -eq 1 ]]; then
    grep -Fx "$PROXY_SERVICE" <<<"$AVAILABLE_SERVICES" >/dev/null || die "Compose service not found: $PROXY_SERVICE"
fi

EXPECTED_ROLLBACK_ID=""
if docker image inspect "$ROLLBACK_IMAGE" >/dev/null 2>&1; then
    EXPECTED_ROLLBACK_ID="$(docker image inspect "$ROLLBACK_IMAGE" --format '{{.Id}}')"
elif [[ "$APPLY" -eq 1 ]]; then
    docker pull "$ROLLBACK_IMAGE"
    EXPECTED_ROLLBACK_ID="$(docker image inspect "$ROLLBACK_IMAGE" --format '{{.Id}}')"
fi

CURRENT_WEB_IMAGE_ID=""
CURRENT_WEB_CONTAINER_ID=""
for service in "$WEB_SERVICE" "$WORKER_SERVICE" "$BEAT_SERVICE"; do
    service_containers="$("${COMPOSE[@]}" ps -q "$service")"
    [[ -n "$service_containers" ]] || die "The current $service service is not running"
    [[ "$(wc -w <<<"$service_containers" | tr -d ' ')" == "1" ]] || die "Only single-replica services are supported ($service has more than one container)"
    current_service_image_id="$(docker inspect "$service_containers" --format '{{.Image}}')"
    if [[ -n "$EXPECTED_ROLLBACK_ID" && "$current_service_image_id" != "$EXPECTED_ROLLBACK_ID" ]]; then
        die "Running $service image does not match --rollback-image"
    fi
    if [[ "$service" == "$WEB_SERVICE" ]]; then
        CURRENT_WEB_IMAGE_ID="$current_service_image_id"
        CURRENT_WEB_CONTAINER_ID="$service_containers"
    fi
    if [[ "$MANAGED_UPGRADE" -eq 1 ]]; then
        service_permission_env="$(docker inspect "$service_containers" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^DD_DOJO_PERMISSION_ENABLED=//p' | tail -1)"
        case "$service_permission_env" in
            1|true|TRUE|True|yes|YES|Yes|on|ON|On) ;;
            *) die "Managed upgrade requires the running $service service to enable the library" ;;
        esac
    fi
done

CURRENT_PERMISSION_ENV="$(docker inspect "$CURRENT_WEB_CONTAINER_ID" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^DD_DOJO_PERMISSION_ENABLED=//p' | tail -1)"
case "$CURRENT_PERMISSION_ENV" in
    1|true|TRUE|True|yes|YES|Yes|on|ON|On)
        [[ "$MANAGED_UPGRADE" -eq 1 ]] || die "The running deployment already enables dojo_permission; use install-existing.sh for a managed update"
        ;;
esac
if [[ "$MANAGED_UPGRADE" -eq 1 ]]; then
    MANAGED_RELEASE_JSON="$("${COMPOSE[@]}" -f "$RELEASE_OVERRIDE" config --format json)"
    MANAGED_RELEASE_IMAGE="$(printf '%s' "$MANAGED_RELEASE_JSON" | python3 -c '
import json,sys
services=json.load(sys.stdin).get("services",{})
images=[]
for name in sys.argv[1:]:
    service=services.get(name,{})
    if str(service.get("environment",{}).get("DD_DOJO_PERMISSION_ENABLED", "")).lower() not in {"1","true","yes","on"}:
        sys.exit("ERROR: Every managed application service must remain enabled")
    images.append(service.get("image", ""))
if not images[0] or len(set(images)) != 1:
    sys.exit("ERROR: The managed application images must match")
print(images[0])
' "$WEB_SERVICE" "$INITIALIZER_SERVICE" "$WORKER_SERVICE" "$BEAT_SERVICE")"
    [[ "$(docker image inspect "$MANAGED_RELEASE_IMAGE" --format '{{.Id}}')" == "$CURRENT_WEB_IMAGE_ID" ]] || die "The managed release does not match the current application image"
    "${COMPOSE[@]}" exec -T "$WEB_SERVICE" python -c 'import importlib.metadata as metadata; assert metadata.version("dojo-permission") in {"0.1.0", "0.2.0", "0.3.0"}, "Unsupported installed library version"'
    ROLLBACK_ENABLED="true"
fi

HOOK_BEGIN="# BEGIN DOJO_PERMISSION_MANAGED_HOOK"
HOOK_END="# END DOJO_PERMISSION_MANAGED_HOOK"
MANAGED_HOOK="$(cat <<'PYTHON'
# BEGIN DOJO_PERMISSION_MANAGED_HOOK
import os as _dojo_permission_os

if _dojo_permission_os.getenv(
    "DD_DOJO_PERMISSION_ENABLED", "false"
).lower() in {"1", "true", "yes", "on"}:
    from dojo_permission.integration import configure as _configure_dojo_permission

    _configure_dojo_permission(globals())
    del _configure_dojo_permission

del _dojo_permission_os
# END DOJO_PERMISSION_MANAGED_HOOK
PYTHON
)"
HOOK_PRESENT=0
HOOK_BEGIN_COUNT="$(grep -Fc "$HOOK_BEGIN" "$SETTINGS_FILE" || true)"
HOOK_END_COUNT="$(grep -Fc "$HOOK_END" "$SETTINGS_FILE" || true)"
if [[ "$HOOK_BEGIN_COUNT" -ne 0 || "$HOOK_END_COUNT" -ne 0 ]]; then
    [[ "$HOOK_BEGIN_COUNT" -eq 1 && "$HOOK_END_COUNT" -eq 1 ]] || die "Settings must contain exactly one complete managed hook"
    EXISTING_HOOK="$(sed -n '/^# BEGIN DOJO_PERMISSION_MANAGED_HOOK$/,/^# END DOJO_PERMISSION_MANAGED_HOOK$/p' "$SETTINGS_FILE")"
    [[ "$EXISTING_HOOK" == "$MANAGED_HOOK" ]] || die "The existing managed hook was modified; review it manually"
    HOOK_PRESENT=1
elif grep -F "dojo_permission.integration" "$SETTINGS_FILE" >/dev/null; then
    die "An unmanaged dojo_permission integration already exists; convert it manually before continuing"
fi
[[ "$MANAGED_UPGRADE" -ne 1 || "$HOOK_PRESENT" -eq 1 ]] || die "A managed upgrade requires the original managed settings hook"

write_override() {
    local output_path="$1"
    local image_ref="$2"
    local enabled="$3"
    local tmp_path
    mkdir -p "$(dirname "$output_path")" || return 1
    tmp_path="$(mktemp "${output_path}.tmp.XXXXXX")" || return 1
    if ! cat > "$tmp_path" <<EOF
# Generated by dojo-permission deploy-existing-compose.sh
services:
  $WEB_SERVICE:
    image: "$image_ref"
    environment:
      DD_DOJO_PERMISSION_ENABLED: "$enabled"
  $INITIALIZER_SERVICE:
    image: "$image_ref"
    environment:
      DD_DOJO_PERMISSION_ENABLED: "$enabled"
  $WORKER_SERVICE:
    image: "$image_ref"
    environment:
      DD_DOJO_PERMISSION_ENABLED: "$enabled"
  $BEAT_SERVICE:
    image: "$image_ref"
    environment:
      DD_DOJO_PERMISSION_ENABLED: "$enabled"
EOF
    then
        rm -f "$tmp_path"
        return 1
    fi
    chmod 600 "$tmp_path" || {
        rm -f "$tmp_path"
        return 1
    }
    mv "$tmp_path" "$output_path" || {
        rm -f "$tmp_path"
        return 1
    }
}

if ! curl --fail --silent --show-error --max-time 10 -- "$HEALTH_URL" >/dev/null; then
    die "The current deployment does not pass the supplied health URL; establish a healthy baseline first"
fi

if [[ "$APPLY" -ne 1 ]]; then
    printf 'Deployment plan only; no changes were made.\n\n'
    printf 'Current web image ID: %s\n' "$CURRENT_WEB_IMAGE_ID"
    printf 'New application image: %s\n' "$DERIVED_IMAGE"
    printf 'Rollback image:        %s\n' "$ROLLBACK_IMAGE"
    printf 'Settings hook:         %s\n' "$([[ "$HOOK_PRESENT" -eq 1 ]] && printf present || printf 'would be appended')"
    printf 'Release override:      %s\n' "$RELEASE_OVERRIDE"
    printf 'Services:              %s, %s, %s, %s\n' "$WEB_SERVICE" "$INITIALIZER_SERVICE" "$WORKER_SERVICE" "$BEAT_SERVICE"
    if [[ "$RESTART_PROXY" -eq 1 ]]; then
        printf 'Proxy image:           unchanged (%s container would restart)\n' "$PROXY_SERVICE"
    else
        printf 'Proxy image:           unchanged (no proxy restart requested)\n'
    fi
    printf '\nRe-run with --backup-evidence PATH --apply after reviewing this plan.\n'
    exit 0
fi

[[ -n "$BACKUP_EVIDENCE" ]] || die "--backup-evidence is required with --apply"
[[ -e "$BACKUP_EVIDENCE" ]] || die "Backup evidence does not exist: $BACKUP_EVIDENCE"
BACKUP_EVIDENCE="$(canonical_path "$BACKUP_EVIDENCE")"
[[ "$RELEASE_OVERRIDE" != "$BACKUP_EVIDENCE" ]] || die "--release-override must not overwrite backup evidence"
if [[ -f "$BACKUP_EVIDENCE" ]]; then
    [[ -r "$BACKUP_EVIDENCE" && -s "$BACKUP_EVIDENCE" ]] || die "Backup evidence file must be readable and non-empty"
elif [[ -d "$BACKUP_EVIDENCE" ]]; then
    [[ -r "$BACKUP_EVIDENCE" ]] || die "Backup evidence directory is not readable"
    [[ -n "$(find "$BACKUP_EVIDENCE" -mindepth 1 -print -quit 2>/dev/null)" ]] || die "Backup evidence directory is empty"
    case "$RELEASE_OVERRIDE/" in
        "$BACKUP_EVIDENCE/"*) die "--release-override must not be inside the backup evidence directory" ;;
    esac
else
    die "Backup evidence must be a regular file or directory"
fi

if ! "${COMPOSE[@]}" exec -T "$WEB_SERVICE" python manage.py migrate --check; then
    die "The customized deployment has pending migrations; resolve them before installing dojo_permission"
fi

STATE_DIR="$(dirname "$RELEASE_OVERRIDE")/.dojo-permission-deploy"
mkdir -p "$STATE_DIR"
LOCK_DIR="$STATE_DIR/lock"
mkdir "$LOCK_DIR" 2>/dev/null || die "Another deployment appears to be running: $LOCK_DIR"
SETTINGS_TMP=""
PRE_CUTOVER_ACTIVE=0
cleanup() {
    if [[ -n "$SETTINGS_TMP" ]]; then
        rm -f "$SETTINGS_TMP"
    fi
    if [[ "${PRE_CUTOVER_ACTIVE:-0}" -eq 1 && "${SETTINGS_CHANGED:-0}" -eq 1 ]]; then
        if ! restore_original_settings; then
            printf 'WARNING: Could not restore original settings from %s\n' "$SETTINGS_BACKUP" >&2
        fi
    fi
    rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_STATE_DIR="$(mktemp -d "$STATE_DIR/run.$TIMESTAMP.XXXXXX")"
SETTINGS_BACKUP="$RUN_STATE_DIR/local_settings.py.bak"
RELEASE_CANDIDATE="$RUN_STATE_DIR/compose.release.yml"
ROLLBACK_OVERRIDE="$RUN_STATE_DIR/compose.rollback.yml"
SETTINGS_CHANGED=0

if ! docker image inspect "$DERIVED_IMAGE" >/dev/null 2>&1; then
    [[ "$DERIVED_IMAGE" == *@sha256:* ]] || die "Local derived image not found: $DERIVED_IMAGE"
    docker pull "$DERIVED_IMAGE"
fi

DERIVED_BASE_REF="$(docker image inspect "$DERIVED_IMAGE" --format '{{index .Config.Labels "io.dojo-permission.base-image"}}')"
[[ -n "$DERIVED_BASE_REF" && "$DERIVED_BASE_REF" != '<no value>' ]] || die "Derived image is missing its customized-base provenance label"
if ! docker image inspect "$DERIVED_BASE_REF" >/dev/null 2>&1; then
    [[ "$DERIVED_BASE_REF" == *@sha256:* ]] || die "Labeled base image is not locally available: $DERIVED_BASE_REF"
    docker pull "$DERIVED_BASE_REF"
fi
DERIVED_BASE_ID="$(docker image inspect "$DERIVED_BASE_REF" --format '{{.Id}}')"
[[ "$DERIVED_BASE_ID" == "$EXPECTED_ROLLBACK_ID" ]] || die "Derived image was not built from the supplied rollback image"
DERIVED_IMAGE_ID="$(docker image inspect "$DERIVED_IMAGE" --format '{{.Id}}')"
[[ "$DERIVED_IMAGE_ID" != "$EXPECTED_ROLLBACK_ID" ]] || die "Derived and rollback images resolve to the same image ID"

docker run --rm --entrypoint python "$DERIVED_IMAGE" -c \
    "import dojo, django, importlib.metadata as metadata; assert dojo.__version__ == '2.58.3'; assert django.get_version() == '5.2.14'; assert metadata.version('dojo-permission') == '0.3.0'"
docker run --rm --entrypoint python "$DERIVED_IMAGE" -m pip check

cp -p "$SETTINGS_FILE" "$SETTINGS_BACKUP"
restore_original_settings() {
    if [[ "$SETTINGS_CHANGED" -eq 1 ]]; then
        SETTINGS_TMP="$(mktemp "${SETTINGS_FILE}.restore.XXXXXX")" || return 1
        cp -p "$SETTINGS_BACKUP" "$SETTINGS_TMP" || {
            rm -f "$SETTINGS_TMP"
            SETTINGS_TMP=""
            return 1
        }
        mv "$SETTINGS_TMP" "$SETTINGS_FILE" || {
            rm -f "$SETTINGS_TMP"
            SETTINGS_TMP=""
            return 1
        }
        SETTINGS_TMP=""
        SETTINGS_CHANGED=0
    fi
}

pre_cutover_signal() {
    trap - INT TERM HUP
    set +e
    restore_original_settings
    printf 'Deployment interrupted before cutover; running services were not replaced.\n' >&2
    exit 130
}
PRE_CUTOVER_ACTIVE=1
trap pre_cutover_signal INT TERM HUP

if [[ "$HOOK_PRESENT" -ne 1 ]]; then
    SETTINGS_TMP="$(mktemp "${SETTINGS_FILE}.dojo-permission.XXXXXX")"
    cp -p "$SETTINGS_FILE" "$SETTINGS_TMP"
    printf '\n%s\n' "$MANAGED_HOOK" >> "$SETTINGS_TMP"
    if ! python3 -c 'import pathlib, sys; path = pathlib.Path(sys.argv[1]); compile(path.read_text(), str(path), "exec")' "$SETTINGS_TMP"; then
        die "The managed settings hook did not compile; the original settings file is unchanged"
    fi
    SETTINGS_CHANGED=1
    mv "$SETTINGS_TMP" "$SETTINGS_FILE"
    SETTINGS_TMP=""
elif ! python3 -c 'import pathlib, sys; path = pathlib.Path(sys.argv[1]); compile(path.read_text(), str(path), "exec")' "$SETTINGS_FILE"; then
    die "The existing settings file does not compile"
fi

if ! write_override "$RELEASE_CANDIDATE" "$DERIVED_IMAGE" "true"; then
    restore_original_settings || true
    die "Could not create the candidate release override"
fi
if ! write_override "$ROLLBACK_OVERRIDE" "$ROLLBACK_IMAGE" "$ROLLBACK_ENABLED"; then
    restore_original_settings || true
    die "Could not create the rollback override"
fi

RELEASE_COMPOSE=("${COMPOSE[@]}" -f "$RELEASE_CANDIDATE")
ROLLBACK_COMPOSE=("${COMPOSE[@]}" -f "$ROLLBACK_OVERRIDE")

# A one-off entrypoint bypasses DefectDojo's normal settings-copy step. Load
# the same mounted settings before running Django in the candidate image.
candidate_django() {
    "${RELEASE_COMPOSE[@]}" run --rm --no-deps --entrypoint /bin/sh \
        "$WEB_SERVICE" -c '
set -e
for settings_file in /app/docker/extra_settings/*; do
    [ -f "$settings_file" ] || continue
    cp "$settings_file" /app/dojo/settings/
done
exec python manage.py "$@"
' dojo-permission-django "$@"
}

if ! "${RELEASE_COMPOSE[@]}" config -q; then
    restore_original_settings || true
    die "The generated release override is not valid with the supplied Compose files"
fi

printf 'Applying the candidate image migrations...\n'
if [[ "$LIBRARY_ONLY" -eq 1 ]]; then
    candidate_django migrate dojo_permission --noinput
else
    "${RELEASE_COMPOSE[@]}" run --rm --no-deps "$INITIALIZER_SERVICE"
fi || {
    trap - INT TERM HUP
    if restore_original_settings; then
        die "Candidate migration failed; running services were not replaced and settings were restored"
    fi
    die "Candidate migration failed and the original settings could not be restored from $SETTINGS_BACKUP"
}
if ! candidate_django check; then
    trap - INT TERM HUP
    restore_original_settings || true
    die "Derived-image Django checks failed before cutover"
fi
if ! candidate_django migrate --check; then
    trap - INT TERM HUP
    restore_original_settings || true
    die "Candidate migrations are incomplete before cutover"
fi
if ! PRE_CUTOVER_MIGRATIONS="$(candidate_django showmigrations dojo_permission)"; then
    trap - INT TERM HUP
    restore_original_settings || true
    die "Could not read dojo_permission migration state before cutover"
fi
if ! grep -F '[X] 0001_initial' <<<"$PRE_CUTOVER_MIGRATIONS" >/dev/null; then
    trap - INT TERM HUP
    restore_original_settings || true
    die "dojo_permission.0001_initial was not applied; cutover was not started"
fi

rollback_cutover() {
    local rollback_ok=1
    local rollback_healthy=0
    local rollback_attempt=1
    local rollback_container_id
    local rollback_actual_id

    trap - ERR
    trap '' INT TERM HUP
    set +e
    printf 'Cutover failed; restoring the previous application image...\n' >&2
    if ! restore_original_settings; then
        printf 'WARNING: Could not restore original settings from %s\n' "$SETTINGS_BACKUP" >&2
        rollback_ok=0
    fi
    if ! write_override "$RELEASE_OVERRIDE" "$ROLLBACK_IMAGE" "$ROLLBACK_ENABLED"; then
        printf 'WARNING: Could not persist the rollback override at %s\n' "$RELEASE_OVERRIDE" >&2
        rollback_ok=0
    fi
    if ! "${ROLLBACK_COMPOSE[@]}" up -d --no-deps --no-build --force-recreate \
        "$WEB_SERVICE" "$WORKER_SERVICE" "$BEAT_SERVICE"; then
        printf 'WARNING: One or more previous application containers failed to start.\n' >&2
        rollback_ok=0
    fi
    if [[ "$RESTART_PROXY" -eq 1 ]]; then
        if ! "${ROLLBACK_COMPOSE[@]}" restart "$PROXY_SERVICE"; then
            printf 'WARNING: The unchanged proxy container did not restart.\n' >&2
            rollback_ok=0
        fi
    fi
    while [[ "$rollback_attempt" -le 30 ]]; do
        if curl --fail --silent --show-error --max-time 5 -- "$HEALTH_URL" >/dev/null 2>&1; then
            rollback_healthy=1
            break
        fi
        sleep 1
        rollback_attempt=$((rollback_attempt + 1))
    done
    if [[ "$rollback_healthy" -ne 1 ]]; then
        printf 'WARNING: The rollback health check did not pass: %s\n' "$HEALTH_URL" >&2
        rollback_ok=0
    fi
    for service in "$WEB_SERVICE" "$WORKER_SERVICE" "$BEAT_SERVICE"; do
        rollback_container_id="$("${ROLLBACK_COMPOSE[@]}" ps -q "$service")"
        if [[ -z "$rollback_container_id" ]]; then
            printf 'WARNING: No rollback container was found for %s.\n' "$service" >&2
            rollback_ok=0
            continue
        fi
        rollback_actual_id="$(docker inspect "$rollback_container_id" --format '{{.Image}}')"
        if [[ "$rollback_actual_id" != "$EXPECTED_ROLLBACK_ID" ]]; then
            printf 'WARNING: %s is not using the expected rollback image.\n' "$service" >&2
            rollback_ok=0
        fi
    done
    if [[ "$rollback_ok" -eq 1 ]]; then
        printf 'Rollback health and image checks passed.\n' >&2
    else
        printf 'ROLLBACK REQUIRES MANUAL ATTENTION.\n' >&2
    fi
    printf 'The additive policy table was not removed. Settings backup: %s\n' "$SETTINGS_BACKUP" >&2
}

unexpected_failure() {
    local failure_status=$?
    rollback_cutover
    printf 'Unexpected deployment failure; exit status %s.\n' "$failure_status" >&2
    exit "$failure_status"
}

cutover_signal() {
    rollback_cutover
    printf 'Deployment interrupted during cutover.\n' >&2
    exit 130
}

trap unexpected_failure ERR
trap cutover_signal INT TERM HUP
PRE_CUTOVER_ACTIVE=0

if ! "${RELEASE_COMPOSE[@]}" stop -t 60 "$BEAT_SERVICE"; then
    rollback_cutover
    die "Could not stop the beat service cleanly"
fi
if ! "${RELEASE_COMPOSE[@]}" stop -t 60 "$WORKER_SERVICE"; then
    rollback_cutover
    die "Could not stop the worker service cleanly"
fi
if ! "${RELEASE_COMPOSE[@]}" stop -t 60 "$WEB_SERVICE"; then
    rollback_cutover
    die "Could not stop the web service cleanly"
fi

if ! "${RELEASE_COMPOSE[@]}" up -d --no-deps --no-build --force-recreate "$WEB_SERVICE"; then
    rollback_cutover
    die "New web container failed to start"
fi
if [[ "$RESTART_PROXY" -eq 1 ]]; then
    if ! "${RELEASE_COMPOSE[@]}" restart "$PROXY_SERVICE"; then
        rollback_cutover
        die "Proxy restart failed"
    fi
fi

HEALTHY=0
attempt=1
while [[ "$attempt" -le 60 ]]; do
    if curl --fail --silent --show-error --max-time 10 -- "$HEALTH_URL" >/dev/null 2>&1; then
        HEALTHY=1
        break
    fi
    sleep 1
    attempt=$((attempt + 1))
done
if [[ "$HEALTHY" -ne 1 ]]; then
    rollback_cutover
    die "Health check failed after 60 attempts: $HEALTH_URL"
fi

if ! "${RELEASE_COMPOSE[@]}" up -d --no-deps --no-build --force-recreate "$WORKER_SERVICE"; then
    rollback_cutover
    die "New worker container failed to start"
fi
if ! "${RELEASE_COMPOSE[@]}" up -d --no-deps --no-build --force-recreate "$BEAT_SERVICE"; then
    rollback_cutover
    die "New beat container failed to start"
fi

if ! "${RELEASE_COMPOSE[@]}" exec -T "$WEB_SERVICE" python manage.py check; then
    rollback_cutover
    die "Django system checks failed after cutover"
fi
if ! "${RELEASE_COMPOSE[@]}" exec -T "$WEB_SERVICE" python manage.py migrate --check; then
    rollback_cutover
    die "Candidate migrations are incomplete after cutover"
fi
if ! MIGRATIONS="$("${RELEASE_COMPOSE[@]}" exec -T "$WEB_SERVICE" python manage.py showmigrations dojo_permission)"; then
    rollback_cutover
    die "Could not read dojo_permission migration state"
fi
grep -F '[X] 0001_initial' <<<"$MIGRATIONS" >/dev/null || {
    rollback_cutover
    die "dojo_permission.0001_initial is not applied"
}

if ! EXPECTED_NEW_ID="$(docker image inspect "$DERIVED_IMAGE" --format '{{.Id}}')"; then
    rollback_cutover
    die "Could not inspect the derived image after cutover"
fi
for service in "$WEB_SERVICE" "$WORKER_SERVICE" "$BEAT_SERVICE"; do
    if ! container_id="$("${RELEASE_COMPOSE[@]}" ps -q "$service")" || [[ -z "$container_id" ]]; then
        rollback_cutover
        die "Could not find the $service container after cutover"
    fi
    if ! actual_id="$(docker inspect "$container_id" --format '{{.Image}}')"; then
        rollback_cutover
        die "Could not inspect the $service container after cutover"
    fi
    [[ "$actual_id" == "$EXPECTED_NEW_ID" ]] || {
        rollback_cutover
        die "$service is not using the derived image"
    }
done

if ! write_override "$RELEASE_OVERRIDE" "$DERIVED_IMAGE" "true"; then
    rollback_cutover
    die "Could not persist the release override"
fi

trap - ERR INT TERM HUP

printf '\nDeployment completed successfully.\n'
printf 'Release override: %s\n' "$RELEASE_OVERRIDE"
printf 'Settings backup:  %s\n' "$SETTINGS_BACKUP"
printf 'Rollback override: %s\n' "$ROLLBACK_OVERRIDE"
printf 'Keep the release override in every future Compose command.\n'
