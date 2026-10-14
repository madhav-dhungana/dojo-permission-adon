# Deploying to a customized DefectDojo instance

This guide covers a **first installation** on a single-PC, single-replica
Docker Compose deployment. The target must be DefectDojo 2.58.3 with Python
3.13 and Django 5.2.14.

The customer's customized Django image is the base. Having the same
DefectDojo version is not enough reason to replace it with the official image.
The customer's existing nginx image, volumes, secrets, entrypoints, and
settings mounts remain in place.

For Kubernetes, Swarm, multiple web replicas, or multiple hosts, build the
same derived image but use a deployment-specific blue/green rollout. The
supplied cutover script deliberately rejects those layouts.

## Before either approach

1. Record every Compose file, project name, project directory, and environment
   file used by the running deployment.
2. Verify that `uwsgi`, `initializer`, `celeryworker`, and `celerybeat` normally
   use the same customized Django image.
3. Record the current immutable image ID/digest and CPU platform.
4. Back up PostgreSQL, media, Compose/environment files, and the active
   `local_settings.py`. Test that the backup is readable and restorable.
5. Confirm that the supplied `local_settings.py` is the file mounted and read
   by all four Python services.
6. Establish a working HTTP(S) login or health URL.

Do not run `scripts/seed_demo.py`, `scripts/validate_demo.py`, or
`scripts/render_admin_snapshot.py` on the remote instance. They are local
staging tools and are excluded from Git and production images.

## Approach A: build in CI and deploy from a registry

Use this when the customer's exact customized Django image is available in a
registry. Both the base and deployed result must be addressed by digest.

First determine the target PC's platform from its running image:

```bash
docker image inspect CUSTOMIZED_IMAGE_ID --format '{{.Os}}/{{.Architecture}}'
```

On the trusted builder, from this repository:

```bash
bash deploy/build-derived-image.sh \
  --base-image registry.example/customer/defectdojo@sha256:BASE_DIGEST \
  --target-image registry.example/customer/defectdojo-permission:0.1.0-SOURCE_SHA \
  --platform linux/amd64 \
  --push \
  --output artifacts/derived-image.env
```

Use `linux/arm64` instead when that is what the target image reports. The
builder verifies DefectDojo, Django, Python, the runtime user, package
dependencies, image provenance, and platform. It refuses `latest`, mutable
base tags, dirty-source pushes, and output paths that could overwrite source.
The output file records the pushed `repository@sha256:digest`.

Copy this repository and the generated image metadata to the target PC, then
continue with **Plan and apply the cutover** below.

## Approach B: build on the target PC without a registry

Use this when the customized image exists only on the customer's machine.
Run the same Compose arguments that operate the current deployment:

```bash
cd /opt/customer-defectdojo

WEB_CONTAINER_ID="$(docker compose \
  --project-directory /opt/customer-defectdojo \
  --env-file /opt/customer-defectdojo/.env \
  --project-name defectdojo \
  --file /opt/customer-defectdojo/docker-compose.yml \
  ps -q uwsgi)"

CUSTOMIZED_IMAGE_ID="$(docker inspect "$WEB_CONTAINER_ID" --format '{{.Image}}')"
docker image inspect "$CUSTOMIZED_IMAGE_ID" --format '{{.Os}}/{{.Architecture}}'
```

Protect the old image from pruning and optionally export a recovery copy:

```bash
docker image tag "$CUSTOMIZED_IMAGE_ID" local/defectdojo-rollback:before-permission
docker image save --output /secure/backups/defectdojo-before-permission.tar \
  local/defectdojo-rollback:before-permission
```

From this library checkout, build directly from the local immutable image ID:

```bash
bash deploy/build-derived-image.sh \
  --base-image "$CUSTOMIZED_IMAGE_ID" \
  --target-image local/defectdojo-permission:0.1.0-SOURCE_SHA \
  --output artifacts/derived-image.env
```

For a local image ID, the platform is inferred from the base. The output file
contains the new local `sha256:image-id`; no push or registry is required.

## Plan and apply the cutover

Load the builder output:

```bash
source artifacts/derived-image.env
```

Run a read-only plan first. Repeat `--compose-file` for every file used by the
existing deployment and use its real service names if they differ:

```bash
bash deploy/deploy-existing-compose.sh \
  --compose-file /opt/customer-defectdojo/docker-compose.yml \
  --project-directory /opt/customer-defectdojo \
  --env-file /opt/customer-defectdojo/.env \
  --project-name defectdojo \
  --image "$DOJO_PERMISSION_IMAGE" \
  --rollback-image "$DOJO_PERMISSION_BASE_IMAGE" \
  --settings-file /opt/customer-defectdojo/docker/extra_settings/local_settings.py \
  --release-override /opt/customer-defectdojo/compose.dojo-permission.release.yml \
  --health-url https://dojo.example.test/login
```

The plan verifies the running service set, current application image, current
health URL, safe file paths, and that this is not an already-enabled upgrade.
It makes no changes.

After reviewing the plan and verifying the backup, repeat the same command
with both of these arguments:

```text
--backup-evidence /secure/backups/dojo-before-permission.dump --apply
```

`--backup-evidence` validates only that the supplied backup artifact is
readable and non-empty. The operator remains responsible for creating and
testing the backup.

The apply phase:

1. Refuses to continue if the current stack is unhealthy or has any pending
   customized migrations.
2. Confirms that the derived image was built from the exact rollback image.
3. Appends a managed, environment-gated integration block to the existing
   settings file; it never replaces the rest of that file.
4. Runs the existing initializer with the derived image and verifies
   `dojo_permission.0001_initial` before stopping application services.
5. Stops beat, worker, and web; starts web; restarts the unchanged proxy only
   so it can resolve the recreated web container; checks health; then starts
   worker and beat.
6. Confirms Django checks, migration state, and actual container image IDs.
7. Writes a persistent four-service image override. Include this override in
   every later Compose command for the deployment.

Use `--no-proxy-restart` only if the customized proxy/service-discovery design
does not cache the old web-container address. The proxy image is never changed.
The script never runs `compose down`, never uses `-v`, and never rebuilds an
inherited Compose `build:` definition.

The static [production override](../docker/compose.permission.production.yml)
is an image-only reference for deployments with the standard service names.
The deployment script generates the safer, exact equivalent for custom names.
Never apply `docker/compose.permission.yml` remotely; that file is only for the
clean local demo stack and supplies local nginx and settings mounts.

## Validation

Before restoring unrestricted production traffic, verify:

1. A superuser can view, create, and edit users and groups.
2. Restricted-group Reader, Writer, and Maintainer members can edit existing
   findings but cannot create engagements/tests/findings, import or re-import
   scans, copy/delete findings, or delete engagements.
3. A restricted member can open the restricted-group member page and add an
   existing account or create a new email/SSO account in that group.
4. Restricted-group Owners and superusers keep native permissions.
5. Unchecking the group restriction disables the overlay without changing
   memberships or roles.

Email invitations require working SMTP. An SSO-created local account can log
in only after the customer's identity provider is configured to supply that
identity.

## Failure during cutover

After the migration preflight, an error, interruption, failed health check, or
failed image check triggers automatic restoration of:

- the original settings file when the script added the hook;
- the previous Django image for web, worker, and beat;
- a disabled, generated rollback override; and
- the unchanged proxy container.

The script then checks rollback health and image IDs. If those checks cannot be
proved, it prints `ROLLBACK REQUIRES MANUAL ATTENTION`. The additive policy
table is intentionally retained.

## Manual rollback after a successful deployment

The successful command prints exact paths for the settings backup and rollback
override. In a maintenance window:

1. Stop beat, worker, and web with the customer's original Compose arguments
   plus the printed rollback override.
2. Restore the printed `local_settings.py` backup.
3. Copy the printed rollback override over the persistent release override.
4. Recreate web, worker, and beat with `--no-deps --no-build --force-recreate`.
5. Restart the unchanged proxy if it resolves the web address only at startup.
6. Verify the login URL and actual image IDs.

Removing or disabling the overlay immediately restores each user's underlying
native DefectDojo permissions. In particular, restricted members may regain
native Writer/Maintainer operations, so rollback is an authorization change.

Migration `0001` creates only the library-owned
`dojo_permission_restrictedgrouppolicy` table; no DefectDojo core column is
altered. Normally retain it for a forward retry. A full uninstall may run
`python manage.py migrate dojo_permission zero` only while the derived image
and integration are still present and all application writes are stopped.
Provisioned core users, group memberships, and product assignments are not
removed by reversing this migration and require a separate reviewed cleanup.
