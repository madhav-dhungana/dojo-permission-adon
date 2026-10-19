"""Exercise an existing deployment install without reaching a Docker daemon."""

import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys

import pytest


REPOSITORY = Path(__file__).resolve().parents[1]
BASE_IMAGE = "sha256:" + "a" * 64
DERIVED_IMAGE = "sha256:" + "b" * 64

COMMAND_STUB = r'''
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(json.dumps({"name": name, "args": args, "environment": {
        key: os.environ.get(key) for key in ("DD_CUSTOM_SETTING", "DD_PORT")
    }}) + "\n")
scenario = os.environ.get("STUB_SCENARIO", "")
base_image = "sha256:" + "a" * 64
derived_image = "sha256:" + "b" * 64
project = os.environ["STUB_PROJECT"]
deployment = Path(os.environ["STUB_DEPLOYMENT"])
settings = Path(os.environ["STUB_SETTINGS"])
compose_files = json.loads(os.environ["STUB_COMPOSE_FILES"])
services = ("uwsgi", "initializer", "celeryworker", "celerybeat", "nginx", "postgres")
managed_scenarios = {"already-installed", "managed-upgrade", "managed-upgrade-0.2", "managed-revision-update", "managed-unsupported"}

def inspected_container(service):
    image = base_image if service in services[:4] else "sha256:" + "c" * 64
    if scenario == "already-installed" and service in services[:4]:
        image = derived_image
    if scenario == "mixed-images" and service == "celeryworker":
        image = "sha256:" + "d" * 64
    labels = {
        "com.docker.compose.project": project,
        "com.docker.compose.service": service,
        "com.docker.compose.project.working_dir": str(deployment),
        "com.docker.compose.project.config_files": ",".join(compose_files),
    }
    if scenario == "wrong-project":
        labels["com.docker.compose.project"] = "unrelated-project"
    mount_source = str(settings.parent)
    if scenario == "wrong-running-mount":
        mount_source = str(deployment / "unrelated-settings")
    return {
        "Id": service + "-container", "Image": image,
        "State": {"Running": True},
        "Config": {"Labels": labels, "User": "1001", "Env": ["DD_CUSTOM_SETTING=keep-this"]},
        "Mounts": [{"Type": "bind", "Source": mount_source,
                    "Destination": "/app/docker/extra_settings", "RW": True}],
    }

if name == "git":
    if args[-2:] == ["status", "--porcelain"]:
        print(" M src/example.py" if scenario == "dirty-source" else "")
    elif "rev-parse" in args:
        print("e" * 40)
    else:
        sys.exit("Unexpected Git command: " + repr(args))
elif name == "curl":
    if scenario == "unhealthy":
        sys.exit(22)
elif name == "docker":
    if args[:2] == ["compose", "version"]:
        print("2.24.4")
    elif args == ["info"]:
        pass
    elif args[:2] == ["image", "tag"]:
        pass
    elif args[:2] == ["image", "inspect"]:
        if "--format" in args:
            template = args[args.index("--format") + 1]
            if ".Id" in template:
                print(derived_image if derived_image in args else base_image)
            elif ".Config.User" in template:
                print("1001")
            elif ".Architecture" in template:
                print("linux/arm64")
            elif "org.opencontainers.image.revision" in template:
                print("f" * 40 if scenario == "managed-revision-update" else "e" * 40)
            else:
                sys.exit("Unexpected image inspect format: " + template)
        else:
            print(json.dumps([{"Id": base_image, "Os": "linux", "Architecture": "arm64", "Config": {"User": "1001"}}]))
    elif args[:1] == ["inspect"]:
        container = inspected_container(args[1].removesuffix("-container"))
        if "--format" in args:
            template = args[args.index("--format") + 1]
            if template == "{{.Image}}":
                print(container["Image"])
            else:
                sys.exit("Unexpected container inspect format: " + template)
        else:
            print(json.dumps([container]))
    elif args[:1] == ["compose"] and "config" in args:
        if "--services" in args:
            print("\n".join(services))
        elif "--format" in args:
            mount_source = str(settings.parent)
            if scenario == "wrong-compose-mount":
                mount_source = str(deployment / "other-settings")
            spec = {"image": "customized/dojo:2.58.3", "environment": {"DD_CUSTOM_SETTING": "keep-this"},
                    "volumes": [{"type": "bind", "source": mount_source,
                                 "target": "/app/docker/extra_settings"}]}
            if scenario in managed_scenarios and any("compose.dojo-permission.release.yml" in arg for arg in args):
                spec["image"] = derived_image if scenario == "already-installed" else base_image
                spec["environment"]["DD_DOJO_PERMISSION_ENABLED"] = "true"
            print(json.dumps({"name": project, "services": {name: spec.copy() for name in services}}))
    elif args[:1] == ["compose"] and "ps" in args:
        service = args[-1]
        if scenario == "replicas" and service == "uwsgi":
            print("uwsgi-container\nuwsgi-second-container")
        elif not (scenario == "stopped-web" and service == "uwsgi"):
            print(service + "-container")
    elif args[:1] == ["compose"] and "exec" in args:
        joined = " ".join(args)
        if "pg_dump" in joined:
            if scenario == "backup-fails":
                print("Controlled pg_dump failure", file=sys.stderr)
                sys.exit(24)
            sys.stdout.buffer.write(b"PGDMP mock verified backup\n")
        elif "pg_restore" in joined:
            if scenario == "backup-invalid":
                sys.exit(25)
            print("; mock archive contents")
        elif "shell" in args:
            installed = scenario in managed_scenarios or scenario == "unmanaged-installed"
            installed_version = "0.1.0" if scenario == "managed-upgrade" else "0.3.0"
            if scenario == "managed-upgrade-0.2": installed_version = "0.2.0"
            if scenario == "managed-unsupported": installed_version = "0.4.0"
            payload = {"app_enabled": installed, "package_version": installed_version if installed else None,
                       "defectdojo_version": "2.57.0" if scenario == "wrong-version" else "2.58.3",
                       "django_version": "5.2.14", "python_version": [3, 13]}
            print(json.dumps(payload))
        elif "migrate" in args and scenario == "pending-migrations":
            sys.exit(1)
        elif "showmigrations" in args:
            print("dojo_permission\n [X] 0001_initial")
        elif "check" in args or "migrate" in args:
            pass
        else:
            sys.exit("Unexpected Compose exec command: " + repr(args))
    else:
        sys.exit("Unexpected Docker command: " + repr(args))
else:
    sys.exit("Unexpected command: " + name)
'''

HELPER_STUB = r'''
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[1]).name
args = sys.argv[2:]
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(json.dumps({"name": name, "args": args, "environment": {
        key: os.environ.get(key) for key in ("DD_CUSTOM_SETTING", "DD_PORT")
    }}) + "\n")
if name == "build-derived-image.sh":
    output = Path(args[args.index("--output") + 1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("# Generated by dojo-permission build-derived-image.sh\n"
                      "DOJO_PERMISSION_IMAGE=sha256:" + "b" * 64 + "\n"
                      "DOJO_PERMISSION_BASE_IMAGE=sha256:" + "a" * 64 + "\n"
                      "DOJO_PERMISSION_SOURCE_REVISION=" + "e" * 40 + "\n"
                      "DOJO_PERMISSION_PLATFORM=linux/arm64\n")
elif name == "deploy-existing-compose.sh":
    if os.environ.get("STUB_SCENARIO") == "deploy-fails":
        sys.exit(31)
else:
    sys.exit("Unknown helper: " + name)
'''


class ExistingDeployment:
    def __init__(self, root):
        self.library = root / "library with spaces"
        self.deployment = root / "custom deployed dojo"
        self.deployment.mkdir()
        self.project = "custom-running-dojo"
        self.settings = self.deployment / "docker/extra_settings/local_settings.py"
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("CUSTOM_SETTING = 'keep this exactly'\n")
        self.compose_files = [self.deployment / "docker-compose.yml", self.deployment / "docker-compose.signing.yml"]
        for index, compose in enumerate(self.compose_files):
            compose.write_text(f"# customized deployment file {index}\nservices: {{}}\n")
        self.env_file = self.deployment / "custom environment.env"
        self.env_file.write_text("CUSTOM_VALUE=preserve\n")
        self.backup = self.deployment / "existing database.dump"
        self.backup.write_text("existing tested backup\n")
        self.config = self.library / "deploy/install-existing.conf"
        self.log = root / "existing-install.jsonl"
        self.cwd = root / "other working directory"
        self.cwd.mkdir()
        binary_dir = root / "fake commands"
        binary_dir.mkdir()
        for name in ("git", "docker", "curl"):
            binary = binary_dir / name
            binary.write_text(f"#!{sys.executable}\n" + COMMAND_STUB)
            binary.chmod(0o755)
        for relative in ("deploy/install-existing.sh", "deploy/local-stack-common.sh"):
            target = self.library / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPOSITORY / relative, target)
        for name in ("build-derived-image.sh", "deploy-existing-compose.sh"):
            helper = self.library / "deploy" / name
            helper.write_text("#!/bin/bash\nexec " + shlex.quote(sys.executable)
                              + " - \"$0\" \"$@\" <<'PYTHON'\n" + HELPER_STUB + "\nPYTHON\n")
            helper.chmod(0o755)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("DOJO_PERMISSION_", "DD_"))}
        self.env.update(PATH=f"{binary_dir}{os.pathsep}{self.env['PATH']}",
                        STUB_LOG=str(self.log), STUB_PROJECT=self.project,
                        STUB_DEPLOYMENT=str(self.deployment), STUB_SETTINGS=str(self.settings),
                        STUB_COMPOSE_FILES=json.dumps([str(path) for path in self.compose_files]))
        self.write_config()

    def write_config(self, *, automatic_backup=False, compose_env=True, **values):
        config = {
            "PROJECT_DIRECTORY": str(self.deployment), "PROJECT_NAME": self.project,
            "SETTINGS_FILE": str(self.settings), "HEALTH_URL": "http://localhost/login",
            "ENV_FILE": str(self.env_file),
            "BACKUP_EVIDENCE": "" if automatic_backup else str(self.backup),
        }
        config.update(values)
        self.config.write_text("\n".join(f"{key}={shlex.quote(value)}" for key, value in config.items())
                               + "\nCOMPOSE_FILES=(" + " ".join(shlex.quote(str(path)) for path in self.compose_files) + ")\n"
                               + ("COMPOSE_ENV=('DD_CUSTOM_SETTING=from config with spaces' 'DD_PORT=8080')\n" if compose_env else "COMPOSE_ENV=()\n"))

    @property
    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def run(self, *args, scenario=""):
        return subprocess.run(["/bin/bash", str(self.library / "deploy/install-existing.sh"), *args],
                              cwd=self.cwd, env={**self.env, "STUB_SCENARIO": scenario},
                              text=True, capture_output=True, timeout=20)

    def helper_calls(self, helper):
        return [call for call in self.calls if call["name"] == helper]


@pytest.fixture
def existing(tmp_path):
    return ExistingDeployment(tmp_path)


def test_existing_plan_checks_running_customization_without_writing(existing):
    before = {path: path.read_bytes() for path in [*existing.compose_files, existing.settings, existing.env_file]}
    result = existing.run("--plan")
    assert result.returncode == 0, result.stdout + result.stderr
    assert existing.calls
    assert not existing.helper_calls("build-derived-image.sh")
    assert not existing.helper_calls("deploy-existing-compose.sh")
    assert not (existing.deployment / ".dojo-permission-existing").exists()
    assert before == {path: path.read_bytes() for path in before}
    assert not any("pg_dump" in " ".join(call["args"]) for call in existing.calls)


def test_install_uses_exact_running_image_and_preserves_deployment_inputs(existing):
    paths = [*existing.compose_files, existing.settings, existing.env_file]
    before = {path: path.read_bytes() for path in paths}
    result = existing.run()
    assert result.returncode == 0, result.stdout + result.stderr
    builder = existing.helper_calls("build-derived-image.sh")
    deployer = existing.helper_calls("deploy-existing-compose.sh")
    assert len(builder) == len(deployer) == 1
    assert builder[0]["args"][builder[0]["args"].index("--base-image") + 1] == BASE_IMAGE
    arguments = deployer[0]["args"]
    assert arguments[arguments.index("--image") + 1] == DERIVED_IMAGE
    assert arguments[arguments.index("--rollback-image") + 1] == BASE_IMAGE
    assert "--library-only" in arguments and "--apply" in arguments
    assert [arguments[index + 1] for index, item in enumerate(arguments) if item == "--compose-file"] == [str(path) for path in existing.compose_files]
    assert arguments[arguments.index("--env-file") + 1] == str(existing.env_file)
    assert arguments[arguments.index("--settings-file") + 1] == str(existing.settings)
    assert arguments[arguments.index("--backup-evidence") + 1] == str(existing.backup)
    assert deployer[0]["environment"]["DD_CUSTOM_SETTING"] == "from config with spaces"
    assert deployer[0]["environment"]["DD_PORT"] == "8080"
    assert before == {path: path.read_bytes() for path in paths}
    assert not any(token in {"down", "prune"} for call in existing.calls for token in call["args"])


@pytest.mark.parametrize("scenario", ["wrong-project", "mixed-images", "replicas", "stopped-web", "wrong-version", "pending-migrations", "wrong-compose-mount", "wrong-running-mount", "unhealthy"])
def test_invalid_existing_deployment_rejected_before_build_or_cutover(existing, scenario):
    original = existing.settings.read_bytes()
    result = existing.run(scenario=scenario)
    assert result.returncode != 0, result.stdout + result.stderr
    assert not existing.helper_calls("build-derived-image.sh")
    assert not existing.helper_calls("deploy-existing-compose.sh")
    assert existing.settings.read_bytes() == original


@pytest.mark.parametrize("scenario", ["backup-fails", "backup-invalid"])
def test_automatic_backup_failure_prevents_cutover(existing, scenario):
    existing.write_config(automatic_backup=True)
    original = existing.settings.read_bytes()
    result = existing.run(scenario=scenario)
    assert result.returncode != 0, result.stdout + result.stderr
    assert not existing.helper_calls("deploy-existing-compose.sh")
    assert existing.settings.read_bytes() == original
    assert any("pg_dump" in " ".join(call["args"]) for call in existing.calls)


def test_automatic_backup_is_verified_and_private(existing):
    existing.write_config(automatic_backup=True)
    result = existing.run()
    assert result.returncode == 0, result.stdout + result.stderr
    backup_calls = [call for call in existing.calls if any(command in " ".join(call["args"]) for command in ("pg_dump", "pg_restore"))]
    assert len(backup_calls) == 2
    deployer = existing.helper_calls("deploy-existing-compose.sh")[0]["args"]
    backup = Path(deployer[deployer.index("--backup-evidence") + 1])
    assert backup.read_bytes().startswith(b"PGDMP")
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_explicit_config_is_resolved_from_another_working_directory(existing):
    moved = existing.config.with_name("custom install.conf")
    existing.config.rename(moved)
    result = existing.run("--config", str(moved), "--plan")
    assert result.returncode == 0, result.stdout + result.stderr


def test_help_does_not_require_a_config_or_docker(existing):
    existing.config.unlink()
    result = existing.run("--help")
    assert result.returncode == 0, result.stderr
    assert "--config" in result.stdout and "--plan" in result.stdout
    assert not existing.calls


def test_missing_config_explains_the_edit_then_run_workflow(existing):
    existing.config.unlink()
    result = existing.run()
    assert result.returncode != 0
    assert "install-existing.conf" in result.stdout + result.stderr
    assert not existing.calls


def test_already_installed_library_is_not_built_or_reapplied(existing):
    (existing.deployment / "compose.dojo-permission.release.yml").write_text(
        "# Generated by dojo-permission deploy-existing-compose.sh\n"
        "services:\n  uwsgi:\n    image: " + DERIVED_IMAGE + "\n")
    result = existing.run(scenario="already-installed")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not existing.helper_calls("build-derived-image.sh")
    assert not existing.helper_calls("deploy-existing-compose.sh")


@pytest.mark.parametrize("scenario", ["managed-upgrade", "managed-upgrade-0.2", "managed-revision-update"])
def test_managed_update_builds_on_the_exact_current_image_without_new_flags(existing, scenario):
    (existing.deployment / "compose.dojo-permission.release.yml").write_text(
        "# Generated by dojo-permission deploy-existing-compose.sh\n"
        "services:\n  uwsgi:\n    image: " + BASE_IMAGE + "\n")
    result = existing.run(scenario=scenario)
    assert result.returncode == 0, result.stdout + result.stderr
    builder = existing.helper_calls("build-derived-image.sh")[0]["args"]
    deployer = existing.helper_calls("deploy-existing-compose.sh")[0]["args"]
    assert builder[builder.index("--base-image") + 1] == BASE_IMAGE
    assert builder[builder.index("--target-image") + 1].startswith("local/dojo-permission-existing:0.3.0-")
    assert "--managed-upgrade" in deployer
    assert "--library-only" in deployer
    assert deployer[deployer.index("--rollback-image") + 1] == BASE_IMAGE


@pytest.mark.parametrize("scenario,previous_version", [("managed-upgrade", "0.1.0"), ("managed-upgrade-0.2", "0.2.0")])
def test_managed_upgrade_plan_does_not_build_backup_or_cut_over(existing, scenario, previous_version):
    (existing.deployment / "compose.dojo-permission.release.yml").write_text(
        "# Generated by dojo-permission deploy-existing-compose.sh\nservices: {}\n")
    result = existing.run("--plan", scenario=scenario)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{previous_version} -> 0.3.0" in result.stdout
    assert not existing.helper_calls("build-derived-image.sh")
    assert not existing.helper_calls("deploy-existing-compose.sh")
    assert not (existing.deployment / ".dojo-permission-existing").exists()


def test_unsupported_managed_version_is_rejected_before_mutation(existing):
    (existing.deployment / "compose.dojo-permission.release.yml").write_text(
        "# Generated by dojo-permission deploy-existing-compose.sh\nservices: {}\n")
    result = existing.run(scenario="managed-unsupported")
    assert result.returncode != 0
    assert "separate upgrade procedure" in result.stderr
    assert not existing.helper_calls("build-derived-image.sh")
    assert not existing.helper_calls("deploy-existing-compose.sh")


def test_unmanaged_enabled_library_requires_review_before_install(existing):
    result = existing.run(scenario="unmanaged-installed")
    assert result.returncode != 0
    assert "already enabled outside this installer" in result.stderr
    assert not existing.helper_calls("build-derived-image.sh")
    assert not existing.helper_calls("deploy-existing-compose.sh")


def test_empty_optional_environment_array_works_with_macos_bash(existing):
    existing.write_config(compose_env=False)
    result = existing.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(existing.helper_calls("deploy-existing-compose.sh")) == 1


def test_default_dotenv_is_preserved_and_not_replaced_with_empty_envfile(existing):
    dotenv = existing.deployment / ".env"
    dotenv.write_text("CUSTOM_DOTENV_SETTING=preserved\n")
    existing.write_config(ENV_FILE="")
    result = existing.run()
    assert result.returncode == 0, result.stdout + result.stderr
    compose_calls = [call["args"] for call in existing.calls if call["name"] == "docker" and call["args"][0] == "compose"]
    assert all("--env-file" not in args for args in compose_calls)
    assert "--env-file" not in existing.helper_calls("deploy-existing-compose.sh")[0]["args"]
    snapshots = list((existing.deployment / ".dojo-permission-existing").glob("run.*/compose.env"))
    assert len(snapshots) == 1
    assert snapshots[0].read_bytes() == dotenv.read_bytes()


def test_cutover_failure_is_not_reported_as_success(existing):
    result = existing.run(scenario="deploy-fails")
    assert result.returncode != 0
    assert len(existing.helper_calls("build-derived-image.sh")) == 1
    assert len(existing.helper_calls("deploy-existing-compose.sh")) == 1
    receipts = list((existing.deployment / ".dojo-permission-existing").glob("run.*/install.receipt"))
    assert len(receipts) == 1
    assert "status=installed" not in receipts[0].read_text()
    assert "status=failed" in receipts[0].read_text()
    assert not (existing.deployment / ".dojo-permission-existing/compose.sh").exists()
    assert str(receipts[0]) in result.stderr


def test_failed_install_preserves_an_existing_compose_launcher(existing):
    launcher = existing.deployment / ".dojo-permission-existing/compose.sh"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/bash\n# existing rollback launcher\n")
    launcher.chmod(0o700)
    original = launcher.read_bytes()
    result = existing.run(scenario="deploy-fails")
    assert result.returncode != 0
    assert launcher.read_bytes() == original
    assert stat.S_IMODE(launcher.stat().st_mode) == 0o700


def test_original_image_gets_a_protected_rollback_tag_before_build(existing):
    result = existing.run()
    assert result.returncode == 0, result.stdout + result.stderr
    tag_calls = [call for call in existing.calls if call["name"] == "docker" and call["args"][:2] == ["image", "tag"]]
    assert len(tag_calls) == 1
    assert tag_calls[0]["args"][2] == BASE_IMAGE
    rollback_tag = tag_calls[0]["args"][3]
    assert rollback_tag.startswith("local/dojo-permission-rollback:")
    assert existing.calls.index(tag_calls[0]) < existing.calls.index(existing.helper_calls("build-derived-image.sh")[0])
    receipt = next((existing.deployment / ".dojo-permission-existing").glob("run.*/install.receipt"))
    assert f"ROLLBACK_TAG={rollback_tag}\n" in receipt.read_text()


@pytest.mark.parametrize("setting", ["STATE_DIR", "RELEASE_OVERRIDE"])
def test_symlink_output_targets_are_rejected_before_canonicalization(existing, setting):
    target = existing.deployment / "unrelated target"
    if setting == "STATE_DIR":
        target.mkdir()
    else:
        target.write_text("# unrelated existing configuration\n")
    link = existing.deployment / "output symlink"
    link.symlink_to(target, target_is_directory=setting == "STATE_DIR")
    existing.write_config(**{setting: str(link)})
    result = existing.run()
    assert result.returncode != 0
    assert "symbolic link" in result.stderr
    assert not existing.calls
    assert link.is_symlink()
    assert not target.is_dir() or not list(target.iterdir())


def test_generated_launcher_keeps_deployment_overrides_and_environment(existing):
    installed = existing.run()
    assert installed.returncode == 0, installed.stdout + installed.stderr
    launcher = existing.deployment / ".dojo-permission-existing/compose.sh"
    result = subprocess.run(["/bin/bash", str(launcher), "ps"], cwd=existing.cwd,
                            env=existing.env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    call = existing.calls[-1]
    arguments = call["args"]
    assert arguments[arguments.index("--project-name") + 1] == existing.project
    assert arguments[arguments.index("--project-directory") + 1] == str(existing.deployment)
    assert arguments[arguments.index("--env-file") + 1] == str(existing.env_file)
    assert [arguments[index + 1] for index, item in enumerate(arguments) if item == "--file"] == [
        *[str(path) for path in existing.compose_files], str(existing.deployment / "compose.dojo-permission.release.yml")]
    assert call["environment"]["DD_CUSTOM_SETTING"] == "from config with spaces"
    assert stat.S_IMODE(launcher.stat().st_mode) == 0o700


def test_dirty_library_source_is_rejected_before_backup_or_build(existing):
    existing.write_config(automatic_backup=True)
    result = existing.run(scenario="dirty-source")
    assert result.returncode != 0
    assert "clean and committed" in result.stderr
    assert not existing.helper_calls("build-derived-image.sh")
    assert not existing.helper_calls("deploy-existing-compose.sh")
    assert not any("pg_dump" in " ".join(call["args"]) for call in existing.calls)


def test_empty_backup_evidence_is_rejected_before_build(existing):
    existing.backup.write_bytes(b"")
    result = existing.run()
    assert result.returncode != 0
    assert "BACKUP_EVIDENCE" in result.stderr
    assert not existing.helper_calls("build-derived-image.sh")
    assert not existing.helper_calls("deploy-existing-compose.sh")


LEGACY_COMMAND_STUB = r'''
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(json.dumps({"name": name, "args": args}) + "\n")
base_image = "sha256:" + "a" * 64
derived_image = "sha256:" + "b" * 64
scenario = os.environ.get("STUB_SCENARIO", "")
state = Path(os.environ["STUB_DEPLOYMENT"]) / "stub-image-state"
image = state.read_text() if state.exists() else base_image
managed_upgrade = scenario.startswith("legacy-managed-")

if name == "curl":
    pass
elif name != "docker":
    sys.exit("Unexpected command: " + name)
elif args[:2] == ["image", "inspect"]:
    if "--format" in args:
        template = args[args.index("--format") + 1]
        if ".Config.Labels" in template:
            print(base_image)
        elif ".Id" in template:
            print(derived_image if args[2] == derived_image else base_image)
        else:
            sys.exit("Unexpected image format: " + template)
elif args[:1] == ["inspect"]:
    template = args[args.index("--format") + 1]
    if template == "{{.Image}}":
        print(image)
    elif ".Config.Env" in template:
        print("DD_DOJO_PERMISSION_ENABLED=" + ("true" if managed_upgrade else "false"))
    else:
        sys.exit("Unexpected inspect format: " + template)
elif args[:1] == ["run"]:
    pass
elif args[:1] == ["compose"] and "config" in args:
    if "--services" in args:
        print("uwsgi\ninitializer\nceleryworker\ncelerybeat\nnginx\npostgres")
    elif "--format" in args:
        print(json.dumps({"services": {name: {"image": base_image, "environment": {"DD_DOJO_PERMISSION_ENABLED": "true"}}
              for name in ("uwsgi", "initializer", "celeryworker", "celerybeat")}}))
elif args[:1] == ["compose"] and "ps" in args:
    print(args[-1] + "-container")
elif args[:1] == ["compose"] and "run" in args:
    if "migrate" in args and scenario.endswith("migrate-fails"):
        sys.exit(32)
    if "migrate" in args and "--check" in args and scenario == "legacy-latest-migrations-pending":
        sys.exit(34)
    if "showmigrations" in args:
        print("dojo_permission\n [X] 0001_initial")
elif args[:1] == ["compose"] and "exec" in args:
    if "showmigrations" in args:
        print("dojo_permission\n [X] 0001_initial")
elif args[:1] == ["compose"] and "up" in args:
    rollback = any("rollback" in value for value in args)
    if not rollback and scenario.endswith("worker-fails") and args[-1] == "celeryworker":
        sys.exit(33)
    state.write_text(base_image if rollback else derived_image)
elif args[:1] == ["compose"] and any(command in args for command in ("stop", "restart")):
    pass
else:
    sys.exit("Unexpected Docker command: " + repr(args))
'''


class LegacyDeployment:
    def __init__(self, root):
        self.directory = root / "existing dojo"
        self.directory.mkdir()
        self.settings = self.directory / "local_settings.py"
        self.settings.write_text("CUSTOM_SETTING = 'preserved'\n")
        self.compose = self.directory / "docker-compose.yml"
        self.compose.write_text("services: {}\n")
        self.backup = self.directory / "backup.dump"
        self.backup.write_text("non-empty backup evidence\n")
        self.override = self.directory / "compose.dojo-permission.release.yml"
        self.log = self.directory / "commands.jsonl"
        binary_dir = root / "legacy commands"
        binary_dir.mkdir()
        for name in ("docker", "curl"):
            binary = binary_dir / name
            binary.write_text(f"#!{sys.executable}\n" + LEGACY_COMMAND_STUB)
            binary.chmod(0o755)
        self.env = {**os.environ, "PATH": f"{binary_dir}{os.pathsep}{os.environ['PATH']}",
                    "STUB_LOG": str(self.log), "STUB_DEPLOYMENT": str(self.directory)}

    @property
    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def run(self, *, library_only=True, managed_upgrade=False, scenario=""):
        arguments = ["/bin/bash", str(REPOSITORY / "deploy/deploy-existing-compose.sh"),
                     "--compose-file", str(self.compose), "--project-name", "existing-dojo",
                     "--image", DERIVED_IMAGE, "--rollback-image", BASE_IMAGE,
                     "--settings-file", str(self.settings), "--release-override", str(self.override),
                     "--backup-evidence", str(self.backup), "--health-url", "http://localhost/login", "--apply"]
        if library_only:
            arguments.append("--library-only")
        if managed_upgrade:
            arguments.append("--managed-upgrade")
        return subprocess.run(arguments, env={**self.env, "STUB_SCENARIO": scenario},
                              text=True, capture_output=True, timeout=20)


@pytest.fixture
def legacy(tmp_path):
    return LegacyDeployment(tmp_path)


@pytest.mark.parametrize("library_only", [False, True])
def test_candidate_django_loads_existing_settings_and_only_requested_migrations(legacy, library_only):
    result = legacy.run(library_only=library_only)
    assert result.returncode == 0, result.stdout + result.stderr
    runs = [call["args"] for call in legacy.calls if call["name"] == "docker" and "run" in call["args"] and call["args"][0] == "compose"]
    initializers = [call for call in runs if call[-1] == "initializer"]
    migration_calls = [call for call in runs if "migrate" in call and "--noinput" in call]
    if library_only:
        assert not initializers
        assert len(migration_calls) == 1
        assert migration_calls[0][-3:] == ["migrate", "dojo_permission", "--noinput"]
    else:
        assert len(initializers) == 1
        assert not migration_calls
    django_calls = [call for call in runs if "--entrypoint" in call]
    assert django_calls
    for call in django_calls:
        assert call[call.index("--entrypoint") + 1] == "/bin/sh"
        shell = call[call.index("-c") + 1]
        assert "/app/docker/extra_settings/*" in shell
        assert "cp" in shell and "/app/dojo/settings/" in shell
        assert 'exec python manage.py "$@"' in shell
    assert legacy.settings.read_text().startswith("CUSTOM_SETTING = 'preserved'\n")
    assert legacy.settings.read_text().count("# BEGIN DOJO_PERMISSION_MANAGED_HOOK") == 1
    assert DERIVED_IMAGE in legacy.override.read_text()


def test_targeted_migration_failure_restores_settings_before_stopping_services(legacy):
    original = legacy.settings.read_bytes()
    result = legacy.run(scenario="legacy-migrate-fails")
    assert result.returncode != 0
    assert legacy.settings.read_bytes() == original
    assert not legacy.override.exists()
    assert not any(command in call["args"] for call in legacy.calls for command in ("stop", "up", "restart"))


def test_latest_candidate_migrations_must_all_be_applied_before_cutover(legacy):
    original = legacy.settings.read_bytes()
    result = legacy.run(scenario="legacy-latest-migrations-pending")
    assert result.returncode != 0
    assert "migrations are incomplete before cutover" in result.stderr
    assert legacy.settings.read_bytes() == original
    assert not any(command in call["args"] for call in legacy.calls for command in ("stop", "up", "restart"))


def test_failed_worker_cutover_restores_original_image_and_settings(legacy):
    original = legacy.settings.read_bytes()
    result = legacy.run(scenario="legacy-worker-fails")
    assert result.returncode != 0
    assert legacy.settings.read_bytes() == original
    assert (legacy.directory / "stub-image-state").read_text() == BASE_IMAGE
    assert BASE_IMAGE in legacy.override.read_text()
    assert 'DD_DOJO_PERMISSION_ENABLED: "false"' in legacy.override.read_text()
    assert "Rollback health and image checks passed" in result.stderr
    assert not any("zero" in call["args"] for call in legacy.calls)


MANAGED_SETTINGS_HOOK = '''
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
'''


@pytest.mark.parametrize("scenario", ["legacy-managed-success", "legacy-managed-worker-fails", "legacy-managed-migrate-fails"])
def test_managed_upgrade_preserves_the_enabled_previous_release_on_failure(legacy, scenario):
    legacy.settings.write_text(legacy.settings.read_text() + MANAGED_SETTINGS_HOOK)
    legacy.override.write_text(
        "# Generated by dojo-permission deploy-existing-compose.sh\n"
        "services:\n  uwsgi:\n    image: " + BASE_IMAGE + '\n    environment:\n      DD_DOJO_PERMISSION_ENABLED: "true"\n')
    original_settings = legacy.settings.read_bytes()
    result = legacy.run(managed_upgrade=True, scenario=scenario)
    assert (result.returncode == 0) == scenario.endswith("success"), result.stdout + result.stderr
    assert legacy.settings.read_bytes() == original_settings
    assert 'DD_DOJO_PERMISSION_ENABLED: "true"' in legacy.override.read_text()
    assert not any("zero" in call["args"] for call in legacy.calls)
    if scenario.endswith("worker-fails"):
        assert (legacy.directory / "stub-image-state").read_text() == BASE_IMAGE
        assert BASE_IMAGE in legacy.override.read_text()
        assert "Rollback health and image checks passed" in result.stderr
    elif scenario.endswith("migrate-fails"):
        assert not any(command in call["args"] for call in legacy.calls for command in ("stop", "up", "restart"))


def test_upgrade_flag_requires_library_only_and_an_existing_managed_release(legacy):
    result = legacy.run(library_only=False, managed_upgrade=True, scenario="legacy-managed-success")
    assert result.returncode != 0
    assert "requires --library-only" in result.stderr
    result = legacy.run(managed_upgrade=True, scenario="legacy-managed-success")
    assert result.returncode != 0
    assert "existing enabled generated release override" in result.stderr
