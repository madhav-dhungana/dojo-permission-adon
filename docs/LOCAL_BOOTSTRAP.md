# Local installation

This guide installs DefectDojo 2.58.3 and the Dojo Permission Library as one
new local staging stack. It is not for changing an already running customized
deployment. Use the [existing-deployment guide](DEPLOYMENT.md) for that case.

There are two local installation choices:

- **Without Signing Certificate**
- **With Signing Certificate**

Both choices use the same two-repository layout. Use the bootstrap for a new
installation without a signing certificate, and `install-local.sh` for a
prepared Compose file or to continue after a failed bootstrap.

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

Start from the directory where you want to keep the installation. The bootstrap
downloads the pinned DefectDojo source beside the library before building and
starting the stack:

```bash
mkdir -p dojo-permission
cd dojo-permission

git clone \
  https://github.com/madhav-dhungana/dojo-permission-adon.git

cd dojo-permission-adon
bash deploy/bootstrap-local-docker.sh
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

If a build or startup step fails after the download succeeds, the completed
`defectdojo-2.58.3` checkout stays on disk. You can edit
`../defectdojo-2.58.3/docker-compose.yml` from the library directory, then
continue with:

```bash
bash deploy/install-local.sh
```

The resume command reads that existing Compose file, accepts local edits, and
pulls the DefectDojo 2.58.3 release images. It does not require the checkout to
be clean or at an exact commit. It still validates the DefectDojo version.

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

Both scripts automatically use the sibling `../defectdojo-2.58.3` path.
`bootstrap-local-docker.sh` downloads the checkout if needed, verifies the exact
official revision, and builds the DefectDojo images.

`install-local.sh` reuses an existing checkout and its edited Compose file,
pulling the DefectDojo 2.58.3, PostgreSQL, and Valkey images. With Signing
Certificate requires this prepared checkout. With no choice flag, a missing
checkout starts the bootstrap automatically; an existing checkout uses the
resume behavior.

Both choices then:

1. verify DefectDojo 2.58.3, Django 5.2.14, and Python 3.13;
2. build the thin Django image containing the permission library;
3. start the complete local stack;
4. apply the library migration; and
5. wait for and validate the login page.

Both scripts select the same Compose project. An owned stack whose saved
installation state matches these exact library and DefectDojo paths keeps its
project name, including an existing `dojo-permission-dev` stack. Otherwise,
they generate a stable name from the checkout paths, avoiding collisions with
unrelated resources using that old fixed name. They do not run
`docker compose down`, delete volumes or application data, reset either
checkout, or remove other Docker resources. A separate project name does not
make an occupied host port available.

A direct `docker compose up` is not enough for the first installation because
it does not perform the permission-image build, migration, and validation.

Reusing an existing checkout through `install-local.sh` applies Compose changes
but uses a prebuilt DefectDojo 2.58.3 application image, with or without a
signing certificate. Changes to DefectDojo Python source files in that checkout
are not built into the image. To extend an already customized image, use the
[existing-deployment guide](DEPLOYMENT.md).

## 5. Installer commands

The user-facing commands are intentionally small:

| Command | Purpose |
| --- | --- |
| `bash deploy/bootstrap-local-docker.sh` | Start a new installation Without Signing Certificate and build the downloaded source. |
| `bash deploy/install-local.sh` | Continue using the existing, optionally edited Compose file; start the bootstrap if no checkout exists. |
| `bash deploy/install-local.sh --without-signing-certificate` | Use the same resume-or-bootstrap behavior explicitly. |
| `bash deploy/install-local.sh --with-signing-certificate` | Install using the signing-certificate settings already added to DefectDojo's Compose file. |
| `bash deploy/install-local.sh --dry-run` | Print the resolved paths and lower-level command without changing Docker state. |
| `bash deploy/install-local.sh --help` | Display usage. |

For advanced bootstrap options:

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

### Docker reports `operation not permitted` for a path inside Trash

The installation was launched from a directory that had been moved to macOS
Trash. A terminal can retain that working directory, so repeating the clone
commands there creates another installation inside Trash. Docker Desktop
cannot bind-mount its settings folder from that location.

Restore the existing installation outside Trash, or start a fresh checkout
from your intended installation folder. Open a terminal in that library
directory and use `pwd -P` to confirm the physical path before retrying. Both
scripts reject Trash paths before downloading images or starting containers.

### The path contains repeated `dojo-permission` directories

The clone commands were probably run while already inside a previous library
checkout. Return to the intended parent directory and keep one copy of each
repository in the sibling layout shown above. `install-local.sh` resolves its
own library path, so it does not need to be launched from nested directories.

### Docker reports unowned resources for `dojo-permission-dev`

Both scripts select a project from the installation paths when the old fixed
name belongs to an unrelated stack. Those unrelated resources remain
untouched. A saved, owned stack for these exact checkout paths keeps its
existing project name so the installer can continue it. Intentionally choosing
a different absolute installation path produces a different project name. Do
not delete an existing stack until its ownership and data are confirmed.

### Port 18080 or 18443 is already allocated

Stop the known old stack using its original checkout and Compose command, or
select unused loopback ports for the new stack. The installer does not stop or
adopt the old stack:

```bash
DD_PORT=18081 DD_TLS_PORT=18444 bash deploy/bootstrap-local-docker.sh
```

When continuing with an edited Compose file, use the same variables with
`bash deploy/install-local.sh`.

For With Signing Certificate, keep the choice flag:

```bash
DD_PORT=18081 DD_TLS_PORT=18444 \
  bash deploy/install-local.sh --with-signing-certificate
```

### Docker cannot find `docker-credential-desktop`

Docker needs its credential helper on `PATH` when pulling images. On macOS,
the bootstrap adds the installed Docker Desktop directory
`/Applications/Docker.app/Contents/Resources/bin` to its own process environment.
If an older checkout fails with `docker-credential-desktop: executable file not
found in $PATH`, update the library and rerun from the same library directory:

```bash
git pull --ff-only
bash deploy/bootstrap-local-docker.sh
```

This reuses the downloaded DefectDojo checkout and available build cache. If
Docker Desktop is installed somewhere else, add that installation's
`Contents/Resources/bin` directory to your shell's `PATH` before running the
script.

### nginx exits with `/entrypoint-nginx.sh: Permission denied`

An earlier bootstrap downloaded source files with private permissions, so the
nginx image's non-root user could not read its entrypoint script. Update the
library and rerun from the same library directory:

```bash
git pull --ff-only
bash deploy/bootstrap-local-docker.sh
```

The source bootstrap restores only the nginx entrypoint to `0755` after
verifying the pinned, clean checkout. New downloads use normal source-file
permissions; local stack-state files remain private. Existing-checkout modes
that use prebuilt images leave the downloaded files unchanged. An image check
also catches an unreadable entrypoint before starting services.

The existing checkout, database, and ports are reused.

### DefectDojo outbound HTTPS reports an unknown certificate authority

Confirm that the downloaded Compose configuration supplies the correct
certificate mount and environment variables, then rerun the same installer
command so the Python services are recreated with those settings.
