import { describe, expect, it, vi } from 'vitest';

import { interceptMedia } from '../src/tier1-cdp.js';

function makePage({ drm = false, block = false, getResponseBody, authHeaders = null } = {}) {
  const handlers = {};
  const pageHandlers = {};
  const client = {
    send: vi.fn(async (cmd, args) => {
      if (cmd === 'Network.getResponseBody') {
        return getResponseBody ? getResponseBody(args) : { body: '', base64Encoded: false };
      }
      return {};
    }),
    on: vi.fn((event, cb) => {
      if (!handlers[event]) {
        handlers[event] = [];
      }
      handlers[event].push(cb);
    }),
    detach: vi.fn(async () => {}),
  };
  const page = {
    context: () => ({ newCDPSession: async () => client }),
    addInitScript: vi.fn(async () => {}),
    on: vi.fn((event, cb) => {
      if (!pageHandlers[event]) {
        pageHandlers[event] = [];
      }
      pageHandlers[event].push(cb);
    }),
    waitForLoadState: vi.fn(async () => {}),
    goto: vi.fn(async () => {}),
    evaluate: vi.fn(async (fn) => {
      const src = fn.toString();
      if (src.includes('__bd_drm')) {
        return drm;
      }
      if (src.includes('__bd_auth_headers')) {
        return authHeaders;
      }
      if (src.includes('document.title')) {
        return block;
      }
      // Fail loudly instead of returning undefined. A falsy default would let a
      // renamed marker (e.g. __bd_drm) silently turn a security assertion into
      // a pass against `undefined` rather than against real detection.
      throw new Error(`unexpected page.evaluate probe in test fake:\n${src}`);
    }),
  };
  const emit = (event, payload) => {
    for (const cb of handlers[event] || []) {
      cb(payload);
    }
  };
  const emitPage = (event, payload) => {
    for (const cb of pageHandlers[event] || []) {
      cb(payload);
    }
  };
  return { page, client, emit, emitPage };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('tier1-cdp — interception', () => {
  it('returns bytes for a direct video response', async () => {
    const body = Buffer.from('video-bytes');
    const { page, client, emit } = makePage({
      getResponseBody: () => ({ body: body.toString('base64'), base64Encoded: true }),
    });
    const p = interceptMedia(page, 'https://x/v.mp4', { timeout: 1000 });
    await flush();
    emit('Network.responseReceived', {
      requestId: 'r1',
      response: { url: 'https://x/v.mp4', mimeType: 'video/mp4', status: 200 },
    });
    emit('Network.loadingFinished', { requestId: 'r1' });
    const result = await p;
    expect(result).toMatchObject({ kind: 'bytes', ext: 'mp4' });
    expect(result.buffer.toString()).toBe('video-bytes');
    expect(client.detach).toHaveBeenCalledTimes(1);
  });

  it('rejects promptly when the AbortSignal fires before any media is found', async () => {
    const ac = new AbortController();
    const { page, client } = makePage({});
    const p = interceptMedia(page, 'https://x/v.mp4', { timeout: 1000, signal: ac.signal });
    ac.abort();
    await expect(p).rejects.toMatchObject({ code: 'timeout' });
    expect(client.detach).toHaveBeenCalledTimes(1);
  });

  it('rejects immediately when the signal is already aborted', async () => {
    const ac = new AbortController();
    ac.abort();
    const { page, client } = makePage({});
    const p = interceptMedia(page, 'https://x/v.mp4', { timeout: 1000, signal: ac.signal });
    await expect(p).rejects.toMatchObject({ code: 'timeout' });
    expect(client.detach).toHaveBeenCalledTimes(1);
  });

  it('returns a manifest descriptor for HLS/DASH URLs (routes to streamlink, DRM-free)', async () => {
    const { page, emit } = makePage({
      getResponseBody: () => ({
        body: '#EXTM3U\n#EXTINF:10,\nseg0.ts\n',
        base64Encoded: false,
      }),
    });
    const p = interceptMedia(page, 'https://x/v.m3u8', { timeout: 1000 });
    await flush();
    emit('Network.responseReceived', {
      requestId: 'r1',
      response: {
        url: 'https://x/v.m3u8?token=1',
        mimeType: 'application/vnd.apple.mpegurl',
        status: 200,
      },
    });
    emit('Network.loadingFinished', { requestId: 'r1' });
    const result = await p;
    expect(result).toMatchObject({ kind: 'manifest', ext: 'mp4' });
    expect(result.streamUrl).toBe('https://x/v.m3u8?token=1');
  });

  it('detects DRM in an HLS manifest (#EXT-X-KEY with SAMPLE-AES) and throws drm_detected', async () => {
    const { page, client, emit } = makePage({
      getResponseBody: () => ({
        body: '#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://key"\nseg0.ts\n',
        base64Encoded: false,
      }),
    });
    const p = interceptMedia(page, 'https://x/v.m3u8', { timeout: 1000 });
    await flush();
    emit('Network.responseReceived', {
      requestId: 'r1',
      response: { url: 'https://x/v.m3u8', mimeType: 'application/vnd.apple.mpegurl', status: 200 },
    });
    emit('Network.loadingFinished', { requestId: 'r1' });
    await expect(p).rejects.toMatchObject({ code: 'drm_detected' });
    expect(client.detach).toHaveBeenCalledTimes(1);
  });

  it('detects DRM in an HLS manifest (non-identity KEYFORMAT) and throws drm_detected', async () => {
    const { page, client, emit } = makePage({
      getResponseBody: () => ({
        body: '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k",KEYFORMAT="com.apple.streamingkeydelivery"\nseg0.ts\n',
        base64Encoded: false,
      }),
    });
    const p = interceptMedia(page, 'https://x/v.m3u8', { timeout: 1000 });
    await flush();
    emit('Network.responseReceived', {
      requestId: 'r1',
      response: { url: 'https://x/v.m3u8', mimeType: 'application/vnd.apple.mpegurl', status: 200 },
    });
    emit('Network.loadingFinished', { requestId: 'r1' });
    await expect(p).rejects.toMatchObject({ code: 'drm_detected' });
    expect(client.detach).toHaveBeenCalledTimes(1);
  });

  it('detects DRM in a #EXT-X-SESSION-KEY tag (SAMPLE-AES) and throws drm_detected', async () => {
    const { page, client, emit } = makePage({
      getResponseBody: () => ({
        body: '#EXTM3U\n#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES,URI="skd://key"\nseg0.ts\n',
        base64Encoded: false,
      }),
    });
    const p = interceptMedia(page, 'https://x/v.m3u8', { timeout: 1000 });
    await flush();
    emit('Network.responseReceived', {
      requestId: 'r1',
      response: { url: 'https://x/v.m3u8', mimeType: 'application/vnd.apple.mpegurl', status: 200 },
    });
    emit('Network.loadingFinished', { requestId: 'r1' });
    await expect(p).rejects.toMatchObject({ code: 'drm_detected' });
    expect(client.detach).toHaveBeenCalledTimes(1);
  });

  it('detects DRM in a DASH manifest (ContentProtection) and throws drm_detected', async () => {
    const { page, client, emit } = makePage({
      getResponseBody: () => ({
        body: '<MPD><Period><AdaptationSet><ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/></AdaptationSet></Period></MPD>',
        base64Encoded: false,
      }),
    });
    const p = interceptMedia(page, 'https://x/v.mpd', { timeout: 1000 });
    await flush();
    emit('Network.responseReceived', {
      requestId: 'r1',
      response: { url: 'https://x/v.mpd', mimeType: 'application/dash+xml', status: 200 },
    });
    emit('Network.loadingFinished', { requestId: 'r1' });
    await expect(p).rejects.toMatchObject({ code: 'drm_detected' });
    expect(client.detach).toHaveBeenCalledTimes(1);
  });

  it('attaches authHeaders to manifest result when captured via CDP', async () => {
    const { page, emit } = makePage({
      getResponseBody: () => ({
        body: '#EXTM3U\n#EXTINF:10,\nseg0.ts\n',
        base64Encoded: false,
      }),
    });
    const p = interceptMedia(page, 'https://x/v.m3u8', { timeout: 1000 });
    await flush();
    // CDP normalizes header names to lowercase.
    emit('Network.requestWillBeSent', {
      requestId: 'r1',
      request: { headers: { referer: 'https://x/page', origin: 'https://x' } },
    });
    emit('Network.responseReceived', {
      requestId: 'r1',
      response: { url: 'https://x/v.m3u8', mimeType: 'application/vnd.apple.mpegurl', status: 200 },
    });
    emit('Network.loadingFinished', { requestId: 'r1' });
    const result = await p;
    expect(result.kind).toBe('manifest');
    expect(result.authHeaders).toEqual({ referer: 'https://x/page', origin: 'https://x' });
  });

  it('allows AES-128 with identity keyformat (not DRM)', async () => {
    const { page, emit } = makePage({
      getResponseBody: () => ({
        body: '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k",KEYFORMAT="identity",IV=0x1234\nseg0.ts\n',
        base64Encoded: false,
      }),
    });
    const p = interceptMedia(page, 'https://x/v.m3u8', { timeout: 1000 });
    await flush();
    emit('Network.responseReceived', {
      requestId: 'r1',
      response: { url: 'https://x/v.m3u8', mimeType: 'application/vnd.apple.mpegurl', status: 200 },
    });
    emit('Network.loadingFinished', { requestId: 'r1' });
    const result = await p;
    expect(result).toMatchObject({ kind: 'manifest' }); // allowed through
  });

  it('rejects a manifest body that exceeds the manifest cap (8 MiB)', async () => {
    const big = 'x'.repeat(9 * 1024 * 1024); // 9 MiB
    const { page, client, emit } = makePage({
      getResponseBody: () => ({ body: big, base64Encoded: false }),
    });
    const p = interceptMedia(page, 'https://x/v.m3u8', { timeout: 1000 });
    await flush();
    emit('Network.responseReceived', {
      requestId: 'r1',
      response: { url: 'https://x/v.m3u8', mimeType: 'application/vnd.apple.mpegurl', status: 200 },
    });
    emit('Network.loadingFinished', { requestId: 'r1' });
    await expect(p).rejects.toMatchObject({ code: 'network_error' });
    expect(client.detach).toHaveBeenCalledTimes(1);
  });
});

describe('tier1-cdp — terminal conditions (no fallthrough)', () => {
  it('throws anti_bot_block on HTTP 403', async () => {
    const { page, client, emit } = makePage();
    const p = interceptMedia(page, 'https://x/v', { timeout: 1000 });
    await flush();
    emit('Network.responseReceived', {
      requestId: 'r1',
      response: { url: 'https://x/v', mimeType: 'text/html', status: 403 },
    });
    await expect(p).rejects.toMatchObject({ code: 'anti_bot_block' });
    expect(client.detach).toHaveBeenCalledTimes(1);
  });

  it('throws anti_bot_block on a block-page (main document)', async () => {
    const { page, client, emitPage } = makePage();
    const p = interceptMedia(page, 'https://x/v', { timeout: 1000 });
    await flush();
    emitPage('response', { status: () => 403, url: () => 'https://x/v' });
    await expect(p).rejects.toMatchObject({ code: 'anti_bot_block' });
    expect(client.detach).toHaveBeenCalledTimes(1);
  });

  it('throws drm_detected when EME is engaged', async () => {
    const { page, client } = makePage({ drm: true });
    const p = interceptMedia(page, 'https://x/v', { timeout: 2000 });
    await expect(p).rejects.toMatchObject({ code: 'drm_detected' });
    expect(client.detach).toHaveBeenCalledTimes(1);
  });

  it('throws anti_bot_block on block-page text indicators', async () => {
    const { page, client } = makePage({ block: true });
    const p = interceptMedia(page, 'https://x/v', { timeout: 2000 });
    await expect(p).rejects.toMatchObject({ code: 'anti_bot_block' });
    expect(client.detach).toHaveBeenCalledTimes(1);
  });
});

describe('tier1-cdp — fallthrough + lifecycle', () => {
  it('resolves null on timeout (fallthrough to Tier 2, never terminal)', async () => {
    const { page, client } = makePage();
    const p = interceptMedia(page, 'https://x/v', { timeout: 200 });
    await expect(p).resolves.toBeNull();
    expect(client.detach).toHaveBeenCalledTimes(1);
  });

  it('clears the fallthrough timer when media resolves early', async () => {
    const body = Buffer.from('abc');
    const { page, client, emit } = makePage({
      getResponseBody: () => ({ body: body.toString('base64'), base64Encoded: true }),
    });
    const clearSpy = vi.spyOn(globalThis, 'clearTimeout');
    const p = interceptMedia(page, 'https://x/v.mp4', { timeout: 1000 });
    await flush();
    emit('Network.responseReceived', {
      requestId: 'r1',
      response: { url: 'https://x/v.mp4', mimeType: 'video/mp4', status: 200 },
    });
    emit('Network.loadingFinished', { requestId: 'r1' });
    const result = await p;
    expect(result.kind).toBe('bytes');
    expect(clearSpy).toHaveBeenCalled(); // fallthrough timer cleared
    expect(client.detach).toHaveBeenCalledTimes(1);
    clearSpy.mockRestore();
  });

  it('rejects oversized response bodies (size cap)', async () => {
    const big = Buffer.alloc(10);
    const { page, client, emit } = makePage({
      getResponseBody: () => ({ body: big.toString('base64'), base64Encoded: true }),
    });
    const p = interceptMedia(page, 'https://x/v.mp4', { timeout: 1000, bodyCap: 5 });
    await flush();
    emit('Network.responseReceived', {
      requestId: 'r1',
      response: { url: 'https://x/v.mp4', mimeType: 'video/mp4', status: 200 },
    });
    emit('Network.loadingFinished', { requestId: 'r1' });
    await expect(p).rejects.toMatchObject({ code: 'network_error' });
    expect(client.detach).toHaveBeenCalledTimes(1);
  });
});

describe('tier1-cdp — code-review fixes (iteration 2)', () => {
  it('does NOT treat a subresource 403 as anti_bot_block (false positive)', async () => {
    const { page, client, emit } = makePage();
    const p = interceptMedia(page, 'https://x/v', { timeout: 300 });
    await flush();
    emit('Network.responseReceived', {
      requestId: 'r1',
      response: { url: 'https://ads.example.com/pixel', mimeType: 'image/png', status: 403 },
    });
    await expect(p).resolves.toBeNull();
    expect(client.detach).toHaveBeenCalledTimes(1);
  });

  it('treats a 403 on the main document (CDP type Document) as anti_bot_block', async () => {
    const { page, client, emit } = makePage();
    const p = interceptMedia(page, 'https://x/v', { timeout: 1000 });
    await flush();
    emit('Network.responseReceived', {
      requestId: 'r1',
      type: 'Document',
      response: { url: 'https://other.example/v', mimeType: 'text/html', status: 403 },
    });
    await expect(p).rejects.toMatchObject({ code: 'anti_bot_block' });
    expect(client.detach).toHaveBeenCalledTimes(1);
  });

  it('detaches the CDP session if Network.enable throws (no leak on early throw)', async () => {
    const client = {
      send: vi.fn(async (cmd) => {
        if (cmd === 'Network.enable') {
          throw new Error('cdp enable failed');
        }
        return {};
      }),
      on: vi.fn(),
      detach: vi.fn(async () => {}),
    };
    const page = {
      context: () => ({ newCDPSession: async () => client }),
      addInitScript: vi.fn(async () => {}),
      on: vi.fn(),
      waitForLoadState: vi.fn(async () => {}),
      goto: vi.fn(async () => {}),
      evaluate: vi.fn(async () => false),
    };
    await expect(interceptMedia(page, 'https://x/v', { timeout: 1000 })).rejects.toThrow(
      /cdp enable/,
    );
    expect(client.detach).toHaveBeenCalledTimes(1);
  });
});
