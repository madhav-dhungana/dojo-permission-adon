ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS release

ARG RUNTIME_USER=1001

USER root
COPY pyproject.toml README.md /tmp/dojo-permission/
COPY src/dojo_permission /tmp/dojo-permission/src/dojo_permission
RUN python -m pip install --no-cache-dir --no-deps --no-build-isolation /tmp/dojo-permission \
    && rm -rf /tmp/dojo-permission
USER ${RUNTIME_USER}
