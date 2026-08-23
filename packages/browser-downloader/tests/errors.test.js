import { describe, expect, it } from 'vitest';

import { DownloaderError, ERROR_CODES, classifyError } from '../src/errors.js';

describe('errors — classifyError', () => {
  it('returns the code of a DownloaderError unchanged', () => {
    expect(classifyError(new DownloaderError('drm_detected'))).toBe(ERROR_CODES.DRM_DETECTED);
    expect(classifyError(new DownloaderError('anti_bot_block'))).toBe(ERROR_CODES.ANTI_BOT_BLOCK);
    expect(classifyError(new DownloaderError('no_media_found'))).toBe(ERROR_CODES.NO_MEDIA_FOUND);
  });

  it('maps network errno codes to network_error', () => {
    const e = new Error('connect refused');
    e.code = 'ECONNREFUSED';
    expect(classifyError(e)).toBe(ERROR_CODES.NETWORK_ERROR);
  });

  it('maps ENOTFOUND to network_error', () => {
    const e = new Error('not found');
    e.code = 'ENOTFOUND';
    expect(classifyError(e)).toBe(ERROR_CODES.NETWORK_ERROR);
  });

  it('maps ETIMEDOUT to timeout', () => {
    const e = new Error('timed out');
    e.code = 'ETIMEDOUT';
    expect(classifyError(e)).toBe(ERROR_CODES.TIMEOUT);
  });

  it('maps AbortError to timeout', () => {
    const e = new Error('aborted');
    e.name = 'AbortError';
    expect(classifyError(e)).toBe(ERROR_CODES.TIMEOUT);
  });

  it('maps a fetch() TypeError with a network cause to network_error', () => {
    const cause = new Error('ECONNREFUSED');
    cause.code = 'ECONNREFUSED';
    const e = new TypeError('fetch failed');
    e.cause = cause;
    expect(classifyError(e)).toBe(ERROR_CODES.NETWORK_ERROR);
  });

  it('does NOT map a bare TypeError to network_error (surfaces the bug)', () => {
    expect(() => classifyError(new TypeError('boom'))).toThrow(TypeError);
  });

  it('does NOT map a ReferenceError to network_error (surfaces the bug)', () => {
    expect(() => classifyError(new ReferenceError('x is not defined'))).toThrow(ReferenceError);
  });

  it('does NOT map a SyntaxError to network_error (surfaces the bug)', () => {
    expect(() => classifyError(new SyntaxError('oops'))).toThrow(SyntaxError);
  });

  it('does NOT map a RangeError to network_error (surfaces the bug)', () => {
    expect(() => classifyError(new RangeError('out of range'))).toThrow(RangeError);
  });

  it('rethrows a TypeError whose cause is an unrelated Error (real bug, not network)', () => {
    const e = new TypeError('boom', { cause: new Error('whatever unrelated') });
    expect(() => classifyError(e)).toThrow(TypeError);
  });

  it('rethrows a TypeError whose cause is a TypeError (bug in the cause, not network)', () => {
    const e = new TypeError('boom', { cause: new TypeError('inner bug') });
    expect(() => classifyError(e)).toThrow(TypeError);
  });

  it('classifies a TypeError whose cause carries a timeout errno as timeout (not bug)', () => {
    const cause = new Error('connect timeout');
    cause.code = 'ETIMEDOUT';
    const e = new TypeError('fetch failed', { cause });
    expect(classifyError(e)).toBe(ERROR_CODES.TIMEOUT);
  });

  it('classifies a TypeError whose cause carries a network errno as network_error', () => {
    const cause = new Error('connect refused');
    cause.code = 'ECONNRESET';
    const e = new TypeError('fetch failed', { cause });
    expect(classifyError(e)).toBe(ERROR_CODES.NETWORK_ERROR);
  });

  it('does not infinite-loop on a self-referential cause chain', () => {
    const e = new TypeError('loop');
    e.cause = e; // self-reference
    expect(() => classifyError(e)).toThrow(TypeError);
  });

  it('defaults to network_error for unknown generic errors', () => {
    expect(classifyError(new Error('something broke'))).toBe(ERROR_CODES.NETWORK_ERROR);
  });
});
