// Prometheus metrics for the browser-downloader microservice.
//
// The worker side is fully instrumented (prometheus_client); this module gives
// the microservice the same visibility: per-terminal-status download counters
// (with tier + error code) and a duration histogram. `GET /metrics` is served
// from server.js. Metrics never throw — recording failures are swallowed so a
// misconfigured registry cannot break downloads.

import { Counter, Histogram, Registry } from 'prom-client';

export const registry = new Registry();

export const downloadsTotal = new Counter({
  name: 'bd_downloads_total',
  help: 'Total download requests by terminal status, tier used, and error code.',
  labelNames: ['status', 'tier', 'error'],
  registers: [registry],
});

export const downloadDurationSeconds = new Histogram({
  name: 'bd_download_duration_seconds',
  help: 'Duration of download requests by terminal status.',
  labelNames: ['status'],
  buckets: [1, 5, 15, 30, 60, 120, 240, 480],
  registers: [registry],
});

/**
 * Records one terminal download outcome. Best-effort: a throwing metrics
 * library must never break the request path.
 * @param {string} status - `success` or `failed`.
 * @param {number|string|null} tier - Tier used (1/2) or null.
 * @param {string|null} error - Microservice error code or null on success.
 * @param {number} startMs - `performance.now()` timestamp at request start.
 */
export function recordDownload(status, tier, error, startMs) {
  try {
    downloadsTotal
      .labels({
        status,
        tier: tier == null ? 'none' : String(tier),
        error: error ?? 'none',
      })
      .inc();
    downloadDurationSeconds.labels({ status }).observe((performance.now() - startMs) / 1000);
  } catch {
    /* metrics are best-effort */
  }
}
