// Shared error vocabulary for the browser-downloader microservice.
//
// The frozen spec defines five public error codes:
//   drm_detected | anti_bot_block | network_error | timeout | no_media_found
//
// `classifyError` maps arbitrary thrown values to one of those codes, but it
// MUST NOT mask genuine code bugs (TypeError / ReferenceError / SyntaxError /
// RangeError) as `network_error`. Such bugs are rethrown so the server logs
// the real stack instead of silently reporting a generic downloader failure.

export const ERROR_CODES = {
  DRM_DETECTED: 'drm_detected',
  ANTI_BOT_BLOCK: 'anti_bot_block',
  NETWORK_ERROR: 'network_error',
  TIMEOUT: 'timeout',
  NO_MEDIA_FOUND: 'no_media_found',
};

// Node errno codes that represent a transport-level failure (not a bug).
const NETWORK_ERRNOS = new Set([
  'ENOTFOUND',
  'ECONNREFUSED',
  'ECONNRESET',
  'ECONNABORTED',
  'EPIPE',
  'EHOSTUNREACH',
  'ENETUNREACH',
  'EAI_AGAIN',
  'ERR_NETWORK',
  'ERR_HTTP2_STREAM_ERROR',
]);

const TIMEOUT_ERRNOS = new Set(['ETIMEDOUT', 'ERR_SOCKET_CONNECTION_TIMEOUT']);

// Genuine JS engine bugs. classifyError must rethrow these rather than
// flatten them into `network_error`.
const BUG_CTORS = [TypeError, ReferenceError, SyntaxError, RangeError];

export class DownloaderError extends Error {
  constructor(code, message, { cause } = {}) {
    super(message || code);
    this.name = 'DownloaderError';
    this.code = code;
    if (cause !== undefined) {
      this.cause = cause;
    }
  }
}

// Hints in an error's name/message that point to a transport-level failure
// rather than a genuine code bug (used when the cause has no errno `code`).
const NETWORK_HINT_RE = /network|fetch|socket|connect|timeout|abort|dns|resolve|econn|getaddrinfo/i;

// Walk an error's `cause` chain looking for evidence of a network/transport
// failure (errno code, AbortError, DownloaderError network/timeout code, or a
// name/message hint). `seen` breaks self-referential cause chains to prevent
/**
 * Determines whether an error's cause chain indicates a network-related failure.
 * @param {*} err - The error whose causes are inspected.
 * @param {Set<object>} seen - Objects already visited while traversing the cause chain.
 * @return {boolean} `true` if the cause chain indicates a network or timeout failure, `false` otherwise.
 */
function causeIsNetwork(err, seen) {
  let cur = err?.cause;
  while (cur) {
    if (cur && typeof cur === 'object') {
      if (seen.has(cur)) {
        break;
      }
      seen.add(cur);
    }
    if (cur && typeof cur.code === 'string') {
      if (NETWORK_ERRNOS.has(cur.code) || TIMEOUT_ERRNOS.has(cur.code)) {
        return true;
      }
    }
    if (cur instanceof DownloaderError) {
      if (cur.code === 'network_error' || cur.code === 'timeout') {
        return true;
      }
    }
    if (cur && cur.name === 'AbortError') {
      return true;
    }
    const hint = `${cur?.name || ''} ${cur?.message || ''}`;
    if (NETWORK_HINT_RE.test(hint)) {
      return true;
    }
    cur = cur?.cause;
  }
  return false;
}

// A genuine code bug (TypeError/ReferenceError/SyntaxError/RangeError) is
// rethrown — UNLESS its cause chain is itself a network failure (e.g. undici's
/**
 * Determines whether an error represents a genuine code bug.
 * @param {*} err - The error to classify.
 * @return {boolean} `true` if the error is a code bug, `false` if it is not or its cause indicates a network failure.
 */
function isBug(err, seen = new WeakSet()) {
  if (!BUG_CTORS.some((Ctor) => err instanceof Ctor)) {
    return false;
  }
  return !causeIsNetwork(err, seen);
}

/**
 * Classifies a thrown value as a downloader error code.
 * @param {*} err - The thrown value to classify.
 * @returns {string} The matching downloader error code.
 * @throws {Error} Re-throws genuine code bugs.
 */
export function classifyError(err) {
  if (!err) {
    return ERROR_CODES.NETWORK_ERROR;
  }
  if (err instanceof DownloaderError) {
    return err.code;
  }

  // Surface genuine code bugs instead of masking them as network errors.
  if (isBug(err)) {
    throw err;
  }

  const code = err.code;
  if (typeof code === 'string') {
    if (TIMEOUT_ERRNOS.has(code)) {
      return ERROR_CODES.TIMEOUT;
    }
    if (NETWORK_ERRNOS.has(code)) {
      return ERROR_CODES.NETWORK_ERROR;
    }
  }

  // fetch() network failures surface as TypeError("fetch failed") with a
  // network `cause`; recurse into the cause for the real errno.
  if (err instanceof TypeError && err.cause) {
    try {
      return classifyError(err.cause);
    } catch {
      // cause was itself a bug — fall through to the default below
    }
  }

  if (err.name === 'AbortError') {
    return ERROR_CODES.TIMEOUT;
  }

  return ERROR_CODES.NETWORK_ERROR;
}
