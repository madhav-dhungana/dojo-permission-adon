# Dojo Permission Library

A standalone authorization extension for customized DefectDojo **2.58.3**
release-mode deployments.

The library adds an opt-in restricted-group policy without changing
DefectDojo's core role tables or redefining the global Writer role. For an
enabled group, Reader, Writer, and Maintainer members keep existing-finding
editing but cannot create engagements, tests, or findings; import or re-import
scans; copy or delete findings; or delete engagements. Restricted members can
add an existing account or create an email/SSO account in that same group.
Restricted-group Owners and superusers keep their native permissions.

See the
[permission model](https://github.com/madhav-dhungana/dojo-permission-adon/blob/main/docs/PERMISSION_SCOPE.md)
for the exact matrix.

## Compatibility

- DefectDojo 2.58.3
- Python 3.13
- Django 5.2.14
- Docker Compose release mode for the supplied deployment automation

The Django system check rejects other DefectDojo versions. A customized
deployment must be extended from its **exact current Django image**, not from a
generic official image with the same version number.

## Local installation

Install Git, Docker Engine or Docker Desktop, Docker Compose 2.24.4 or newer,
and curl. Start Docker before running the installer. There are two local
choices.

### Without Signing Certificate

From the directory where you want to keep the installation, clone the library
and run the bootstrap script. It downloads DefectDojo beside the library before
building and starting the stack.

Use a normal folder outside Trash. If you moved an earlier installation to
Trash, open a terminal in the intended folder before running these commands.

```bash
mkdir -p dojo-permission
cd dojo-permission

git clone \
  https://github.com/madhav-dhungana/dojo-permission-adon.git

cd dojo-permission-adon
bash deploy/bootstrap-local-docker.sh
```

If Docker Desktop fails with `docker-credential-desktop: executable file not
found in $PATH`, update the library and retry. The bootstrap now makes Docker
Desktop's credential-helper directory available automatically. Replace the
placeholder below with your existing library checkout path:

```bash
cd "/absolute/path/to/dojo-permission-adon"
git pull --ff-only
bash deploy/bootstrap-local-docker.sh
```

This reuses the downloaded DefectDojo checkout and available build cache.

Use the same update-and-retry commands if nginx exits with
`/entrypoint-nginx.sh: Permission denied`. The bootstrap repairs that script's
permissions in a verified source checkout and uses normal source-file
permissions for new downloads. A fresh clone is not required.

If a later build or startup step fails, the completed
`../defectdojo-2.58.3` checkout stays on disk. Edit its `docker-compose.yml` as
needed, then run this from the library directory to continue with that file:

```bash
bash deploy/install-local.sh
```

This resume command uses the downloaded Compose configuration and prebuilt
DefectDojo 2.58.3 images. It accepts your local Compose edits without requiring
a clean checkout.

### With Signing Certificate

Download both repositories with the signing certificate, then update the
downloaded DefectDojo Compose file as required by your environment:

```bash
INSTALL_ROOT="$HOME/dojo-permission"
export SIGNING_CERTIFICATE="/absolute/path/to/ca-bundle.pem"
test -r "$SIGNING_CERTIFICATE"

mkdir -p "$INSTALL_ROOT"
cd "$INSTALL_ROOT"

git -c http.sslCAInfo="$SIGNING_CERTIFICATE" clone \
  https://github.com/madhav-dhungana/dojo-permission-adon.git

git -c http.sslCAInfo="$SIGNING_CERTIFICATE" clone \
  --branch 2.58.3 \
  --single-branch \
  https://github.com/DefectDojo/django-DefectDojo.git \
  defectdojo-2.58.3

# Edit defectdojo-2.58.3/docker-compose.yml before continuing.
cd "$INSTALL_ROOT/dojo-permission-adon"
bash deploy/install-local.sh --with-signing-certificate
```

Docker or Docker Desktop must also trust the signing certificate before it
can pull images. This is a host setting; Compose environment variables cannot
fix a Docker daemon certificate error. The installer does not accept the
certificate path; it uses the settings you put in DefectDojo's Compose file.
The checkout's `.env` file is intentionally ignored. Export each
signing-certificate variable you add or reference in `docker-compose.yml` in
the same shell before running the installer, or use literal values in the
Compose file.

The Without Signing Certificate bootstrap builds the pinned DefectDojo source.
`install-local.sh` reuses an existing sibling
`defectdojo-2.58.3/docker-compose.yml` and pulls the required release images,
including when you continue after a failed bootstrap. Changes to DefectDojo
Python source files are not built in this resume mode. Both scripts build the
permission-enabled Django image, start the complete stack, apply migrations,
and validate the result. Your Compose edits are retained. The installer
does not reset the DefectDojo checkout, run `compose down`, or delete volumes.
Direct `docker compose up` is not a replacement for the first installation
because it does not perform the library build, migrations, and validation.
Both scripts select the same Compose project. An owned stack recorded for
these exact checkout paths keeps its project name; otherwise a path-specific
name avoids collisions with unrelated resources. An old running stack can
still occupy ports `18080` and `18443`. Stop that known stack from its original
checkout, or choose free ports for this run:

```bash
DD_PORT=18081 DD_TLS_PORT=18444 bash deploy/bootstrap-local-docker.sh
```

Use the same port variables with `install-local.sh` when continuing an existing
installation.

See the
[local installation guide](https://github.com/madhav-dhungana/dojo-permission-adon/blob/main/docs/LOCAL_BOOTSTRAP.md)
for certificate trust, the generated Compose project name, and troubleshooting.

The Without Signing Certificate run ends with output like:

```text
Local staging is ready
DefectDojo:       2.58.3
Dojo Permission: 0.1.0
Image source:     build
URL:              http://127.0.0.1:18080/login
Admin username:   admin
```

To preview the resolved paths and command without changing Docker state, run:

```bash
bash deploy/install-local.sh --dry-run
```

Use `bash deploy/install-local.sh --help` for resume and signing-certificate
options. With no downloaded DefectDojo checkout, the no-flag installer starts
the bootstrap automatically.

The installer is safe to rerun with the same checkout and Compose project. It
may recreate its service containers, but it does not reset a checkout, run
`compose down`, or delete persistent volumes or application data.

After a successful run, the installer prints the exact status and
admin-password reset commands with every required environment value and
Compose file. Use those printed commands for that stack.

Run the installer again to rebuild or recreate missing containers. Never add
`--volumes` to a removal command unless deleting the local database and uploads
is intentional.

> **Local staging only:** this uses DefectDojo's evaluation Compose stack,
> sample secrets, and this project's complete sample settings mount. Do not
> expose it directly to the Internet or apply its Compose overlay to an
> existing customized deployment.

## Existing customized deployments

Two supported approaches produce an immutable derived Django image and use the
same plan-first cutover:

| Approach | Use it when |
| --- | --- |
| **Registry/CI** | The exact currently deployed image is available as `repository@sha256:digest`; build, push, and deploy the resulting digest. |
| **On-host** | The exact image exists only in the deployment host's Docker daemon as `sha256:image-id`; build and deploy locally without a registry. |

Start with the guarded builder rather than invoking `docker build` directly:

```bash
bash deploy/build-derived-image.sh --help
bash deploy/deploy-existing-compose.sh --help
```

The deployment command is plan-only until `--apply` is supplied. It preserves
the existing nginx image, volumes, secrets, entrypoints, and settings mounts.
The
[step-by-step deployment guide](https://github.com/madhav-dhungana/dojo-permission-adon/blob/main/docs/DEPLOYMENT.md)
includes prerequisites, complete commands for both approaches, validation,
configuration, and rollback.

The `docker/compose.permission*.yml` files are only for bootstrap-created local
staging stacks. Never apply them to an existing deployment because they control
local images and settings mounts.

## Development

Create a Python 3.13 virtual environment and install the test dependencies:

```bash
python3.13 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest tests
```

Those are the standalone compatibility/settings tests. From a configured
DefectDojo 2.58.3 checkout with the integration settings active, run the
authorization suite against this repository's source tree:

```bash
PYTHONPATH=/absolute/path/to/dojo-permission-adon/src \
  python manage.py test dojo_permission.tests --noinput
```

The production image installs the package with `--no-deps`, so it cannot
silently replace packages in the customized base image. The image contains no
new static assets; the deployment's existing matching nginx image remains
unchanged.

## Security

This package changes authorization decisions. Review the
[security policy](https://github.com/madhav-dhungana/dojo-permission-adon/blob/main/SECURITY.md),
back up the database and configuration, and validate in staging before a
production rollout.
