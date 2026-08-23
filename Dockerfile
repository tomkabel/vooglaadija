# syntax=docker/dockerfile:1.7
# ============================================
# Optimized Production Dockerfile - BuildKit 1.7+
# UV-based builds, native SBOM, non-root, cache mounts
# ============================================

# ============================================
# Stage 1: Python Dependency Builder
# ============================================
FROM python@sha256:6c4dd321d176d61ea848dc8c73a4f7dbae8f70e0ee48bb411ea2f045b599fa8e AS python-builder
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system build dependencies with apt cache mount
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# Install uv binary (single static binary, ~25MB, not copied to final image)
COPY --from=ghcr.io/astral-sh/uv@sha256:4a6c9444b126bd325fba904bff796bf91fb777bf6148d60109c4cb1de2ffc497 /uv /bin/uv

# Copy manifest and lockfile first → cacheable dependency layer
COPY pyproject.toml uv.lock ./

# Install runtime dependencies ONLY (no project code yet)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# ============================================
# Stage 2: Frontend Builder
# ============================================
FROM node@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS frontend-builder
WORKDIR /app

# Install pnpm for package management (version pinned in frontend/package.json packageManager field)
RUN corepack enable && corepack prepare pnpm@10.33.2 --activate

# Copy frontend package files and pnpm lockfile to frontend subdirectory
COPY frontend/package*.json frontend/pnpm-lock.yaml ./frontend/

# Install frontend dependencies using pnpm from the frontend directory
# Use --frozen-lockfile for reproducible builds; if lockfile has outdated checksums
# due to republished packages (npmjs.org quirk), prune store and retry with no-frozen
WORKDIR /app/frontend
RUN --mount=type=cache,target=/root/.local/share/pnpm/store pnpm install --frozen-lockfile || \
    (pnpm store prune && pnpm install --no-frozen-lockfile)

# Copy Tailwind config
COPY frontend/tailwind.config.js ./tailwind.config.js
COPY frontend/postcss.config.js ./postcss.config.js
COPY frontend/.browserslistrc ./.browserslistrc

# Copy source templates for Tailwind scanning
COPY app/templates ./app/templates

# Copy CSS source
COPY frontend/css ./css

# Build Tailwind CSS
RUN pnpm run build

# ============================================
# Stage 3: Application Builder
# ============================================
FROM python-builder AS app-builder

# Copy source code (this invalidates frequently, but deps are already cached)
COPY app ./app
COPY worker ./worker
COPY core ./core
COPY scripts ./scripts
COPY alembic.ini .
COPY alembic ./alembic

# Install the local package (wheel build only, no dependency resolution)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

# Copy built frontend assets
COPY --link --from=frontend-builder /app/frontend/css/dist/styles.css /app/app/static/css/styles.css
COPY --link --from=frontend-builder /app/frontend/node_modules/htmx.org/dist/htmx.min.js /app/app/static/js/htmx.min.js

# Download Swagger UI assets (version 5.32.5 - exact pin for SRI integrity)
RUN mkdir -p /app/app/static/swagger && \
    curl -fsSL https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.32.5/swagger-ui-bundle.js -o /app/app/static/swagger/swagger-ui-bundle.js && \
    curl -fsSL https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.32.5/swagger-ui.css -o /app/app/static/swagger/swagger-ui.css && \
    echo "0028baa75a6060bac3a81329f501985abbdc1d527a5c16ac87977fb8722684d27a0092ae437ab3be434867ae18f9156d  /app/app/static/swagger/swagger-ui-bundle.js" | sha384sum -c - && \
    echo "f50d9fa52fb1792e1f7c9cba09a827c28525fb895d01884eb3da6066e10ac72a5532876199917378c96f56c0237fbb93  /app/app/static/swagger/swagger-ui.css" | sha384sum -c -

# ============================================
# Stage 4: Runtime Base
# ============================================
FROM python@sha256:6c4dd321d176d61ea848dc8c73a4f7dbae8f70e0ee48bb411ea2f045b599fa8e AS runtime-base
ENV PYTHONDONTWRITEBYTECODE=1

# Install runtime dependencies with apt cache mounts
# Node.js is required for yt-dlp to solve YouTube video signatures (JS-based decryption)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    redis-tools \
    curl \
    gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key -o /tmp/nodesource-repo.gpg.key \
    && echo "b42e0321dabdc24e892115da705cf061167eac12a317f23d329862d0aa0a271d  /tmp/nodesource-repo.gpg.key" | sha256sum -c - \
    && gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg /tmp/nodesource-repo.gpg.key \
    && rm /tmp/nodesource-repo.gpg.key \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Copy Python runtime from builder (with dependencies installed)
COPY --from=app-builder /opt/venv /opt/venv

WORKDIR /app

# Copy application code
COPY --from=app-builder /app/app ./app
COPY --from=app-builder /app/pyproject.toml ./pyproject.toml
COPY --from=app-builder /app/worker ./worker
COPY --from=app-builder /app/core ./core
COPY --from=app-builder /app/scripts ./scripts
COPY --from=app-builder /app/alembic.ini /app/alembic.ini
COPY --from=app-builder /app/alembic /app/alembic

# Create non-root user
RUN groupadd -r appuser -g 1000 && \
    useradd -r -g appuser -u 1000 appuser

# Create storage directory and set ownership
RUN mkdir -p /app/storage && \
    chown -R appuser:appuser /app /opt/venv

# ============================================
# Stage 5: API Service
# ============================================
FROM runtime-base AS api
WORKDIR /app
ENV PYTHONPATH=/app \
    PATH=/opt/venv/bin:$PATH \
    STORAGE_PATH=/app/storage

COPY entrypoint.sh /app/entrypoint.sh
COPY migrate.sh /app/migrate.sh
COPY seed_demo.sh /app/seed_demo.sh
RUN chmod +x /app/entrypoint.sh /app/migrate.sh /app/seed_demo.sh && \
    chown appuser:appuser /app/entrypoint.sh /app/migrate.sh /app/seed_demo.sh

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["curl", "-fsS", "-o", "/dev/null", "http://localhost:8000/health"]

EXPOSE 8000

ARG GIT_SHA
LABEL org.opencontainers.image.source="https://github.com/team21/vooglaadija" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.description="YouTube Link Processor API" \
      org.opencontainers.image.licenses="GPL-3.0" \
      org.opencontainers.image.revision=${GIT_SHA:-unknown}

# Note: When using bind mounts (not named volumes), the host directory must be
# pre-created with UID/GID 1000 ownership, or the container will fail to write.
# docker-compose.yml uses named volumes which correctly inherit image ownership.
USER appuser
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["/opt/venv/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

# ============================================
# Stage 6: Worker Service
# ============================================
FROM runtime-base AS worker
WORKDIR /app
ARG GIT_SHA
ENV PYTHONPATH=/app \
    PATH=/opt/venv/bin:$PATH \
    STORAGE_PATH=/app/storage

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["curl", "-fsS", "-o", "/dev/null", "http://localhost:8082/health"]

EXPOSE 8082

COPY --from=app-builder /app/worker/entrypoint-worker.sh ./entrypoint-worker.sh
COPY migrate.sh /app/migrate.sh
RUN chmod +x ./entrypoint-worker.sh /app/migrate.sh && \
    chown appuser:appuser ./entrypoint-worker.sh /app/migrate.sh

# Note: When using bind mounts (not named volumes), the host directory must be
# pre-created with UID/GID 1000 ownership, or the container will fail to write.
# docker-compose.yml uses named volumes which correctly inherit image ownership.
USER appuser

LABEL org.opencontainers.image.source="https://github.com/team21/vooglaadija" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.description="YouTube Link Processor Worker" \
      org.opencontainers.image.licenses="GPL-3.0" \
      org.opencontainers.image.revision=${GIT_SHA:-unknown}

ENTRYPOINT ["./entrypoint-worker.sh"]
