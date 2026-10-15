# Local Docker bootstrap

This guide creates a local DefectDojo 2.58.3 staging stack with the Dojo
Permission Library installed. It supports a pinned clean-source build and two
ways to reuse an existing DefectDojo checkout.

This workflow is for a new local staging stack. Do not point it at a running
customized deployment. For an existing deployment, use the
[existing-deployment guide](DEPLOYMENT.md).

## 1. Prerequisites

Install and start:

- Git;
- Docker Engine or Docker Desktop;
- Docker Compose 2.24.4 or newer; and
- curl.

Confirm that Docker is available:

```bash
git --version
docker --version
docker compose version
curl --version
docker info
```

The default local ports are `127.0.0.1:18080` for HTTP and
`127.0.0.1:18443` for HTTPS.

## 2. Clone the library

For a normal Internet connection:

```bash
mkdir -p dojo-permission
cd dojo-permission

git clone \
  https://github.com/madhav-dhungana/dojo-permission-adon.git

cd dojo-permission-adon
```

If HTTPS is inspected by a corporate proxy, use the combined corporate and
public CA bundle for this first clone. The bootstrap script cannot configure a
clone that happens before the script is available.

```bash
CORPORATE_CA="/absolute/path/to/rak-ca-bundle.pem"
test -r "$CORPORATE_CA"

mkdir -p dojo-permission
cd dojo-permission

git -c http.sslCAInfo="$CORPORATE_CA" clone \
  https://github.com/madhav-dhungana/dojo-permission-adon.git

cd dojo-permission-adon
```

Use the CA bundle path for the current machine. Do not copy a private
certificate into this repository, and do not disable TLS verification.

## 3. Choose a bootstrap mode

| Mode | Command options | Use it when |
| --- | --- | --- |
| Strict source build | `--image-source build` (default) | DefectDojo is not downloaded yet and the machine can clone and build the pinned source. |
| Existing checkout, local images | `--existing-defectdojo-dir DIR --image-source local` | The checkout may be edited and the required DefectDojo 2.58.3, PostgreSQL, and Valkey images are already in the local Docker daemon. The script performs no registry pulls. |
| Existing checkout, remote images | `--existing-defectdojo-dir DIR --image-source pull` | The checkout may be edited and Docker should pull the required DefectDojo 2.58.3, PostgreSQL, and Valkey images before building the permission image. |

The strict mode enforces the official `2.58.3` tag, its pinned commit, and a
clean Git tree. The two existing-checkout modes explicitly relax only those
source-tree checks: the checkout may be dirty or may not be a Git repository.
They still verify the actual Django image as DefectDojo 2.58.3, Django 5.2.14,
and Python 3.13 before starting the stack.

Existing-checkout mode reads the checkout's Compose configuration but uses a
prebuilt DefectDojo 2.58.3 Django image. Compose edits are applied; changes to
DefectDojo application source files in the checkout are not built into that
image. To extend an already customized Django image, use the
[existing-deployment guide](DEPLOYMENT.md) instead.

All modes build a thin local Django image containing this library. The build
installs the local package with dependencies disabled; it does not fetch the
package from PyPI.

### Mode A: strict source build

Run this from the library checkout:

```bash
bash deploy/bootstrap-local-docker.sh
```

With a corporate CA bundle:

```bash
CORPORATE_CA="/absolute/path/to/rak-ca-bundle.pem"

bash deploy/bootstrap-local-docker.sh \
  --ca-bundle "$CORPORATE_CA"
```

The script clones DefectDojo 2.58.3 beside the library checkout, verifies the
pinned source revision, and builds the DefectDojo and permission images.
`--ca-bundle` supplies trust to that Git clone and to the running Python
services. It does not inject the CA into third-party DefectDojo Dockerfile
build steps. If package downloads inside the source build are intercepted,
use an existing checkout with `--image-source local` or `pull` instead of this
mode.

### Mode B: use an existing checkout and local images

Use this after the exact DefectDojo images have already been pulled
successfully:

```bash
CORPORATE_CA="/absolute/path/to/rak-ca-bundle.pem"
DEFECTDOJO_DIR="/absolute/path/to/defectdojo-2.58.3"

test -r "$CORPORATE_CA"
test -f "$DEFECTDOJO_DIR/docker-compose.yml"

bash deploy/bootstrap-local-docker.sh \
  --existing-defectdojo-dir "$DEFECTDOJO_DIR" \
  --image-source local \
  --ca-bundle "$CORPORATE_CA"
```

This mode requires these exact DefectDojo tags to exist in the local Docker
daemon:

```text
defectdojo/defectdojo-django:2.58.3
defectdojo/defectdojo-nginx:2.58.3
```

The merged Compose configuration's PostgreSQL and Valkey images must also be
local. The script checks those four inputs and performs no registry pulls in
this mode. It verifies the DefectDojo runtime contents before using them. This
is the recommended corporate-PC mode after an approved image pull has
succeeded.

### Mode C: use an existing checkout and pull images

Use this when the Docker daemon is already configured to trust the corporate
CA and should fetch the exact images:

```bash
CORPORATE_CA="/absolute/path/to/rak-ca-bundle.pem"
DEFECTDOJO_DIR="/absolute/path/to/defectdojo-2.58.3"

test -r "$CORPORATE_CA"
test -f "$DEFECTDOJO_DIR/docker-compose.yml"

bash deploy/bootstrap-local-docker.sh \
  --existing-defectdojo-dir "$DEFECTDOJO_DIR" \
  --image-source pull \
  --ca-bundle "$CORPORATE_CA"
```

This mode pulls the official DefectDojo Django and nginx `2.58.3` tags plus the
PostgreSQL and Valkey images from the merged Compose configuration. It then
performs the same DefectDojo runtime verification as local-image mode.

## 4. Add office-specific Compose configuration

In either existing-checkout mode, add each extra Compose file in the same
order used for normal local operations:

```bash
CORPORATE_CA="/absolute/path/to/rak-ca-bundle.pem"
DEFECTDOJO_DIR="/absolute/path/to/defectdojo-2.58.3"

bash deploy/bootstrap-local-docker.sh \
  --existing-defectdojo-dir "$DEFECTDOJO_DIR" \
  --image-source local \
  --compose-file "$DEFECTDOJO_DIR/docker-compose.corporate.yml" \
  --ca-bundle "$CORPORATE_CA"
```

`--compose-file` is repeatable. The script merges files in this order:

1. the existing checkout's `docker-compose.yml`;
2. each `--compose-file` in command-line order;
3. the permission-library overlay;
4. the prebuilt-image helper overlay; and
5. the CA overlay when `--ca-bundle` is supplied.

Non-conflicting office-specific settings remain in the merged configuration.
The later permission overlays intentionally control the Django and nginx
images, loopback-only published ports, extra-settings mount, and CA mount. The
permission overlay uses Compose `!override` for the `uwsgi` build definition,
replacing any checkout or office build definition with the thin library build
based on the selected prebuilt image. The script validates the final
configuration before starting services. It does not reset, clean, check out,
or overwrite the existing DefectDojo directory.

Existing-checkout mode deliberately ignores an `.env` file in that directory
and uses controlled local-staging values for the database, broker, generated
administrator, and published ports. Put required non-conflicting Compose
changes in files supplied with `--compose-file`; do not rely on the checkout's
`.env` being loaded.

## 5. Understand the three certificate trust boundaries

A corporate CA may be needed in three independent places:

| TLS client | How trust is configured | What it covers |
| --- | --- | --- |
| Host Git and bootstrap curl | Use `git -c http.sslCAInfo="$CORPORATE_CA" ...` for the initial library clone, then pass `--ca-bundle "$CORPORATE_CA"` | The script uses the supplied CA for its strict-mode DefectDojo clone and for an HTTPS health check. |
| Docker daemon or Docker Desktop | Install the corporate signing CA in the Docker daemon/Desktop trust store, then restart Docker | Pulling DefectDojo, PostgreSQL, Valkey, and other service images. Container environment variables cannot repair a daemon-side `x509: certificate signed by unknown authority` error. |
| DefectDojo Python services | `--ca-bundle "$CORPORATE_CA"` | Outbound HTTPS from web, initializer, worker, and beat containers. |

The `--ca-bundle` option validates the file and saves a managed copy under the
library checkout's ignored `.dojo-permission-local` directory. It uses the
bundle for eligible host-side Git and curl requests, mounts the managed copy
read-only inside the Python services, and sets both:

```text
REQUESTS_CA_BUNDLE=/etc/ssl/certs/dojo-permission-ca-bundle.pem
SSL_CERT_FILE=/etc/ssl/certs/dojo-permission-ca-bundle.pem
```

The variable is `REQUESTS_CA_BUNDLE`, with an `S`. The bundle should contain
the corporate signing CA and normal public roots. It must not contain a
private key.

If `docker pull` fails certificate verification, configure Docker's trust
store first. Passing `--ca-bundle` cannot affect Docker daemon traffic. As an
alternative, pull the exact images through the organization's approved method
and then use `--image-source local`.

## 6. Result and normal operation

On success, open:

<http://127.0.0.1:18080/login>

The first initializer run prints the generated `admin` password. The script
also prints the exact Compose status command for the chosen checkout, project,
extra Compose files, and CA configuration.

Run the bootstrap again with the same arguments to rebuild or recreate missing
containers. It may recreate service containers, but it does not run
`docker compose down` or delete persistent volumes or application data.

Display every option with:

```bash
bash deploy/bootstrap-local-docker.sh --help
```

This local stack uses DefectDojo's evaluation Compose configuration and sample
secrets. Both published ports bind only to `127.0.0.1`. Do not expose the stack
directly to the Internet.

## 7. Troubleshooting

### The existing checkout is rejected for its commit, tag, or changes

Use the explicit existing-checkout mode instead of `--defectdojo-dir`:

```bash
bash deploy/bootstrap-local-docker.sh \
  --existing-defectdojo-dir "/absolute/path/to/defectdojo-2.58.3" \
  --image-source local
```

`--defectdojo-dir` belongs to strict mode and retains pinned source
enforcement.

### Local-image mode cannot find an image

Confirm both exact tags are present:

```bash
docker image inspect defectdojo/defectdojo-django:2.58.3
docker image inspect defectdojo/defectdojo-nginx:2.58.3
```

Pull them through the approved network path or select `--image-source pull`
after Docker trust is configured.

### Docker reports an unknown certificate authority

This is a Docker daemon trust problem when it occurs during an image pull.
Install the corporate signing CA for Docker and restart Docker. Do not use an
insecure registry setting and do not disable certificate verification.

### DefectDojo outbound HTTPS reports an unknown certificate authority

Confirm the same readable combined bundle was passed with `--ca-bundle`, then
rerun the bootstrap with the same arguments so the affected services are
recreated with the CA mount and environment variables.
