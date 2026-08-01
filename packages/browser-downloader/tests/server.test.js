import { once } from 'node:events';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  download: vi.fn(),
  validateUrl: vi.fn(),
  validateOutputDir: vi.fn(),
}));

vi.mock('../src/downloader.js', () => ({ download: (...a) => mocks.download(...a) }));
vi.mock('../src/validate.js', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    validateUrl: (...a) => mocks.validateUrl(...a),
    validateOutputDir: (...a) => mocks.validateOutputDir(...a),
  };
});

import { createApp, startServer } from '../src/server.js';

async function start() {
  const server = createApp().listen(0);
  await once(server, 'listening');
  return { server, port: server.address().port };
}

function stop(server) {
  return new Promise((resolve) => server.close(() => resolve()));
}

function post(port, body, init = {}) {
  return fetch(`http://127.0.0.1:${port}/download`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...(init.headers || {}) },
    body: typeof body === 'string' ? body : JSON.stringify(body),
  });
}

describe('server — GET /health', () => {
  it('returns { status: "ok" }', async () => {
    const { server, port } = await start();
    const res = await fetch(`http://127.0.0.1:${port}/health`);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: 'ok' });
    await stop(server);
  });
});

describe('server — POST /download client errors (400)', () => {
  let env;
  beforeEach(() => {
    env = { ...process.env };
    mocks.validateUrl.mockReset();
    mocks.validateOutputDir.mockReset();
    mocks.download.mockReset();
    mocks.validateUrl.mockResolvedValue(new URL('https://example.com'));
    mocks.validateOutputDir.mockResolvedValue('/output');
  });
  afterEach(() => {
    process.env = env;
  });

  it('rejects missing url/output_dir with 400', async () => {
    const { server, port } = await start();
    const res = await post(port, { url: 'https://x' });
    expect(res.status).toBe(400);
    const json = await res.json();
    expect(json.error).toBe('invalid_request');
    await stop(server);
  });

  it('rejects a non-string body object with 400', async () => {
    const { server, port } = await start();
    const res = await post(port, { url: 123, output_dir: '/output' });
    expect(res.status).toBe(400);
    await stop(server);
  });

  it('rejects an invalid url (validation failure) with 400', async () => {
    mocks.validateUrl.mockRejectedValue(new Error('private host blocked'));
    const { server, port } = await start();
    const res = await post(port, { url: 'http://127.0.0.1/', output_dir: '/output' });
    expect(res.status).toBe(400);
    const json = await res.json();
    expect(json.error).toBe('invalid_request');
    expect(json.message).toMatch(/private/);
    await stop(server);
  });

  it('rejects malformed JSON with 400', async () => {
    const { server, port } = await start();
    const res = await post(port, '{ not json', {
      headers: { 'content-type': 'application/json' },
    });
    expect(res.status).toBe(400);
    const json = await res.json();
    expect(json.error).toBe('invalid_request');
    await stop(server);
  });

  it('rejects an oversized body (413) as 400', async () => {
    const { server, port } = await start();
    const big = 'x'.repeat(1_100_000);
    const res = await post(port, { url: 'https://x', output_dir: '/output', pad: big });
    expect(res.status).toBe(400);
    await stop(server);
  });
});

describe('server — POST /download gateway errors (502) and success (200)', () => {
  beforeEach(() => {
    mocks.validateUrl.mockReset();
    mocks.validateOutputDir.mockReset();
    mocks.download.mockReset();
    mocks.validateUrl.mockResolvedValue(new URL('https://example.com'));
    mocks.validateOutputDir.mockResolvedValue('/output');
  });

  it('returns 502 when the downloader reports a failed status', async () => {
    mocks.download.mockResolvedValue({ status: 'failed', error: 'drm_detected', tier_used: null });
    const { server, port } = await start();
    const res = await post(port, { url: 'https://example.com/v', output_dir: '/output' });
    expect(res.status).toBe(502);
    expect(await res.json()).toMatchObject({ status: 'failed', error: 'drm_detected' });
    await stop(server);
  });

  it('returns 502 and logs when the downloader throws unexpectedly', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    mocks.download.mockRejectedValue(new TypeError('boom'));
    const { server, port } = await start();
    const res = await post(port, { url: 'https://example.com/v', output_dir: '/output' });
    expect(res.status).toBe(502);
    const json = await res.json();
    expect(json.status).toBe('failed');
    expect(errSpy).toHaveBeenCalled(); // stack logged
    errSpy.mockRestore();
    await stop(server);
  });

  it('returns 200 on success', async () => {
    mocks.download.mockResolvedValue({
      status: 'success',
      file_path: '/output/abc.mp4',
      tier_used: 1,
    });
    const { server, port } = await start();
    const res = await post(port, { url: 'https://example.com/v.mp4', output_dir: '/output' });
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({
      status: 'success',
      file_path: '/output/abc.mp4',
      tier_used: 1,
    });
    await stop(server);
  });
});

describe('server — concurrency overflow (503)', () => {
  beforeEach(() => {
    mocks.validateUrl.mockReset();
    mocks.validateOutputDir.mockReset();
    mocks.download.mockReset();
    mocks.validateUrl.mockResolvedValue(new URL('https://example.com'));
    mocks.validateOutputDir.mockResolvedValue('/output');
  });

  it('returns 503 when 2 downloads are in flight (semaphore max=2)', async () => {
    const gate = [];
    mocks.download.mockImplementation(() => new Promise((resolve) => gate.push(resolve)));
    const { server, port } = await start();
    const body = { url: 'https://example.com/v', output_dir: '/output' };
    const r1 = post(port, body);
    const r2 = post(port, body);
    await new Promise((r) => setTimeout(r, 30)); // let both acquire
    const r3 = await post(port, body);
    expect(r3.status).toBe(503);
    expect((await r3.json()).error).toBe('concurrency_limit');
    for (const resolve of gate) {
      resolve({ status: 'success', file_path: '/output/x.mp4', tier_used: 1 });
    }
    await r1;
    await r2;
    await stop(server);
  });
});

describe('server — error event handling (EADDRINUSE)', () => {
  it('handles the server error event without an unhandled crash', async () => {
    const exitSpy = vi.spyOn(process, 'exit').mockImplementation(() => undefined);
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const first = createApp().listen(0);
    await once(first, 'listening');
    const port = first.address().port;

    const second = startServer(createApp(), port); // EADDRINUSE
    await new Promise((r) => setTimeout(r, 50));
    expect(exitSpy).toHaveBeenCalledWith(1);
    expect(errSpy).toHaveBeenCalled();

    second.close?.(() => {});
    await stop(first);
    exitSpy.mockRestore();
    errSpy.mockRestore();
  });

  it('handles an EACCES listen error (and any fatal listen error) without crashing', async () => {
    const exitSpy = vi.spyOn(process, 'exit').mockImplementation(() => undefined);
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const server = startServer(createApp(), 0);
    await once(server, 'listening');
    server.emit('error', Object.assign(new Error('permission denied'), { code: 'EACCES' }));
    await new Promise((r) => setTimeout(r, 30));
    expect(exitSpy).toHaveBeenCalledWith(1);
    expect(errSpy).toHaveBeenCalled();
    await stop(server);
    exitSpy.mockRestore();
    errSpy.mockRestore();
  });
});

describe('server — per-request timeout releases the semaphore slot', () => {
  let env;
  beforeEach(() => {
    env = { ...process.env };
    mocks.validateUrl.mockReset();
    mocks.validateOutputDir.mockReset();
    mocks.download.mockReset();
    mocks.validateUrl.mockResolvedValue(new URL('https://example.com'));
    mocks.validateOutputDir.mockResolvedValue('/output');
  });
  afterEach(() => {
    process.env = env;
  });

  it('returns 502 and frees the slot when a download hangs past the request timeout', async () => {
    process.env.BD_REQUEST_TIMEOUT_MS = '80';
    // First download never resolves → the request-timeout race must reject.
    mocks.download.mockImplementationOnce(() => new Promise(() => {}));
    mocks.download.mockResolvedValue({
      status: 'success',
      file_path: '/output/x.mp4',
      tier_used: 1,
    });
    const { server, port } = await start();
    const res1 = await post(port, { url: 'https://example.com/v', output_dir: '/output' });
    expect(res1.status).toBe(502); // timed out
    // The slot was released by the timeout: a follow-up request is NOT 503.
    const res2 = await post(port, { url: 'https://example.com/v', output_dir: '/output' });
    expect(res2.status).toBe(200);
    await stop(server);
  });
});
