# Local installation

This guide installs DefectDojo 2.58.3 and the Dojo Permission Library as one
new local staging stack. It is not for changing an already running customized
deployment. Use the [existing-deployment guide](DEPLOYMENT.md) for that case.

The local installer has two user-facing choices:

- **Without Signing Certificate**
- **With Signing Certificate**

Both choices use the same two-repository layout and finish the installation
with one script.

## 1. Prerequisites

Install and start:

- Git;
- Docker Engine or Docker Desktop;
- Docker Compose 2.24.4 or newer; and
- curl.

Confirm that they are available:

```bash
git --version
docker --version
docker compose version
curl --version
docker info
```

The default local ports are `127.0.0.1:18080` for HTTP and
`127.0.0.1:18443` for HTTPS.

## 2. Without Signing Certificate

Set one absolute installation directory so repeating the commands does not
create nested copies. The installer downloads the pinned DefectDojo source
beside the library:

```bash
INSTALL_ROOT="$HOME/dojo-permission"
mkdir -p "$INSTALL_ROOT"
cd "$INSTALL_ROOT"

git clone \
  https://github.com/madhav-dhungana/dojo-permission-adon.git

cd "$INSTALL_ROOT/dojo-permission-adon"
bash deploy/install-local.sh
```

The resulting layout is:

```text
dojo-permission/
├── dojo-permission-adon/
└── defectdojo-2.58.3/
```

Do not repeat `mkdir -p dojo-permission` and `cd dojo-permission` after you are
already inside either repository. Repeating those commands creates nested
copies instead of updating the original checkout.

## 3. With Signing Certificate

Use this choice when HTTPS traffic is signed by an additional certificate.
Set absolute paths for the current machine and download both repositories:

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
```

Make the required certificate, volume, environment, or proxy changes in:

```text
defectdojo-2.58.3/docker-compose.yml
```

Then run the one-shot installer:

```bash
cd "$INSTALL_ROOT/dojo-permission-adon"
bash deploy/install-local.sh --with-signing-certificate
```

The option indicates that the downloaded Compose configuration has been
prepared for the signing certificate. The installer reads that configuration
and does not reset or overwrite the DefectDojo checkout. It does not accept a
certificate path; configure the checkout before running it.

The checkout's `.env` file is intentionally ignored. If
`docker-compose.yml` contains a value such as `${SIGNING_CERTIFICATE}`, export
that variable in the same shell before running the installer, as shown above.
Literal paths in the Compose file do not need a shell variable.

## 4. What the installer does

`install-local.sh` automatically uses the sibling
`../defectdojo-2.58.3` path. Without Signing Certificate, it creates that
checkout if needed, verifies the exact official revision, and builds the
DefectDojo images. With Signing Certificate, it requires the checkout you
prepared and pulls the exact DefectDojo 2.58.3, PostgreSQL, and Valkey images.

Both choices then:

1. verify DefectDojo 2.58.3, Django 5.2.14, and Python 3.13;
2. build the thin Django image containing the permission library;
3. start the complete local stack;
4. apply the library migration; and
5. wait for and validate the login page.

The installer generates a stable, path-specific Compose project name. This
avoids collisions with an unrelated stack that used the old fixed
`dojo-permission-dev` name. It does not run `docker compose down`, delete
volumes or application data, reset either checkout, or remove other Docker
resources. A separate project name does not make an occupied host port
available.

A direct `docker compose up` is not enough for the first installation because
it does not perform the permission-image build, migration, and validation.

With Signing Certificate applies Compose changes but uses a prebuilt
DefectDojo 2.58.3 application image. Changes to DefectDojo Python source files
in that checkout are not built into the image. To extend an already customized
image, use the [existing-deployment guide](DEPLOYMENT.md).

## 5. Installer commands

The user-facing commands are intentionally small:

| Command | Purpose |
| --- | --- |
| `bash deploy/install-local.sh` | Install Without Signing Certificate. |
| `bash deploy/install-local.sh --without-signing-certificate` | Explicitly install Without Signing Certificate. |
| `bash deploy/install-local.sh --with-signing-certificate` | Install using the signing-certificate settings already added to DefectDojo's Compose file. |
| `bash deploy/install-local.sh --dry-run` | Print the resolved paths and lower-level command without changing Docker state. |
| `bash deploy/install-local.sh --help` | Display usage. |

The lower-level bootstrap remains available for advanced diagnostics:

```bash
bash deploy/bootstrap-local-docker.sh --help
```

## 6. Signing certificate boundaries

The same signing certificate may be needed by three separate TLS clients:

| TLS client | Configuration |
| --- | --- |
| Host Git | Use `git -c http.sslCAInfo="$SIGNING_CERTIFICATE" clone ...`. |
| Docker daemon or Docker Desktop | Add the signing certificate to Docker's trusted certificates and restart Docker before pulling images. |
| DefectDojo Python services | Configure the `uwsgi`, `initializer`, `celeryworker`, and `celerybeat` services in the downloaded Compose file before running the installer. |

These settings are independent. In particular, `REQUESTS_CA_BUNDLE` and
`SSL_CERT_FILE` inside a container cannot fix this daemon-side error:

```text
x509: certificate signed by unknown authority
```

If that error occurs during `docker pull`, configure Docker or Docker Desktop
trust and restart it. Do not disable certificate verification.

## 7. Result and normal operation

On success, open:

<http://127.0.0.1:18080/login>

The first initializer run prints the generated `admin` password. The installer
also prints exact commands for checking this stack and resetting the password.

It is safe to rerun the same install command. Missing containers may be
recreated, but the installer does not delete persistent volumes or data. Never
add `--volumes` to a removal command unless deleting the local database and
uploads is intentional.

This is a local evaluation stack with sample secrets. Both published ports
bind only to `127.0.0.1`. Do not expose it directly to the Internet.

## 8. Troubleshooting

### The path contains repeated `dojo-permission` directories

The clone commands were probably run while already inside a previous library
checkout. Return to the intended parent directory and keep one copy of each
repository in the sibling layout shown above. `install-local.sh` resolves its
own library path, so it does not need to be launched from nested directories.

### Docker reports unowned resources for `dojo-permission-dev`

The installer no longer uses that fixed project name by default. It generates
a project name from the installation path, so old unrelated resources remain
untouched. Intentionally choosing a different absolute installation path
produces a different project name. Do not delete an existing stack until its
ownership and data are confirmed.

### Port 18080 or 18443 is already allocated

Stop the known old stack using its original checkout and Compose command, or
select unused loopback ports for the new stack. The installer does not stop or
adopt the old stack:

```bash
DD_PORT=18081 DD_TLS_PORT=18444 bash deploy/install-local.sh
```

For With Signing Certificate, keep the choice flag:

```bash
DD_PORT=18081 DD_TLS_PORT=18444 \
  bash deploy/install-local.sh --with-signing-certificate
```

### DefectDojo outbound HTTPS reports an unknown certificate authority

Confirm that the downloaded Compose configuration supplies the correct
certificate mount and environment variables, then rerun the same installer
command so the Python services are recreated with those settings.
