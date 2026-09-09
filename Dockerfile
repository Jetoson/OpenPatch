# OpenPatch server image: the API and the dashboard, from one build.

FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Dependencies first, and only the server's.
COPY requirements-server.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements-server.txt

# runtime
FROM python:3.13-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl openssl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 openpatch

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=openpatch:openpatch server/ /app/

# The Windows agent, when a build put one there - CI does, from the artefact
# the executables job produced. It makes the dashboard's deployment bundle
# complete out of the box: an endpoint then needs nothing but that zip.
COPY --chown=openpatch:openpatch packaging/agent-payload/ /app/agent-payload/

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OPENPATCH_DATA_DIR=/data \
    OPENPATCH_HOST=0.0.0.0 \
    OPENPATCH_PORT=8000

# Both owned by the unprivileged user before they become volumes.
RUN mkdir -p /data /tls && chown openpatch:openpatch /data /tls
VOLUME ["/data", "/tls"]

USER openpatch
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD case "$OPENPATCH_TLS_AUTO" in 0|false|False) auto="" ;; *) auto=1 ;; esac; \
        certs="${OPENPATCH_TLS_DIR:-${OPENPATCH_DATA_DIR:-/data}/certs}"; \
        if [ -n "$OPENPATCH_SSL_CERTFILE" ] || \
           { [ -n "$auto" ] && [ -f "$certs/server.crt" ]; }; then \
            curl -fsSk "https://127.0.0.1:${OPENPATCH_PORT:-8000}/" || exit 1; \
        else \
            curl -fsS "http://127.0.0.1:${OPENPATCH_PORT:-8000}/" || exit 1; \
        fi

# run.py migrates before serving, so a fresh volume and an upgraded image
# both start correctly without a documented manual step.
ENTRYPOINT ["python", "run.py"]
CMD ["serve"]
