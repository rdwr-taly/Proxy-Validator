# Stage 1: Builder Stage - Install dependencies including build tools
FROM python:3.11-slim AS builder

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        build-essential \
        gcc \
        libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install poetry

# Verify Poetry version
RUN poetry --version

# Copy dependency definition files
COPY pyproject.toml poetry.lock ./

# Install project dependencies (including aiohttp needed by validator)
RUN poetry install --no-root

# Copy the rest of the application code (including proXXy.py)
COPY . /app/

# Install the project itself
RUN poetry install

# ---

# Stage 2: Runtime Stage - Create the final lean image
FROM python:3.11-slim AS runtime

# Set environment variables for runtime and validation script
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    # --- Add ENV VARS for validation script ---
    VALIDATION_TARGET_URL="http://httpbin.org/ip" \
    VALIDATION_TIMEOUT="5" \
    VALIDATION_CONCURRENCY="100"

# Install runtime deps + showrunner-sdk
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl rsync openssh-client jq sshpass && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir poetry aiohttp "showrunner-sdk[full]>=0.1.0"

# Create a non-root user and group for security
ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} proxxy_user && \
    useradd -u ${UID} -g ${GID} -m -s /bin/bash proxxy_user

WORKDIR /app

# Change ownership before copying
RUN chown proxxy_user:proxxy_user /app

# Copy installed dependencies and application from the builder stage
COPY --from=builder --chown=proxxy_user:proxxy_user /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder --chown=proxxy_user:proxxy_user /app /app

# ----> ADD: Copy validator and entrypoint scripts <----
COPY --chown=proxxy_user:proxxy_user validate_proxies.py /app/validate_proxies.py
COPY --chown=proxxy_user:proxxy_user entrypoint.sh /usr/local/bin/entrypoint.sh

# ----> ADD: Make entrypoint script executable <----
RUN chmod +x /usr/local/bin/entrypoint.sh

# Copy ShowRunner entry point
COPY --chown=proxxy_user:proxxy_user main.py /app/main.py

# Create config mount point
RUN mkdir -p /config && chown proxxy_user:proxxy_user /config

# Switch to the non-root user
USER proxxy_user

EXPOSE 9090

ENTRYPOINT ["python", "main.py"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD curl -sf http://127.0.0.1:9090/healthz || exit 1
