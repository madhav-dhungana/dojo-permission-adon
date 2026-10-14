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

See [the permission contract](docs/PERMISSION_SCOPE.md) for the exact matrix.

## Compatibility

- DefectDojo 2.58.3
- Python 3.13
- Django 5.2.14
- Docker Compose release mode for the supplied deployment automation

The Django system check rejects other DefectDojo versions. A customized
deployment must be extended from its **exact current Django image**, not from a
generic official image with the same version number.

## Deploying to another customized instance

Two supported approaches use the same immutable derived image and controlled
cutover:

1. **Registry/CI:** build from the customer's current
   `repository@sha256:digest`, push the derived image, then deploy that digest.
2. **On-host:** build directly on the customer's PC from the current local
   `sha256:image-id`; no registry is required.

Start with the guarded builder rather than invoking `docker build` directly:

```bash
bash deploy/build-derived-image.sh --help
bash deploy/deploy-existing-compose.sh --help
```

The deployment command is plan-only until `--apply` is supplied. It keeps the
customer's nginx image, volumes, secrets, entrypoints, and existing settings
mounts intact. Full commands and rollback instructions are in
[the deployment guide](docs/DEPLOYMENT.md).

`docker/compose.permission.yml` is only for this project's clean local staging
stack. Never apply it unchanged to a customer's customized deployment because
it supplies local nginx and settings mounts.

## Development

Create a Python 3.13 virtual environment and install the test dependencies:

```bash
python3.13 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

The production image installs the package with `--no-deps`, so it cannot
silently replace packages in the customized base image. The image contains no
new static assets; the customer's matching customized nginx image remains
unchanged.

## Security

This package changes authorization decisions. Review [SECURITY.md](SECURITY.md),
back up the database and configuration, and validate in staging before a
production rollout.
