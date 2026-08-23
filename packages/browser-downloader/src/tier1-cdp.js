// Tier 1 — CDP network interception (primary path).
//
// Matches video/* content-types plus .m3u8/.mpd/.mp4/.webm/.ts URLs, fetches
// the response body via `Network.getResponseBody` (with a size cap), and routes
// HLS/DASH manifests to the streamlink backend.
//
// Terminal-condition propagation (mandatory): DRM (EME
// `requestMediaKeySystemAccess` / `encrypted` event) and anti-bot (HTTP 403 or
// block-page indicators) are detected here and throw immediately — they NEVER
// fall through to Tier 2. The internal fallthrough timer resolves `null`
// (fallthrough to Tier 2) and is NEVER a terminal `timeout` rejection.
//
// Structured DRM manifest heuristics (Phase 2.1): before routing an
// intercepted HLS/DASH manifest to streamlink, the manifest body is fetched
// and scanned for DRM markers:
//   - HLS: #EXT-X-KEY:METHOD=SAMPLE-AES*, KEYFORMAT=com.apple/playready/widevine
//   - DASH: <ContentProtection schemeIdUri="urn:uuid:...">
// DRM is detected at manifest-parse time (milliseconds) instead of waiting for
// the EME API to fire. Only AES-128 with identity keyformat passes through.
//
// Auth header capture (Phase 2.2): CDP `Network.requestWillBeSent` events
// capture the page's Referer and Origin headers so the streamlink backend can
// replay them on segment fetches (sites that validate headers at the CDN edge).
//
// Resource lifecycle (mandatory): the CDP session is `detach()`ed in cleanup
// (not just `off()`), and the fallthrough timer is cleared when media is found
// or a terminal condition throws before the timer fires.

import { DownloaderError } from './errors.js';
import { pickExtension } from './validate.js';

const DEFAULT_BODY_CAP = 500 * 1024 * 1024; // 500MB
const MANIFEST_CAP = 8 * 1024 * 1024; // 8MiB — manifests are small
// Bound the per-intercept requestHeaders map so captured credentials/headers
// cannot accumulate unbounded across a long-lived navigation.
const REQUEST_HEADERS_CAP = 256;

const VIDEO_CT_RE = /^video\//i;
const VIDEO_URL_RE = /\.(mp4|webm|ts|m4v|m4s)(\?|$)/i;
const MANIFEST_RE = /\.(m3u8|mpd)(\?|$)/i;
const BLOCK_RE =
  /just a moment|access denied|are you a robot|captcha|verify you are human|blocked|forbidden|unauthorized/i;

// -- DRM manifest heuristics (Phase 2.1) -----------------------------------

// HLS: any #EXT-X-KEY or #EXT-X-SESSION-KEY with a DRM METHOD or a
// non-identity KEYFORMAT.
const HLS_DRM_METHOD_RE =
  /#EXT-X-(?:KEY|SESSION-KEY):.*METHOD=(SAMPLE-AES|SAMPLE-AES-CTR|SAMPLE-AES-CENC)/i;
const HLS_DRM_KEYFORMAT_RE = /#EXT-X-(?:KEY|SESSION-KEY):.*KEYFORMAT="(?!identity).+"/i;
// DASH: any <ContentProtection> element with a DRM schemeIdUri.
const DASH_DRM_RE = /<ContentProtection[\s/>]/i;

// Key headers captured for auth replay on downstream segment fetches.
const AUTH_HEADER_NAMES = ['referer', 'origin', 'cookie', 'authorization'];

/**
 * Detects DRM indicators in a DASH or HLS manifest.
 * @param {string} body - The manifest content to inspect.
 * @param {string} url - The manifest URL used to identify its format.
 * @return {string|null} A description of the detected DRM indicator, or `null` when none is found.
 */
function scanManifestForDrm(body, url) {
  const isDash = /\.mpd(\?|$)/i.test(url);
  if (isDash) {
    if (DASH_DRM_RE.test(body)) {
      return 'DASH <ContentProtection> DRM';
    }
  } else {
    // HLS: check for DRM methods first, then KEYFORMAT
    if (HLS_DRM_METHOD_RE.test(body)) {
      return 'HLS #EXT-X-KEY DRM method (SAMPLE-AES*)';
    }
    if (HLS_DRM_KEYFORMAT_RE.test(body)) {
      return 'HLS #EXT-X-KEY non-identity KEYFORMAT (DRM)';
    }
  }
  return null;
}

// -- Init script (DRM + auth header hooks) ----------------------------------

// Injected before navigation so EME usage is flagged on the page and
// auth headers are captured from the page's own fetch/XHR calls.
const DRM_INIT_SCRIPT = () => {
  window.__bd_drm = false;
  window.__bd_auth_headers = {};
  if (navigator && typeof navigator.requestMediaKeySystemAccess === 'function') {
    const orig = navigator.requestMediaKeySystemAccess.bind(navigator);
    navigator.requestMediaKeySystemAccess = (...args) => {
      window.__bd_drm = true;
      return orig(...args);
    };
  }
  document.addEventListener(
    'encrypted',
    () => {
      window.__bd_drm = true;
    },
    true,
  );
  // Capture auth headers from the page's own fetch calls so they can be
  // replayed on downstream segment fetches in the streamlink backend.
  if (typeof window.fetch === 'function') {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (input, init) => {
      try {
        if (init?.headers) {
          const raw =
            init.headers instanceof Headers
              ? Object.fromEntries(init.headers.entries())
              : Array.isArray(init.headers)
                ? Object.fromEntries(init.headers)
                : { ...init.headers };
          // Normalise to lowercase — Headers.entries() does this already,
          // but array and object forms preserve caller casing.
          const h = {};
          for (const [k, v] of Object.entries(raw)) {
            h[String(k).toLowerCase()] = v;
          }
          const auth = {};
          if (h.referer) auth.referer = h.referer;
          if (h.origin) auth.origin = h.origin;
          if (h.cookie) auth.cookie = h.cookie;
          if (h.authorization) auth.authorization = h.authorization;
          if (Object.keys(auth).length > 0) {
            window.__bd_auth_headers = { ...window.__bd_auth_headers, ...auth };
          }
        }
      } catch {
        /* best-effort */
      }
      return nativeFetch(input, init);
    };
  }
  // Also capture from XHR calls.
  if (typeof XMLHttpRequest !== 'undefined' && XMLHttpRequest.prototype.setRequestHeader) {
    const origSetHeader = XMLHttpRequest.prototype.setRequestHeader;
    XMLHttpRequest.prototype.setRequestHeader = function (name, value) {
      try {
        const lower = name.toLowerCase();
        if (['referer', 'origin', 'cookie', 'authorization'].includes(lower)) {
          window.__bd_auth_headers[lower] = value;
        }
      } catch {
        /* best-effort */
      }
      return origSetHeader.call(this, name, value);
    };
  }
};

// -- Helpers -----------------------------------------------------------------

async function safeDetach(client) {
  try {
    await client.detach();
  } catch {
    // already detached
  }
}

/**
 * Intercepts media responses during navigation and returns the first supported media or manifest result.
 * @param {import('playwright').Page} page - The page to navigate and monitor.
 * @param {string} url - The URL to navigate to.
 * @param {object} [opts] - Interception options.
 * @param {number} [opts.timeout=30000] - Maximum time to wait for a media response.
 * @param {number} [opts.bodyCap] - Maximum allowed size of a media response body.
 * @return {Promise<object|null>} The intercepted media result, a manifest result, or `null` if no media is found before the timeout.
 */

export async function interceptMedia(page, url, opts = {}) {
  const timeout = opts.timeout ?? 30_000;
  const bodyCap = opts.bodyCap ?? DEFAULT_BODY_CAP;

  const client = await page.context().newCDPSession(page);
  // If CDP setup fails (Network.enable / addInitScript), detach the session
  // before rethrowing so we never leak a CDP session on an early throw.
  try {
    await client.send('Network.enable');
    await page.addInitScript(DRM_INIT_SCRIPT);
  } catch (err) {
    await safeDetach(client);
    throw err;
  }

  const candidates = new Map();
  // Per-requestId storage of captured request headers (from CDP).
  const requestHeaders = new Map();

  return new Promise((resolve, reject) => {
    let settled = false;
    let fallthroughTimer = null;
    let drmTimer = null;

    const stopTimers = () => {
      if (fallthroughTimer) {
        clearTimeout(fallthroughTimer);
        fallthroughTimer = null;
      }
      if (drmTimer) {
        clearInterval(drmTimer);
        drmTimer = null;
      }
    };

    const settle = (fn) => {
      if (settled) {
        return;
      }
      settled = true;
      stopTimers();
      fn();
    };
    const onFound = (result) => settle(() => resolve(result));
    const onTerminal = (err) => settle(() => reject(err));

    // Internal fallthrough timer — resolves null (NEVER a terminal rejection).
    fallthroughTimer = setTimeout(() => onFound(null), timeout);

    // -- CDP: capture request headers before response -----------------------
    const onRequestWillBeSent = (params) => {
      const headers = params.request?.headers || {};
      const captured = {};
      for (const name of AUTH_HEADER_NAMES) {
        if (headers[name] && typeof headers[name] === 'string') {
          captured[name] = headers[name];
        }
      }
      if (Object.keys(captured).length > 0) {
        requestHeaders.set(params.requestId, captured);
        // Evict the oldest entry if we exceed the cap (unbounded credential
        // retention guard).
        if (requestHeaders.size > REQUEST_HEADERS_CAP) {
          const oldest = requestHeaders.keys().next().value;
          requestHeaders.delete(oldest);
        }
      }
    };

    // -- CDP: handle completed responses ------------------------------------
    const onLoadingFinished = async (params) => {
      const candidate = candidates.get(params.requestId);
      if (!candidate) {
        return;
      }
      candidates.delete(params.requestId);
      try {
        const isManifest = MANIFEST_RE.test(candidate.url);
        // Pre-read guard: reject candidates whose encoded transfer size already
        // exceeds the applicable cap before we materialize the body. The
        // post-read caps below remain as backstops (Content-Length / encoded
        // length can under-report for compressed responses).
        const cap = isManifest ? MANIFEST_CAP : bodyCap;
        const encLen =
          typeof params.encodedDataLength === 'number' ? params.encodedDataLength : null;
        if (encLen != null && encLen > cap) {
          onTerminal(new DownloaderError('network_error', 'response body exceeds size cap'));
          return;
        }

        if (isManifest) {
          // Phase 2.1: fetch manifest body and scan for DRM before routing
          // to streamlink.
          const resp = await client.send('Network.getResponseBody', {
            requestId: params.requestId,
          });
          const body = resp.base64Encoded
            ? Buffer.from(resp.body, 'base64').toString('utf8')
            : resp.body;
          if (!body || body.length === 0) {
            return; // empty manifest — keep waiting
          }
          if (Buffer.byteLength(body) > MANIFEST_CAP) {
            onTerminal(new DownloaderError('network_error', 'manifest body exceeds size cap'));
            return;
          }
          const drmReason = scanManifestForDrm(body, candidate.url);
          if (drmReason) {
            onTerminal(new DownloaderError('drm_detected', drmReason));
            return;
          }
          // Collect auth headers from CDP + page init script for replay.
          let authHeaders = null;
          try {
            const pageHeaders = await page.evaluate(() => {
              const h = window.__bd_auth_headers;
              return h && Object.keys(h).length > 0 ? h : null;
            });
            if (pageHeaders) {
              authHeaders = pageHeaders;
            }
          } catch {
            /* best-effort */
          }
          // CDP-level headers override page-level (more reliable).
          const cdpHeaders = requestHeaders.get(params.requestId);
          requestHeaders.delete(params.requestId);
          if (cdpHeaders) {
            authHeaders = { ...authHeaders, ...cdpHeaders };
            requestHeaders.delete(params.requestId); // free memory
          }
          onFound({
            kind: 'manifest',
            streamUrl: candidate.url,
            ext: 'mp4',
            authHeaders:
              authHeaders && Object.keys(authHeaders).length > 0 ? authHeaders : undefined,
          });
          return;
        }

        // Non-manifest: fetch bytes directly.
        const resp = await client.send('Network.getResponseBody', {
          requestId: params.requestId,
        });
        const buf = Buffer.from(resp.body, resp.base64Encoded ? 'base64' : 'utf8');
        if (buf.length > bodyCap) {
          onTerminal(new DownloaderError('network_error', 'response body exceeds size cap'));
          return;
        }
        if (buf.length === 0) {
          return; // not real media; keep waiting for the timer/other responses
        }
        const ext = pickExtension(candidate.url, candidate.ct);
        onFound({ kind: 'bytes', buffer: buf, ext });
      } catch {
        // body not available yet / transient — ignore, timer still guards us
      }
    };

    const onResponseReceived = (params) => {
      const { response } = params;
      const respUrl = response.url || '';
      const ct = response.mimeType || '';
      // Anti-bot: a 403 on the MAIN document response (the page navigation) is
      // terminal. A 403 on a subresource (ad, pixel, asset) is NOT — it must
      // not trigger the terminal anti-bot error. The main document is the
      // response whose URL matches the navigation URL or whose CDP resource
      // type is 'Document'.
      if (response.status === 403) {
        const isMain = respUrl === url || params.type === 'Document';
        if (isMain) {
          onTerminal(new DownloaderError('anti_bot_block', `403 on ${respUrl}`));
          return;
        }
        // subresource 403 — not terminal; just don't treat as a media candidate
        return;
      }
      const isCandidate =
        VIDEO_CT_RE.test(ct) || VIDEO_URL_RE.test(respUrl) || MANIFEST_RE.test(respUrl);
      if (isCandidate) {
        candidates.set(params.requestId, { url: respUrl, ct });
      }
    };

    const checkDrm = async () => {
      try {
        const drm = await page.evaluate(() => !!window.__bd_drm);
        if (drm) {
          onTerminal(new DownloaderError('drm_detected'));
        }
      } catch {
        // page not ready yet — keep polling
      }
    };

    const onMainResponse = (resp) => {
      // Only a 403 on the MAIN document (navigation) is anti-bot. A 403 on a
      // subresource must NOT trigger the terminal error.
      if (resp.status() === 403) {
        let respUrl = '';
        try {
          respUrl = typeof resp.url === 'function' ? resp.url() : '';
        } catch {
          respUrl = '';
        }
        let isNav = false;
        try {
          isNav = !!resp.request?.()?.isNavigationRequest?.();
        } catch {
          isNav = false;
        }
        if (respUrl === url || isNav) {
          onTerminal(new DownloaderError('anti_bot_block', '403 on main document'));
        }
      }
    };

    const onDomReady = async () => {
      try {
        const blocked = await page.evaluate(
          ({ source, flags }) => {
            const text = `${document.title || ''} ${document.body?.innerText || ''}`;
            return new RegExp(source, flags).test(text);
          },
          { source: BLOCK_RE.source, flags: BLOCK_RE.flags },
        );
        if (blocked) {
          onTerminal(new DownloaderError('anti_bot_block'));
        }
      } catch {
        // ignore
      }
    };

    client.on('Network.requestWillBeSent', onRequestWillBeSent);
    client.on('Network.responseReceived', onResponseReceived);
    client.on('Network.loadingFinished', onLoadingFinished);
    page.on('response', onMainResponse);
    page
      .waitForLoadState('domcontentloaded')
      .then(onDomReady)
      .catch(() => {});
    drmTimer = setInterval(checkDrm, 500);

    // Start navigation after interception is armed. Swallow goto rejections —
    // CDP events + the fallthrough timer drive resolution.
    void page.goto(url, { waitUntil: 'domcontentloaded', timeout }).catch(() => {});
  }).then(
    async (result) => {
      await safeDetach(client);
      return result;
    },
    async (err) => {
      await safeDetach(client);
      throw err;
    },
  );
}
