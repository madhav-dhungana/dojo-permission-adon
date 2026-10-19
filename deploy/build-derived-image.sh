#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

EXPECTED_DD_VERSION="2.58.3"
EXPECTED_DJANGO_VERSION="5.2.14"
PACKAGE_VERSION="0.2.0"
PUSH_IMAGE=0
ALLOW_DIRTY=0
OUTPUT_FILE=""
BASE_IMAGE="${BASE_IMAGE:-}"
TARGET_IMAGE="${TARGET_IMAGE:-}"
RUNTIME_USER="${RUNTIME_USER:-}"
TARGET_PLATFORM="${TARGET_PLATFORM:-}"
CREATED_BASE_TAG=""
PUSH_LOG=""
TMP_OUTPUT=""

cleanup() {
    if [[ -n "$PUSH_LOG" ]]; then
        rm -f "$PUSH_LOG"
    fi
    if [[ -n "$TMP_OUTPUT" ]]; then
        rm -f "$TMP_OUTPUT"
    fi
    if [[ -n "$CREATED_BASE_TAG" ]]; then
        docker image rm "$CREATED_BASE_TAG" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

usage() {
    cat <<'EOF'
Build the permission library as a final layer on an existing customized
DefectDojo 2.58.3 Django image.

Registry/CI approach:
  build-derived-image.sh \
    --base-image registry.example/custom-dojo@sha256:<digest> \
    --target-image registry.example/custom-dojo-permission:0.2.0 \
    --platform linux/amd64 \
    --push --output artifacts/derived-image.env

On-host approach (no registry required):
  build-derived-image.sh \
    --base-image sha256:<current-local-uwsgi-image-id> \
    --target-image local/custom-dojo-permission:0.2.0 \
    --output artifacts/derived-image.env

Options:
  --base-image REF     Exact registry digest or local sha256 image ID.
  --target-image TAG   New versioned image tag; "latest" is rejected.
  --runtime-user USER  Runtime UID/name. Defaults to the base image user.
  --platform PLATFORM  linux/amd64 or linux/arm64. Required for registry bases;
                       inferred from a local image ID for on-host builds.
  --output FILE        Write shell-safe image metadata to FILE.
  --push               Push TARGET_IMAGE and output its registry digest.
  --allow-dirty        Permit building from an uncommitted worktree.
  -h, --help           Show this help.
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

while [[ $# -gt 0 ]]; do
    case "$1" in
        --base-image)
            [[ $# -ge 2 ]] || die "--base-image requires a value"
            BASE_IMAGE="$2"
            shift 2
            ;;
        --target-image)
            [[ $# -ge 2 ]] || die "--target-image requires a value"
            TARGET_IMAGE="$2"
            shift 2
            ;;
        --runtime-user)
            [[ $# -ge 2 ]] || die "--runtime-user requires a value"
            RUNTIME_USER="$2"
            shift 2
            ;;
        --platform)
            [[ $# -ge 2 ]] || die "--platform requires a value"
            TARGET_PLATFORM="$2"
            shift 2
            ;;
        --output)
            [[ $# -ge 2 ]] || die "--output requires a value"
            OUTPUT_FILE="$2"
            shift 2
            ;;
        --push)
            PUSH_IMAGE=1
            shift
            ;;
        --allow-dirty)
            ALLOW_DIRTY=1
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
need git
need python3

[[ -n "$BASE_IMAGE" ]] || die "--base-image is required"
[[ -n "$TARGET_IMAGE" ]] || die "--target-image is required"
is_immutable_image "$BASE_IMAGE" || die "The base must be repo@sha256:<digest> or a local sha256:<image-id>"
[[ "$TARGET_IMAGE" != *:latest ]] || die "The target tag must not be latest"
[[ "${TARGET_IMAGE##*/}" == *:* ]] || die "The target image must include a version tag"
[[ "$TARGET_IMAGE" != *[[:space:]\"\']* ]] || die "Image references must not contain whitespace or quotes"
if docker image inspect "$TARGET_IMAGE" >/dev/null 2>&1; then
    die "The target image tag already exists; use a new versioned tag: $TARGET_IMAGE"
fi
if [[ -n "$RUNTIME_USER" ]]; then
    [[ "$RUNTIME_USER" =~ ^[A-Za-z0-9_.:-]+$ ]] || die "Invalid --runtime-user value"
fi
if [[ -n "$TARGET_PLATFORM" ]]; then
    [[ "$TARGET_PLATFORM" == "linux/amd64" || "$TARGET_PLATFORM" == "linux/arm64" ]] || die "--platform must be linux/amd64 or linux/arm64"
fi
if [[ "$BASE_IMAGE" == *@sha256:* && -z "$TARGET_PLATFORM" ]]; then
    die "--platform is required when the base is a registry digest"
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
DOCKERFILE="$REPO_ROOT/docker/Dockerfile"
[[ -f "$DOCKERFILE" ]] || die "Dockerfile not found: $DOCKERFILE"
if [[ -n "$OUTPUT_FILE" ]]; then
    OUTPUT_FILE="$(python3 -c 'import os, sys; print(os.path.realpath(os.path.abspath(sys.argv[1])))' "$OUTPUT_FILE")"
    case "$OUTPUT_FILE" in
        "$REPO_ROOT"/*)
            case "$OUTPUT_FILE" in
                "$REPO_ROOT/artifacts/"*) ;;
                *) die "Output files inside the repository must be under artifacts/" ;;
            esac
            ;;
    esac
    if [[ -e "$OUTPUT_FILE" ]]; then
        [[ -f "$OUTPUT_FILE" ]] || die "Existing --output is not a regular file"
        [[ "$(head -n 1 "$OUTPUT_FILE")" == '# Generated by dojo-permission build-derived-image.sh' ]] || die "Existing --output is not managed by this script"
    fi
fi

REVISION="uncommitted"
if git -C "$REPO_ROOT" rev-parse --verify HEAD >/dev/null 2>&1; then
    REVISION="$(git -C "$REPO_ROOT" rev-parse HEAD)"
fi
if [[ -n "$(git -C "$REPO_ROOT" status --porcelain)" ]]; then
    [[ "$ALLOW_DIRTY" -eq 1 ]] || die "The source tree is dirty. Commit it first or pass --allow-dirty."
    [[ "$PUSH_IMAGE" -ne 1 ]] || die "A dirty source tree cannot be pushed as a production image"
    REVISION="${REVISION}-dirty"
fi

if ! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
    [[ "$BASE_IMAGE" == *@sha256:* ]] || die "Local base image ID was not found: $BASE_IMAGE"
    docker pull --platform "$TARGET_PLATFORM" "$BASE_IMAGE"
fi

BASE_PLATFORM="$(docker image inspect "$BASE_IMAGE" --format '{{.Os}}/{{.Architecture}}')"
if [[ -z "$TARGET_PLATFORM" ]]; then
    TARGET_PLATFORM="$BASE_PLATFORM"
fi
[[ "$BASE_PLATFORM" == "$TARGET_PLATFORM" ]] || die "Base image platform is $BASE_PLATFORM, expected $TARGET_PLATFORM"

BUILD_BASE_IMAGE="$BASE_IMAGE"
if [[ "$BASE_IMAGE" =~ ^sha256:([0-9a-f]{64})$ ]]; then
    LOCAL_BASE_TAG="dojo-permission/local-base:${BASH_REMATCH[1]}"
    if docker image inspect "$LOCAL_BASE_TAG" >/dev/null 2>&1; then
        LOCAL_TAG_ID="$(docker image inspect "$LOCAL_BASE_TAG" --format '{{.Id}}')"
        [[ "$LOCAL_TAG_ID" == "$BASE_IMAGE" ]] || die "Temporary local base tag points to a different image: $LOCAL_BASE_TAG"
    else
        CREATED_BASE_TAG="$LOCAL_BASE_TAG"
        if ! docker image tag "$BASE_IMAGE" "$LOCAL_BASE_TAG"; then
            CREATED_BASE_TAG=""
            die "Could not create a temporary local base tag"
        fi
    fi
    if docker image inspect "$TARGET_IMAGE" >/dev/null 2>&1; then
        die "The target image collides with the temporary local base tag; choose another target tag"
    fi
    BUILD_BASE_IMAGE="$LOCAL_BASE_TAG"
fi

docker run --rm --platform "$TARGET_PLATFORM" --entrypoint python "$BASE_IMAGE" -c \
    "import dojo, django, sys; assert dojo.__version__ == '$EXPECTED_DD_VERSION', dojo.__version__; assert django.get_version() == '$EXPECTED_DJANGO_VERSION', django.get_version(); assert sys.version_info[:2] == (3, 13), sys.version"

BASE_USER="$(docker image inspect "$BASE_IMAGE" --format '{{.Config.User}}')"
if [[ -z "$RUNTIME_USER" ]]; then
    RUNTIME_USER="$BASE_USER"
fi
[[ -n "$RUNTIME_USER" ]] || die "The base image has no runtime USER. Pass --runtime-user explicitly."

docker build --pull=false --platform "$TARGET_PLATFORM" \
    --build-arg "BASE_IMAGE=$BUILD_BASE_IMAGE" \
    --build-arg "RUNTIME_USER=$RUNTIME_USER" \
    --label "org.opencontainers.image.revision=$REVISION" \
    --label "io.dojo-permission.base-image=$BASE_IMAGE" \
    --label "io.dojo-permission.defectdojo-version=$EXPECTED_DD_VERSION" \
    --label "io.dojo-permission.package-version=$PACKAGE_VERSION" \
    --label "io.dojo-permission.platform=$TARGET_PLATFORM" \
    --file "$DOCKERFILE" \
    --tag "$TARGET_IMAGE" \
    "$REPO_ROOT"

DERIVED_USER="$(docker image inspect "$TARGET_IMAGE" --format '{{.Config.User}}')"
[[ "$DERIVED_USER" == "$RUNTIME_USER" ]] || die "Runtime user mismatch (expected='$RUNTIME_USER', derived='$DERIVED_USER')"
DERIVED_PLATFORM="$(docker image inspect "$TARGET_IMAGE" --format '{{.Os}}/{{.Architecture}}')"
[[ "$DERIVED_PLATFORM" == "$TARGET_PLATFORM" ]] || die "Derived image platform is $DERIVED_PLATFORM, expected $TARGET_PLATFORM"

docker run --rm --platform "$TARGET_PLATFORM" --entrypoint python "$TARGET_IMAGE" -c \
    "import dojo, django, importlib.metadata as metadata; assert dojo.__version__ == '$EXPECTED_DD_VERSION'; assert django.get_version() == '$EXPECTED_DJANGO_VERSION'; assert metadata.version('dojo-permission') == '$PACKAGE_VERSION'"
docker run --rm --platform "$TARGET_PLATFORM" --entrypoint python "$TARGET_IMAGE" -m pip check

FINAL_IMAGE="$(docker image inspect "$TARGET_IMAGE" --format '{{.Id}}')"
if [[ "$PUSH_IMAGE" -eq 1 ]]; then
    PUSH_LOG="$(mktemp)"
    docker push "$TARGET_IMAGE" | tee "$PUSH_LOG"
    PUSH_DIGEST="$(awk '{for (i = 1; i < NF; i++) if ($i == "digest:" && $(i + 1) ~ /^sha256:/) print $(i + 1)}' "$PUSH_LOG" | tail -1)"
    [[ "$PUSH_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || die "Could not determine the pushed digest"
    TARGET_TAG="${TARGET_IMAGE##*:}"
    TARGET_REPOSITORY="${TARGET_IMAGE%:$TARGET_TAG}"
    FINAL_IMAGE="$TARGET_REPOSITORY@$PUSH_DIGEST"
fi

if [[ -n "$OUTPUT_FILE" ]]; then
    mkdir -p "$(dirname "$OUTPUT_FILE")" || die "Could not create the output directory"
    TMP_OUTPUT="$(mktemp "${OUTPUT_FILE}.tmp.XXXXXX")" || die "Could not create a temporary output file"
    if ! {
        printf '# Generated by dojo-permission build-derived-image.sh\n'
        printf 'DOJO_PERMISSION_IMAGE=%q\n' "$FINAL_IMAGE"
        printf 'DOJO_PERMISSION_BASE_IMAGE=%q\n' "$BASE_IMAGE"
        printf 'DOJO_PERMISSION_SOURCE_REVISION=%q\n' "$REVISION"
        printf 'DOJO_PERMISSION_PLATFORM=%q\n' "$TARGET_PLATFORM"
    } > "$TMP_OUTPUT"; then
        rm -f "$TMP_OUTPUT"
        die "Could not write image metadata"
    fi
    if ! mv "$TMP_OUTPUT" "$OUTPUT_FILE"; then
        rm -f "$TMP_OUTPUT"
        die "Could not persist image metadata"
    fi
    TMP_OUTPUT=""
fi

printf '\nDerived image ready: %s\n' "$FINAL_IMAGE"
printf 'Base image:          %s\n' "$BASE_IMAGE"
printf 'Source revision:     %s\n' "$REVISION"
printf 'Target platform:     %s\n' "$TARGET_PLATFORM"
