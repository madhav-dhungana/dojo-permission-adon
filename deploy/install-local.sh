#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Friendly entry point for a new local staging stack. The guarded bootstrap
# remains the single implementation of cloning, image validation, startup,
# migrations, and health checks.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
LIBRARY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
LIBRARY_PARENT="$(dirname "$LIBRARY_ROOT")"
BOOTSTRAP="$SCRIPT_DIR/bootstrap-local-docker.sh"

source "$SCRIPT_DIR/local-stack-common.sh"

MODE="without"
MODE_WAS_SET="false"
DEFECTDOJO_DIR="${DOJO_PERMISSION_DEFECTDOJO_DIR:-$LIBRARY_PARENT/defectdojo-2.58.3}"
DRY_RUN="false"

usage() {
    cat <<'EOF'
Install DefectDojo 2.58.3 and the Dojo Permission Library as one local stack.

Usage:
  bash deploy/install-local.sh [mode] [options]

Modes:
  --without-signing-certificate
      Without Signing Certificate (default). Reuses a downloaded sibling
      defectdojo-2.58.3 checkout, including docker-compose.yml edits.
      If the checkout is missing, runs the original source bootstrap.

  --with-signing-certificate
      With Signing Certificate. Reuses an already downloaded sibling
      defectdojo-2.58.3 checkout, including changes made to docker-compose.yml.
      Docker must trust the signing certificate before downloading images.

Options:
  --dry-run             Print the resolved bootstrap command without running it.
  -h, --help            Show this help.

Common commands:
  bash deploy/bootstrap-local-docker.sh
  bash deploy/install-local.sh              # Resume a downloaded checkout.
  bash deploy/install-local.sh --with-signing-certificate

Both scripts use the same project name for this pair of checkout paths.
An existing checkout uses the release images; application source edits are
not built. Docker must trust any signing certificate before pulling images.
The installer may recreate this stack's service containers. It never runs
Compose down or deletes volumes, networks, checkouts, or application data.
EOF
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

select_mode() {
    local requested_mode="$1"
    if [[ "$MODE_WAS_SET" == true && "$MODE" != "$requested_mode" ]]; then
        die "Choose only one mode: --without-signing-certificate or --with-signing-certificate"
    fi
    MODE="$requested_mode"
    MODE_WAS_SET="true"
}

print_shell_command() {
    printf '  '
    printf '%q ' "$@"
    printf '\n'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --without-signing-certificate)
            select_mode without
            shift
            ;;
        --with-signing-certificate)
            select_mode with
            shift
            ;;
        --dry-run)
            DRY_RUN="true"
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

[[ -x "$BOOTSTRAP" ]] || die "Bootstrap script is missing or not executable: $BOOTSTRAP"

local_require_usable_checkout_path "$LIBRARY_ROOT"
DEFECTDOJO_DIR="$(local_checkout_path "$DEFECTDOJO_DIR")"
local_require_usable_checkout_path "$DEFECTDOJO_DIR"
DEFECTDOJO_EXISTS="false"
if [[ -e "$DEFECTDOJO_DIR" ]]; then
    [[ -d "$DEFECTDOJO_DIR" ]] || die "DefectDojo path is not a directory: $DEFECTDOJO_DIR"
    [[ -f "$DEFECTDOJO_DIR/docker-compose.yml" ]] || die \
        "DefectDojo docker-compose.yml is missing: $DEFECTDOJO_DIR/docker-compose.yml"
    DEFECTDOJO_EXISTS="true"
fi

if [[ "$MODE" == with && "$DEFECTDOJO_EXISTS" != true ]]; then
    die "With Signing Certificate requires the sibling DefectDojo checkout at $DEFECTDOJO_DIR. Download DefectDojo 2.58.3 there, update docker-compose.yml with the signing-certificate settings, and rerun this command."
fi

PROJECT_NAME="${DOJO_PERMISSION_PROJECT_NAME:-$(local_default_project_name "$LIBRARY_ROOT" "$DEFECTDOJO_DIR")}"

INSTALL_COMMAND=(
    "$BOOTSTRAP"
    --project-name "$PROJECT_NAME"
)

if [[ "$DEFECTDOJO_EXISTS" == true ]]; then
    INSTALL_COMMAND+=(
        --existing-defectdojo-dir "$DEFECTDOJO_DIR"
        --image-source pull
    )
else
    INSTALL_COMMAND+=(--defectdojo-dir "$DEFECTDOJO_DIR")
fi

if [[ "$MODE" == with ]]; then
    MODE_LABEL="With Signing Certificate"
else
    MODE_LABEL="Without Signing Certificate"
fi

cat <<EOF
LOCAL INSTALLATION
Mode:            $MODE_LABEL
Library:         $LIBRARY_ROOT
DefectDojo:      $DEFECTDOJO_DIR
Compose project: $PROJECT_NAME
EOF

if [[ "$MODE" == with ]]; then
    cat <<'EOF'

Using the signing-certificate settings from the DefectDojo Compose checkout.
Docker registry trust must be configured separately when images are pulled.
EOF
fi

printf '\nBootstrap command:\n'
print_shell_command "${INSTALL_COMMAND[@]}"

if [[ "$DRY_RUN" == true ]]; then
    printf '\nDry run only; no checkout, image, container, volume, or network was changed.\n'
    exit 0
fi

exec "${INSTALL_COMMAND[@]}"
