# Security Policy

This package changes DefectDojo authorization decisions. Treat every release
as security-sensitive and validate it against a staging copy of the target
customized DefectDojo deployment before production use.

## Supported baseline

Only DefectDojo 2.58.3 release mode with Python 3.13 and Django 5.2.14 is
supported by the current release.

## Reporting a vulnerability

Do not open a public issue containing credentials, exploit details, customer
data, or deployment configuration. Report privately to the repository owner
through GitHub's private vulnerability reporting feature. If that feature is
not enabled, contact the owner privately and provide a minimal reproduction.

## Deployment cautions

- Derive the application image from the target's exact customized image
  digest; never replace it with a generic upstream image.
- Apply the same derived digest to every Django and Celery process.
- Back up PostgreSQL and configuration before migration.
- Do not run files excluded under `scripts/` on a remote deployment; the local
  demo tools can reset accounts and modify roles and data.
- Disabling this package restores the users' underlying native DefectDojo
  permissions immediately. Plan rollback as an authorization change.
