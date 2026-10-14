# Deployment guide

This guide installs the library into an existing customized DefectDojo 2.58.3
Docker Compose deployment. The included automation supports a **first
installation** on one host with one web, worker, and beat replica.

Choose one image-build approach:

| Approach | Use it when | Result |
| --- | --- | --- |
| [A. Registry/CI](#3-approach-a-build-and-push-through-a-registry) | The exact currently deployed Django image is available from a registry by digest. | A pushed `repository@sha256:digest`. |
| [B. On-host](#4-approach-b-build-on-the-deployment-host-without-a-registry) | The exact Django image exists only in the deployment host's Docker daemon. | A local `sha256:image-id`; no registry is needed. |

Both approaches derive from the exact Django image already in use. They do not
replace the existing nginx image, volumes, secrets, entrypoints, or settings
mounts. The proxy container may restart after web cutover so it can resolve the
recreated web container, but its image remains unchanged.

The script rejects an already-enabled installation. Upgrades, Kubernetes,
Swarm, multiple hosts, and replicated web/worker/beat services need a
release-specific blue/green plan rather than this cutover script.

## 1. Prerequisites

Install these commands on the relevant host:

```bash
docker --version
docker compose version
git --version
curl --version
python3 --version
bash --version
```

The deployment must use:

- DefectDojo 2.58.3 release mode;
- Python 3.13 and Django 5.2.14 in the Django image;
- Compose services for web, initializer, worker, beat, and optionally nginx;
- one running web, worker, and beat container; and
- a host-side `local_settings.py` used by all Python services.

Also confirm:

- the operator can access Docker and write the settings, override, and state
  directories;
- the library checkout is clean and committed;
- the builder supports the deployment's CPU platform, including emulation if
  cross-building;
- there is enough space for the derived image and rollback archive; and
- a maintenance window is available for a brief web/worker/beat outage.

Create and test a PostgreSQL/configuration backup before continuing. Keep its
path available for `--backup-evidence`. That option verifies only that the path
is readable and non-empty; it does not create or restore a backup.

Do not run `scripts/seed_demo.py`, `scripts/validate_demo.py`, or
`scripts/render_admin_snapshot.py` against an existing deployment. They are
local staging tools and are excluded from Git and production images.

## 2. Prepare the deployment host

Run this section on the host where DefectDojo is running.

### 2.1 Clone and pin the library source

```bash
sudo mkdir -p /opt/dojo-permission-adon
sudo chown "$(id -u):$(id -g)" /opt/dojo-permission-adon
git clone https://github.com/madhav-dhungana/dojo-permission-adon.git \
  /opt/dojo-permission-adon
cd /opt/dojo-permission-adon

# Replace main with a release tag or commit when one is published.
git checkout main
git pull --ff-only
DD_PERMISSION_COMMIT="$(git rev-parse HEAD)"
test -z "$(git status --porcelain)"
printf 'Pinned dojo-permission commit: %s\n' "$DD_PERMISSION_COMMIT"
```

If the repository already exists, run `git fetch --all --prune`, check out the
chosen tag or commit, and confirm that `git status --porcelain` is empty.

### 2.2 Set deployment-specific values

Edit only the values in this block. The examples assume one Compose file and
the standard DefectDojo service names. Run the remaining commands in the same
Bash session. After restarting the shell, rerun this block, rebuild
`DD_COMPOSE`, and repeat the discovery/verification commands in section 2.3
before continuing.

```bash
set -Eeuo pipefail
umask 077

DD_LIBRARY_ROOT="/opt/dojo-permission-adon"
DD_PERMISSION_COMMIT="$(git -C "$DD_LIBRARY_ROOT" rev-parse HEAD)"
DD_RELEASE_ID="0.1.0-${DD_PERMISSION_COMMIT:0:12}-$(date -u +%Y%m%dT%H%M%SZ)"

DD_DEPLOYMENT_ROOT="/opt/defectdojo"
DD_COMPOSE_FILE="$DD_DEPLOYMENT_ROOT/docker-compose.yml"
DD_ENV_FILE="$DD_DEPLOYMENT_ROOT/.env"
DD_PROJECT_NAME="defectdojo"

DD_WEB_SERVICE="uwsgi"
DD_INITIALIZER_SERVICE="initializer"
DD_WORKER_SERVICE="celeryworker"
DD_BEAT_SERVICE="celerybeat"
DD_PROXY_SERVICE="nginx"

DD_SETTINGS_FILE="$DD_DEPLOYMENT_ROOT/docker/extra_settings/local_settings.py"
DD_RELEASE_OVERRIDE="$DD_DEPLOYMENT_ROOT/compose.dojo-permission.release.yml"
DD_HEALTH_URL="https://dojo.example.org/login"
DD_BACKUP_EVIDENCE="/secure/backups/dojo-before-permission.dump"
```

If no explicit environment file is used, remove `--env-file "$DD_ENV_FILE"`
from later arrays. If multiple Compose files are used, add every file in the
same order used for normal operations.

Create a reusable Compose command:

```bash
DD_COMPOSE=(
  docker compose
  --project-directory "$DD_DEPLOYMENT_ROOT"
  --env-file "$DD_ENV_FILE"
  --project-name "$DD_PROJECT_NAME"
  --file "$DD_COMPOSE_FILE"
)

"${DD_COMPOSE[@]}" config -q
"${DD_COMPOSE[@]}" ps
```

For another Compose file, add another pair before the closing parenthesis:

```bash
# --file "$DD_DEPLOYMENT_ROOT/docker-compose.production.yml"
```

### 2.3 Discover the exact running base image

```bash
DD_WEB_CONTAINER_ID="$("${DD_COMPOSE[@]}" ps -q "$DD_WEB_SERVICE")"
test -n "$DD_WEB_CONTAINER_ID"

DD_BASE_IMAGE_ID="$(
  docker inspect "$DD_WEB_CONTAINER_ID" --format '{{.Image}}'
)"
DD_PLATFORM="$(
  docker image inspect "$DD_BASE_IMAGE_ID" \
    --format '{{.Os}}/{{.Architecture}}'
)"
DD_BASE_RUNTIME_USER="$(
  docker image inspect "$DD_BASE_IMAGE_ID" --format '{{.Config.User}}'
)"

printf 'Current Django image ID: %s\n' "$DD_BASE_IMAGE_ID"
printf 'Deployment platform:     %s\n' "$DD_PLATFORM"
printf 'Image runtime user:      %s\n' "${DD_BASE_RUNTIME_USER:-<unset>}"
"${DD_COMPOSE[@]}" config --images
```

Confirm the running web, worker, and beat containers use the same image:

```bash
for DD_SERVICE in \
  "$DD_WEB_SERVICE" "$DD_WORKER_SERVICE" "$DD_BEAT_SERVICE"
do
  DD_CONTAINER_ID="$("${DD_COMPOSE[@]}" ps -q "$DD_SERVICE")"
  test -n "$DD_CONTAINER_ID"
  test "$(docker inspect "$DD_CONTAINER_ID" --format '{{.Image}}')" \
    = "$DD_BASE_IMAGE_ID"
done
unset DD_SERVICE DD_CONTAINER_ID
```

Verify the current stack is healthy and has no pending migrations:

```bash
test -f "$DD_SETTINGS_FILE"
test -e "$DD_BACKUP_EVIDENCE"
curl --fail --silent --show-error "$DD_HEALTH_URL" >/dev/null
"${DD_COMPOSE[@]}" exec -T "$DD_WEB_SERVICE" \
  python manage.py migrate --check
```

Protect the running image from pruning. Saving an archive is recommended but
does not replace the database/configuration backup.

```bash
DD_ROLLBACK_TAG="local/defectdojo-rollback:$DD_RELEASE_ID"
DD_IMAGE_ARCHIVE="/secure/backups/defectdojo-image-$DD_RELEASE_ID.tar"

docker image tag "$DD_BASE_IMAGE_ID" "$DD_ROLLBACK_TAG"
docker image save --output "$DD_IMAGE_ARCHIVE" "$DD_ROLLBACK_TAG"
test -s "$DD_IMAGE_ARCHIVE"
```

Read the manual rollback section before applying, retain both images, and do
not delete `.dojo-permission-deploy` until the rollback window has closed.

Now follow exactly one build approach.

## 3. Approach A: build and push through a registry

Use this approach when the exact customized base image has a registry digest
and the builder or CI runner can pull it.

### A1. Select and verify the exact base digest

On the deployment host, list the digests attached to the running image:

```bash
docker image inspect "$DD_BASE_IMAGE_ID" \
  --format '{{range .RepoDigests}}{{println .}}{{end}}'
```

Select the digest for the customized Django repository. Do not substitute an
official DefectDojo image merely because its version is also 2.58.3.

```bash
DD_BASE_IMAGE="registry.example.org/defectdojo/custom-django@sha256:REPLACE_BASE_DIGEST"

docker pull --platform "$DD_PLATFORM" "$DD_BASE_IMAGE"
test "$(docker image inspect "$DD_BASE_IMAGE" --format '{{.Id}}')" \
  = "$DD_BASE_IMAGE_ID"
```

If no repository digest is listed, use Approach B or first publish the exact
customized image to a private registry through a separately reviewed process.

### A2. Build on the trusted builder or CI runner

Clone the same library revision on the builder:

```bash
set -Eeuo pipefail
umask 077

DD_PERMISSION_COMMIT="REPLACE_WITH_FULL_COMMIT_FROM_DEPLOYMENT_HOST"
git clone https://github.com/madhav-dhungana/dojo-permission-adon.git
cd dojo-permission-adon
git checkout "$DD_PERMISSION_COMMIT"
test -z "$(git status --porcelain)"
DD_RELEASE_ID="0.1.0-${DD_PERMISSION_COMMIT:0:12}-$(date -u +%Y%m%dT%H%M%SZ)"
```

Set the exact values recorded on the deployment host. Authenticate using the
registry's normal secure login process, then build and push:

```bash
DD_BASE_IMAGE="registry.example.org/defectdojo/custom-django@sha256:REPLACE_BASE_DIGEST"
DD_PLATFORM="linux/amd64"  # Use linux/arm64 when that was detected.
DD_TARGET_IMAGE="registry.example.org/defectdojo/dojo-permission:$DD_RELEASE_ID"

bash deploy/build-derived-image.sh \
  --base-image "$DD_BASE_IMAGE" \
  --target-image "$DD_TARGET_IMAGE" \
  --platform "$DD_PLATFORM" \
  --push \
  --output artifacts/derived-image.env

cat artifacts/derived-image.env
```

If the base image has no configured runtime user, add an explicit
`--runtime-user` matching the current deployment. The builder verifies package
versions, dependencies, provenance, runtime user, and platform. It refuses
`latest`, mutable base tags, dirty-source pushes, and an existing local target
tag. Configure immutable tags in the registry as well.

### A3. Transfer metadata and pre-pull both digests

Copy `artifacts/derived-image.env` to the same library revision on the
deployment host. One possible transfer command is:

```bash
DD_DEPLOY_SSH="REPLACE_WITH_USER_AT_HOST"
test "$DD_DEPLOY_SSH" != "REPLACE_WITH_USER_AT_HOST"

ssh "$DD_DEPLOY_SSH" 'mkdir -p /opt/dojo-permission-adon/artifacts'
scp artifacts/derived-image.env \
  "$DD_DEPLOY_SSH:/opt/dojo-permission-adon/artifacts/derived-image.env"
```

Back on the deployment host, ensure the variables and discovered image ID from
section 2 are still defined. After starting a new shell, rerun the command
blocks in sections 2.2 and 2.3 through the health/migration verification; the
rollback archive does not need to be recreated. Then run:

```bash
cd "$DD_LIBRARY_ROOT"
source artifacts/derived-image.env
test "$(git rev-parse HEAD)" = "$DOJO_PERMISSION_SOURCE_REVISION"

docker pull "$DOJO_PERMISSION_BASE_IMAGE"
docker pull "$DOJO_PERMISSION_IMAGE"
test "$(docker image inspect "$DOJO_PERMISSION_BASE_IMAGE" --format '{{.Id}}')" \
  = "$DD_BASE_IMAGE_ID"
```

Registry credentials on the deployment host must permit pulling both the
derived digest and its labeled base digest.

Continue with section 5.

## 4. Approach B: build on the deployment host without a registry

Use this approach when the exact customized image exists only in the local
Docker daemon. Run these commands after completing sections 1 and 2.

### B1. Build from the immutable local image ID

```bash
cd "$DD_LIBRARY_ROOT"
DD_TARGET_IMAGE="local/defectdojo-dojo-permission:$DD_RELEASE_ID"
DD_RUNTIME_USER="${DD_BASE_RUNTIME_USER:-root}"

bash deploy/build-derived-image.sh \
  --base-image "$DD_BASE_IMAGE_ID" \
  --target-image "$DD_TARGET_IMAGE" \
  --runtime-user "$DD_RUNTIME_USER" \
  --output artifacts/derived-image.env

cat artifacts/derived-image.env
```

The platform is inferred from the local image. The output records the new
local `sha256:image-id`; nothing is pushed.

### B2. Load and verify the generated metadata

```bash
source artifacts/derived-image.env
test "$DOJO_PERMISSION_BASE_IMAGE" = "$DD_BASE_IMAGE_ID"
test "$(git rev-parse HEAD)" = "$DOJO_PERMISSION_SOURCE_REVISION"
docker image inspect "$DOJO_PERMISSION_IMAGE" >/dev/null
```

Continue with section 5.

## 5. Plan and apply the installation

Run this section on the deployment host after completing Approach A or B.

### 5.1 Build the deployment argument list

```bash
cd "$DD_LIBRARY_ROOT"
source artifacts/derived-image.env

DD_DEPLOY_ARGS=(
  --compose-file "$DD_COMPOSE_FILE"
  --project-directory "$DD_DEPLOYMENT_ROOT"
  --env-file "$DD_ENV_FILE"
  --project-name "$DD_PROJECT_NAME"
  --image "$DOJO_PERMISSION_IMAGE"
  --rollback-image "$DOJO_PERMISSION_BASE_IMAGE"
  --settings-file "$DD_SETTINGS_FILE"
  --release-override "$DD_RELEASE_OVERRIDE"
  --health-url "$DD_HEALTH_URL"
  --web-service "$DD_WEB_SERVICE"
  --initializer-service "$DD_INITIALIZER_SERVICE"
  --worker-service "$DD_WORKER_SERVICE"
  --beat-service "$DD_BEAT_SERVICE"
  --proxy-service "$DD_PROXY_SERVICE"
)
```

Add another `--compose-file "/absolute/path/to/file.yml"` for every additional
Compose file, preserving the normal file order. Omit `--env-file` when no
explicit environment file is used. Add `--no-proxy-restart` only when service
discovery guarantees that the proxy does not cache the old web-container
address.

### 5.2 Run the read-only plan

```bash
bash deploy/deploy-existing-compose.sh "${DD_DEPLOY_ARGS[@]}"
```

The plan changes nothing. Review the current image, new image, settings hook,
release override, services, and proxy behavior. Resolve every error first.

### 5.3 Apply during a maintenance window

Reconfirm that the backup is current and restorable, then run:

```bash
DD_DEPLOY_LOG="$DD_LIBRARY_ROOT/artifacts/deploy-$(date -u +%Y%m%dT%H%M%SZ).log"

bash deploy/deploy-existing-compose.sh \
  "${DD_DEPLOY_ARGS[@]}" \
  --backup-evidence "$DD_BACKUP_EVIDENCE" \
  --apply 2>&1 | tee "$DD_DEPLOY_LOG"
```

Save the persistent release override, settings backup, and rollback override
paths printed after success.

The apply phase verifies the current stack and image provenance, appends an
environment-gated hook while preserving the existing settings content, runs
the initializer and migration checks, cuts over web/worker/beat, checks health,
and writes the persistent four-service image override. It never runs
`docker compose down`, deletes volumes, changes the nginx image, reverses
migrations, or builds an inherited Compose `build:` definition.

## 6. Configure a restricted group

Installation creates the policy table but does not enable a group. Use either
the UI or the management command.

### Option 1: DefectDojo UI

1. Create or edit a group.
2. Check **Do you want to apply delete/edit restrictions to this group?**
3. Assign the group to the intended product or product type with **Writer**
   access.
4. Add intended members as Reader, Writer, or Maintainer. An Owner is exempt
   from the restriction, as is a superuser.

Eligible restricted members who open the normal group link are redirected to
the limited page for adding an existing account or creating an email/SSO
account in that same group.

### Option 2: management command

Find the relevant IDs in DefectDojo, then run the command through the active
web service. Select only the intended scope. This example uses one product; to
scope by product type instead, replace the `DD_SCOPE_ARGS` line with
`DD_SCOPE_ARGS=(--product-type "$DD_PRODUCT_TYPE_ID")`.

```bash
DD_RELEASE_COMPOSE=("${DD_COMPOSE[@]}" --file "$DD_RELEASE_OVERRIDE")
DD_PRODUCT_ID="REPLACE_WITH_PRODUCT_ID"
DD_PRODUCT_TYPE_ID="REPLACE_WITH_PRODUCT_TYPE_ID"
DD_MEMBER_USERNAME="REPLACE_WITH_EXISTING_USERNAME"

test "$DD_PRODUCT_ID" != "REPLACE_WITH_PRODUCT_ID"
test "$DD_MEMBER_USERNAME" != "REPLACE_WITH_EXISTING_USERNAME"
DD_SCOPE_ARGS=(--product "$DD_PRODUCT_ID")

"${DD_RELEASE_COMPOSE[@]}" exec -T "$DD_WEB_SERVICE" \
  python manage.py provision_dojo_permission_group \
  --name "Restricted Writers" \
  --description "Restricted existing-finding editors" \
  "${DD_SCOPE_ARGS[@]}" \
  --member "$DD_MEMBER_USERNAME"
```

The command enables the policy, creates Writer product/product-type links, and
adds the optional initial member as Writer. It refuses to overwrite an existing
non-Writer assignment. Adjust the member's group role through the UI if Reader
or Maintainer is required.

## 7. Validate the installation

Include the generated release override in every future Compose command:

```bash
DD_RELEASE_COMPOSE=("${DD_COMPOSE[@]}" --file "$DD_RELEASE_OVERRIDE")

"${DD_RELEASE_COMPOSE[@]}" config -q
"${DD_RELEASE_COMPOSE[@]}" ps
"${DD_RELEASE_COMPOSE[@]}" exec -T "$DD_WEB_SERVICE" \
  python manage.py check
"${DD_RELEASE_COMPOSE[@]}" exec -T "$DD_WEB_SERVICE" \
  python manage.py showmigrations dojo_permission | \
  grep -F '[X] 0001_initial'
curl --fail --silent --show-error "$DD_HEALTH_URL" >/dev/null
```

Confirm web, worker, and beat use the derived image:

```bash
DD_EXPECTED_IMAGE_ID="$(
  docker image inspect "$DOJO_PERMISSION_IMAGE" --format '{{.Id}}'
)"

for DD_SERVICE in \
  "$DD_WEB_SERVICE" "$DD_WORKER_SERVICE" "$DD_BEAT_SERVICE"
do
  DD_CONTAINER_ID="$("${DD_RELEASE_COMPOSE[@]}" ps -q "$DD_SERVICE")"
  test "$(docker inspect "$DD_CONTAINER_ID" --format '{{.Image}}')" \
    = "$DD_EXPECTED_IMAGE_ID"
done
unset DD_SERVICE DD_CONTAINER_ID
```

Complete these browser checks:

1. A superuser can view, create, and edit users and groups.
2. Restricted Reader, Writer, and Maintainer members can edit existing findings
   but cannot create engagements/tests/findings, import or re-import scans,
   copy/delete findings, or delete engagements.
3. A restricted member can add an existing account or create a new email/SSO
   account in the same restricted group.
4. Restricted-group Owners and superusers retain native permissions.
5. The restriction checkbox remains enabled. If deactivation is tested in a
   staging environment, immediately re-enable it and re-verify the restricted
   permission matrix before allowing production traffic.

Email invitations require working SMTP. SSO sign-in requires a configured
identity provider.

## 8. Failure handling

After migration preflight, an error, interruption, failed health check, or
failed image check triggers automatic restoration of the original settings,
previous Django image, disabled rollback override, and unchanged proxy
container. The script verifies rollback health and image IDs. If it cannot
prove a healthy rollback, it prints `ROLLBACK REQUIRES MANUAL ATTENTION`.

The additive policy table is retained.

## 9. Manual rollback after a successful installation

Rerun section 2.2 and recreate `DD_COMPOSE` if this is a new shell. Then load
the build metadata, identify the successful deployment log, and recreate the
active Compose command:

```bash
cd "$DD_LIBRARY_ROOT"
source artifacts/derived-image.env
DD_DEPLOY_LOG="$DD_LIBRARY_ROOT/artifacts/REPLACE_WITH_SUCCESSFUL_DEPLOY_LOG"
DD_RELEASE_COMPOSE=("${DD_COMPOSE[@]}" --file "$DD_RELEASE_OVERRIDE")
test -s "$DD_DEPLOY_LOG"
```

Extract the exact backup paths from that log:

```bash
DD_SETTINGS_BACKUP="$(
  sed -n 's/^Settings backup:  //p' "$DD_DEPLOY_LOG" | tail -1
)"
DD_ROLLBACK_OVERRIDE="$(
  sed -n 's/^Rollback override: //p' "$DD_DEPLOY_LOG" | tail -1
)"
test -s "$DD_SETTINGS_BACKUP"
test -s "$DD_ROLLBACK_OVERRIDE"
```

Before rollback, check whether the settings file changed after installation
without printing its potentially sensitive contents:

```bash
if ! cmp -s "$DD_SETTINGS_BACKUP" "$DD_SETTINGS_FILE"; then
  printf '%s\n' \
    'Settings differ; review them locally through an approved secure process.'
fi
```

Do not copy the old settings backup over the current file during a later
manual rollback; doing so could discard legitimate changes made since
installation. The rollback override sets `DD_DOJO_PERMISSION_ENABLED=false`,
so the managed hook stays dormant even when the old image does not contain the
package. Remove or merge that exact managed block later through a reviewed
settings change if desired. Do not paste or record a settings diff in a
terminal/session log because it may contain database credentials or secret
keys.

In a maintenance window, stop services, install the rollback override, and
recreate the services:

```bash
"${DD_RELEASE_COMPOSE[@]}" stop -t 60 "$DD_BEAT_SERVICE"
"${DD_RELEASE_COMPOSE[@]}" stop -t 60 "$DD_WORKER_SERVICE"
"${DD_RELEASE_COMPOSE[@]}" stop -t 60 "$DD_WEB_SERVICE"

cp -p "$DD_ROLLBACK_OVERRIDE" "$DD_RELEASE_OVERRIDE"

DD_ROLLBACK_COMPOSE=("${DD_COMPOSE[@]}" --file "$DD_RELEASE_OVERRIDE")
"${DD_ROLLBACK_COMPOSE[@]}" config -q
"${DD_ROLLBACK_COMPOSE[@]}" up -d --no-deps --no-build --force-recreate \
  "$DD_WEB_SERVICE"
"${DD_ROLLBACK_COMPOSE[@]}" restart "$DD_PROXY_SERVICE"

DD_ROLLBACK_HEALTHY=0
for DD_ATTEMPT in {1..60}; do
  if curl --fail --silent --show-error --max-time 5 \
    "$DD_HEALTH_URL" >/dev/null 2>&1
  then
    DD_ROLLBACK_HEALTHY=1
    break
  fi
  sleep 1
done
test "$DD_ROLLBACK_HEALTHY" -eq 1

"${DD_ROLLBACK_COMPOSE[@]}" up -d --no-deps --no-build --force-recreate \
  "$DD_WORKER_SERVICE" "$DD_BEAT_SERVICE"
```

Verify the old image is active:

```bash
DD_EXPECTED_ROLLBACK_ID="$(
  docker image inspect "$DOJO_PERMISSION_BASE_IMAGE" --format '{{.Id}}'
)"
for DD_SERVICE in \
  "$DD_WEB_SERVICE" "$DD_WORKER_SERVICE" "$DD_BEAT_SERVICE"
do
  DD_CONTAINER_ID="$("${DD_ROLLBACK_COMPOSE[@]}" ps -q "$DD_SERVICE")"
  test "$(docker inspect "$DD_CONTAINER_ID" --format '{{.Image}}')" \
    = "$DD_EXPECTED_ROLLBACK_ID"
done
unset DD_SERVICE DD_CONTAINER_ID
```

Omit the proxy restart if the installation used `--no-proxy-restart`.
Disabling the overlay immediately restores underlying native DefectDojo
permissions, so rollback is an authorization change.

## 10. Full uninstall

Migration `0001` creates only the library-owned
`dojo_permission_restrictedgrouppolicy` table; no DefectDojo core table or
column is altered. Normally retain it for a forward retry.

To remove the table, first rerun section 2.2, rebuild `DD_COMPOSE`, and load the
build metadata in a new shell if necessary:

```bash
cd "$DD_LIBRARY_ROOT"
source artifacts/derived-image.env
DD_RELEASE_COMPOSE=("${DD_COMPOSE[@]}" --file "$DD_RELEASE_OVERRIDE")
"${DD_RELEASE_COMPOSE[@]}" config -q
```

Stop every process that can write through Django, then reverse the migration
while the derived image and integration are still available:

```bash
"${DD_RELEASE_COMPOSE[@]}" stop -t 60 "$DD_BEAT_SERVICE"
"${DD_RELEASE_COMPOSE[@]}" stop -t 60 "$DD_WORKER_SERVICE"
"${DD_RELEASE_COMPOSE[@]}" stop -t 60 "$DD_WEB_SERVICE"

"${DD_RELEASE_COMPOSE[@]}" run --rm --no-deps \
  --entrypoint python "$DD_WEB_SERVICE" \
  manage.py migrate dojo_permission zero
```

Then continue the manual rollback from the rollback-override step; do not
restart a derived-image application process after dropping the table. Leave
the environment-gated hook dormant or remove its exact managed block through a
reviewed settings edit. Reversing the migration does not remove accounts,
memberships, product assignments, or other core DefectDojo records created
while the library was installed. Review those separately.

## Local staging override

`docker/compose.permission.yml` is only for this repository's clean local
staging stack. Never apply it to an existing deployment: it supplies a local
nginx image and replaces the entire sample extra-settings mount.
