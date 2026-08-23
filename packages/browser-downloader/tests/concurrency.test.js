import { describe, expect, it } from 'vitest';

import { ConcurrencyLimitError, createSemaphore } from '../src/concurrency.js';

describe('concurrency — semaphore max=2', () => {
  it('allows up to max concurrent acquires', () => {
    const sem = createSemaphore(2);
    const r1 = sem.acquire();
    const r2 = sem.acquire();
    expect(sem.active).toBe(2);
    expect(typeof r1).toBe('function');
    expect(typeof r2).toBe('function');
    r1();
    expect(sem.active).toBe(1);
    r2();
    expect(sem.active).toBe(0);
  });

  it('rejects with ConcurrencyLimitError (503) when at capacity', () => {
    const sem = createSemaphore(2);
    sem.acquire();
    sem.acquire();
    expect(() => sem.acquire()).toThrow(ConcurrencyLimitError);
    const err = (() => {
      try {
        sem.acquire();
      } catch (e) {
        return e;
      }
      return null;
    })();
    expect(err.statusCode).toBe(503);
  });

  it('allows a new acquire after a release', () => {
    const sem = createSemaphore(2);
    const r1 = sem.acquire();
    sem.acquire();
    expect(() => sem.acquire()).toThrow();
    r1();
    expect(() => sem.acquire()).not.toThrow();
  });

  it('release is idempotent', () => {
    const sem = createSemaphore(1);
    const r = sem.acquire();
    r();
    r();
    expect(sem.active).toBe(0);
    sem.acquire();
  });
});
