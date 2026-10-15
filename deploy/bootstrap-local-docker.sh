#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# This script intentionally creates only a local evaluation stack. Its default
# mode uses a clean, pinned source checkout. An explicit existing-checkout mode
# accepts local Compose changes, but still verifies the runtime image versions.

EXPECTED_DD_TAG="2.58.3"
EXPECTED_DD_COMMIT="ea611112772b08b74e6429e833728ce500b4c284"
EXPECTED_DJANGO_VERSION="5.2.14"
EXPECTED_PYTHON_MAJOR="3"
EXPECTED_PYTHON_MINOR="13"
DEFECTDOJO_REPOSITORY="${DEFECTDOJO_REPOSITORY:-https://github.com/DefectDojo/django-DefectDojo.git}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
LIBRARY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
LIBRARY_PARENT="$(dirname "$LIBRARY_ROOT")"

source "$SCRIPT_DIR/local-stack-common.sh"

DEFECTDOJO_DIR="${DEFECTDOJO_DIR:-$LIBRARY_PARENT/defectdojo-2.58.3}"
PROJECT_NAME="${DOJO_PERMISSION_PROJECT_NAME:-}"
PROJECT_NAME_WAS_SET="false"
if [[ -n "$PROJECT_NAME" ]]; then
    PROJECT_NAME_WAS_SET="true"
fi
HTTP_PORT="${DD_PORT:-18080}"
TLS_PORT="${DD_TLS_PORT:-18443}"
INITIALIZER_TIMEOUT="${DOJO_PERMISSION_INITIALIZER_TIMEOUT:-900}"
HTTP_TIMEOUT="${DOJO_PERMISSION_HTTP_TIMEOUT:-300}"
HEALTH_URL="${DOJO_PERMISSION_HEALTH_URL:-}"
IMAGE_SOURCE="${DOJO_PERMISSION_IMAGE_SOURCE:-build}"
DEPLOYMENT_MODE="strict"
CA_BUNDLE=""
CA_CONTAINER_PATH="/etc/ssl/certs/dojo-permission-ca-bundle.pem"
DIR_OPTION=""
EXTRA_COMPOSE_FILES=()
CLONE_TEMP=""
STATE_TEMP=""
CA_TEMP=""

cleanup() {
    if [[ -n "$CLONE_TEMP" && -d "$CLONE_TEMP" ]]; then
        rm -rf "$CLONE_TEMP"
    fi
    if [[ -n "$STATE_TEMP" ]]; then
        rm -f "$STATE_TEMP"
    fi
    if [[ -n "$CA_TEMP" ]]; then
        rm -f "$CA_TEMP"
    fi
}
trap cleanup EXIT

usage() {
    cat <<'EOF'
Create or resume a local DefectDojo 2.58.3 permission-library stack.

Usage:
  bash deploy/bootstrap-local-docker.sh [options]

Options:
  --defectdojo-dir DIR  Checkout location. Defaults to defectdojo-2.58.3
                        beside this library checkout.
  --existing-defectdojo-dir DIR
                        Use an already downloaded, locally modified checkout.
                        Compose overrides are applied, but checkout source is
                        not built. Nothing in DIR is reset or overwritten.
                        Requires --image-source local or pull.
  --image-source SOURCE How the official 2.58.3 base images are obtained:
                          build  build from the pinned clean checkout (default)
                          local  use already-pulled local images; never pull
                          pull   explicitly pull the official images
  --compose-file FILE   Additional Compose override for existing-checkout mode.
                        May be supplied more than once; order is preserved.
  --ca-bundle FILE      Host CA bundle to mount read-only in Python services.
                        Also used by Git if strict mode must clone DefectDojo.
  --project-name NAME   Docker Compose project (default: stable name based on
                        the library and DefectDojo checkout paths).
  --port PORT           Local HTTP port (default: 18080).
  --tls-port PORT       Local HTTPS port (default: 18443).
  --health-url URL      URL used for the final HTTP check. By default this is
                        http://127.0.0.1:PORT/login.
  --initializer-timeout SECONDS
                        Maximum initializer wait (default: 900).
  --http-timeout SECONDS
                        Maximum login-page wait (default: 300).
  -h, --help            Show this help.

The equivalent environment variables are DEFECTDOJO_DIR,
DOJO_PERMISSION_IMAGE_SOURCE,
DOJO_PERMISSION_PROJECT_NAME, DD_PORT, DD_TLS_PORT,
DOJO_PERMISSION_HEALTH_URL, DOJO_PERMISSION_INITIALIZER_TIMEOUT, and
DOJO_PERMISSION_HTTP_TIMEOUT.

This is local staging only. It requires Docker Compose 2.24.4 or newer, uses
DefectDojo's sample Compose stack, and must not be pointed at a running remote
or production deployment. --ca-bundle configures Git and the running Python
containers; Docker daemon registry trust must be configured separately.
EOF
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

need() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

is_port() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    [[ "$1" -ge 1 && "$1" -le 65535 ]]
}

is_positive_integer() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    [[ "$1" -gt 0 ]]
}

print_shell_command() {
    printf '  '
    printf '%q ' "$@"
    printf '\n'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --defectdojo-dir)
            [[ $# -ge 2 ]] || die "--defectdojo-dir requires a value"
            [[ -z "$DIR_OPTION" ]] || die \
                "--defectdojo-dir and --existing-defectdojo-dir are mutually exclusive"
            DEFECTDOJO_DIR="$2"
            DIR_OPTION="strict"
            DEPLOYMENT_MODE="strict"
            shift 2
            ;;
        --existing-defectdojo-dir)
            [[ $# -ge 2 ]] || die "--existing-defectdojo-dir requires a value"
            [[ -z "$DIR_OPTION" ]] || die \
                "--defectdojo-dir and --existing-defectdojo-dir are mutually exclusive"
            DEFECTDOJO_DIR="$2"
            DIR_OPTION="existing"
            DEPLOYMENT_MODE="existing"
            shift 2
            ;;
        --image-source)
            [[ $# -ge 2 ]] || die "--image-source requires a value"
            IMAGE_SOURCE="$2"
            shift 2
            ;;
        --compose-file)
            [[ $# -ge 2 ]] || die "--compose-file requires a value"
            EXTRA_COMPOSE_FILES+=("$2")
            shift 2
            ;;
        --ca-bundle)
            [[ $# -ge 2 ]] || die "--ca-bundle requires a value"
            CA_BUNDLE="$2"
            shift 2
            ;;
        --project-name)
            [[ $# -ge 2 ]] || die "--project-name requires a value"
            PROJECT_NAME="$2"
            PROJECT_NAME_WAS_SET="true"
            shift 2
            ;;
        --port)
            [[ $# -ge 2 ]] || die "--port requires a value"
            HTTP_PORT="$2"
            shift 2
            ;;
        --tls-port)
            [[ $# -ge 2 ]] || die "--tls-port requires a value"
            TLS_PORT="$2"
            shift 2
            ;;
        --health-url)
            [[ $# -ge 2 ]] || die "--health-url requires a value"
            HEALTH_URL="$2"
            shift 2
            ;;
        --initializer-timeout)
            [[ $# -ge 2 ]] || die "--initializer-timeout requires a value"
            INITIALIZER_TIMEOUT="$2"
            shift 2
            ;;
        --http-timeout)
            [[ $# -ge 2 ]] || die "--http-timeout requires a value"
            HTTP_TIMEOUT="$2"
            shift 2
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

case "$IMAGE_SOURCE" in
    build|local|pull) ;;
    *) die "Invalid image source: $IMAGE_SOURCE (expected build, local, or pull)" ;;
esac
if [[ "$DEPLOYMENT_MODE" == strict ]]; then
    [[ "$IMAGE_SOURCE" == build ]] || die \
        "--image-source local or pull requires --existing-defectdojo-dir"
    [[ ${#EXTRA_COMPOSE_FILES[@]} -eq 0 ]] || die \
        "--compose-file requires --existing-defectdojo-dir"
else
    [[ "$IMAGE_SOURCE" == local || "$IMAGE_SOURCE" == pull ]] || die \
        "--existing-defectdojo-dir requires --image-source local or pull"
fi

is_port "$HTTP_PORT" || die "Invalid HTTP port: $HTTP_PORT"
is_port "$TLS_PORT" || die "Invalid HTTPS port: $TLS_PORT"
[[ "$HTTP_PORT" != "$TLS_PORT" ]] || die "HTTP and HTTPS ports must differ"
is_positive_integer "$INITIALIZER_TIMEOUT" || die "Invalid initializer timeout: $INITIALIZER_TIMEOUT"
is_positive_integer "$HTTP_TIMEOUT" || die "Invalid HTTP timeout: $HTTP_TIMEOUT"
if [[ -z "$HEALTH_URL" ]]; then
    HEALTH_URL="http://127.0.0.1:$HTTP_PORT/login"
fi
[[ "$HEALTH_URL" == http://* || "$HEALTH_URL" == https://* ]] || die \
    "The health URL must use http:// or https://"
[[ "$HEALTH_URL" != *[[:space:]]* ]] || die "The health URL must not contain whitespace"

DEFECTDOJO_DIR="$(local_checkout_path "$DEFECTDOJO_DIR")"
if [[ "$PROJECT_NAME_WAS_SET" != true ]]; then
    PROJECT_NAME="$(local_default_project_name "$LIBRARY_ROOT" "$DEFECTDOJO_DIR")"
fi
[[ "$PROJECT_NAME" =~ ^[a-z0-9][a-z0-9_.-]*$ ]] || die "Invalid Compose project name: $PROJECT_NAME"

if [[ -n "$CA_BUNDLE" ]]; then
    case "$CA_BUNDLE" in
        /*) ;;
        *) CA_BUNDLE="$(pwd -P)/$CA_BUNDLE" ;;
    esac
    [[ -f "$CA_BUNDLE" && -r "$CA_BUNDLE" ]] || die \
        "CA bundle is not a readable regular file: $CA_BUNDLE"
    CA_BUNDLE="$(cd "$(dirname "$CA_BUNDLE")" && pwd -P)/$(basename "$CA_BUNDLE")"
    grep -F -- '-----BEGIN CERTIFICATE-----' "$CA_BUNDLE" >/dev/null || die \
        "CA bundle contains no PEM certificates: $CA_BUNDLE"
    if grep -E -- '-----BEGIN ([A-Z0-9]+ )?PRIVATE KEY-----' "$CA_BUNDLE" >/dev/null; then
        die "CA bundle must not contain a private key: $CA_BUNDLE"
    fi
fi

if [[ ${#EXTRA_COMPOSE_FILES[@]} -gt 0 ]]; then
    for EXTRA_INDEX in "${!EXTRA_COMPOSE_FILES[@]}"; do
        EXTRA_COMPOSE_FILE="${EXTRA_COMPOSE_FILES[$EXTRA_INDEX]}"
        case "$EXTRA_COMPOSE_FILE" in
            /*) ;;
            *) EXTRA_COMPOSE_FILE="$(pwd -P)/$EXTRA_COMPOSE_FILE" ;;
        esac
        [[ -f "$EXTRA_COMPOSE_FILE" && -r "$EXTRA_COMPOSE_FILE" ]] || die \
            "Compose override is not a readable regular file: $EXTRA_COMPOSE_FILE"
        EXTRA_COMPOSE_FILES[$EXTRA_INDEX]="$(cd "$(dirname "$EXTRA_COMPOSE_FILE")" && pwd -P)/$(basename "$EXTRA_COMPOSE_FILE")"
    done
    unset EXTRA_INDEX EXTRA_COMPOSE_FILE
fi

need git
need curl

if command -v docker >/dev/null 2>&1; then
    DOCKER=(docker)
elif [[ -x /Applications/Docker.app/Contents/Resources/bin/docker ]]; then
    DOCKER=(/Applications/Docker.app/Contents/Resources/bin/docker)
else
    die "Docker CLI not found. Install and start Docker before continuing."
fi

"${DOCKER[@]}" compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"
COMPOSE_VERSION="$("${DOCKER[@]}" compose version --short)"
COMPOSE_VERSION="${COMPOSE_VERSION#v}"
COMPOSE_VERSION="${COMPOSE_VERSION%%[-+]*}"
IFS=. read -r COMPOSE_MAJOR COMPOSE_MINOR COMPOSE_PATCH <<< "$COMPOSE_VERSION"
for COMPOSE_COMPONENT in "$COMPOSE_MAJOR" "$COMPOSE_MINOR" "$COMPOSE_PATCH"; do
    case "$COMPOSE_COMPONENT" in
        ''|*[!0-9]*) die "Could not parse Docker Compose version: $COMPOSE_VERSION" ;;
    esac
done
unset COMPOSE_COMPONENT
if [[ "$COMPOSE_MAJOR" -lt 2 ]] \
    || { [[ "$COMPOSE_MAJOR" -eq 2 && "$COMPOSE_MINOR" -lt 24 ]]; } \
    || { [[ "$COMPOSE_MAJOR" -eq 2 && "$COMPOSE_MINOR" -eq 24 && "$COMPOSE_PATCH" -lt 4 ]]; }
then
    die "Docker Compose 2.24.4 or newer is required; found $COMPOSE_VERSION"
fi
"${DOCKER[@]}" info >/dev/null 2>&1 || die "Docker is not running or is not accessible"

cat <<EOF
LOCAL STAGING ONLY
This will use DefectDojo's sample Compose stack under project '$PROJECT_NAME'.
It will not run Compose down, delete persistent volumes or application data,
or reset an existing checkout.

Library:    $LIBRARY_ROOT
DefectDojo: $DEFECTDOJO_DIR
Mode:       $DEPLOYMENT_MODE checkout, $IMAGE_SOURCE images
HTTP URL:   http://127.0.0.1:$HTTP_PORT/login
EOF

if [[ -n "$CA_BUNDLE" ]]; then
    cat <<EOF
CA bundle:  $CA_BUNDLE

The CA bundle is used for Git, the host health check, and Python services.
Docker registry trust is controlled by the Docker daemon, not this option.
EOF
fi

if [[ "$DEPLOYMENT_MODE" == strict ]]; then
    if [[ ! -e "$DEFECTDOJO_DIR" ]]; then
        [[ -d "$(dirname "$DEFECTDOJO_DIR")" ]] || die \
            "Parent directory does not exist: $(dirname "$DEFECTDOJO_DIR")"
        printf '\nCloning DefectDojo %s...\n' "$EXPECTED_DD_TAG"
        CLONE_TEMP="$(mktemp -d "$(dirname "$DEFECTDOJO_DIR")/.defectdojo-2.58.3.clone.XXXXXX")"
        GIT_CLONE=(git)
        if [[ -n "$CA_BUNDLE" ]]; then
            GIT_CLONE+=(-c "http.sslCAInfo=$CA_BUNDLE")
        fi
        "${GIT_CLONE[@]}" clone --branch "$EXPECTED_DD_TAG" --single-branch \
            "$DEFECTDOJO_REPOSITORY" "$CLONE_TEMP"
        unset GIT_CLONE
        [[ "$(git -C "$CLONE_TEMP" rev-parse HEAD)" == "$EXPECTED_DD_COMMIT" ]] || die \
            "The cloned DefectDojo tag did not resolve to the pinned commit"
        mv "$CLONE_TEMP" "$DEFECTDOJO_DIR"
        CLONE_TEMP=""
    fi

    [[ -d "$DEFECTDOJO_DIR" ]] || die "DefectDojo path is not a directory: $DEFECTDOJO_DIR"
    git -C "$DEFECTDOJO_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
        || die "Existing DefectDojo path is not a Git checkout: $DEFECTDOJO_DIR"

    DEFECTDOJO_DIR="$(cd "$DEFECTDOJO_DIR" && pwd -P)"
    ACTUAL_COMMIT="$(git -C "$DEFECTDOJO_DIR" rev-parse HEAD)"
    ACTUAL_TAG="$(git -C "$DEFECTDOJO_DIR" describe --tags --exact-match HEAD 2>/dev/null || true)"

    [[ "$ACTUAL_COMMIT" == "$EXPECTED_DD_COMMIT" ]] || die \
        "DefectDojo must be commit $EXPECTED_DD_COMMIT; found $ACTUAL_COMMIT. This script will not reset an existing checkout."
    [[ "$ACTUAL_TAG" == "$EXPECTED_DD_TAG" ]] || die \
        "DefectDojo HEAD must have exact tag $EXPECTED_DD_TAG; found ${ACTUAL_TAG:-no exact tag}."
    [[ -z "$(git -C "$DEFECTDOJO_DIR" status --porcelain)" ]] || die \
        "DefectDojo has local changes. Use --existing-defectdojo-dir with local or pull images to opt in to a modified checkout."
    [[ ! -e "$DEFECTDOJO_DIR/.env" ]] || die \
        "Remove or move $DEFECTDOJO_DIR/.env; strict bootstrap uses a controlled Compose environment."
else
    [[ -d "$DEFECTDOJO_DIR" ]] || die \
        "Existing DefectDojo path is not a directory: $DEFECTDOJO_DIR"
    DEFECTDOJO_DIR="$(cd "$DEFECTDOJO_DIR" && pwd -P)"
    printf '\nUsing the existing checkout without Git/tag/clean-tree enforcement.\n'
    printf 'The checkout is never reset, cleaned, or overwritten by this script.\n'
fi

[[ -f "$DEFECTDOJO_DIR/docker-compose.yml" ]] || die "DefectDojo docker-compose.yml is missing"
[[ -f "$LIBRARY_ROOT/docker/compose.permission.yml" ]] || die "Permission Compose overlay is missing"
[[ -f "$LIBRARY_ROOT/docker/compose.permission.prebuilt.yml" ]] || die "Prebuilt-image Compose overlay is missing"
if [[ -n "$CA_BUNDLE" ]]; then
    [[ -f "$LIBRARY_ROOT/docker/compose.permission.ca.yml" ]] || die "CA Compose overlay is missing"
fi
[[ -f "$LIBRARY_ROOT/integration/extra_settings/local_settings.py" ]] || die "Permission local settings file is missing"

unset DD_DATABASE_URL DD_DATABASE_NAME DD_DATABASE_USER DD_DATABASE_PASSWORD
unset DD_CELERY_BROKER_URL DD_SECRET_KEY DD_CREDENTIAL_AES_256_KEY
unset DD_ADMIN_PASSWORD
export DD_PORT="$HTTP_PORT"
export DD_TLS_PORT="$TLS_PORT"
export DD_DATABASE_HOST="postgres"
export DD_DATABASE_PORT="5432"
export DD_INITIALIZE="true"
export DD_ADMIN_USER="admin"
export DD_ADMIN_FIRST_NAME="Admin"
export DD_ADMIN_LAST_NAME="User"
export DD_ALLOWED_HOSTS="*"
export DD_DATABASE_READINESS_TIMEOUT="30"
export DD_DJANGO_METRICS_ENABLED="False"
export DD_MEDIA_ROOT="/app/media"
export DD_UWSGI_HOST="uwsgi"
export DD_UWSGI_PORT="3031"
export NGINX_METRICS_ENABLED="false"
export DEFECT_DOJO_OS="debian"
export DJANGO_VERSION="$EXPECTED_DD_TAG"
export NGINX_VERSION="$EXPECTED_DD_TAG"
export DOJO_PERMISSION_LIBRARY_ROOT="$LIBRARY_ROOT"
BASE_SOURCE_IMAGE="defectdojo/defectdojo-django:$EXPECTED_DD_TAG"
NGINX_SOURCE_IMAGE="defectdojo/defectdojo-nginx:$EXPECTED_DD_TAG"
export DOJO_PERMISSION_BASE_IMAGE="$BASE_SOURCE_IMAGE"
export DOJO_PERMISSION_IMAGE="dojo-permission/defectdojo-django:$EXPECTED_DD_TAG"
export DOJO_PERMISSION_RUNTIME_USER="1001"
export DOJO_PERMISSION_BUILD_NETWORK="none"
unset COMPOSE_ENV_FILES COMPOSE_DISABLE_ENV_FILE DOCKER_DEFAULT_PLATFORM

VANILLA_COMPOSE=(
    "${DOCKER[@]}" compose
    --env-file /dev/null
    --project-directory "$DEFECTDOJO_DIR"
    --project-name "$PROJECT_NAME"
    --file "$DEFECTDOJO_DIR/docker-compose.yml"
)
if [[ ${#EXTRA_COMPOSE_FILES[@]} -gt 0 ]]; then
    for EXTRA_COMPOSE_FILE in "${EXTRA_COMPOSE_FILES[@]}"; do
        VANILLA_COMPOSE+=(--file "$EXTRA_COMPOSE_FILE")
    done
    unset EXTRA_COMPOSE_FILE
fi
PERMISSION_COMPOSE=(
    "${VANILLA_COMPOSE[@]}"
    --file "$LIBRARY_ROOT/docker/compose.permission.yml"
    --file "$LIBRARY_ROOT/docker/compose.permission.prebuilt.yml"
)
if [[ -n "$CA_BUNDLE" ]]; then
    PERMISSION_COMPOSE+=(--file "$LIBRARY_ROOT/docker/compose.permission.ca.yml")
fi

STATE_DIR="$LIBRARY_ROOT/.dojo-permission-local"
STATE_FILE="$STATE_DIR/$PROJECT_NAME.state"
EXPECTED_STATE="$(printf '%s\n' \
    'DOJO_PERMISSION_LOCAL_STACK_V1' \
    "project=$PROJECT_NAME" \
    "defectdojo=$DEFECTDOJO_DIR" \
    "library=$LIBRARY_ROOT")"

EXISTING_CONTAINER_IDS="$("${DOCKER[@]}" ps -a -q \
    --filter "label=com.docker.compose.project=$PROJECT_NAME")"
EXISTING_VOLUME_NAMES="$("${DOCKER[@]}" volume ls -q \
    --filter "label=com.docker.compose.project=$PROJECT_NAME")"
EXISTING_NETWORK_IDS="$("${DOCKER[@]}" network ls -q \
    --filter "label=com.docker.compose.project=$PROJECT_NAME")"

[[ ! -L "$STATE_DIR" ]] || die "Local stack state directory must not be a symlink: $STATE_DIR"
[[ ! -L "$STATE_FILE" ]] || die "Local stack state file must not be a symlink: $STATE_FILE"
if [[ -e "$STATE_FILE" ]]; then
    [[ -f "$STATE_FILE" && ! -L "$STATE_FILE" ]] || die \
        "Local stack state is not a regular file: $STATE_FILE"
    [[ "$(cat "$STATE_FILE")" == "$EXPECTED_STATE" ]] || die \
        "Local stack state does not match this checkout and project: $STATE_FILE"
elif [[ -n "$EXISTING_CONTAINER_IDS$EXISTING_VOLUME_NAMES$EXISTING_NETWORK_IDS" ]]; then
    die "Compose project '$PROJECT_NAME' already has unowned Docker resources. Choose another --project-name."
else
    [[ ! -e "$STATE_DIR" || -d "$STATE_DIR" ]] || die \
        "Local stack state path is not a directory: $STATE_DIR"
    mkdir -p "$STATE_DIR"
    STATE_TEMP="$(mktemp "$STATE_DIR/.state.XXXXXX")"
    printf '%s\n' "$EXPECTED_STATE" > "$STATE_TEMP"
    mv "$STATE_TEMP" "$STATE_FILE"
    STATE_TEMP=""
fi

if [[ -n "$CA_BUNDLE" ]]; then
    MANAGED_CA_BUNDLE="$STATE_DIR/$PROJECT_NAME-ca-bundle.pem"
    [[ ! -L "$MANAGED_CA_BUNDLE" ]] || die \
        "Managed CA bundle must not be a symlink: $MANAGED_CA_BUNDLE"
    CA_TEMP="$(mktemp "$STATE_DIR/.ca-bundle.XXXXXX")"
    cp "$CA_BUNDLE" "$CA_TEMP"
    chmod 0644 "$CA_TEMP"
    mv "$CA_TEMP" "$MANAGED_CA_BUNDLE"
    CA_TEMP=""
    export DOJO_PERMISSION_CA_BUNDLE="$MANAGED_CA_BUNDLE"
fi

if [[ -n "$EXISTING_CONTAINER_IDS" ]]; then
    while IFS= read -r EXISTING_CONTAINER_ID; do
        [[ -n "$EXISTING_CONTAINER_ID" ]] || continue
        EXISTING_WORKDIR="$("${DOCKER[@]}" inspect --format \
            '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' \
            "$EXISTING_CONTAINER_ID")"
        EXISTING_CONFIG_FILES="$("${DOCKER[@]}" inspect --format \
            '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' \
            "$EXISTING_CONTAINER_ID")"
        [[ "$EXISTING_WORKDIR" == "$DEFECTDOJO_DIR" ]] || die \
            "Compose project '$PROJECT_NAME' belongs to another directory: $EXISTING_WORKDIR"
        case ",$EXISTING_CONFIG_FILES," in
            *",$LIBRARY_ROOT/docker/compose.permission.yml,"*) ;;
            *) die "Compose project '$PROJECT_NAME' was not created with this local permission overlay" ;;
        esac
    done <<< "$EXISTING_CONTAINER_IDS"
    unset EXISTING_CONTAINER_ID EXISTING_WORKDIR EXISTING_CONFIG_FILES
fi

printf '\nVerifying the exact DefectDojo Compose configuration...\n'
"${VANILLA_COMPOSE[@]}" config -q
BASE_SERVICES="$("${VANILLA_COMPOSE[@]}" config --services)"
for REQUIRED_SERVICE in nginx uwsgi initializer celeryworker celerybeat postgres valkey; do
    printf '%s\n' "$BASE_SERVICES" | grep -Fx "$REQUIRED_SERVICE" >/dev/null || die \
        "Required Compose service is missing: $REQUIRED_SERVICE"
done
unset BASE_SERVICES REQUIRED_SERVICE

case "$IMAGE_SOURCE" in
    build)
        printf '\nBuilding the pinned DefectDojo %s Django and nginx images...\n' \
            "$EXPECTED_DD_TAG"
        "${VANILLA_COMPOSE[@]}" build --pull uwsgi nginx
        printf 'Pulling the pinned local database and broker images...\n'
        "${VANILLA_COMPOSE[@]}" pull --policy always postgres valkey
        ;;
    pull)
        printf '\nPulling the exact DefectDojo %s Django and nginx images...\n' \
            "$EXPECTED_DD_TAG"
        if ! "${DOCKER[@]}" pull "$BASE_SOURCE_IMAGE" \
            || ! "${DOCKER[@]}" pull "$NGINX_SOURCE_IMAGE"
        then
            die "Could not pull DefectDojo images. Configure Docker daemon/Desktop CA trust, then retry."
        fi
        if ! "${VANILLA_COMPOSE[@]}" pull --policy always postgres valkey; then
            die "Could not pull the local database or broker image"
        fi
        ;;
    local)
        printf '\nUsing only images already present in the local Docker daemon...\n'
        "${DOCKER[@]}" image inspect "$BASE_SOURCE_IMAGE" >/dev/null 2>&1 || die \
            "Missing local image: $BASE_SOURCE_IMAGE"
        "${DOCKER[@]}" image inspect "$NGINX_SOURCE_IMAGE" >/dev/null 2>&1 || die \
            "Missing local image: $NGINX_SOURCE_IMAGE"
        SUPPORT_IMAGES="$("${VANILLA_COMPOSE[@]}" config --images postgres valkey)"
        while IFS= read -r SUPPORT_IMAGE; do
            [[ -n "$SUPPORT_IMAGE" ]] || continue
            "${DOCKER[@]}" image inspect "$SUPPORT_IMAGE" >/dev/null 2>&1 || die \
                "Missing local image required by Compose: $SUPPORT_IMAGE"
        done <<< "$SUPPORT_IMAGES"
        unset SUPPORT_IMAGE SUPPORT_IMAGES
        ;;
esac

BASE_IMAGE_ID="$("${DOCKER[@]}" image inspect "$BASE_SOURCE_IMAGE" --format '{{.Id}}')"
NGINX_IMAGE_ID="$("${DOCKER[@]}" image inspect "$NGINX_SOURCE_IMAGE" --format '{{.Id}}')"

"${DOCKER[@]}" run --pull=never --rm --entrypoint python "$BASE_IMAGE_ID" -c \
    "import dojo, django, sys; assert dojo.__version__ == '$EXPECTED_DD_TAG', dojo.__version__; assert django.get_version() == '$EXPECTED_DJANGO_VERSION', django.get_version(); assert sys.version_info[:2] == ($EXPECTED_PYTHON_MAJOR, $EXPECTED_PYTHON_MINOR), sys.version"

BASE_ID_SUFFIX="${BASE_IMAGE_ID#sha256:}"
NGINX_ID_SUFFIX="${NGINX_IMAGE_ID#sha256:}"
LOCAL_BASE_IMAGE="dojo-permission/local-defectdojo-base:${EXPECTED_DD_TAG}-${BASE_ID_SUFFIX:0:12}"
LOCAL_NGINX_IMAGE="dojo-permission/local-defectdojo-nginx:${EXPECTED_DD_TAG}-${NGINX_ID_SUFFIX:0:12}"
"${DOCKER[@]}" image tag "$BASE_IMAGE_ID" "$LOCAL_BASE_IMAGE"
"${DOCKER[@]}" image tag "$NGINX_IMAGE_ID" "$LOCAL_NGINX_IMAGE"
export DOJO_PERMISSION_BASE_IMAGE="$LOCAL_BASE_IMAGE"
export DOJO_PERMISSION_NGINX_IMAGE="$LOCAL_NGINX_IMAGE"

if [[ -n "$CA_BUNDLE" ]]; then
    CA_MOUNT="type=bind,source=$MANAGED_CA_BUNDLE,target=$CA_CONTAINER_PATH,readonly"
    "${DOCKER[@]}" run --pull=never --rm --mount "$CA_MOUNT" \
        --entrypoint python "$BASE_IMAGE_ID" -c \
        "import pathlib, ssl; p = pathlib.Path('$CA_CONTAINER_PATH'); assert p.is_file(); p.open('rb').read(1); ssl.create_default_context(cafile=str(p))"
    unset CA_MOUNT
fi

printf '\nVerifying the combined local permission configuration...\n'
"${PERMISSION_COMPOSE[@]}" config -q
REQUIRED_SERVICES="$("${PERMISSION_COMPOSE[@]}" config --services)"
for REQUIRED_SERVICE in nginx uwsgi initializer celeryworker celerybeat postgres valkey; do
    printf '%s\n' "$REQUIRED_SERVICES" | grep -Fx "$REQUIRED_SERVICE" >/dev/null || die \
        "Required Compose service is missing: $REQUIRED_SERVICE"
done
unset REQUIRED_SERVICE REQUIRED_SERVICES

printf '\nBuilding the thin permission Django image without network access...\n'
"${PERMISSION_COMPOSE[@]}" build uwsgi

"${DOCKER[@]}" run --pull=never --rm --entrypoint python "$DOJO_PERMISSION_IMAGE" -c \
    "import dojo, django, importlib.metadata as metadata; assert dojo.__version__ == '$EXPECTED_DD_TAG'; assert django.get_version() == '$EXPECTED_DJANGO_VERSION'; assert metadata.version('dojo-permission') == '0.1.0'"
"${DOCKER[@]}" run --pull=never --rm --entrypoint python "$DOJO_PERMISSION_IMAGE" -m pip check

printf '\nStarting the local database and broker...\n'
if ! "${PERMISSION_COMPOSE[@]}" up -d --pull never --no-build postgres valkey; then
    "${PERMISSION_COMPOSE[@]}" ps --all >&2 || true
    die "Could not start the local database and broker"
fi

printf 'Starting and monitoring the DefectDojo initializer...\n'
INITIALIZER_STARTED_AT=$SECONDS
if ! "${PERMISSION_COMPOSE[@]}" up -d --pull never --no-deps --no-build \
    --force-recreate initializer
then
    "${PERMISSION_COMPOSE[@]}" logs --no-color initializer >&2 || true
    die "Could not start the DefectDojo initializer"
fi
printf 'Waiting up to %s seconds for the initializer...\n' "$INITIALIZER_TIMEOUT"
while true; do
    INITIALIZER_ID="$("${DOCKER[@]}" ps -a -q \
        --filter "label=com.docker.compose.project=$PROJECT_NAME" \
        --filter 'label=com.docker.compose.service=initializer' \
        --filter 'label=com.docker.compose.oneoff=False')"
    if [[ -n "$INITIALIZER_ID" ]]; then
        INITIALIZER_STATE="$("${DOCKER[@]}" inspect --format '{{.State.Status}} {{.State.ExitCode}}' "$INITIALIZER_ID" 2>/dev/null || true)"
        case "$INITIALIZER_STATE" in
            'exited 0') break ;;
            exited\ *|dead\ *)
                "${PERMISSION_COMPOSE[@]}" logs --no-color initializer >&2 || true
                die "DefectDojo initializer failed with state: $INITIALIZER_STATE"
                ;;
        esac
    fi
    if [[ $((SECONDS - INITIALIZER_STARTED_AT)) -ge "$INITIALIZER_TIMEOUT" ]]; then
        "${PERMISSION_COMPOSE[@]}" logs --no-color initializer >&2 || true
        die "Timed out waiting for the DefectDojo initializer"
    fi
    sleep 2
done

printf '\nStarting the web and task services...\n'
if ! "${PERMISSION_COMPOSE[@]}" up -d --pull never --no-deps --no-build --force-recreate \
    uwsgi celeryworker celerybeat
then
    "${PERMISSION_COMPOSE[@]}" logs --no-color --tail 100 \
        uwsgi celeryworker celerybeat >&2 || true
    die "Could not start the DefectDojo web and task services"
fi
if ! "${PERMISSION_COMPOSE[@]}" up -d --pull never --no-deps --no-build \
    --force-recreate nginx
then
    "${PERMISSION_COMPOSE[@]}" logs --no-color --tail 100 nginx >&2 || true
    die "Could not start the local nginx service"
fi

printf 'Waiting up to %s seconds for %s...\n' "$HTTP_TIMEOUT" "$HEALTH_URL"
HTTP_STARTED_AT=$SECONDS
CURL_HEALTH=(curl --fail --silent --show-error --max-time 5)
if [[ "$HEALTH_URL" == https://* && -n "$CA_BUNDLE" ]]; then
    CURL_HEALTH+=(--cacert "$MANAGED_CA_BUNDLE")
fi
while ! "${CURL_HEALTH[@]}" -- "$HEALTH_URL" >/dev/null 2>&1; do
    if [[ $((SECONDS - HTTP_STARTED_AT)) -ge "$HTTP_TIMEOUT" ]]; then
        "${PERMISSION_COMPOSE[@]}" ps >&2 || true
        "${PERMISSION_COMPOSE[@]}" logs --no-color --tail 100 uwsgi nginx >&2 || true
        die "Timed out waiting for the DefectDojo login page"
    fi
    sleep 2
done
unset CURL_HEALTH

printf '\nRunning application and migration checks...\n'
"${PERMISSION_COMPOSE[@]}" exec -T uwsgi python manage.py check
MIGRATIONS="$("${PERMISSION_COMPOSE[@]}" exec -T uwsgi python manage.py showmigrations dojo_permission)"
printf '%s\n' "$MIGRATIONS"
printf '%s\n' "$MIGRATIONS" | grep -F '[X] 0001_initial' >/dev/null \
    || die "dojo_permission migration 0001_initial is not applied"
if [[ -n "$CA_BUNDLE" ]]; then
    "${PERMISSION_COMPOSE[@]}" exec -T uwsgi python -c \
        "import os, pathlib, ssl; expected='$CA_CONTAINER_PATH'; assert os.environ.get('REQUESTS_CA_BUNDLE') == expected; assert os.environ.get('SSL_CERT_FILE') == expected; p=pathlib.Path(expected); assert p.is_file(); p.open('rb').read(1); ssl.create_default_context(cafile=str(p))"
fi

EXPECTED_IMAGE_ID="$("${DOCKER[@]}" image inspect "$DOJO_PERMISSION_IMAGE" --format '{{.Id}}')"
for SERVICE in uwsgi initializer celeryworker celerybeat; do
    CONTAINER_ID="$("${DOCKER[@]}" ps -a -q \
        --filter "label=com.docker.compose.project=$PROJECT_NAME" \
        --filter "label=com.docker.compose.service=$SERVICE" \
        --filter 'label=com.docker.compose.oneoff=False')"
    [[ -n "$CONTAINER_ID" ]] || die "No container found for service: $SERVICE"
    ACTUAL_IMAGE_ID="$("${DOCKER[@]}" inspect "$CONTAINER_ID" --format '{{.Image}}')"
    [[ "$ACTUAL_IMAGE_ID" == "$EXPECTED_IMAGE_ID" ]] || die \
        "$SERVICE is not using the expected permission image"
    SERVICE_STATE="$("${DOCKER[@]}" inspect "$CONTAINER_ID" --format '{{.State.Status}} {{.State.ExitCode}}')"
    if [[ "$SERVICE" == initializer ]]; then
        [[ "$SERVICE_STATE" == 'exited 0' ]] || die \
            "initializer is not successfully completed: $SERVICE_STATE"
    else
        [[ "$SERVICE_STATE" == running\ * ]] || die \
            "$SERVICE is not running: $SERVICE_STATE"
    fi
done
NGINX_ID="$("${DOCKER[@]}" ps -a -q \
    --filter "label=com.docker.compose.project=$PROJECT_NAME" \
    --filter 'label=com.docker.compose.service=nginx' \
    --filter 'label=com.docker.compose.oneoff=False')"
[[ -n "$NGINX_ID" ]] || die "No container found for service: nginx"
EXPECTED_NGINX_IMAGE_ID="$("${DOCKER[@]}" image inspect \
    "$DOJO_PERMISSION_NGINX_IMAGE" --format '{{.Id}}')"
[[ "$("${DOCKER[@]}" inspect "$NGINX_ID" --format '{{.Image}}')" \
    == "$EXPECTED_NGINX_IMAGE_ID" ]] || die "nginx is not using the expected local image"
[[ "$("${DOCKER[@]}" inspect "$NGINX_ID" --format '{{.State.Running}}')" == true ]] || die \
    "nginx is not running"
unset SERVICE CONTAINER_ID ACTUAL_IMAGE_ID SERVICE_STATE NGINX_ID EXPECTED_NGINX_IMAGE_ID

printf '\nInitializer admin status (the generated password appears only on first initialization):\n'
ADMIN_STATUS="$("${PERMISSION_COMPOSE[@]}" logs --no-color initializer 2>/dev/null \
    | grep -E 'Admin password:|Admin user already exists' || true)"
if [[ -n "$ADMIN_STATUS" ]]; then
    printf '%s\n' "$ADMIN_STATUS"
else
    printf '%s\n' 'No new admin-password line was found. Use the credentials from the original initialization.'
fi

"${PERMISSION_COMPOSE[@]}" ps

cat <<EOF

Local staging is ready
DefectDojo:       $EXPECTED_DD_TAG
Dojo Permission: 0.1.0
Image source:     $IMAGE_SOURCE
URL:              $HEALTH_URL
Admin username:   admin
EOF

COMPOSE_COMMAND_ENV=(
    "DD_PORT=$HTTP_PORT"
    "DD_TLS_PORT=$TLS_PORT"
    "DOJO_PERMISSION_LIBRARY_ROOT=$LIBRARY_ROOT"
    "DOJO_PERMISSION_BASE_IMAGE=$LOCAL_BASE_IMAGE"
    "DOJO_PERMISSION_IMAGE=$DOJO_PERMISSION_IMAGE"
    "DOJO_PERMISSION_NGINX_IMAGE=$LOCAL_NGINX_IMAGE"
    "DOJO_PERMISSION_RUNTIME_USER=$DOJO_PERMISSION_RUNTIME_USER"
    "DOJO_PERMISSION_BUILD_NETWORK=$DOJO_PERMISSION_BUILD_NETWORK"
)
if [[ -n "$CA_BUNDLE" ]]; then
    COMPOSE_COMMAND_ENV+=("DOJO_PERMISSION_CA_BUNDLE=$MANAGED_CA_BUNDLE")
fi

printf '\nKeep the same project name and complete Compose-file list. Status command:\n\n'
print_shell_command "${COMPOSE_COMMAND_ENV[@]}" "${PERMISSION_COMPOSE[@]}" ps --all

printf '\nIf the first-run password is no longer available, reset it with:\n\n'
print_shell_command "${COMPOSE_COMMAND_ENV[@]}" "${PERMISSION_COMPOSE[@]}" \
    exec uwsgi ./manage.py changepassword admin

printf '\nDo not use this local bootstrap against a running shared or production deployment.\n'
