// HLS / DASH backend.
//
// Primary path: `streamlink` CLI via child_process.spawn (handles key
// decryption, adaptive bitrate, segment retry, live streams — no custom
// parsers). Fallback path: manual segment download + ffmpeg concat, used only
// when streamlink fails on an HLS (.m3u8) playlist.
//
// Resource lifecycle (mandatory):
//   - subprocess stderr is DRAINED (a `data` listener is attached even when
//     the content is unused) to avoid pipe-buffer deadlock;
//   - every subprocess has a timeout/AbortSignal and is `kill()`ed on
//     cancellation or timeout (SIGTERM, then SIGKILL after a grace window);
//   - manifest/segment fetches use an AbortController with a timeout.
//
// HLS correctness (mandatory):
//   - `#EXT-X-KEY` with DRM methods (SAMPLE-AES*) or non-identity KEYFORMAT
//     fails with `drm_detected` — ciphertext is never fetched and concatenated
//     into a "success" file;
//   - plain AES-128 (`METHOD=AES-128`, identity keyformat) IS supported in the
//     fallback: the key is fetched (64 KiB cap, SSRF-validated) and each
//     segment is decrypted (AES-128-CBC, IV attribute or media-sequence
//     default) before concat — encrypted segments are never written raw;
//   - variant (master) playlist URLs with query strings are resolved with
//     `new URL(variant, baseUrl)` so the query string survives recursion;
//   - the best variant (highest BANDWIDTH) is chosen in the fallback, not the
//     first one encountered;
//   - manifests without `#EXTM3U` (HTML error pages served with HTTP 200) and
//     live playlists (no `#EXT-X-ENDLIST`) fail fast with `no_media_found`
//     instead of fetching junk URLs or writing a silently partial snapshot.
//
// Segment fetch improvements (Phase 2.3):
//   - per-resource-kind size caps: manifest=8MiB, key=64KiB, segment=256MiB
//   - exponential backoff retry with jitter (max 3 retries per segment)
//   - context fallback: try same-origin CORS → include CORS credentials
//   - auth header forwarding: captured CDP/page headers replayed on sub-fetches
//
// Progress (optional): `onProgress` receives `{ phase, percent?,
// downloaded_bytes? }` events — throttled to one per 500 ms — sourced from
// streamlink's forced progress output and the fallback's per-segment counter.

import { spawn } from 'node:child_process';
import { createDecipheriv } from 'node:crypto';
import { mkdtemp, rm, unlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { isAbsolute, join, resolve as resolvePath } from 'node:path';

import { DownloaderError } from './errors.js';
import { parseTimeout, validateUrl } from './validate.js';

// Separate download timeout for the streamlink/ffmpeg subprocess (env
// BD_DOWNLOAD_TIMEOUT_MS). The Tier 1 interception timeout (30s) is for
// detecting media via CDP; the actual download may take much longer.
const DEFAULT_DOWNLOAD_TIMEOUT = parseTimeout(process.env.BD_DOWNLOAD_TIMEOUT_MS, 120_000);
const DEFAULT_STREAMLINK_TIMEOUT = DEFAULT_DOWNLOAD_TIMEOUT;
const DEFAULT_FFMPEG_TIMEOUT = DEFAULT_DOWNLOAD_TIMEOUT;
const DEFAULT_FETCH_TIMEOUT = DEFAULT_DOWNLOAD_TIMEOUT;
const MAX_DEPTH = 5; // master-playlist recursion guard
const MAX_REDIRECTS = 5;
const MAX_RETRIES = 3; // max segment-level retries
const BASE_RETRY_MS = 500; // base delay for exponential backoff

// -- Per-resource-kind size caps (Phase 2.3) --------------------------------
/**
 * Determines the maximum permitted response size for a resource category.
 * @param {string} resourceKind - The resource category, such as `manifest` or `key`.
 * @return {number} The maximum response size in bytes.
 */

function maxBodyBytes(resourceKind) {
  if (resourceKind === 'manifest') return 8 * 1024 * 1024; // 8 MiB
  if (resourceKind === 'key') return 64 * 1024; // 64 KiB
  return 256 * 1024 * 1024; // 256 MiB (segments, init, generic)
}

/**
 * Classifies a media resource based on its URL extension.
 * @param {string} url - The resource URL.
 * @return {string} `manifest` for HLS or DASH manifests, `key` for key files, or `segment` otherwise.
 */
function classifyResource(url) {
  if (/\.(m3u8|mpd)(\?|$)/i.test(url)) return 'manifest';
  if (/\.key(\?|$)/i.test(url)) return 'key';
  return 'segment';
}

// -- HLS AES-128 key parsing + segment decryption --------------------------

/**
 * Parses an `#EXT-X-KEY` tag line.
 * @param {string} line - The tag line (e.g. `#EXT-X-KEY:METHOD=AES-128,URI="https://k/key",IV=0x...`).
 * @return {{uri: string, ivHex: string|null}|null} The key URI and optional
 *   explicit IV (32 hex chars, no `0x`), or `null` when the tag does not
 *   describe plain AES-128 (DRM methods are rejected upstream; METHOD-less
 *   tags mean "no encryption").
 */
export function parseHlsKeyTag(line) {
  const method = /METHOD=([^,\s]+)/.exec(line)?.[1]?.trim().toUpperCase();
  if (method !== 'AES-128') return null;
  // Non-identity KEYFORMAT means DRM, not plain AES-128 — reject rather than
  // decrypt as AES-128 and write corrupt output. The unquoted form here
  // bypasses the manifest-level HLS_DRM_KEYFORMAT_RE gate.
  const keyformat = /KEYFORMAT="?([^",\s]+)/.exec(line)?.[1];
  if (keyformat && keyformat.toUpperCase() !== 'IDENTITY') {
    throw new DownloaderError('drm_detected', 'non-identity KEYFORMAT in #EXT-X-KEY');
  }
  const uri = /URI="([^"]+)"/.exec(line)?.[1];
  // A METHOD=AES-128 tag without a URI would silently leave segments
  // cleartext — reject instead of producing corrupt output.
  if (!uri) throw new DownloaderError('drm_detected', 'AES-128 #EXT-X-KEY missing URI');
  const ivMatch = /IV=0x([0-9a-fA-F]{32})/.exec(line);
  return { uri, ivHex: ivMatch ? ivMatch[1] : null };
}

/**
 * Decrypts one HLS AES-128 segment (AES-128-CBC, PKCS#7).
 *
 * The IV is the tag's `IV=` attribute when present, otherwise the 16-byte
 * big-endian media sequence number (HLS spec §5.2 — the default is the media
 * sequence of the segment, not the playlist start).
 * @param {Buffer} segmentBuf - Raw (encrypted) segment bytes.
 * @param {Buffer} keyBuf - 16-byte AES-128 key.
 * @param {string|null} ivHex - Explicit IV from the key tag, or null for the
 *   media-sequence default.
 * @param {number} mediaSequence - Media sequence number of this segment.
 * @return {Buffer} Decrypted segment bytes.
 * @throws {DownloaderError} If the key is not 16 bytes or decryption fails.
 */
export function decryptHlsSegment(segmentBuf, keyBuf, ivHex, mediaSequence) {
  if (keyBuf.length !== 16) {
    throw new DownloaderError(
      'network_error',
      `AES-128 key has invalid length ${keyBuf.length} (expected 16)`,
    );
  }
  const iv = ivHex
    ? Buffer.from(ivHex, 'hex')
    : Buffer.from(mediaSequence.toString(16).padStart(32, '0'), 'hex');
  const decipher = createDecipheriv('aes-128-cbc', keyBuf, iv);
  try {
    return Buffer.concat([decipher.update(segmentBuf), decipher.final()]);
  } catch {
    throw new DownloaderError(
      'network_error',
      'failed to decrypt HLS segment (corrupt or wrong key)',
    );
  }
}

/**
 * Fetches an HLS AES-128 key, resolving its URI against the playlist URL and
 * SSRF-validating it like any other derived URL.
 * @param {{uri: string}} keyTag - Parsed key tag from the playlist.
 * @param {string} playlistUrl - The media playlist URL (relative-URI base).
 * @param {Object} opts - fetch options (signal, timeout, lookup, authHeaders).
 * @return {Promise<Buffer>} The 16-byte key.
 */
async function fetchHlsKey(keyTag, playlistUrl, opts) {
  let keyUrl;
  try {
    keyUrl = new URL(keyTag.uri, playlistUrl).href;
  } catch {
    throw new DownloaderError('network_error', 'invalid #EXT-X-KEY URI');
  }
  // SSRF defence — fail fast, never retried, before any fetch.
  await validateUrl(keyUrl, { lookup: opts.lookup });
  const res = await fetchResWithRetry(keyUrl, {
    signal: opts.signal,
    timeout: opts.timeout,
    lookup: opts.lookup,
    bodyCap: maxBodyBytes('key'),
    authHeaders: opts.authHeaders,
  });
  const keyBuf = Buffer.from(await res.arrayBuffer());
  // Post-fetch guard: fetchResOne only caps via Content-Length, which a
  // chunked/no-CL response bypasses — the attacker-influenced key URI could
  // otherwise exhaust worker memory before the cap is checked.
  const keyCap = maxBodyBytes('key');
  if (keyBuf.length > keyCap) {
    throw new DownloaderError(
      'network_error',
      `AES-128 key exceeds size cap (${keyBuf.length} > ${keyCap})`,
    );
  }
  return keyBuf;
}

// -- Progress helper --------------------------------------------------------

/**
 * Wraps an onProgress callback so at most one event is delivered per
 * minIntervalMs. Progress is best-effort: callback exceptions are swallowed.
 */
function throttledProgress(onProgress, minIntervalMs = 500) {
  if (typeof onProgress !== 'function') return () => {};
  let last = 0;
  return (event) => {
    const now = Date.now();
    if (now - last < minIntervalMs) return;
    last = now;
    try {
      onProgress(event);
    } catch {
      /* progress is best-effort */
    }
  };
}

// -- Retry delay with exponential backoff + jitter (Phase 2.3) --------------
/**
 * Calculates the delay before a retry using capped exponential backoff and random jitter.
 * @param {number} attempt - The zero-based retry attempt number.
 * @return {number} The delay in milliseconds, including up to 250 milliseconds of jitter.
 */

function retryDelayMs(attempt) {
  const base = Math.min(BASE_RETRY_MS * 2 ** attempt, 8_000);
  const jitter = Math.random() * 250;
  return base + jitter;
}

/**
 * Delays completion for the specified duration.
 * @param {number} ms - The delay duration in milliseconds.
 * @return {Promise<void>} Resolves after the delay.
 */
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// -- Context fallback strategy (Phase 2.3) ----------------------------------
// Tries multiple fetch() credential modes, deduplicating equivalents. Mirrors
/**
 * Generates credential and CORS request contexts, prioritizing captured authentication headers.
 * @param {Object} authHeaders - Authentication headers to include in credentialed requests.
 * @return {Array<Object>} The deduplicated request contexts.
 */

function contextCandidates(authHeaders) {
  const candidates = [];
  // If we have auth headers from CDP/page capture, use them with include
  // credentials so cookies + authorization are sent to the CDN.
  if (authHeaders && Object.keys(authHeaders).length > 0) {
    candidates.push({ credentials: 'include', mode: 'cors', headers: authHeaders });
    // Also try same-origin as fallback (some CDNs reject cross-origin creds).
    candidates.push({ credentials: 'same-origin', mode: 'cors', headers: authHeaders });
  }
  // Standard no-auth fallbacks.
  candidates.push({ credentials: 'same-origin', mode: 'cors' });
  candidates.push({ credentials: 'include', mode: 'cors' });
  // Deduplicate by serialized key.
  const seen = new Set();
  return candidates.filter((c) => {
    const key = `${c.credentials}::${c.mode}::${Object.keys(c.headers || {})
      .sort()
      .join(',')}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

// -- HTTP helpers ------------------------------------------------------------

// Single fetch with a per-call timeout, parent AbortSignal forwarding, and
/**
 * Fetch a resource with manual redirect handling and request cancellation.
 * @param {string} url - The resource URL.
 * @param {Object} options - Request options.
 * @param {AbortSignal} [options.signal] - Signal that cancels the request.
 * @param {number} options.timeout - Request timeout in milliseconds.
 * @param {Object} [options.headers] - Additional request headers.
 * @return {Promise<Response>} The fetch response.
 */
async function fetchOnce(url, { signal, timeout, headers: extraHeaders, credentials, mode }) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), timeout);
  const onParentAbort = () => ac.abort();
  if (signal) {
    if (signal.aborted) {
      ac.abort();
    } else {
      signal.addEventListener('abort', onParentAbort, { once: true });
    }
  }
  try {
    const init = { signal: ac.signal, redirect: 'manual' };
    if (extraHeaders) {
      init.headers = extraHeaders;
    }
    // Apply the credential context selected by contextCandidates (e.g.
    // `include` credentials so captured cookies/authorization are honoured on
    // cross-origin CDN requests).
    if (credentials) {
      init.credentials = credentials;
    }
    if (mode) {
      init.mode = mode;
    }
    return await fetch(url, init);
  } finally {
    clearTimeout(timer);
    if (signal) {
      signal.removeEventListener('abort', onParentAbort);
    }
  }
}

/**
 * Fetches a resource across credential contexts with retry backoff.
 * @param {string} url - The resource URL.
 * @param {Object} [opts] - Request options, including timeout, cancellation signal, URL lookup, and authentication headers.
 * @returns {Promise<Response>} The fetched response.
 * @throws {*} The last request error when all attempts fail.
 */
async function fetchResWithRetry(url, opts = {}) {
  const { signal, timeout = DEFAULT_FETCH_TIMEOUT, lookup, authHeaders } = opts;
  const contexts = contextCandidates(authHeaders);
  let lastError = null;

  for (const context of contexts) {
    for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
      try {
        return await fetchResOne(url, { ...opts, signal, timeout, lookup, ...context });
      } catch (err) {
        lastError = err;
        // Non-retryable errors (403, 401, 410, DownloaderError with terminal codes).
        if (err instanceof DownloaderError) {
          if (['drm_detected', 'anti_bot_block'].includes(err.code)) {
            throw err;
          }
          // network_error is retryable; others fall through to retry
        }
        if (err && typeof err.status === 'number') {
          if (err.status === 403) throw err; // forbidden — don't retry
          if (err.status === 404) throw err; // not found — don't retry
          if (err.status === 401 || err.status === 410) throw err; // expired
        }
        // SSRF / DNS rejection from validateUrl — don't retry.
        if (err instanceof Error && err.message) {
          if (/private|link-local|host could not be resolved/i.test(err.message)) {
            throw err;
          }
        }
        if (attempt < MAX_RETRIES) {
          await sleep(retryDelayMs(attempt));
        }
      }
    }
  }
  throw lastError;
}

// Follow redirects manually, validating every Location via `validateUrl`.
/**
 * Fetch a resource while following validated redirects and enforcing its size limit.
 * @param {string} url - The resource URL to fetch.
 * @param {Object} [opts] - Request and size-limit options.
 * @param {AbortSignal} [opts.signal] - Signal used to cancel the request.
 * @param {number} [opts.timeout] - Per-request timeout in milliseconds.
 * @param {Function} [opts.lookup] - Function used to validate resolved redirect URLs.
 * @param {string} [opts.credentials] - Credentials mode for the request.
 * @param {string} [opts.mode] - Request mode.
 * @param {Object} [opts.headers] - Additional request headers.
 * @param {number} [opts.bodyCap] - Maximum permitted response size in bytes.
 * @return {Promise<Response>} The successful resource response.
 * @throws {DownloaderError} If the response is unsuccessful, exceeds the size limit, contains an invalid redirect, or exceeds the redirect limit.
 */
async function fetchResOne(url, opts = {}) {
  const {
    signal,
    timeout = DEFAULT_FETCH_TIMEOUT,
    lookup,
    credentials,
    mode,
    headers: extraHeaders,
  } = opts;
  const resourceKind = classifyResource(url);
  const bodyCap = opts.bodyCap ?? maxBodyBytes(resourceKind);
  let next = url;
  for (let hops = 0; hops <= MAX_REDIRECTS; hops += 1) {
    const res = await fetchOnce(next, {
      signal,
      timeout,
      headers: extraHeaders,
      credentials,
      mode,
    });
    if (res.status >= 300 && res.status < 400) {
      const loc = res.headers?.get?.('location');
      if (!loc) {
        throw new DownloaderError('network_error', 'redirect response missing Location');
      }
      let resolved;
      try {
        resolved = new URL(loc, next).href;
      } catch {
        throw new DownloaderError('network_error', 'invalid redirect Location');
      }
      await validateUrl(resolved, { lookup });
      next = resolved;
      continue;
    }
    if (!res.ok) {
      const err = new DownloaderError('network_error', `fetch failed: HTTP ${res.status}`);
      err.status = res.status;
      throw err;
    }
    const cl = contentLength(res.headers);
    if (cl != null && bodyCap != null && cl > bodyCap) {
      throw new DownloaderError('network_error', `response exceeds size cap (${cl} > ${bodyCap})`);
    }
    return res;
  }
  throw new DownloaderError('network_error', 'too many redirects');
}

// -- Subprocess runner -------------------------------------------------------

// Run a subprocess with drained stdio, a timeout, and an optional AbortSignal.
/**
 * Runs a subprocess and captures its exit status and standard error.
 * @param {string} cmd - The executable to run.
 * @param {string[]} args - Arguments passed to the executable.
 * @param {Object} [opts] - Execution options.
 * @param {number} [opts.timeout] - Maximum runtime in milliseconds.
 * @param {AbortSignal} [opts.signal] - Signal that cancels the subprocess.
 * @return {{code: number|null, stderr: string, killed: boolean}} The exit code, captured standard error, and whether termination was requested.
 */
export function runSpawn(cmd, args, opts = {}) {
  return new Promise((resolve) => {
    const timeout = opts.timeout ?? DEFAULT_STREAMLINK_TIMEOUT;
    const signal = opts.signal;
    let stderr = '';
    let killed = false;
    let child;
    try {
      // detached: true places the child in a new process group/session so we
      // can signal the whole group (streamlink spawns ffmpeg as a child).
      child = spawn(cmd, args, { stdio: ['ignore', 'pipe', 'pipe'], detached: true });
    } catch (err) {
      resolve({ code: -1, stderr: String(err), killed: false });
      return;
    }

    // Drain stderr + stdout to prevent pipe-buffer deadlock. The optional
    // onOutput callback receives raw stderr chunks (used for progress parsing).
    child.stderr.on('data', (chunk) => {
      const s = chunk.toString();
      stderr += s;
      opts.onOutput?.(s);
    });
    child.stdout.on('data', () => {});

    let termTimer = null;
    let killTimer = null;

    const cleanup = () => {
      if (termTimer) {
        clearTimeout(termTimer);
      }
      if (killTimer) {
        clearTimeout(killTimer);
      }
      if (signal) {
        signal.removeEventListener('abort', onAbort);
      }
    };

    const killGroup = (sig) => {
      if (child.pid) {
        try {
          process.kill(-child.pid, sig);
        } catch {
          // group already dead
        }
      }
      try {
        child.kill(sig);
      } catch {
        // direct child already dead
      }
    };

    const killNow = () => killGroup('SIGKILL');

    const onAbort = () => {
      if (!killed) {
        killed = true;
        killGroup('SIGTERM');
        killTimer = setTimeout(killNow, 2000);
      }
    };

    if (signal) {
      if (signal.aborted) {
        onAbort();
      } else {
        signal.addEventListener('abort', onAbort, { once: true });
      }
    }

    termTimer = setTimeout(() => {
      if (!killed) {
        killed = true;
        killGroup('SIGTERM');
        killTimer = setTimeout(killNow, 2000);
      }
    }, timeout);

    child.on('error', (err) => {
      cleanup();
      resolve({ code: -1, stderr: stderr + String(err), killed });
    });
    child.on('close', (code) => {
      cleanup();
      resolve({ code, stderr, killed });
    });
  });
}

/**
 * Parses the response content length from HTTP headers.
 * @param {Headers} headers - The headers containing the content length.
 * @return {number|null} The content length in bytes, or `null` when unavailable or invalid.
 */

function contentLength(headers) {
  const v = headers?.get?.('content-length');
  if (v == null) {
    return null;
  }
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/**
 * Fetches a resource as text while enforcing its applicable body-size limit.
 * @param {string} url - The resource URL.
 * @param {Object} [opts] - Fetch and retry options.
 * @returns {string} The response body as text.
 */
async function fetchText(url, opts = {}) {
  const resourceKind = classifyResource(url);
  const cap = opts.bodyCap ?? maxBodyBytes(resourceKind);
  const res = await fetchResWithRetry(url, opts);
  const text = await res.text();
  if (cap != null && Buffer.byteLength(text) > cap) {
    throw new DownloaderError('network_error', `${resourceKind} body exceeds size cap`);
  }
  return text;
}

/**
 * Fetch binary content and write it to a file.
 * @param {string} url - The resource URL.
 * @param {string} destPath - The destination file path. MUST be service-derived
 *   (never taken from network data) — callers build it from `mkdtemp()` plus a
 *   generated index, or from the validated output dir plus a whitelisted ext.
 * @param {Object} [opts] - Fetch options, including an optional body size cap.
 */
async function fetchToFile(url, destPath, opts = {}) {
  const resourceKind = classifyResource(url);
  const cap = opts.bodyCap ?? maxBodyBytes(resourceKind);
  const res = await fetchResWithRetry(url, opts);
  const buf = Buffer.from(await res.arrayBuffer());
  if (cap != null && buf.length > cap) {
    throw new DownloaderError('network_error', `${resourceKind} body exceeds size cap`);
  }
  // Defensive assertion of the caller contract above: the destination must be a
  // fully-resolved absolute path. If a future caller ever threads a
  // network-derived name in here, this fails closed instead of writing outside
  // the intended directory.
  if (!isAbsolute(destPath) || resolvePath(destPath) !== destPath) {
    throw new DownloaderError('network_error', 'refusing to write to a non-normalized path');
  }
  // Intentional: the file is the downloaded media itself. `destPath` is fully
  // controlled by the service (join(outputDir, `${uuid}.${ext})` where outputDir
  // is validated under BD_OUTPUT_BASE and `ext` is whitelisted in safeExt), and
  // the URL was SSRF-validated in downloadStream/fetchResOne. This is the
  // service's purpose, not untrusted-data-to-path — flagged by CodeQL only
  // because the bytes originate from the network.
  // codeql[js/http-to-file-access]
  await writeFile(destPath, buf);
  return buf.length;
}

// -- HLS manifest parsing ----------------------------------------------------

// Extract the first variant URL from a master playlist, preserving query
// strings. Parse failures and validation failures (e.g. SSRF to a private IP)
// `continue` to the next candidate rather than `return null` (which would skip
/**
 * Finds the first valid variant URL in an HLS master playlist.
 * @param {string} manifestText - The master playlist contents.
 * @param {string} baseUrl - The URL used to resolve relative variant references.
 * @return {string|null} The resolved variant URL, or `null` if no valid variant is found.
 */
async function pickVariantUrl(manifestText, baseUrl, { lookup } = {}) {
  const lines = manifestText.split('\n').map((l) => l.trim());
  // Pick the variant with the highest BANDWIDTH (best quality) instead of the
  // first valid one; the streamlink primary path uses `--default-stream best`,
  // so the fallback should not silently degrade to the lowest variant.
  let best = null; // { url, bandwidth }
  for (let i = 0; i < lines.length; i += 1) {
    if (!lines[i].startsWith('#EXT-X-STREAM-INF')) continue;
    const bwMatch = /BANDWIDTH=(\d+)/.exec(lines[i]);
    const bandwidth = bwMatch ? Number.parseInt(bwMatch[1], 10) : 0;
    // Per the HLS spec the variant URI must be the immediate next line.
    // Scanning further would risk binding the FOLLOWING variant's URI to
    // this bandwidth on a malformed playlist, so only line i+1 is checked
    // and a variant with no valid URI of its own is skipped entirely.
    const uriLine = lines[i + 1];
    if (!uriLine || uriLine.startsWith('#')) continue;
    let href;
    try {
      href = new URL(uriLine, baseUrl).href;
    } catch {
      continue;
    }
    try {
      await validateUrl(href, { lookup });
    } catch {
      continue;
    }
    if (!best || bandwidth > best.bandwidth) {
      best = { url: href, bandwidth };
    }
  }
  return best ? best.url : null;
}

// -- HLS encrypted-stream detection (kept for streamlink fallback path) ------

// Matches any #EXT-X-KEY or #EXT-X-SESSION-KEY tag that indicates DRM (not
// plain AES-128 with identity keyformat).
const HLS_DRM_RE =
  /#EXT-X-(?:KEY|SESSION-KEY):.*METHOD=(?:SAMPLE-AES|SAMPLE-AES-CTR|SAMPLE-AES-CENC)/i;
const HLS_DRM_KEYFORMAT_RE = /#EXT-X-KEY:.*KEYFORMAT="(?!identity).+"/i;

/**
 * Determines whether an HLS manifest indicates DRM encryption.
 * @param {string} manifestText - The HLS manifest content to inspect.
 * @return {boolean} `true` if the manifest indicates DRM encryption, `false` otherwise.
 */
function isDrmEncrypted(manifestText) {
  return HLS_DRM_RE.test(manifestText) || HLS_DRM_KEYFORMAT_RE.test(manifestText);
}

/**
 * Downloads an HLS manifest by fetching its segments and combining them into an output file.
 * @param {string} url - The HLS manifest URL.
 * @param {string} outPath - The destination file path.
 * @param {Object} [opts] - Download, authentication, lookup, timeout, and cancellation options.
 * @return {Promise<string>} The output file path.
 * @throws {DownloaderError} If the playlist is invalid, exceeds recursion limits, uses unsupported DRM, or the download fails.
 */

export async function downloadManifestFallback(url, outPath, opts = {}) {
  const dlTimeout = opts.timeout ?? DEFAULT_DOWNLOAD_TIMEOUT;
  const bodyCap = opts.bodyCap ?? maxBodyBytes('manifest');
  const lookup = opts.lookup;
  const authHeaders = opts.authHeaders;
  const depth = opts.__depth ?? 0;
  if (depth > MAX_DEPTH) {
    throw new DownloaderError('network_error', 'manifest recursion depth exceeded');
  }

  let aggregate = opts.__aggregate;
  let aggregateTimer = null;
  let ownsAggregate = false;
  if (!aggregate) {
    const ac = new AbortController();
    aggregateTimer = setTimeout(() => ac.abort(), dlTimeout);
    ownsAggregate = true;
    if (opts.signal) {
      if (opts.signal.aborted) {
        ac.abort();
      } else {
        opts.signal.addEventListener('abort', () => ac.abort(), { once: true });
      }
    }
    aggregate = ac.signal;
  }

  try {
    const text = await fetchText(url, {
      signal: aggregate,
      timeout: dlTimeout,
      lookup,
      bodyCap,
      authHeaders,
    });

    // Preflight: not an HLS playlist (an HTML error page can be served with
    // HTTP 200) → fast terminal error instead of retrying junk segment URLs.
    if (!text.trimStart().startsWith('#EXTM3U')) {
      throw new DownloaderError('no_media_found', 'not an HLS playlist (missing #EXTM3U header)');
    }

    // Encrypted HLS: DRM methods or non-identity KEYFORMAT.
    if (/#EXT-X-(?:KEY|SESSION-KEY)/.test(text)) {
      if (isDrmEncrypted(text)) {
        throw new DownloaderError(
          'drm_detected',
          'HLS manifest contains DRM encryption (#EXT-X-KEY with SAMPLE-AES / non-identity KEYFORMAT)',
        );
      }
      // Plain AES-128 with identity keyformat is supported (see below).
    }

    // Master/variant playlist → recurse into the best variant.
    if (/#EXT-X-STREAM-INF/.test(text)) {
      const variantUrl = await pickVariantUrl(text, url, { lookup });
      if (!variantUrl) {
        throw new DownloaderError('network_error', 'master playlist has no variant url');
      }
      return downloadManifestFallback(variantUrl, outPath, {
        ...opts,
        __depth: depth + 1,
        __aggregate: aggregate,
      });
    }

    // Live/unbounded playlist: without #EXT-X-ENDLIST the segment list is a
    // moving snapshot — a concat would silently produce a partial file. The
    // streamlink primary path handles live streams; the fallback fails fast.
    if (!/#EXT-X-ENDLIST/.test(text)) {
      throw new DownloaderError(
        'no_media_found',
        'live HLS stream (no #EXT-X-ENDLIST) is not supported by the fallback',
      );
    }

    // Walk the playlist in order: #EXT-X-KEY declarations apply to the
    // segments that follow them (key rotation), and #EXT-X-MEDIA-SEQUENCE
    // seeds the default IV for AES-128 decryption.
    const segments = [];
    let mediaSequence = 0;
    let currentKey = null;
    for (const line of text.split('\n').map((l) => l.trim())) {
      if (!line) continue;
      if (line.startsWith('#')) {
        if (line.startsWith('#EXT-X-MEDIA-SEQUENCE:')) {
          const n = Number.parseInt(line.slice('#EXT-X-MEDIA-SEQUENCE:'.length), 10);
          if (Number.isFinite(n)) mediaSequence = n;
        } else if (line.startsWith('#EXT-X-KEY:')) {
          currentKey = parseHlsKeyTag(line);
        }
        continue;
      }
      segments.push({ uri: line, key: currentKey });
    }
    if (segments.length === 0) {
      throw new DownloaderError('network_error', 'playlist contains no segments');
    }

    // Re-validate every derived segment URL (SSRF) before fetching. Validation
    // is cached per hostname for the duration of this call so repeated segments
    // from the same CDN host are not re-resolved on every iteration.
    const segUrls = [];
    const segKeys = [];
    const validatedHosts = new Map();
    for (const s of segments) {
      let href;
      try {
        href = new URL(s.uri, url).href;
      } catch {
        href = s.uri;
      }
      const host = (() => {
        try {
          return new URL(href).hostname;
        } catch {
          return href;
        }
      })();
      if (!validatedHosts.has(host)) {
        await validateUrl(href, { lookup });
        validatedHosts.set(host, true);
      }
      segUrls.push(href);
      segKeys.push(s.key);
    }

    const dir = await mkdtemp(join(tmpdir(), 'bd-hls-'));
    const listFile = join(dir, 'concat.txt');
    const segFiles = [];
    const segCap = maxBodyBytes('segment');
    // AES-128 keys are fetched once per distinct key URI.
    const keyCache = new Map();
    const emitProgress = throttledProgress(opts.onProgress);
    let downloadedBytes = 0;
    try {
      for (let i = 0; i < segUrls.length; i += 1) {
        const segPath = join(dir, `seg-${String(i).padStart(5, '0')}.ts`);
        const key = segKeys[i];
        let segBytes;
        if (key) {
          if (!keyCache.has(key.uri)) {
            keyCache.set(
              key.uri,
              fetchHlsKey(key, url, {
                signal: aggregate,
                timeout: dlTimeout,
                lookup,
                authHeaders,
              }),
            );
          }
          const keyBuf = await keyCache.get(key.uri);
          const res = await fetchResWithRetry(segUrls[i], {
            signal: aggregate,
            timeout: dlTimeout,
            lookup,
            bodyCap: segCap,
            authHeaders,
          });
          const buf = Buffer.from(await res.arrayBuffer());
          if (buf.length > segCap) {
            throw new DownloaderError('network_error', 'segment body exceeds size cap');
          }
          // IV default = media sequence of THIS segment (playlist start + i).
          await writeFile(segPath, decryptHlsSegment(buf, keyBuf, key.ivHex, mediaSequence + i));
          segBytes = buf.length;
        } else {
          segBytes = await fetchToFile(segUrls[i], segPath, {
            signal: aggregate,
            timeout: dlTimeout,
            lookup,
            bodyCap: segCap,
            authHeaders,
          });
        }
        segFiles.push(segPath);
        downloadedBytes += segBytes;
        emitProgress({
          phase: 'downloading',
          percent: Math.round(((i + 1) / segUrls.length) * 100),
          downloaded_bytes: downloadedBytes,
        });
      }
      const listBody = segFiles.map((p) => `file '${p.replace(/'/g, "'\\''")}'`).join('\n');
      await writeFile(listFile, listBody, 'utf8');

      const ff = await runSpawn(
        'ffmpeg',
        ['-y', '-f', 'concat', '-safe', '0', '-i', listFile, '-c', 'copy', outPath],
        { timeout: opts.ffmpegTimeout ?? DEFAULT_FFMPEG_TIMEOUT, signal: aggregate },
      );

      if (ff.code !== 0) {
        throw new DownloaderError(
          'network_error',
          `ffmpeg concat failed (code ${ff.code}): ${ff.stderr.slice(-512)}`,
        );
      }
      return outPath;
    } finally {
      await rm(dir, { recursive: true, force: true }).catch(() => {});
    }
  } finally {
    if (ownsAggregate && aggregateTimer) {
      clearTimeout(aggregateTimer);
    }
  }
}

/**
 * Downloads an HLS or DASH stream to a file using streamlink.
 * @param {string} url - The stream URL.
 * @param {string} outPath - The destination file path.
 * @param {Object} [opts] - Download options, including timeout, cancellation signal, URL lookup, and authentication headers.
 * @return {Promise<string>} The destination file path.
 * @throws {DownloaderError} If the download fails and no applicable fallback succeeds.
 */
export async function downloadStream(url, outPath, opts = {}) {
  const timeout = opts.timeout ?? DEFAULT_DOWNLOAD_TIMEOUT;
  const lookup = opts.lookup;

  // Re-validate the derived stream URL (it may come from CDP interception and
  // has not been validated upstream) — SSRF defence before spawning/fetching.
  await validateUrl(url, { lookup });

  const isHls = HLS_RE.test(url);
  const isDash = DASH_RE.test(url);

  const args = ['--progress=force', '--url', url, '--default-stream', 'best', '-o', outPath];
  const emitProgress = throttledProgress(opts.onProgress);

  // Forward captured auth headers to streamlink. Credential values
  // (cookie/authorization) MUST NOT be placed on the command line: argv is
  // world-readable via /proc/<pid>/cmdline and shows up in process listings
  // and crash dumps. Instead we write them to a throwaway streamlink config
  // file and reference it by path.
  let authConfigPath = null;
  try {
    if (opts.authHeaders && Object.keys(opts.authHeaders).length > 0) {
      authConfigPath = await writeAuthHeaderConfig(opts.authHeaders);
      args.push('--config', authConfigPath);
    }

    const result = await runSpawn('streamlink', args, {
      timeout,
      signal: opts.signal,
      // Parse streamlink's forced progress output (`--progress=force` writes
      // `[download] 42.5% of ...` lines to stderr even without a TTY).
      onOutput: (chunk) => {
        const m = /(\d+(?:\.\d+)?)%/.exec(chunk);
        if (m) {
          emitProgress({ phase: 'downloading', percent: Number.parseFloat(m[1]) });
        }
      },
    });

    if (result.code === 0) {
      return outPath;
    }

    // DASH has no manual fallback (no custom parser). HLS falls back to manual
    // segment fetch + ffmpeg concat.
    if (isHls) {
      return downloadManifestFallback(url, outPath, { ...opts, authHeaders: opts.authHeaders });
    }
    throw new DownloaderError(
      'network_error',
      `streamlink failed${isDash ? ' (DASH, no manual fallback)' : ''} (code ${result.code}): ${result.stderr.slice(-512)}`,
    );
  } finally {
    if (authConfigPath) {
      // Await removal so the credential file never outlives the subprocess.
      await unlink(authConfigPath).catch(() => {});
    }
  }
}

/**
 * Write captured auth headers to a throwaway streamlink config file.
 *
 * Each header becomes a `http-header=name=value` line. Referencing the file via
 * `--config` keeps credential values off the process command line (visible
 * through /proc/<pid>/cmdline). The file is the caller's responsibility to
 * delete; `downloadStream` removes it after the subprocess exits.
 *
 * @param {Object} authHeaders - Header name → value map.
 * @return {Promise<string>} Absolute path to the config file.
 */
async function writeAuthHeaderConfig(authHeaders) {
  const lines = [];
  for (const [name, value] of Object.entries(authHeaders)) {
    // One header per line; collapse embedded newlines so a value never spills
    // onto a second config line. streamlink's config parser splits each line on
    // the first `=`, so `=` inside header values is handled correctly.
    const safeValue = String(value).replace(/[\r\n]+/g, ' ');
    lines.push(`http-header=${name}=${safeValue}`);
  }
  const path = join(
    tmpdir(),
    `bd-auth-${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2)}.conf`,
  );
  // Owner-only (0o600) + exclusive-create ('wx') so another process cannot
  // pre-create or read the credential file. The path lives under tmpdir().
  await writeFile(path, lines.join('\n'), { mode: 0o600, flag: 'wx', encoding: 'utf8' });
  return path;
}

// Match `.m3u8`/`.mpd` followed by `?`, `#`, or end-of-string.
const HLS_RE = /\.m3u8([?#]|$)/i;
const DASH_RE = /\.mpd([?#]|$)/i;
