#!/usr/bin/env bash

# Shared environment, path, and project setup for local installation commands.

local_add_docker_bin_to_path() {
    local docker_bin="$1"

    if [[ -x "$docker_bin/docker" ]]; then
        case ":${PATH:-}:" in
            *":$docker_bin:"*) ;;
            *) PATH="${PATH:+$PATH:}$docker_bin" ;;
        esac
        export PATH
    fi
    return 0
}

local_checkout_path() {
    local supplied_path="$1"
    local parent_path
    local base_name

    case "$supplied_path" in
        /*) ;;
        *) supplied_path="$(pwd -P)/$supplied_path" ;;
    esac

    if [[ -d "$supplied_path" ]]; then
        (cd "$supplied_path" && pwd -P)
        return
    fi

    parent_path="$(dirname "$supplied_path")"
    base_name="$(basename "$supplied_path")"
    if [[ ! -d "$parent_path" ]]; then
        printf 'ERROR: Parent directory does not exist: %s\n' "$parent_path" >&2
        return 1
    fi
    parent_path="$(cd "$parent_path" && pwd -P)"
    printf '%s/%s\n' "$parent_path" "$base_name"
}

local_default_project_name() {
    local library_root="$1"
    local defectdojo_dir="$2"
    local digest
    local project_name
    local state_dir="$library_root/.dojo-permission-local"
    local legacy_state="$state_dir/dojo-permission-dev.state"
    local expected_legacy_state

    if command -v shasum >/dev/null 2>&1; then
        digest="$(printf '%s' "$library_root|$defectdojo_dir" | shasum -a 256)"
    elif command -v sha256sum >/dev/null 2>&1; then
        digest="$(printf '%s' "$library_root|$defectdojo_dir" | sha256sum)"
    else
        digest="$(printf '%s' "$library_root|$defectdojo_dir" | cksum)"
    fi
    digest="${digest%% *}"
    project_name="dojo-permission-local-${digest:0:12}"

    # Keep a previously owned legacy stack on its existing database/volumes.
    # Once a path-specific stack exists, it takes precedence over legacy state.
    if [[ ! -e "$state_dir/$project_name.state" && ! -L "$state_dir/$project_name.state" \
        && ! -L "$state_dir" && -f "$legacy_state" && ! -L "$legacy_state" ]]; then
        expected_legacy_state="$(printf '%s\n' \
            'DOJO_PERMISSION_LOCAL_STACK_V1' \
            'project=dojo-permission-dev' \
            "defectdojo=$defectdojo_dir" \
            "library=$library_root")"
        if [[ "$(cat "$legacy_state")" == "$expected_legacy_state" ]]; then
            project_name="dojo-permission-dev"
        fi
    fi

    printf '%s\n' "$project_name"
}
