// HTTP API.
//
// Contract (KEEP): `POST /download { url, output_dir }` →
// `{ status, file_path?, error?, tier_used? }`; `GET /health` → `{ status }`.
//
// Error handling (mandatory): client errors (malformed JSON, missing fields,
// 413 payload-too-large, validation failures) → HTTP 400; downloader failures
// → HTTP 502; concurrency overflow → HTTP 503. Every error is logged with its
// stack. The server `error` event (e.g. EADDRINUSE) is handled to avoid an
// unhandled crash.

import { pathToFileURL } from 'node:url';

import express from 'express';

import { ConcurrencyLimitError, createSemaphore } from './concurrency.js';
import { download } from './downloader.js';
import { classifyError } from './errors.js';
import { parseTimeout, validateOutputDir, validateUrl } from './validate.js';

const PORT = Number(process.env.BD_PORT) || 3000;
const MAX_CONCURRENCY = 2;
const BODY_LIMIT = '1mb';
// Per-request overall timeout. With max concurrency=2, two slow targets could
// otherwise block the service indefinitely. The timeout races the download and
// releases the semaphore slot when it fires (env BD_REQUEST_TIMEOUT_MS).
const DEFAULT_REQUEST_TIMEOUT_MS = 300_000;

const limiter = createSemaphore(MAX_CONCURRENCY);

function safeClassify(err) {
  try {
    return classifyError(err);
  } catch {
    return 'network_error';
  }
}

export function createApp() {
  const app = express();
  app.use(express.json({ limit: BODY_LIMIT }));

  app.get('/health', (_req, res) => {
    res.json({ status: 'ok' });
  });

  app.post('/download', async (req, res) => {
    const body = req.body;
    if (!body || typeof body !== 'object') {
      return res.status(400).json({
        status: 'failed',
        error: 'invalid_request',
        message: 'request body must be JSON object',
      });
    }
    const { url, output_dir } = body;
    if (typeof url !== 'string' || typeof output_dir !== 'string') {
      return res.status(400).json({
        status: 'failed',
        error: 'invalid_request',
        message: 'url and output_dir are required strings',
      });
    }

    try {
      await validateUrl(url);
      await validateOutputDir(output_dir);
    } catch (err) {
      return res
        .status(400)
        .json({ status: 'failed', error: 'invalid_request', message: err.message });
    }

    const tier1Timeout = parseTimeout(process.env.BD_TIER1_TIMEOUT_MS, 30_000);
    const tier2Timeout = parseTimeout(process.env.BD_TIER2_TIMEOUT_MS, 30_000);

    let release;
    try {
      release = limiter.acquire();
    } catch (err) {
      if (err instanceof ConcurrencyLimitError) {
        return res.status(503).json({ status: 'failed', error: 'concurrency_limit' });
      }
      console.error('[download] concurrency acquire error:', err);
      return res.status(503).json({ status: 'failed', error: 'concurrency_limit' });
    }

    let requestTimer = null;
    try {
      // Race the download against an overall request timeout. If the download
      // hangs (e.g. a hung browser/streamlink), the timeout rejects so the
      // finally block releases the semaphore slot instead of holding it
      // forever. Read per-request so tests can override via env.
      const requestTimeout = parseTimeout(
        process.env.BD_REQUEST_TIMEOUT_MS,
        DEFAULT_REQUEST_TIMEOUT_MS,
      );
      const result = await Promise.race([
        download(url, output_dir, { tier1Timeout, tier2Timeout }),
        new Promise((_, reject) => {
          requestTimer = setTimeout(() => reject(new Error('request_timeout')), requestTimeout);
        }),
      ]);
      if (result.status === 'success') {
        return res.status(200).json(result);
      }
      return res.status(502).json(result);
    } catch (err) {
      console.error('[download] unexpected error:', err);
      return res.status(502).json({ status: 'failed', error: safeClassify(err), tier_used: null });
    } finally {
      if (requestTimer) {
        clearTimeout(requestTimer);
      }
      release();
    }
  });

  // Body-parser / JSON errors → 400 (malformed JSON, 413 too large).
  app.use((err, _req, res, next) => {
    if (
      err &&
      (err.type === 'entity.parse.failed' ||
        err.type === 'entity.too.large' ||
        err.status === 400 ||
        err.status === 413)
    ) {
      return res
        .status(400)
        .json({ status: 'failed', error: 'invalid_request', message: err.message });
    }
    return next(err);
  });

  // Final error handler → 502, logged with stack.
  app.use((err, _req, res, _next) => {
    console.error('[server] unhandled error:', err);
    return res.status(502).json({ status: 'failed', error: safeClassify(err) });
  });

  return app;
}

export function startServer(app, port = PORT) {
  const server = app.listen(port, () => {
    console.log(`browser-downloader listening on :${port}`);
  });
  server.on('error', (err) => {
    // Any listen-time error is fatal — EADDRINUSE, EACCES (permission denied),
    // or any other bind failure. Log and exit rather than crashing unhandled.
    console.error(`[server] listen error: ${err.code || ''} ${err.message}`, err.stack);
    process.exit(1);
  });
  return server;
}

// Use pathToFileURL so paths with spaces or non-ASCII compare correctly.
const mainHref = process.argv[1] ? pathToFileURL(process.argv[1]).href : '';
const isMain = import.meta.url === mainHref;
if (isMain) {
  startServer(createApp(), PORT);
}
