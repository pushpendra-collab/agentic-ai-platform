# Application image (Phase 7, ADR-050; hardened in Phase 15, ADR-064): one image, three
# process roles.
#
#   api          python -m agentic_platform.api            (default command)
#   worker       python -m agentic_platform.maintenance --loop   (or one-shot from a scheduler)
#   migrate      alembic upgrade head                       (one-shot job, schema-owner role)
#
# No secrets are baked in: every credential arrives at run time as an environment
# variable or a secret file (APP_SECRETS_DIR, e.g. /run/secrets). The image defaults to
# APP_ENV=production, so development authentication cannot be used by accident; local
# stacks set APP_ENV explicitly.
#
#   docker build -t agentic-platform:<tag> \
#       --build-arg APP_BUILD_COMMIT=$(git rev-parse HEAD) \
#       --build-arg APP_BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ) .
#
# Reproducible by construction (Phase 15): the base image is pinned by digest, and the
# Python dependencies are installed with constraints.txt (exact versions). Update either
# deliberately, then run the full test suite and deploy/smoke/run_smoke.py.
#
# Runtime image contents: the Python runtime, the application's virtual environment
# (runtime dependencies + the OIDC extra; no pip, setuptools or wheel) and alembic.ini.
# No compiler, VCS, downloader, tests or development tools.

# python:3.11-slim-bookworm, pinned 2026-10-05. Override only with another digest.
ARG PYTHON_IMAGE=python:3.11-slim-bookworm@sha256:2333bd330d12de02514770b3585cad313644316047cdee24a7acfdece6de6efb

FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY pyproject.toml constraints.txt ./
COPY src ./src
# Non-editable install with the OIDC extra (PyJWT + cryptography); exact versions from
# constraints.txt; no dev tools. Then remove the installers from the environment. The
# bytecode pip compiled stays: the root filesystem is read-only at run time, so nothing
# could be recompiled there.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --constraint constraints.txt ".[oidc]" \
    && /opt/venv/bin/pip uninstall --yes pip setuptools wheel

FROM ${PYTHON_IMAGE} AS runtime
# Build information (core/build.py): baked in, so nothing reads git at run time.
ARG APP_BUILD_COMMIT=unknown
ARG APP_BUILD_TIME=unknown
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    APP_ENV=production \
    LOG_FORMAT=json \
    API_HOST=0.0.0.0 \
    API_PORT=8000 \
    APP_BUILD_COMMIT=${APP_BUILD_COMMIT} \
    APP_BUILD_TIME=${APP_BUILD_TIME}
# The base image's own installers are not needed at run time either.
RUN python -m pip uninstall --yes pip setuptools wheel \
    && rm -rf /usr/local/lib/python3.11/ensurepip \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home \
       --home-dir /nonexistent --shell /usr/sbin/nologin app
COPY --from=build /opt/venv /opt/venv
COPY alembic.ini /app/alembic.ini
WORKDIR /app
USER 10001:10001
EXPOSE 8000
# Python is PID 1 and handles SIGTERM itself (uvicorn; the worker installs its own
# handlers). Run with an init process (`docker run --init`, compose `init: true`) when
# the container may start child processes; the application starts none.
STOPSIGNAL SIGTERM
# Liveness only (no dependencies); orchestrators should route traffic on GET /ready.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
CMD ["python", "-m", "agentic_platform.api"]
