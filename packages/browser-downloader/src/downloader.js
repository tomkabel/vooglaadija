// Orchestrator: validate inputs, launch stealth Chromium, try Tier 1 (CDP),
// fall through to Tier 2 (DOM) only if Tier 1 resolves `null`, then write the
// file with a whitelisted extension.
//
// Tier ordering (KEEP): Tier 1 → Tier 2 fallthrough. Tier 1's internal timer
// resolves `null` (fallthrough) and is NEVER a terminal rejection, so the
// orchestrator does NOT race Tier 1 against a terminal-timeout promise.
//
// Lazy Playwright import (KEEP): `playwright-extra` + stealth are imported
// inside `download()` so the HTTP layer is unit-testable without a browser.

import { randomUUID } from 'node:crypto';
import { rm, statfs, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

import { DownloaderError, classifyError } from './errors.js';
import { downloadStream } from './streamlink-backend.js';
import { interceptMedia } from './tier1-cdp.js';
import { detectBlob } from './tier2-dom.js';
import { VIDEO_EXTS, parseTimeout, safeExt, validateOutputDir, validateUrl } from './validate.js';

const DEFAULT_TIER1_TIMEOUT_MS = 30_000;
const DEFAULT_TIER2_TIMEOUT_MS = 30_000;
// The streamlink/ffmpeg download may take much longer than Tier 1 interception.
const DEFAULT_DOWNLOAD_TIMEOUT_MS = 120_000;
const DEFAULT_BODY_CAP = 500 * 1024 * 1024;
// Refuse to launch a browser/streamlink when the output filesystem has less
// than this many free bytes (env BD_MIN_FREE_BYTES). A storage failure is
// cheap to detect up front and expensive to hit mid-download.
const DEFAULT_MIN_FREE_BYTES = 1024 * 1024 * 1024; // 1 GiB

let stealthReady = null;

/**
 * Verifies the output filesystem has enough free space before any browser or
 * subprocess is launched (each job costs a full stealth Chromium).
 * @param {string} dir - Validated output directory.
 * @return {Promise<void>}
 * @throws {DownloaderError} With code `storage_error` when free space is low.
 */
async function verifyDiskSpace(dir) {
  const minFree = parseTimeout(process.env.BD_MIN_FREE_BYTES, DEFAULT_MIN_FREE_BYTES);
  let stats;
  try {
    stats = await statfs(dir);
  } catch {
    throw new DownloaderError('storage_error', `cannot stat output filesystem: ${dir}`);
  }
  const free = BigInt(stats.bavail) * BigInt(stats.bsize);
  if (free < BigInt(minFree)) {
    throw new DownloaderError(
      'storage_error',
      `insufficient disk space (${free} free bytes < ${minFree} required)`,
    );
  }
}

/**
 * Launches a headless Chromium browser with stealth enhancements enabled.
 * @returns {Promise<import('playwright').Browser>} The launched browser instance.
 */
async function launchStealthBrowser() {
  const { chromium } = await import('playwright-extra');
  stealthReady ??= (async () => {
    const StealthPlugin = (await import('puppeteer-extra-plugin-stealth')).default;
    chromium.use(StealthPlugin());
  })();
  await stealthReady;
  return chromium.launch({ headless: true });
}

/**
 * Extract and download media from a URL into an output directory.
 * @param {string} rawUrl - The media source URL.
 * @param {string} rawOutputDir - The directory where the downloaded file is saved.
 * @param {Object} [opts] - Extraction and download options.
 * @param {AbortSignal} [opts.signal] - Cancels the download. On abort the browser
 *   is torn down immediately and the streamlink/ffmpeg subprocesses are killed,
 *   so nothing outlives the caller's request.
 * @returns {Promise<Object>} A result containing the status, output file path on success, and error code on failure.
 */
export async function download(rawUrl, rawOutputDir, opts = {}) {
  const tier1Timeout = parseTimeout(opts.tier1Timeout, DEFAULT_TIER1_TIMEOUT_MS);
  const tier2Timeout = parseTimeout(opts.tier2Timeout, DEFAULT_TIER2_TIMEOUT_MS);
  // Separate download timeout for the streamlink/ffmpeg subprocess (Tier 1
  // interception uses tier1Timeout; the download itself may take much longer).
  const downloadTimeout = parseTimeout(
    opts.downloadTimeout ?? process.env.BD_DOWNLOAD_TIMEOUT_MS,
    DEFAULT_DOWNLOAD_TIMEOUT_MS,
  );
  const bodyCap = opts.bodyCap ?? DEFAULT_BODY_CAP;
  const signal = opts.signal;

  // Validate inputs first (SSRF + path traversal), then fail fast on a full
  // disk BEFORE launching the browser.
  await validateUrl(rawUrl);
  const outputDir = await validateOutputDir(rawOutputDir);
  await verifyDiskSpace(outputDir);
  const url = rawUrl;

  // Best-effort progress events: `{ phase, percent?, downloaded_bytes? }`.
  // The streamlink backend and the HLS fallback emit `downloading` updates;
  // byte-capture and manifest paths emit coarse phase markers.
  const emitProgress = typeof opts.onProgress === 'function' ? opts.onProgress : null;

  if (signal?.aborted) {
    throw new DownloaderError('timeout', 'aborted before browser launch');
  }

  const browser = await launchStealthBrowser();
  // Closing the browser is what actually stops Tier 1/Tier 2 work: Playwright's
  // page/CDP calls reject with "Target closed" and the Chromium process exits.
  // Without this, an aborted request would free its concurrency slot while its
  // browser kept running (see the request timeout in server.js).
  let onAbort = null;
  if (signal) {
    onAbort = () => {
      browser.close().catch(() => {});
    };
    if (signal.aborted) {
      onAbort();
    } else {
      signal.addEventListener('abort', onAbort, { once: true });
    }
  }
  // newContext/newPage happen INSIDE the try so that if they throw, the
  // finally still closes the browser (no leak on a failed context/page setup).
  let context;
  let page;
  const uuid = randomUUID();

  let tierUsed = 1;
  let outPath;
  try {
    context = await browser.newContext();
    page = await context.newPage();

    emitProgress?.({ phase: 'intercepting' });

    // Tier 1: internal timer resolves null → fallthrough (never terminal).
    let result = await interceptMedia(page, url, { timeout: tier1Timeout, bodyCap, signal });

    if (!result) {
      result = await detectBlob(page, { timeout: tier2Timeout, bodyCap, signal });
      tierUsed = 2;
    }

    const ext = safeExt(result.ext);
    if (!VIDEO_EXTS.includes(ext)) {
      // Defensive: safeExt guarantees whitelist, but never trust the path.
      throw new DownloaderError('network_error', `unwhitelisted extension: ${result.ext}`);
    }
    outPath = join(outputDir, `${uuid}.${ext}`);

    if (result.kind === 'bytes') {
      emitProgress?.({ phase: 'saving', percent: 100 });
      await writeFile(outPath, result.buffer);
    } else if (result.kind === 'manifest') {
      await downloadStream(result.streamUrl, outPath, {
        timeout: downloadTimeout,
        bodyCap,
        authHeaders: result.authHeaders,
        signal,
        onProgress: emitProgress ?? undefined,
      });
    } else {
      throw new DownloaderError('network_error', `unknown result kind: ${result.kind}`);
    }

    return { status: 'success', file_path: outPath, tier_used: tierUsed };
  } catch (err) {
    if (outPath) {
      await rm(outPath, { force: true }).catch(() => {});
    }
    if (err instanceof DownloaderError) {
      return { status: 'failed', error: err.code, tier_used: null };
    }
    // classifyError rethrows genuine code bugs (TypeError/ReferenceError/...)
    // so the server logs the real stack instead of masking it.
    const code = classifyError(err);
    return { status: 'failed', error: code, tier_used: null };
  } finally {
    if (signal && onAbort) {
      signal.removeEventListener('abort', onAbort);
    }
    if (context) {
      await context.close().catch(() => {});
    }
    await browser.close().catch(() => {});
  }
}
