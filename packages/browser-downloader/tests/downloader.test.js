import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  interceptMedia: vi.fn(),
  detectBlob: vi.fn(),
  downloadStream: vi.fn(),
  launch: vi.fn(),
  newContext: vi.fn(),
  newPage: vi.fn(),
  contextClose: vi.fn(),
  browserClose: vi.fn(),
}));

vi.mock('../src/tier1-cdp.js', () => ({
  interceptMedia: (...a) => mocks.interceptMedia(...a),
}));
vi.mock('../src/tier2-dom.js', () => ({
  detectBlob: (...a) => mocks.detectBlob(...a),
}));
vi.mock('../src/streamlink-backend.js', () => ({
  downloadStream: (...a) => mocks.downloadStream(...a),
}));
// Keep the pure helpers (parseTimeout/safeExt/VIDEO_EXTS) real; stub only the
// async validators so no real DNS / filesystem resolution runs in these tests.
vi.mock('../src/validate.js', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    validateUrl: vi.fn(async (u) => new URL(u)),
    validateOutputDir: vi.fn(async (d) => d),
  };
});
vi.mock('playwright-extra', () => ({
  chromium: { use: vi.fn(), launch: (...a) => mocks.launch(...a) },
}));
vi.mock('puppeteer-extra-plugin-stealth', () => ({
  default: vi.fn(() => ({})),
}));

import { download } from '../src/downloader.js';
import { DownloaderError } from '../src/errors.js';

describe('downloader — orchestration', () => {
  let base;

  beforeEach(async () => {
    base = await mkdtemp(join(tmpdir(), 'bd-dl-'));
    for (const m of Object.values(mocks)) {
      m.mockReset();
    }
    mocks.newPage.mockResolvedValue({});
    mocks.newContext.mockResolvedValue({
      newPage: (...a) => mocks.newPage(...a),
      close: (...a) => mocks.contextClose(...a),
    });
    mocks.launch.mockResolvedValue({
      newContext: (...a) => mocks.newContext(...a),
      close: (...a) => mocks.browserClose(...a),
    });
    mocks.contextClose.mockResolvedValue();
    mocks.browserClose.mockResolvedValue();
    // Isolate the download timeout from any inherited BD_DOWNLOAD_TIMEOUT_MS so
    // the default-value assertions stay deterministic.
    vi.stubEnv('BD_DOWNLOAD_TIMEOUT_MS', '120000');
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('Tier 1 bytes success writes a UUID-named mp4 and reports tier_used:1', async () => {
    mocks.interceptMedia.mockResolvedValue({
      kind: 'bytes',
      buffer: Buffer.from('hello'),
      ext: 'mp4',
    });
    const result = await download('https://example.com/v.mp4', base, {
      tier1Timeout: 1000,
      tier2Timeout: 1000,
    });
    expect(result.status).toBe('success');
    expect(result.tier_used).toBe(1);
    expect(result.file_path).toMatch(/\.mp4$/);
    expect(await readFile(result.file_path, 'utf8')).toBe('hello');
    expect(mocks.detectBlob).not.toHaveBeenCalled();
    expect(mocks.browserClose).toHaveBeenCalledTimes(1);
  });

  it('Tier 1 null falls through to Tier 2 (KEEP ordering)', async () => {
    mocks.interceptMedia.mockResolvedValue(null);
    mocks.detectBlob.mockResolvedValue({
      kind: 'bytes',
      buffer: Buffer.from('blob'),
      ext: 'webm',
    });
    const result = await download('https://example.com/v', base, {
      tier1Timeout: 1000,
      tier2Timeout: 1000,
    });
    expect(result.tier_used).toBe(2);
    expect(result.file_path).toMatch(/\.webm$/);
    expect(await readFile(result.file_path, 'utf8')).toBe('blob');
  });

  it('Tier 1 manifest routes to streamlink (writes via downloadStream)', async () => {
    mocks.interceptMedia.mockResolvedValue({
      kind: 'manifest',
      streamUrl: 'https://x/v.m3u8?token=1',
      ext: 'mp4',
    });
    mocks.downloadStream.mockImplementation(async (_url, outPath) => {
      await writeFile(outPath, 'stream');
      return outPath;
    });
    const result = await download('https://example.com/v.m3u8', base, {
      tier1Timeout: 1000,
    });
    expect(result.tier_used).toBe(1);
    expect(mocks.downloadStream).toHaveBeenCalledTimes(1);
    expect(mocks.downloadStream.mock.calls[0][0]).toBe('https://x/v.m3u8?token=1');
    // Fix #24: a separate (longer) download timeout is passed to streamlink,
    // NOT the Tier 1 interception timeout (tier1Timeout=1000 here).
    expect(mocks.downloadStream.mock.calls[0][2].timeout).toBe(120_000);
    expect(result.file_path).toMatch(/\.mp4$/);
    expect(await readFile(result.file_path, 'utf8')).toBe('stream');
  });

  it('Tier 1 terminal DRM does NOT fall through to Tier 2', async () => {
    mocks.interceptMedia.mockRejectedValue(new DownloaderError('drm_detected'));
    const result = await download('https://example.com/v', base, {});
    expect(result).toMatchObject({
      status: 'failed',
      error: 'drm_detected',
      tier_used: 1,
    });
    expect(mocks.detectBlob).not.toHaveBeenCalled();
    expect(mocks.browserClose).toHaveBeenCalledTimes(1);
  });

  it('Tier 2 no_media_found is reported (terminal after fallthrough)', async () => {
    mocks.interceptMedia.mockResolvedValue(null);
    mocks.detectBlob.mockRejectedValue(new DownloaderError('no_media_found'));
    const result = await download('https://example.com/v', base, {});
    expect(result).toMatchObject({ status: 'failed', error: 'no_media_found' });
  });

  it('never writes an unwhitelisted extension (falls back to mp4)', async () => {
    mocks.interceptMedia.mockResolvedValue({
      kind: 'bytes',
      buffer: Buffer.from('x'),
      ext: 'html',
    });
    const result = await download('https://example.com/v', base, {});
    expect(result.file_path).toMatch(/\.mp4$/);
  });

  it('launches Chromium lazily inside download() (one launch per call)', async () => {
    mocks.interceptMedia.mockResolvedValue({
      kind: 'bytes',
      buffer: Buffer.from('x'),
      ext: 'mp4',
    });
    await download('https://example.com/v.mp4', base, {});
    expect(mocks.launch).toHaveBeenCalledTimes(1);
  });
});

describe('downloader — code-review fixes (iteration 2)', () => {
  let base;
  let env;

  beforeEach(async () => {
    base = await mkdtemp(join(tmpdir(), 'bd-dl-'));
    env = { ...process.env };
    for (const m of Object.values(mocks)) {
      m.mockReset();
    }
    mocks.newPage.mockResolvedValue({});
    mocks.newContext.mockResolvedValue({
      newPage: (...a) => mocks.newPage(...a),
      close: (...a) => mocks.contextClose(...a),
    });
    mocks.launch.mockResolvedValue({
      newContext: (...a) => mocks.newContext(...a),
      close: (...a) => mocks.browserClose(...a),
    });
    mocks.contextClose.mockResolvedValue();
    mocks.browserClose.mockResolvedValue();
  });

  afterEach(() => {
    process.env = env;
  });

  it('closes the browser if newContext throws (no browser leak)', async () => {
    mocks.newContext.mockRejectedValue(new Error('cannot create context'));
    const result = await download('https://example.com/v', base, {});
    expect(result.status).toBe('failed');
    expect(mocks.browserClose).toHaveBeenCalledTimes(1);
    expect(mocks.contextClose).not.toHaveBeenCalled();
  });

  it('respects BD_DOWNLOAD_TIMEOUT_MS for the streamlink download timeout', async () => {
    process.env.BD_DOWNLOAD_TIMEOUT_MS = '60000';
    mocks.interceptMedia.mockResolvedValue({
      kind: 'manifest',
      streamUrl: 'https://x/v.m3u8',
      ext: 'mp4',
    });
    mocks.downloadStream.mockImplementation(async (_u, outPath) => {
      await writeFile(outPath, 's');
      return outPath;
    });
    const result = await download('https://example.com/v.m3u8', base, { tier1Timeout: 1000 });
    expect(result.status).toBe('success');
    expect(mocks.downloadStream.mock.calls[0][2].timeout).toBe(60_000);
  });

  it('fails with storage_error before launching the browser when disk is full', async () => {
    // A free-space floor larger than any real filesystem (1e18 bytes) forces
    // the preflight to fail — BEFORE Chromium is launched. Pre-launch failures
    // reject (same contract as input validation and pre-aborted signals).
    process.env.BD_MIN_FREE_BYTES = '1000000000000000000';
    mocks.interceptMedia.mockResolvedValue({
      kind: 'bytes',
      buffer: Buffer.from('x'),
      ext: 'mp4',
    });
    await expect(download('https://example.com/v.mp4', base, {})).rejects.toMatchObject({
      code: 'storage_error',
    });
    expect(mocks.launch).not.toHaveBeenCalled();
  });

  it('passes onProgress through to downloadStream for manifest jobs', async () => {
    const onProgress = vi.fn();
    mocks.interceptMedia.mockResolvedValue({
      kind: 'manifest',
      streamUrl: 'https://x/v.m3u8',
      ext: 'mp4',
    });
    mocks.downloadStream.mockImplementation(async (_u, outPath) => {
      await writeFile(outPath, 's');
      return outPath;
    });
    await download('https://example.com/v.m3u8', base, {
      tier1Timeout: 1000,
      onProgress,
    });
    expect(mocks.downloadStream.mock.calls[0][2].onProgress).toBe(onProgress);
    // Phase markers are emitted for byte-capture jobs.
    mocks.interceptMedia.mockResolvedValue({
      kind: 'bytes',
      buffer: Buffer.from('x'),
      ext: 'mp4',
    });
    const events = [];
    await download('https://example.com/v.mp4', base, {
      onProgress: (ev) => events.push(ev),
    });
    expect(events).toContainEqual({ phase: 'saving', percent: 100 });
  });
});

describe('downloader — cancellation (opts.signal)', () => {
  let base;

  beforeEach(async () => {
    base = await mkdtemp(join(tmpdir(), 'bd-dl-'));
    for (const m of Object.values(mocks)) {
      m.mockReset();
    }
    mocks.newPage.mockResolvedValue({});
    mocks.newContext.mockResolvedValue({
      newPage: (...a) => mocks.newPage(...a),
      close: (...a) => mocks.contextClose(...a),
    });
    mocks.launch.mockResolvedValue({
      newContext: (...a) => mocks.newContext(...a),
      close: (...a) => mocks.browserClose(...a),
    });
    mocks.contextClose.mockResolvedValue();
    mocks.browserClose.mockResolvedValue();
    vi.stubEnv('BD_DOWNLOAD_TIMEOUT_MS', '120000');
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('closes the browser as soon as the signal aborts mid-download', async () => {
    const ac = new AbortController();
    let closedDuringFlight = false;
    // Abort while Tier 1 is still in flight; the browser must be torn down at
    // that moment, not only when download() finally returns.
    mocks.interceptMedia.mockImplementation(async () => {
      ac.abort();
      await new Promise((r) => setTimeout(r, 10));
      // Assert AFTER the call: an assertion thrown inside the mock would be
      // swallowed by download()'s catch and the test would pass regardless.
      closedDuringFlight = mocks.browserClose.mock.calls.length > 0;
      return { kind: 'bytes', buffer: Buffer.from('x'), ext: 'mp4' };
    });
    await download('https://example.com/v', base, { signal: ac.signal });
    expect(closedDuringFlight).toBe(true);
  });

  it('forwards the signal to the tier functions and to downloadStream', async () => {
    const ac = new AbortController();
    mocks.interceptMedia.mockResolvedValue({
      kind: 'manifest',
      streamUrl: 'https://x/v.m3u8',
      ext: 'mp4',
    });
    mocks.downloadStream.mockImplementation(async (_u, outPath) => {
      await writeFile(outPath, 's');
      return outPath;
    });
    const result = await download('https://example.com/v.m3u8', base, { signal: ac.signal });
    expect(result.status).toBe('success');
    expect(mocks.interceptMedia.mock.calls[0][2].signal).toBe(ac.signal);
    expect(mocks.downloadStream.mock.calls[0][2].signal).toBe(ac.signal);
  });

  it('forwards the signal to Tier 2 on fallthrough', async () => {
    const ac = new AbortController();
    mocks.interceptMedia.mockResolvedValue(null);
    mocks.detectBlob.mockResolvedValue({
      kind: 'bytes',
      buffer: Buffer.from('b'),
      ext: 'webm',
    });
    const result = await download('https://example.com/v', base, { signal: ac.signal });
    expect(result.status).toBe('success');
    expect(mocks.detectBlob.mock.calls[0][1].signal).toBe(ac.signal);
  });

  it('never launches a browser when the signal is already aborted', async () => {
    const ac = new AbortController();
    ac.abort();
    await expect(download('https://example.com/v', base, { signal: ac.signal })).rejects.toThrow(
      /aborted before browser launch/,
    );
    expect(mocks.launch).not.toHaveBeenCalled();
  });
});
