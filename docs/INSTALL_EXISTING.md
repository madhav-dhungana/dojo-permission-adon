# Install into an already running instance

Use this workflow to add the library to a running DefectDojo **2.58.3** Docker
Compose deployment on one host. It supports a customized deployment by
extending the exact Django image currently running on that host.

Copy and edit one configuration file, then run one command. The installer
creates the library image and a persistent Compose override, applies the
library migration, and restarts the application services. Plan for a brief
web, worker, and beat outage.

## 1. Check the existing deployment

Run this on the host where Docker is running DefectDojo. You need Git, Bash,
Python 3, curl, Docker, and Docker Compose 2.24.4 or newer, plus permission to
access Docker and write the deployment's settings and configuration files.

The deployment must have one running web, worker, and beat service, an
initializer service, and a host-side `local_settings.py` shared by all Python
services. The running Django image must use Python 3.13 and Django 5.2.14.
The installer checks compatibility before cutover.

Keep your normal Compose files, environment file, volumes, and settings. If
the deployment uses signing certificates, retain those mounts and variables
in its existing Compose configuration. Docker must already trust its image
registries, and host curl must trust the URL used for the health check. Set
`CURL_CA_BUNDLE` in the installer configuration when your host requires a
separate CA bundle.

This is a first installation workflow. A deployment already enabled by
another installation method must be returned to its known working baseline
before using it. Do not use this installer as a library upgrade mechanism.
Multi-host or replicated deployments need their own cutover plan.

## 2. Clone the library

Choose a normal directory outside Trash. If the library checkout already
exists, update that checkout with `git pull --ff-only` instead of cloning over
it.

```bash
git clone https://github.com/madhav-dhungana/dojo-permission-adon.git
cd dojo-permission-adon
cp deploy/install-existing.conf.example deploy/install-existing.conf
```

The configuration copy is ignored by Git. Edit it locally and keep its file
permissions private; it is a Bash configuration file and must be trusted.
Keep the tracked library files clean and committed; installation rejects
uncommitted source changes.

## 3. Edit the configuration

Open `deploy/install-existing.conf` and set these values for the existing
deployment. Use absolute paths and the same Compose file order as its normal
startup command:

```bash
PROJECT_DIRECTORY="/absolute/path/to/defectdojo"
PROJECT_NAME="your-existing-compose-project"
COMPOSE_FILES=(
  "/absolute/path/to/defectdojo/docker-compose.yml"
  # "/absolute/path/to/defectdojo/docker-compose.production.yml"
)

# Empty uses normal Compose environment-file discovery.
ENV_FILE="/absolute/path/to/defectdojo/.env"

SETTINGS_FILE="/absolute/path/to/defectdojo/docker/extra_settings/local_settings.py"
HEALTH_URL="https://dojo.example.org/login"
```

`PROJECT_NAME` must identify the already running stack, not a new project. Use
`docker compose ls` if you need to check its name. Include every existing
Compose file in `COMPOSE_FILES`; omitting an override can omit settings needed
by the recreated application containers. Exclude this repository's local
bootstrap overlays and the generated library release override.

`SETTINGS_FILE` must be the existing host-side settings file used by all four
Python services. The installer appends a small environment-gated library hook
while preserving the existing contents. It does not replace your settings
with the sample local settings supplied by this repository.

The remaining template values use DefectDojo's standard service names:

| Setting | Default / purpose |
| --- | --- |
| `WEB_SERVICE` | `uwsgi` |
| `INITIALIZER_SERVICE` | `initializer` |
| `WORKER_SERVICE` | `celeryworker` |
| `BEAT_SERVICE` | `celerybeat` |
| `PROXY_SERVICE` | `nginx` |
| `POSTGRES_SERVICE` | `postgres`; used for an automatic database dump |
| `RESTART_PROXY` | `true`; refreshes the proxy after web-container replacement |
| `BACKUP_EVIDENCE` | Empty creates a local PostgreSQL dump; otherwise points to an existing readable, nonempty database backup |
| `RELEASE_OVERRIDE` | Empty selects `compose.dojo-permission.release.yml` in the project directory |
| `STATE_DIR` | Empty selects `.dojo-permission-existing` in the project directory; holds private installation state and the Compose launcher |
| `COMPOSE_ENV` | Additional `KEY=value` entries needed for the original Compose interpolation |

Change service names only when the existing deployment uses different names.
Disable the proxy restart only when its service discovery follows recreated
web containers without needing a restart.

If normal operation requires exported interpolation variables, record them
in the configuration rather than repeating them on the command line:

```bash
COMPOSE_ENV=(
  "DD_PORT=8080"
  "SIGNING_CERTIFICATE=/absolute/path/to/ca-bundle.pem"
)
```

Use only the variables your existing Compose files already require. These
values and the generated Compose launcher are private deployment state; do
not commit or share them.

For a health URL signed by a certificate authority that curl does not already
trust, add this to the same configuration file:

```bash
export CURL_CA_BUNDLE="/absolute/path/to/ca-bundle.pem"
```

For PostgreSQL in the same Compose stack, leave `BACKUP_EVIDENCE` empty and
set `POSTGRES_SERVICE` if needed. The installer writes a custom-format
PostgreSQL dump and checks its archive listing before cutover. For an external
database, create and verify a backup through your
normal database procedure, then set its readable local path:

```bash
BACKUP_EVIDENCE="/secure/backups/dojo-before-permission.dump"
```

The backup path may also be a nonempty directory. The existing-backup check
verifies that the file or directory is readable and nonempty;
it cannot prove that the database backup is current or restorable. Retain your
normal backup of uploads, secrets, and deployment configuration as well.

## 4. Install

From the library checkout, run:

```bash
bash deploy/install-existing.sh
```

The command uses `deploy/install-existing.conf` and applies the installation
without interactive prompts or deployment arguments. To inspect the resolved
deployment first, an optional read-only preview is available:

```bash
bash deploy/install-existing.sh --plan
```

The installation performs these operations:

1. Validate the current Compose deployment, health, migrations, and compatible
   running image.
2. Save a configuration snapshot and create or verify the database backup.
3. Build a derived image from that exact image ID, preserving its runtime
   user and platform, then save rollback state and append the settings hook.
4. Apply the library migration and recreate web, worker, and beat with the
   derived image. Restart the existing proxy when configured.
5. Verify health and image IDs, then retain the four-service release override
   and print the command for future Compose operations.

The release override changes the Django image and library-enablement
environment variable for web, initializer, worker, and beat. Existing port
mappings, volume mounts, entrypoints, secrets, certificate settings, and the
proxy image continue to come from your original Compose configuration.

The installation creates the library-owned policy table. It does not enable
restrictions for any group automatically. Rerunning this command for the same
managed release verifies the installation; it does not create another stack.

## 5. Use the installed deployment

Save the `compose.sh` path printed after success. It includes the original
Compose files, the generated release override, and the recorded interpolation
environment, so use it for later Compose commands. Replace the placeholder
below with that printed path:

```bash
bash "/absolute/path/to/generated/compose.sh" ps
bash "/absolute/path/to/generated/compose.sh" exec -T uwsgi python manage.py check
bash "/absolute/path/to/generated/compose.sh" exec -T uwsgi \
  python manage.py showmigrations dojo_permission
```

Use your configured web service name instead of `uwsgi` when it differs.
`showmigrations` should show `[X] 0001_initial`. Continue to use the same
DefectDojo URL and existing administrator credentials.

To enable a group, create or edit it as an administrator and check
**Do you want to apply delete/edit restrictions to this group?** Assign the
group to its intended product or product type with Writer access. Reader,
Writer, and Maintainer members receive the restriction; restricted-group
Owners and superusers retain native permissions.

Verify in the browser that an administrator can still manage users and
groups, a restricted member can edit an existing finding and add another
member, and blocked create/delete/import operations are denied. Email
invitations require working SMTP; SSO requires the existing identity-provider
configuration.

Running only the original `docker compose up` can select the old image and
disable the library. Use the generated launcher, or include the generated
release override and the same environment in your deployment automation.

## Failure handling and rollback

If cutover fails, the deployment helper attempts to restore the original
settings and Django image and verifies rollback health. Keep the run directory,
settings backup, database backup, and rollback override printed by the
installer until the installation has been accepted. The additive policy table
remains available for a retry.

If the output says `ROLLBACK REQUIRES MANUAL ATTENTION`, inspect the reported
service state before rerunning. For rollback after a successful installation,
use the saved original image and rollback override as described in the
[advanced rollback guide](DEPLOYMENT.md#9-manual-rollback-after-a-successful-installation).
Disabling the library restores native DefectDojo permissions, so verify access
again after rollback.

The [advanced deployment guide](DEPLOYMENT.md) also covers registry builds,
explicit cutover commands, and full uninstall. The sample
`docker/compose.permission*.yml` files are for the two local bootstrap workflows;
use this existing-instance configuration for a running customized deployment.
