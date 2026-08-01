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
import { rm, writeFile } from 'node:fs/promises';
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

let stealthReady = null;

async function launchStealthBrowser() {
  const { chromium } = await import('playwright-extra');
  stealthReady ??= (async () => {
    const StealthPlugin = (await import('puppeteer-extra-plugin-stealth')).default;
    chromium.use(StealthPlugin());
  })();
  await stealthReady;
  return chromium.launch({ headless: true });
}

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

  // Validate inputs first (SSRF + path traversal).
  await validateUrl(rawUrl);
  const outputDir = await validateOutputDir(rawOutputDir);
  const url = rawUrl;

  const browser = await launchStealthBrowser();
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

    // Tier 1: internal timer resolves null → fallthrough (never terminal).
    let result = await interceptMedia(page, url, { timeout: tier1Timeout, bodyCap });

    if (!result) {
      result = await detectBlob(page, { timeout: tier2Timeout, bodyCap });
      tierUsed = 2;
    }

    const ext = safeExt(result.ext);
    if (!VIDEO_EXTS.includes(ext)) {
      // Defensive: safeExt guarantees whitelist, but never trust the path.
      throw new DownloaderError('network_error', `unwhitelisted extension: ${result.ext}`);
    }
    outPath = join(outputDir, `${uuid}.${ext}`);

    if (result.kind === 'bytes') {
      await writeFile(outPath, result.buffer);
    } else if (result.kind === 'manifest') {
      await downloadStream(result.streamUrl, outPath, {
        timeout: downloadTimeout,
        bodyCap,
        authHeaders: result.authHeaders,
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
    if (context) {
      await context.close().catch(() => {});
    }
    await browser.close().catch(() => {});
  }
}
