"""Exercise local installation without touching a real Docker daemon or Git remote."""

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest


REPOSITORY = Path(__file__).resolve().parents[1]
STUB = r'''
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(json.dumps([name, *args]) + "\n")
if name == "git":
    if os.environ.get("GIT_FORBIDDEN") == "1":
        sys.exit("Unexpected Git access for an existing Compose checkout")
    if "clone" in args:
        target = Path(args[-1])
        target.mkdir(exist_ok=True)
        (target / ".git").mkdir()
        (target / "docker-compose.yml").write_text("services: {}\n")
    elif args[-2:] == ["rev-parse", "HEAD"]:
        print("ea611112772b08b74e6429e833728ce500b4c284")
    elif args[-2:] == ["rev-parse", "--is-inside-work-tree"]:
        print("true")
    elif "describe" in args:
        print("2.58.3")
    elif args[-2:] != ["status", "--porcelain"]:
        sys.exit("Unexpected Git command: " + repr(args))
elif name == "docker":
    if args[:2] == ["compose", "version"]:
        print("2.24.4")
    elif args == ["info"]:
        pass
    elif args[:1] == ["ps"] or args[:2] in (["volume", "ls"], ["network", "ls"]):
        foreign = os.environ.get("FOREIGN_PROJECT", "dojo-permission-dev")
        if foreign and "label=com.docker.compose.project=" + foreign in args:
            print("foreign-resource")
    elif args[:1] == ["compose"] and "config" in args:
        if "--services" in args:
            print("nginx\nuwsgi\ninitializer\nceleryworker\ncelerybeat\npostgres\nvalkey")
    elif args[:1] == ["compose"] and "build" in args:
        print("controlled image-build failure", file=sys.stderr)
        sys.exit(42)
    elif args[:1] == ["pull"]:
        print("controlled image-pull failure", file=sys.stderr)
        sys.exit(43)
    else:
        sys.exit("Unexpected Docker command: " + repr(args))
else:
    sys.exit("Unexpected network command: " + name)
'''


class LocalStack:
    def __init__(self, root):
        self.library = root / "checkout with spaces" / "dojo-permission-adon"
        self.dojo = self.library.parent / "defectdojo-2.58.3"
        self.cwd = root / "unrelated working directory"
        self.cwd.mkdir()
        self.log = root / "commands.jsonl"
        binary_dir = root / "fake commands"
        binary_dir.mkdir()
        for name in ("git", "docker", "curl"):
            executable = binary_dir / name
            executable.write_text(f"#!{sys.executable}\n" + STUB)
            executable.chmod(0o755)
        for relative in (
            "deploy/bootstrap-local-docker.sh",
            "deploy/install-local.sh",
            "deploy/local-stack-common.sh",
            "docker/compose.permission.yml",
            "docker/compose.permission.prebuilt.yml",
            "integration/extra_settings/local_settings.py",
        ):
            target = self.library / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPOSITORY / relative, target)
        self.env = {
            key: value for key, value in os.environ.items()
            if not key.startswith("DOJO_PERMISSION_")
            and key not in {"DEFECTDOJO_DIR", "DEFECTDOJO_REPOSITORY", "DD_PORT", "DD_TLS_PORT"}
        }
        self.env.update(PATH=f"{binary_dir}{os.pathsep}{self.env['PATH']}", STUB_LOG=str(self.log))

    @property
    def project(self):
        pair = f"{self.library.resolve()}|{self.dojo.resolve()}"
        return "dojo-permission-local-" + hashlib.sha256(pair.encode()).hexdigest()[:12]

    @property
    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def run(self, script="bootstrap-local-docker.sh", *args, **env):
        return subprocess.run(
            ["/bin/bash", str(self.library / "deploy" / script), *args],
            cwd=self.cwd, env={**self.env, **env}, text=True, capture_output=True, timeout=15,
        )

    def existing_compose(self):
        self.dojo.mkdir(exist_ok=True)
        compose = self.dojo / "docker-compose.yml"
        compose.write_text("services: {}\n# locally edited certificate settings\n")
        return compose

    def state(self, project, *, library=None):
        state = self.library / ".dojo-permission-local" / f"{project}.state"
        state.parent.mkdir(exist_ok=True)
        state.write_text(
            f"DOJO_PERMISSION_LOCAL_STACK_V1\nproject={project}\n"
            f"defectdojo={self.dojo.resolve()}\nlibrary={library or self.library.resolve()}\n"
        )
        return state


@pytest.fixture
def stack(tmp_path):
    return LocalStack(tmp_path)


def bootstrap_command(result):
    assert result.returncode == 0, result.stderr
    return shlex.split(result.stdout.split("Bootstrap command:\n", 1)[1].splitlines()[0])


def compose_project(call):
    return call[call.index("--project-name") + 1]


def test_failed_build_keeps_checkout_for_edited_compose_recovery(stack):
    first = stack.run()
    assert first.returncode == 42, first.stdout + first.stderr
    builds = [call for call in stack.calls if call[:2] == ["docker", "compose"] and "build" in call]
    assert len(builds) == 1
    assert compose_project(builds[0]) == stack.project
    assert builds[0][-4:] == ["build", "--pull", "uwsgi", "nginx"]
    assert (stack.dojo / ".git").is_dir()
    compose = stack.dojo / "docker-compose.yml"
    assert compose.is_file()
    assert len([call for call in stack.calls if call[0] == "git" and "clone" in call]) == 1
    initial_calls = stack.calls

    edited = compose.read_text() + "# operator's signing certificate configuration\n"
    compose.write_text(edited)
    stack.log.unlink()
    second = stack.run("install-local.sh", GIT_FORBIDDEN="1")
    assert "controlled image-pull failure" in second.stderr
    assert second.returncode != 0
    assert compose.read_text() == edited
    assert all(call[0] != "git" for call in stack.calls)
    configs = [call for call in stack.calls if call[:2] == ["docker", "compose"] and "config" in call]
    assert configs and all(compose_project(call) == stack.project for call in configs)
    assert not any(
        token in {"down", "rm", "prune"} for call in initial_calls + stack.calls for token in call
    )


@pytest.mark.parametrize("mode", [(), ("--without-signing-certificate",)])
@pytest.mark.parametrize("existing", [False, True])
def test_installer_resolves_checkout_and_project_without_docker(stack, mode, existing):
    if existing:
        stack.existing_compose()  # Deliberately not a Git checkout.
    command = bootstrap_command(stack.run("install-local.sh", *mode, "--dry-run"))
    assert command[1:3] == ["--project-name", stack.project]
    assert command[3:] == (
        ["--existing-defectdojo-dir", str(stack.dojo), "--image-source", "pull"]
        if existing else ["--defectdojo-dir", str(stack.dojo)]
    )
    assert stack.calls == []
    assert not (stack.library / ".dojo-permission-local").exists()


def test_signing_certificate_requires_existing_compose_and_preserves_it(stack):
    missing = stack.run("install-local.sh", "--with-signing-certificate", "--dry-run")
    assert missing.returncode != 0
    assert "requires the sibling DefectDojo checkout" in missing.stderr
    assert stack.calls == []
    compose = stack.existing_compose()
    original = compose.read_bytes()
    command = bootstrap_command(stack.run("install-local.sh", "--with-signing-certificate", "--dry-run"))
    assert command[-4:] == ["--existing-defectdojo-dir", str(stack.dojo), "--image-source", "pull"]
    assert compose.read_bytes() == original
    assert stack.calls == []


@pytest.mark.parametrize("args,env", [
    (("--project-name", "dojo-permission-dev"), {"DOJO_PERMISSION_PROJECT_NAME": "ignored-name"}),
    ((), {"DOJO_PERMISSION_PROJECT_NAME": "dojo-permission-dev"}),
])
def test_explicit_foreign_project_is_still_rejected(stack, args, env):
    result = stack.run("bootstrap-local-docker.sh", *args, **env)
    assert result.returncode != 0
    assert "already has unowned Docker resources" in result.stderr
    assert (stack.dojo / "docker-compose.yml").is_file()
    assert not any("build" in call for call in stack.calls)


def test_installer_honors_explicit_project_environment(stack):
    command = bootstrap_command(stack.run(
        "install-local.sh", "--dry-run", DOJO_PERMISSION_PROJECT_NAME="chosen-project",
    ))
    assert command[1:3] == ["--project-name", "chosen-project"]
    assert stack.calls == []


@pytest.mark.parametrize("legacy", ["matching", "mismatched", "hashed-already-exists"])
@pytest.mark.parametrize("script", ["bootstrap-local-docker.sh", "install-local.sh"])
def test_legacy_project_reused_only_for_matching_ownership(stack, legacy, script):
    state = stack.state(
        "dojo-permission-dev", library="/some/other/checkout" if legacy == "mismatched" else None,
    )
    original = state.read_bytes()
    if legacy == "hashed-already-exists":
        stack.state(stack.project)
    expected = "dojo-permission-dev" if legacy == "matching" else stack.project
    if script == "install-local.sh":
        command = bootstrap_command(stack.run(script, "--dry-run"))
        assert command[1:3] == ["--project-name", expected]
        assert stack.calls == []
    else:
        result = stack.run(script, FOREIGN_PROJECT="")
        assert result.returncode == 42, result.stdout + result.stderr
        build = next(call for call in stack.calls if call[:2] == ["docker", "compose"] and "build" in call)
        assert compose_project(build) == expected
    assert state.read_bytes() == original


@pytest.fixture
def desktop_bins(tmp_path):
    initial = tmp_path / "initial commands"
    desktop = tmp_path / "Docker Desktop.app" / "Contents" / "Resources" / "bin"
    initial.mkdir()
    desktop.mkdir(parents=True)
    for name, body in {
        "docker": 'exec docker-credential-desktop "$@"',
        "docker-credential-desktop": 'printf "desktop credential: %s\\n" "$1"',
    }.items():
        executable = desktop / name
        executable.write_text(f"#!/bin/sh\n{body}\n")
        executable.chmod(0o755)
    return initial, desktop


def run_docker_path_helper(initial, desktop, body):
    return subprocess.run(
        [
            "/bin/bash", "-c",
            'set -eu\nsource "$1"\n' + body,
            "path-regression", str(REPOSITORY / "deploy/local-stack-common.sh"), str(desktop),
        ],
        env={"PATH": str(initial)}, text=True, capture_output=True, timeout=10,
    )


def test_desktop_path_enables_docker_credentials_and_is_exported(desktop_bins):
    initial, desktop = desktop_bins
    result = run_docker_path_helper(initial, desktop, r'''
! command -v docker
! command -v docker-credential-desktop
export -n PATH
local_add_docker_bin_to_path "$2"
docker parent
/bin/sh -c 'docker child'
''')
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["desktop credential: parent", "desktop credential: child"]


def test_desktop_path_preserves_existing_cli_and_helper_preference(desktop_bins):
    initial, desktop = desktop_bins
    for name, output in {"docker": "preferred CLI", "docker-credential-desktop": "preferred helper"}.items():
        executable = initial / name
        executable.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n")
        executable.chmod(0o755)
    result = run_docker_path_helper(initial, desktop, r'''
local_add_docker_bin_to_path "$2"
docker
docker-credential-desktop
printf '%s\n' "$PATH"
''')
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["preferred CLI", "preferred helper", f"{initial}:{desktop}"]


def test_desktop_path_is_not_added_twice(desktop_bins):
    initial, desktop = desktop_bins
    result = run_docker_path_helper(initial, desktop, r'''
local_add_docker_bin_to_path "$2"
export -n PATH
local_add_docker_bin_to_path "$2"
printf '%s\n' "$PATH"
/bin/sh -c 'docker child'
''')
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [f"{initial}:{desktop}", "desktop credential: child"]


@pytest.mark.parametrize("installation", ["missing-directory", "nonexecutable-docker"])
def test_unavailable_desktop_installations_leave_path_unchanged(desktop_bins, installation):
    initial, desktop = desktop_bins
    if installation == "missing-directory":
        desktop = desktop.parent / "not installed"
    else:
        (desktop / "docker").chmod(0o644)
    result = run_docker_path_helper(initial, desktop, r'''
local_add_docker_bin_to_path "$2"
printf '%s\n' "$PATH"
! command -v docker
''')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(initial)
