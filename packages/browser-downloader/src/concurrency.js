// Concurrency limiter. Each download request launches a full Chromium, so
// unbounded concurrency causes OOM. This semaphore caps in-flight downloads at
// `max` (default 2, matching YT_DLP_EXTRACTION_CONCURRENCY=2). Requests beyond
// the limit are rejected synchronously so the HTTP layer can return 503
// instead of queueing.

export class ConcurrencyLimitError extends Error {
  constructor(message = 'concurrency limit reached') {
    super(message);
    this.name = 'ConcurrencyLimitError';
    this.statusCode = 503;
    this.code = 'CONCURRENCY_LIMIT';
  }
}

export function createSemaphore(max = 2) {
  if (!Number.isInteger(max) || max <= 0) {
    throw new TypeError('semaphore max must be a positive integer');
  }
  let active = 0;
  return {
    acquire() {
      if (active >= max) {
        throw new ConcurrencyLimitError();
      }
      active += 1;
      let released = false;
      return function release() {
        if (released) {
          return;
        }
        released = true;
        active = Math.max(0, active - 1);
      };
    },
    get active() {
      return active;
    },
    get max() {
      return max;
    },
  };
}
