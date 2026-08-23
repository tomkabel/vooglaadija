# Browser downloader microservice — Phase 0.
# Based on the Playwright glibc image (real Chromium + real fonts), with system
# ffmpeg and streamlink for HLS/DASH. Runs as non-root UID 1000.
# Digest-pinned: v1.55.1-jammy (Ubuntu 22.04, still in standard support).
FROM mcr.microsoft.com/playwright:v1.55.1-jammy@sha256:896d18fd3b82436ceb92c4d3040f1c2f2c316100ebf159583a8a0a455418609d

ENV DEBIAN_FRONTEND=noninteractive \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    PNPM_HOME=/tmp/pnpm \
    PATH=/tmp/pnpm:$PATH \
    NODE_ENV=production

# System ffmpeg + streamlink + curl (for HEALTHCHECK).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
        python3 \
        python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install --no-cache-dir streamlink==7.2.0 \
    && npm install -g pnpm@10.33.2

WORKDIR /app

# pnpm workspace layout: the package lives at packages/browser-downloader, so
# the workspace manifests must be copied before install (repo-root build
# context). Only this package's production deps are installed.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages/browser-downloader/package.json ./packages/browser-downloader/

# Install production deps only. Skip scripts so the `playwright` package does
# not re-download browsers that the base image already ships.
RUN pnpm install --prod --ignore-scripts --frozen-lockfile --filter browser-downloader \
    && rm -rf /tmp/pnpm/store

COPY packages/browser-downloader/src ./packages/browser-downloader/src

# Output bind-mount target + non-root ownership (UID/GID 1000).
RUN mkdir -p /output \
    && chown -R 1000:1000 /app /output

USER 1000:1000

ENV BD_OUTPUT_BASE=/output \
    HOME=/home/pwuser

WORKDIR /app/packages/browser-downloader

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:3000/health || exit 1

CMD ["node", "src/server.js"]
