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

## Quick start on a new machine

For a clean local staging stack, install Git, Docker Engine or Docker Desktop,
Docker Compose 2.24.4 or newer, and curl. Start Docker, then run:

```bash
mkdir -p dojo-permission
cd dojo-permission

git clone \
  https://github.com/madhav-dhungana/dojo-permission-adon.git

cd dojo-permission-adon
bash deploy/bootstrap-local-docker.sh
```

The script clones the official DefectDojo `2.58.3` tag beside this repository,
verifies its pinned commit, builds the matching Django and nginx images,
installs this package in the Django image, starts the complete Compose stack,
applies migrations, and waits for the login page. The default URL is
<http://127.0.0.1:18080>. DefectDojo prints the generated `admin` password in
the initializer output on the first run. Both published ports bind only to
`127.0.0.1`.

The same bootstrap also supports an existing DefectDojo checkout with local
Compose edits. Choose whether it must reuse already-pulled images or pull the
exact 2.58.3 images:

```bash
CORPORATE_CA="/absolute/path/to/rak-ca-bundle.pem"
DEFECTDOJO_DIR="/absolute/path/to/defectdojo-2.58.3"

# No registry pulls; required DD, PostgreSQL, and Valkey images are local:
bash deploy/bootstrap-local-docker.sh \
  --existing-defectdojo-dir "$DEFECTDOJO_DIR" \
  --image-source local \
  --ca-bundle "$CORPORATE_CA"

# Or explicitly pull the required release images:
bash deploy/bootstrap-local-docker.sh \
  --existing-defectdojo-dir "$DEFECTDOJO_DIR" \
  --image-source pull \
  --ca-bundle "$CORPORATE_CA"
```

Existing-checkout mode permits a dirty or non-Git source tree but still
verifies the actual runtime image as DefectDojo 2.58.3, Django 5.2.14, and
Python 3.13. It applies Compose changes but does not build DefectDojo
application-source edits from the checkout. A repeatable `--compose-file FILE`
option adds office-specific Compose overrides without modifying the checkout.
`--ca-bundle FILE` mounts a per-developer combined corporate/public CA bundle
into the Python services; it does not configure Docker daemon trust for image
pulls.

See the
[local Docker bootstrap guide](https://github.com/madhav-dhungana/dojo-permission-adon/blob/main/docs/LOCAL_BOOTSTRAP.md)
for copy/paste commands, Compose ordering, certificate trust boundaries, and
troubleshooting.

A successful run ends with:

```text
Local staging is ready
DefectDojo:       2.58.3
Dojo Permission: 0.1.0
Image source:     build
URL:              http://127.0.0.1:18080/login
Admin username:   admin
```

The checkout directory, Compose project name, ports, health URL, and timeouts
are configurable:

```bash
bash deploy/bootstrap-local-docker.sh --help
```

The bootstrap is safe to rerun with the same checkout and Compose project. It
may recreate its service containers, but it does not reset a checkout, run
`compose down`, or delete persistent volumes or application data.

After a successful run, the script prints the exact status and admin-password
reset commands with every required environment value and Compose file. Use
those printed commands for that stack; they include image-specific helper
tags, optional office overrides, and the optional CA overlay.

Run the bootstrap again to rebuild or recreate missing containers. Never add
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
