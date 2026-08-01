// Tier 2 — DOM blob detection (fallthrough for blob-based platforms).
//
// Terminal-condition propagation (mandatory): DRM (EME) and anti-bot are
// checked BEFORE polling for blobs. If detected, the terminal error is thrown
// (NEVER masked as `no_media_found`). Only exhausting the timeout without a
// blob is terminal as `no_media_found`.
//
// Resource lifecycle (mandatory): the `__bd_blobs` hook array is capped (oldest
// evicted beyond a limit) and `createObjectURL` guards against non-Blob inputs
// (e.g. `MediaSource`) — only real Blobs are recorded, and the original call's
// return value is always preserved (no swallowed throws that silently miss
// URLs).

import { DownloaderError } from './errors.js';
import { pickExtension } from './validate.js';

const DEFAULT_BODY_CAP = 500 * 1024 * 1024; // 500MB
const BLOB_CAP = 64;
const POLL_INTERVAL = 250;
const BLOCK_RE =
  /just a moment|access denied|are you a robot|captcha|verify you are human|blocked|forbidden|unauthorized/i;

// Pure helper mirroring the in-page cap+evict logic. Exported for unit testing
// without a browser. `obj instanceof Blob` is the non-Blob guard.
export function pushBlobUrl(arr, obj, url, cap = BLOB_CAP) {
  if (!(obj instanceof Blob)) {
    return arr; // guard non-Blob (e.g. MediaSource) — do not record
  }
  if (arr.length >= cap) {
    arr.shift(); // evict oldest
  }
  arr.push(url);
  return arr;
}

// Live-patches URL.createObjectURL on the loaded page so blobs created after
// injection (e.g. on play) are captured. Must always return the real URL so
// non-Blob (MediaSource) callers still work.
const HOOK_SRC = () => {
  window.__bd_blobs = [];
  window.__bd_drm ||= false;
  const CAP = 64;
  const record = (u) => {
    if (window.__bd_blobs.length >= CAP) {
      window.__bd_blobs.shift();
    }
    window.__bd_blobs.push(u);
  };
  if (window.URL && typeof URL.createObjectURL === 'function') {
    const orig = URL.createObjectURL;
    URL.createObjectURL = (obj) => {
      const u = orig.call(URL, obj);
      try {
        if (obj instanceof Blob) {
          record(u);
        }
      } catch {
        // recording failure must never lose the URL — `u` is already returned
      }
      return u;
    };
  }
  if (navigator && typeof navigator.requestMediaKeySystemAccess === 'function') {
    const origRsa = navigator.requestMediaKeySystemAccess.bind(navigator);
    navigator.requestMediaKeySystemAccess = (...a) => {
      window.__bd_drm = true;
      return origRsa(...a);
    };
  }
  document.addEventListener(
    'encrypted',
    () => {
      window.__bd_drm = true;
    },
    true,
  );
};

async function tryClickPlay(page) {
  const selectors = ['[aria-label="Play"]', '[data-testid="play"]', 'video', 'button'];
  for (const sel of selectors) {
    try {
      const handle = await page.$(sel);
      if (handle) {
        await handle.click({ timeout: 1000 }).catch(() => {});
        return;
      }
    } catch {
      // continue to next selector
    }
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function detectBlob(page, opts = {}) {
  const timeout = opts.timeout ?? 30_000;
  const bodyCap = opts.bodyCap ?? DEFAULT_BODY_CAP;

  // Live-patch createObjectURL + EME on the already-loaded page.
  await page.evaluate(HOOK_SRC).catch(() => {});

  // BEFORE polling: detect terminal conditions (DRM + anti-bot). These throw
  // and NEVER fall through to `no_media_found`.
  let drm = false;
  try {
    drm = await page.evaluate(() => !!window.__bd_drm);
  } catch {
    drm = false;
  }
  if (drm) {
    throw new DownloaderError('drm_detected');
  }

  let blocked = false;
  try {
    blocked = await page.evaluate(() => {
      const text = `${document.title || ''} ${document.body?.innerText || ''}`;
      return BLOCK_RE.test(text);
    });
  } catch {
    blocked = false;
  }
  if (blocked) {
    throw new DownloaderError('anti_bot_block');
  }

  await tryClickPlay(page);

  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    let blobUrl = null;
    try {
      blobUrl = await page.evaluate(() => {
        const v = document.querySelector('video');
        if (v && typeof v.src === 'string' && v.src.startsWith('blob:')) {
          return v.src;
        }
        if (window.__bd_blobs && window.__bd_blobs.length > 0) {
          return window.__bd_blobs[window.__bd_blobs.length - 1];
        }
        return null;
      });
    } catch {
      blobUrl = null;
    }

    if (blobUrl) {
      let payload = null;
      try {
        // Check the blob size BEFORE materializing the body as a JS array of
        // numbers across CDP (avoids loading a huge blob into V8 + CDP JSON).
        // An AbortController bounds each in-page fetch so a hung blob URL
        // cannot block the polling loop forever.
        payload = await page.evaluate(
          async (u, cap, timeoutMs) => {
            const ac = new AbortController();
            const timer = setTimeout(() => ac.abort(), timeoutMs);
            try {
              const r = await fetch(u, { signal: ac.signal });
              const type = r.headers.get('content-type') || '';
              const cl = Number(r.headers.get('content-length'));
              if (Number.isFinite(cl) && cl > cap) {
                return { tooLarge: true, type, size: cl };
              }
              const ab = await r.arrayBuffer();
              if (ab.byteLength > cap) {
                return { tooLarge: true, type, size: ab.byteLength };
              }
              const bytes = Array.from(new Uint8Array(ab));
              return { bytes, type, size: bytes.length };
            } finally {
              clearTimeout(timer);
            }
          },
          blobUrl,
          bodyCap,
          timeout,
        );
      } catch {
        payload = null;
      }

      if (payload) {
        if (payload.tooLarge || (typeof payload.size === 'number' && payload.size > bodyCap)) {
          throw new DownloaderError('network_error', 'blob body exceeds size cap');
        }
        if (typeof payload.size === 'number' && payload.size > 0 && Array.isArray(payload.bytes)) {
          const buf = Buffer.from(payload.bytes);
          const ext = pickExtension(blobUrl, payload.type);
          return { kind: 'bytes', buffer: buf, ext };
        }
      }
    }

    // Re-check terminal conditions during polling (page may engage DRM or an
    // anti-bot challenge late — e.g. after a JS-driven redirect or after
    // clicking play triggers a challenge). Mirrors tier1-cdp.js's continuous
    // DRM monitoring so neither terminal condition is masked as no_media_found.
    try {
      const drmNow = await page.evaluate(() => !!window.__bd_drm);
      if (drmNow) {
        throw new DownloaderError('drm_detected');
      }
      const blockedNow = await page.evaluate(() => {
        const text = `${document.title || ''} ${document.body?.innerText || ''}`;
        return BLOCK_RE.test(text);
      });
      if (blockedNow) {
        throw new DownloaderError('anti_bot_block');
      }
    } catch (err) {
      if (err instanceof DownloaderError) {
        throw err;
      }
      // evaluate failed — ignore
    }

    await sleep(POLL_INTERVAL);
  }

  throw new DownloaderError('no_media_found');
}
