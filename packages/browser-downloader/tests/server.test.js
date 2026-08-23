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
    ...(init.signal ? { signal: init.signal } : {}),
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

describe('server — NDJSON progress stream', () => {
  beforeEach(() => {
    mocks.validateUrl.mockReset();
    mocks.validateOutputDir.mockReset();
    mocks.download.mockReset();
    mocks.validateUrl.mockResolvedValue(new URL('https://example.com'));
    mocks.validateOutputDir.mockResolvedValue('/output');
  });

  it('streams progress events followed by the final result line', async () => {
    mocks.download.mockImplementation(async (_url, _dir, opts) => {
      opts.onProgress?.({ phase: 'intercepting' });
      opts.onProgress?.({ phase: 'downloading', percent: 42, downloaded_bytes: 1000 });
      return { status: 'success', file_path: '/output/abc.mp4', tier_used: 1 };
    });
    const { server, port } = await start();
    const res = await post(
      port,
      { url: 'https://example.com/v.m3u8', output_dir: '/output' },
      { headers: { accept: 'application/x-ndjson' } },
    );
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toContain('application/x-ndjson');
    const lines = (await res.text())
      .trim()
      .split('\n')
      .map((l) => JSON.parse(l));
    expect(lines[0]).toMatchObject({ phase: 'intercepting' });
    expect(lines[1]).toMatchObject({ phase: 'downloading', percent: 42 });
    expect(lines[2]).toMatchObject({
      status: 'success',
      file_path: '/output/abc.mp4',
      tier_used: 1,
    });
    await stop(server);
  });

  it('streams a failed final line (HTTP 200) when the downloader fails', async () => {
    mocks.download.mockResolvedValue({ status: 'failed', error: 'drm_detected', tier_used: null });
    const { server, port } = await start();
    const res = await post(
      port,
      { url: 'https://example.com/v', output_dir: '/output' },
      { headers: { accept: 'application/x-ndjson' } },
    );
    expect(res.status).toBe(200);
    const lines = (await res.text())
      .trim()
      .split('\n')
      .map((l) => JSON.parse(l));
    expect(lines[lines.length - 1]).toMatchObject({
      status: 'failed',
      error: 'drm_detected',
    });
    await stop(server);
  });

  it('keeps the default JSON contract when no NDJSON accept header is sent', async () => {
    mocks.download.mockResolvedValue({
      status: 'success',
      file_path: '/output/abc.mp4',
      tier_used: 1,
    });
    const { server, port } = await start();
    const res = await post(port, { url: 'https://example.com/v', output_dir: '/output' });
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toContain('application/json');
    await stop(server);
  });
});

describe('server — prometheus metrics', () => {
  beforeEach(() => {
    mocks.validateUrl.mockReset();
    mocks.validateOutputDir.mockReset();
    mocks.download.mockReset();
    mocks.validateUrl.mockResolvedValue(new URL('https://example.com'));
    mocks.validateOutputDir.mockResolvedValue('/output');
  });

  it('exposes /metrics with download counters and duration histogram', async () => {
    const { server, port } = await start();
    const res = await fetch(`http://127.0.0.1:${port}/metrics`);
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toContain('text/plain');
    const body = await res.text();
    expect(body).toContain('bd_downloads_total');
    expect(body).toContain('bd_download_duration_seconds');
    await stop(server);
  });

  it('records a download outcome as a counter increment', async () => {
    mocks.download.mockResolvedValue({
      status: 'success',
      file_path: '/output/abc.mp4',
      tier_used: 1,
    });
    const { server, port } = await start();
    await post(port, { url: 'https://example.com/v', output_dir: '/output' });
    const body = await (await fetch(`http://127.0.0.1:${port}/metrics`)).text();
    // Label order in the exposition format is registry-internal — assert the
    // counter line carries the outcome values, not their exact ordering.
    expect(body).toMatch(/bd_downloads_total\{[^}]*status="success"/);
    expect(body).toMatch(/bd_downloads_total\{[^}]*tier="1"/);
    expect(body).toMatch(/bd_downloads_total\{[^}]*error="none"/);
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
    const entered = [];
    const bothEntered = new Promise((resolveBarrier) => {
      mocks.download.mockImplementation(() => {
        entered.push(true);
        if (entered.length === 2) {
          resolveBarrier();
        }
        return new Promise((resolve) => gate.push(resolve));
      });
    });
    const { server, port } = await start();
    const body = { url: 'https://example.com/v', output_dir: '/output' };
    const r1 = post(port, body);
    const r2 = post(port, body);
    await bothEntered; // both requests have acquired a semaphore slot
    const r3 = await post(port, body);
    expect(r3.status).toBe(503);
    expect((await r3.json()).error).toBe('concurrency_limit');
    // 503 rejections must be visible in metrics (previously unrecorded).
    const metricsBody = await (await fetch(`http://127.0.0.1:${port}/metrics`)).text();
    expect(metricsBody).toMatch(/bd_downloads_total\{[^}]*error="concurrency_limit"/);
    for (const resolve of gate) {
      resolve({ status: 'success', file_path: '/output/x.mp4', tier_used: 1 });
    }
    await r1;
    await r2;
    await stop(server);
  });
});

describe('server — client disconnect cancels the download', () => {
  beforeEach(() => {
    mocks.validateUrl.mockReset();
    mocks.validateOutputDir.mockReset();
    mocks.download.mockReset();
    mocks.validateUrl.mockResolvedValue(new URL('https://example.com'));
    mocks.validateOutputDir.mockResolvedValue('/output');
  });

  it('aborts the download signal and releases the slot when the client disconnects', async () => {
    const { server, port } = await start();
    mocks.download.mockImplementation(async () => {
      await new Promise((r) => setTimeout(r, 150));
      return { status: 'success', file_path: '/output/abc.mp4', tier_used: 1 };
    });
    const ac = new AbortController();
    const req = post(
      port,
      { url: 'https://example.com/v', output_dir: '/output' },
      {
        signal: ac.signal,
      },
    );
    // Abort the client side before the download settles.
    await new Promise((r) => setTimeout(r, 20));
    ac.abort();
    req.catch(() => {}); // AbortError is expected — swallow it

    // The server-side socket close is asynchronous; poll briefly for it.
    let aborted = false;
    for (let i = 0; i < 40 && !aborted; i += 1) {
      await new Promise((r) => setTimeout(r, 25));
      aborted = mocks.download.mock.calls[0]?.[2]?.signal?.aborted === true;
    }
    expect(mocks.download).toHaveBeenCalledTimes(1);
    expect(aborted).toBe(true);

    // Let the mocked download settle so the request handler finishes and the
    // server can close cleanly (the response write is guarded by canRespond).
    await new Promise((r) => setTimeout(r, 200));
    await stop(server);
  });
});

describe('server — /metrics failure path', () => {
  it('returns 500 instead of crashing when the registry read throws', async () => {
    const { registry } = await import('../src/metrics.js');
    const spy = vi.spyOn(registry, 'metrics').mockRejectedValueOnce(new Error('registry boom'));
    const { server, port } = await start();
    const res = await fetch(`http://127.0.0.1:${port}/metrics`);
    expect(res.status).toBe(500);
    expect(await res.json()).toEqual({ status: 'failed', error: 'metrics_error' });
    spy.mockRestore();
    await stop(server);
  });
});

describe('server — 400 rejections are recorded in metrics', () => {
  it('records invalid_request for a missing output_dir', async () => {
    const { server, port } = await start();
    const res = await post(port, { url: 'https://example.com/v' });
    expect(res.status).toBe(400);
    const body = await (await fetch(`http://127.0.0.1:${port}/metrics`)).text();
    expect(body).toMatch(/bd_downloads_total\{[^}]*error="invalid_request"/);
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
    await once(second, 'error');
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
    // The error handler logs + exits without re-emitting the event, so wait
    // for the (mocked) process.exit instead of `once(server, 'error')`.
    await vi.waitFor(() => expect(exitSpy).toHaveBeenCalledWith(1));
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

  it('aborts the hung download so the browser/subprocesses do not outlive the slot', async () => {
    process.env.BD_REQUEST_TIMEOUT_MS = '80';
    let observed;
    mocks.download.mockImplementationOnce((_url, _dir, opts) => {
      observed = opts.signal;
      return new Promise(() => {}); // never settles
    });
    const { server, port } = await start();
    const res = await post(port, { url: 'https://example.com/v', output_dir: '/output' });
    expect(res.status).toBe(502);
    expect(observed).toBeInstanceOf(AbortSignal);
    // Releasing the semaphore is not enough — without the abort, Chromium/CDP
    // and streamlink would keep running while a new request takes the slot.
    expect(observed.aborted).toBe(true);
    await stop(server);
  });

  it('cancels the signal on the success path too (nothing outlives the request)', async () => {
    let observed;
    mocks.download.mockImplementationOnce((_url, _dir, opts) => {
      observed = opts.signal;
      return Promise.resolve({ status: 'success', file_path: '/output/x.mp4', tier_used: 1 });
    });
    const { server, port } = await start();
    const res = await post(port, { url: 'https://example.com/v', output_dir: '/output' });
    expect(res.status).toBe(200);
    expect(observed.aborted).toBe(true);
    await stop(server);
  });

  it('does not raise an unhandled rejection when the download rejects after the timeout', async () => {
    process.env.BD_REQUEST_TIMEOUT_MS = '50';
    const unhandled = vi.fn();
    process.on('unhandledRejection', unhandled);
    mocks.download.mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          setTimeout(() => reject(new Error('late browser failure')), 120);
        }),
    );
    const { server, port } = await start();
    const res = await post(port, { url: 'https://example.com/v', output_dir: '/output' });
    expect(res.status).toBe(502);
    await new Promise((r) => setTimeout(r, 200)); // let the late rejection land
    expect(unhandled).not.toHaveBeenCalled();
    process.off('unhandledRejection', unhandled);
    await stop(server);
  });
});
