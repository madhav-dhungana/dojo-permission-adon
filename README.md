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

## Installation

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

`docker/compose.permission.yml` is only for this project's clean local staging
stack. Never apply it to an existing deployment because it supplies local nginx
and settings mounts.

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
