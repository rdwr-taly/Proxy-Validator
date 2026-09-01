# Stage 1: Builder — install dependencies with build tools
FROM python:3.11-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on

WORKDIR /app

# System build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl build-essential gcc libssl-dev git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies via pip (exported from poetry)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Install showrunner-sdk (git dep, not in requirements.txt export)
RUN pip install --no-cache-dir "showrunner-sdk[full] @ git+https://github.com/rdwr-taly/showrunner-sdk.git@main"

# Copy application code
COPY . /app/

# ---

# Stage 2: Runtime — lean final image
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    VALIDATION_TARGET_URL="https://httpbin.org/ip" \
    VALIDATION_TIMEOUT="6" \
    VALIDATION_CONCURRENCY="200"

# Runtime system deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl rsync openssh-client jq sshpass && \
    rm -rf /var/lib/apt/lists/*

# Non-root user
ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} proxxy_user && \
    useradd -u ${UID} -g ${GID} -m -s /bin/bash proxxy_user

WORKDIR /app
RUN chown proxxy_user:proxxy_user /app

# Copy installed packages and app from builder
COPY --from=builder --chown=proxxy_user:proxxy_user /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder --chown=proxxy_user:proxxy_user /usr/local/bin /usr/local/bin
COPY --from=builder --chown=proxxy_user:proxxy_user /app /app

# Copy validator and entrypoint
COPY --chown=proxxy_user:proxxy_user validate_proxies.py /app/validate_proxies.py
COPY --chown=proxxy_user:proxxy_user entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# ShowRunner entry point
COPY --chown=proxxy_user:proxxy_user main.py /app/main.py
# SR3 report writer
COPY --chown=proxxy_user:proxxy_user report.py /app/report.py

# Config mount point for SDK
RUN mkdir -p /config && chown proxxy_user:proxxy_user /config

# SR3: writable dir for the report ShowRunner pulls (/report/report.json).
RUN mkdir -p /report && chown proxxy_user:proxxy_user /report

USER proxxy_user

EXPOSE 9090

ENTRYPOINT ["python", "main.py"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD curl -sf http://127.0.0.1:9090/healthz || exit 1
