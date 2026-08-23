import { EventEmitter } from 'node:events';
import { createCipheriv } from 'node:crypto';
import { access } from 'node:fs/promises';
import { afterEach, describe, expect, it, vi } from 'vitest';

const hoisted = vi.hoisted(() => ({ spawn: vi.fn() }));

vi.mock('node:child_process', () => ({
  spawn: (...args) => hoisted.spawn(...args),
}));

// Capture the credential config-file write so we can assert its permissions and
// that it is removed after the subprocess completes.
const fsCapture = vi.hoisted(() => ({ writeFile: null, unlink: null }));
vi.mock('node:fs/promises', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    writeFile: vi.fn(async (path, data, opts) => {
      fsCapture.writeFile = { path, opts };
      return actual.writeFile(path, data, opts);
    }),
    unlink: vi.fn(async (path) => {
      fsCapture.unlink = path;
      return actual.unlink(path);
    }),
  };
});

import { downloadManifestFallback, downloadStream, runSpawn } from '../src/streamlink-backend.js';
import {
  decryptHlsSegment,
  parseHlsKeyTag,
} from '../src/streamlink-backend.js';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  fsCapture.writeFile = null;
  fsCapture.unlink = null;
});

function fakeChild({ autoClose = true, code = 0 } = {}) {
  const child = new EventEmitter();
  child.stderr = new EventEmitter();
  child.stdout = new EventEmitter();
  child.kill = vi.fn((sig) => {
    if (autoClose && (sig === 'SIGTERM' || sig === 'SIGKILL')) {
      child.emit('close', code);
    }
  });
  return child;
}

// DNS lookup stub returning a public address so validateUrl passes in tests
// (no real DNS resolution). Pass as `lookup` to downloadStream/downloadManifestFallback.
const publicLookup = async () => [{ address: '203.0.113.10' }];

// Build a fetch Response-like object with real Headers (so res.headers.get
// works for the redirect + content-length checks in fetchRes).
function okRes({ text, arrayBuffer, status = 200, headers = {} } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(headers),
    ...(text !== undefined ? { text: async () => text } : {}),
    ...(arrayBuffer !== undefined ? { arrayBuffer: async () => arrayBuffer } : {}),
  };
}

describe('streamlink backend — downloadStream', () => {
  it('spawns streamlink and drains stderr on success', async () => {
    const child = fakeChild();
    hoisted.spawn.mockImplementation(() => {
      process.nextTick(() => child.emit('close', 0));
      return child;
    });
    const out = await downloadStream('https://x/v.m3u8', '/tmp/out.mp4', {
      timeout: 5000,
      lookup: publicLookup,
    });
    expect(out).toBe('/tmp/out.mp4');
    expect(hoisted.spawn).toHaveBeenCalled();
    expect(hoisted.spawn.mock.calls[0][0]).toBe('streamlink');
    // stderr 'data' listener attached → pipe drained
    expect(child.stderr.listenerCount('data')).toBeGreaterThanOrEqual(1);
  });

  it('throws network_error when streamlink fails on DASH (no manual fallback)', async () => {
    const child = fakeChild();
    hoisted.spawn.mockImplementation(() => {
      process.nextTick(() => child.emit('close', 1));
      return child;
    });
    await expect(
      downloadStream('https://x/v.mpd', '/tmp/out.mp4', { timeout: 5000, lookup: publicLookup }),
    ).rejects.toThrow(/streamlink failed/);
  });

  it('forwards auth headers via a config file, never on argv', async () => {
    const child = fakeChild();
    hoisted.spawn.mockImplementation(() => {
      process.nextTick(() => child.emit('close', 0));
      return child;
    });
    const out = await downloadStream('https://x/v.m3u8', '/tmp/out.mp4', {
      timeout: 5000,
      lookup: publicLookup,
      authHeaders: { cookie: 'secret-session=abc', authorization: 'Bearer xyz' },
    });
    expect(out).toBe('/tmp/out.mp4');
    const args = hoisted.spawn.mock.calls[0][1];
    // Credentials must not appear on the (world-readable) command line.
    expect(args.join(' ')).not.toContain('secret-session=abc');
    expect(args.join(' ')).not.toContain('Bearer xyz');
    expect(args).toContain('--config');
    // The credential file is created owner-only (0o600) and exclusively.
    expect(fsCapture.writeFile).not.toBeNull();
    expect(fsCapture.writeFile.opts).toMatchObject({ mode: 0o600, flag: 'wx' });
    // And it is removed after the subprocess completes.
    expect(fsCapture.unlink).toBe(fsCapture.writeFile.path);
    await expect(access(fsCapture.writeFile.path)).rejects.toThrow();
  });
});

describe('streamlink backend — runSpawn resource lifecycle', () => {
  it('kills the subprocess on timeout', async () => {
    const child = fakeChild({ autoClose: true });
    hoisted.spawn.mockImplementation(() => child); // never emits close on its own
    await runSpawn('sleep', ['99'], { timeout: 50 });
    expect(child.kill).toHaveBeenCalledWith('SIGTERM');
  });

  it('kills the subprocess when the AbortSignal fires', async () => {
    const child = fakeChild({ autoClose: true });
    hoisted.spawn.mockImplementation(() => child);
    const ac = new AbortController();
    const p = runSpawn('sleep', ['99'], { timeout: 60_000, signal: ac.signal });
    ac.abort();
    await p;
    expect(child.kill).toHaveBeenCalledWith('SIGTERM');
  });
});

describe('streamlink backend — HLS fallback correctness', () => {
  it('fails with drm_detected on encrypted HLS (#EXT-X-KEY with SAMPLE-AES) without fetching segments', async () => {
    let calls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        calls += 1;
        return okRes({
          text: '#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://key"\nseg0.ts\n',
        });
      }),
    );
    await expect(
      downloadManifestFallback('https://x/master.m3u8', '/tmp/out.mp4', { timeout: 1000 }),
    ).rejects.toMatchObject({ code: 'drm_detected' });
    expect(calls).toBe(1); // manifest only — no segment fetch
  });

  it('fetches the AES-128 key and decrypts segments (plain encryption, not DRM)', async () => {
    // Real key/ciphertext: the fallback must fetch the key and decrypt each
    // segment before concat — never write ciphertext as a "success" file.
    const KEY = Buffer.alloc(16, 0x11);
    const IV = Buffer.alloc(16);
    IV[15] = 0x01;
    const plaintext = Buffer.from('hello hls world!!'); // exactly one block
    const cipher = createCipheriv('aes-128-cbc', KEY, IV);
    const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);

    const seen = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url) => {
        seen.push(url);
        if (String(url).includes('key.bin')) {
          return okRes({ arrayBuffer: KEY });
        }
        if (String(url).includes('seg0.ts')) {
          return okRes({ arrayBuffer: ciphertext });
        }
        return okRes({
          text: '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="https://k.example/key.bin",IV=0x00000000000000000000000000000001\n#EXT-X-ENDLIST\nseg0.ts\n',
        });
      }),
    );
    const child = fakeChild();
    hoisted.spawn.mockImplementation(() => {
      process.nextTick(() => child.emit('close', 0));
      return child;
    });
    const out = await downloadManifestFallback('https://x/m.m3u8', '/tmp/out.mp4', {
      timeout: 5000,
      lookup: publicLookup,
    });
    expect(out).toBe('/tmp/out.mp4');
    // manifest + key + segment — no extra fetches
    expect(seen.length).toBe(3);
    expect(seen.some((u) => String(u).includes('key.bin'))).toBe(true);
    // ffmpeg concat ran on the DECRYPTED segments
    expect(hoisted.spawn.mock.calls.some((c) => c[0] === 'ffmpeg')).toBe(true);
  });

  it('recurses into variant playlists preserving query strings', async () => {
    const seen = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url) => {
        seen.push(url);
        if (url.includes('master')) {
          return okRes({
            text: '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000\nvariant.m3u8?token=abc\n',
          });
        }
        if (url.includes('variant.m3u8')) {
          return okRes({ text: '#EXTM3U\n#EXT-X-ENDLIST\nseg0.ts?sig=z\n' });
        }
        return okRes({ arrayBuffer: new ArrayBuffer(8) });
      }),
    );
    const child = fakeChild();
    hoisted.spawn.mockImplementation(() => {
      process.nextTick(() => child.emit('close', 0));
      return child;
    });

    const out = await downloadManifestFallback('https://x/master.m3u8', '/tmp/out.mp4', {
      timeout: 5000,
      lookup: publicLookup,
    });
    expect(out).toBe('/tmp/out.mp4');
    expect(seen.some((u) => u.includes('variant.m3u8?token=abc'))).toBe(true);
    expect(seen.some((u) => u.includes('seg0.ts?sig=z'))).toBe(true);
    // ffmpeg was invoked for concat
    expect(hoisted.spawn.mock.calls.some((c) => c[0] === 'ffmpeg')).toBe(true);
  });

  it('propagates a fetch timeout (AbortController)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url, init) => {
        const e = new Error('aborted');
        e.name = 'AbortError';
        if (init?.signal) {
          throw e;
        }
        throw new Error('no signal');
      }),
    );
    await expect(
      downloadManifestFallback('https://x/m.m3u8', '/tmp/o.mp4', { timeout: 500 }),
    ).rejects.toThrow();
  });
});

describe('streamlink backend — code-review fixes (iteration 2)', () => {
  it('fails with drm_detected on #EXT-X-SESSION-KEY with SAMPLE-AES without fetching segments', async () => {
    let calls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        calls += 1;
        return okRes({
          text: '#EXTM3U\n#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES,URI="skd://key"\nseg0.ts\n',
        });
      }),
    );
    await expect(
      downloadManifestFallback('https://x/master.m3u8', '/tmp/out.mp4', { timeout: 1000 }),
    ).rejects.toMatchObject({ code: 'drm_detected' });
    expect(calls).toBe(1); // manifest only — no segment fetch
  });

  it('pickVariantUrl continues past a bad variant line to a valid one', async () => {
    const seen = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url) => {
        seen.push(url);
        if (url.includes('master')) {
          // First variant line is unparseable (new URL throws); second is valid.
          return okRes({
            text: '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nhttp://[::1\n#EXT-X-STREAM-INF:BANDWIDTH=2\nok.m3u8\n',
          });
        }
        if (url.includes('ok.m3u8')) {
          return okRes({ text: '#EXTM3U\n#EXT-X-ENDLIST\nseg0.ts\n' });
        }
        return okRes({ arrayBuffer: new ArrayBuffer(8) });
      }),
    );
    const child = fakeChild();
    hoisted.spawn.mockImplementation(() => {
      process.nextTick(() => child.emit('close', 0));
      return child;
    });
    const out = await downloadManifestFallback('https://x/master.m3u8', '/tmp/out.mp4', {
      timeout: 5000,
      lookup: publicLookup,
    });
    expect(out).toBe('/tmp/out.mp4');
    // The valid variant (ok.m3u8) was fetched, proving the bad line was skipped.
    expect(seen.some((u) => u.includes('ok.m3u8'))).toBe(true);
  });

  it('rejects a variant that resolves to a private IP (SSRF) and skips it', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url) => {
        if (url.includes('master')) {
          return okRes({
            text: '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nhttp://169.254.169.254/x.m3u8\n',
          });
        }
        return okRes({ text: '#EXTM3U\n#EXT-X-ENDLIST\nseg0.ts\n' });
      }),
    );
    // Only a private-IP variant → none valid → network_error (never fetched).
    await expect(
      downloadManifestFallback('https://x/master.m3u8', '/tmp/out.mp4', {
        timeout: 5000,
        lookup: publicLookup,
      }),
    ).rejects.toThrow(/variant url/);
  });

  it('bounds master-playlist recursion (depth > 5 → network_error)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        okRes({
          // self-referencing master: variant URL is the same master URL
          text: '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nmaster.m3u8\n',
        }),
      ),
    );
    await expect(
      downloadManifestFallback('https://x/master.m3u8', '/tmp/out.mp4', {
        timeout: 60_000,
        lookup: publicLookup,
      }),
    ).rejects.toThrow(/depth exceeded/);
  });

  it('rejects a redirect to a private IP (redirect-SSRF defence)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        okRes({ status: 302, headers: { location: 'http://169.254.169.254/seg.ts' } }),
      ),
    );
    await expect(
      downloadManifestFallback('https://x/m.m3u8', '/tmp/o.mp4', {
        timeout: 5000,
        lookup: publicLookup,
      }),
    ).rejects.toThrow(/private|link-local|resolves/i);
  });

  it('rejects a manifest whose Content-Length exceeds the body cap', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        okRes({ text: 'x'.repeat(100), headers: { 'content-length': '999999999' } }),
      ),
    );
    await expect(
      downloadManifestFallback('https://x/m.m3u8', '/tmp/o.mp4', {
        timeout: 5000,
        lookup: publicLookup,
        bodyCap: 1024,
      }),
    ).rejects.toThrow(/size cap/);
  });

  it('detects HLS/DASH URLs with fragments (#) and query strings (?)', async () => {
    hoisted.spawn.mockImplementation(() => {
      const c = fakeChild();
      process.nextTick(() => c.emit('close', 0));
      return c;
    });
    // `.m3u8#t=0` and `.m3u8?token=` must be detected as HLS (else DASH
    // "no manual fallback" path would throw on a streamlink failure).
    for (const u of ['https://x/stream.m3u8?token=abc', 'https://x/v.m3u8#t=0']) {
      await downloadStream(u, '/tmp/out.mp4', { timeout: 5000, lookup: publicLookup });
    }
    expect(hoisted.spawn).toHaveBeenCalled();
  });
});

describe('streamlink backend — manifest preflight (phase 3)', () => {
  it('rejects a 200 HTML error page (no #EXTM3U) with no_media_found, single fetch', async () => {
    let calls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        calls += 1;
        return okRes({ text: '<html><body>checking your browser...</body></html>' });
      }),
    );
    await expect(
      downloadManifestFallback('https://x/m.m3u8', '/tmp/out.mp4', { timeout: 1000 }),
    ).rejects.toMatchObject({ code: 'no_media_found' });
    expect(calls).toBe(1); // no junk segment retries
  });

  it('rejects a live playlist (no #EXT-X-ENDLIST) with no_media_found', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        okRes({ text: '#EXTM3U\n#EXT-X-TARGETDURATION:6\nseg0.ts\nseg1.ts\n' }),
      ),
    );
    await expect(
      downloadManifestFallback('https://x/live.m3u8', '/tmp/out.mp4', {
        timeout: 1000,
        lookup: publicLookup,
      }),
    ).rejects.toMatchObject({ code: 'no_media_found' });
  });

  it('picks the highest-bandwidth variant, not the first', async () => {
    const seen = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url) => {
        seen.push(url);
        if (url.includes('master')) {
          return okRes({
            text: '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000\nlow.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=5000000\nhigh.m3u8\n',
          });
        }
        if (String(url).endsWith('.m3u8')) {
          return okRes({ text: '#EXTM3U\n#EXT-X-ENDLIST\nseg0.ts\n' });
        }
        return okRes({ arrayBuffer: new ArrayBuffer(8) });
      }),
    );
    const child = fakeChild();
    hoisted.spawn.mockImplementation(() => {
      process.nextTick(() => child.emit('close', 0));
      return child;
    });
    await downloadManifestFallback('https://x/master.m3u8', '/tmp/out.mp4', {
      timeout: 5000,
      lookup: publicLookup,
    });
    expect(seen.some((u) => String(u).includes('high.m3u8'))).toBe(true);
    expect(seen.some((u) => String(u).includes('low.m3u8'))).toBe(false);
  });

  it('emits throttled segment progress events', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url) => {
        if (String(url).endsWith('.m3u8')) {
          return okRes({ text: '#EXTM3U\n#EXT-X-ENDLIST\nseg0.ts\n' });
        }
        return okRes({ arrayBuffer: new ArrayBuffer(8) });
      }),
    );
    const child = fakeChild();
    hoisted.spawn.mockImplementation(() => {
      process.nextTick(() => child.emit('close', 0));
      return child;
    });
    const events = [];
    await downloadManifestFallback('https://x/m.m3u8', '/tmp/out.mp4', {
      timeout: 5000,
      lookup: publicLookup,
      onProgress: (ev) => events.push(ev),
    });
    expect(events.length).toBeGreaterThanOrEqual(1);
    expect(events[0]).toMatchObject({ phase: 'downloading', percent: 100, downloaded_bytes: 8 });
  });
});

describe('streamlink backend — AES-128 key handling (phase 3)', () => {
  it('parseHlsKeyTag parses METHOD/URI/IV and ignores non-AES-128 tags', () => {
    expect(
      parseHlsKeyTag(
        '#EXT-X-KEY:METHOD=AES-128,URI="https://k/key",KEYFORMAT="identity",IV=0x00000000000000000000000000000001',
      ),
    ).toEqual({ uri: 'https://k/key', ivHex: '00000000000000000000000000000001' });
    expect(parseHlsKeyTag('#EXT-X-KEY:METHOD=AES-128,URI="https://k/key"')).toEqual({
      uri: 'https://k/key',
      ivHex: null,
    });
    expect(parseHlsKeyTag('#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://key"')).toBeNull();
    expect(parseHlsKeyTag('#EXT-X-KEY:URI="https://k/key"')).toBeNull();
  });

  it('decryptHlsSegment round-trips with an explicit IV and the media-sequence default', () => {
    const key = Buffer.alloc(16, 0x22);
    const plain = Buffer.from('round trip works!'); // 16 bytes
    const ivHex = '00000000000000000000000000000007';
    const iv = Buffer.from(ivHex, 'hex');
    const cipher = createCipheriv('aes-128-cbc', key, iv);
    const ct = Buffer.concat([cipher.update(plain), cipher.final()]);
    expect(decryptHlsSegment(ct, key, ivHex, 0)).toEqual(plain);
    // Default IV = 16-byte BE media sequence (here: 5).
    const seqIv = Buffer.from('00000000000000000000000000000005', 'hex');
    const cipher2 = createCipheriv('aes-128-cbc', key, seqIv);
    const ct2 = Buffer.concat([cipher2.update(plain), cipher2.final()]);
    expect(decryptHlsSegment(ct2, key, null, 5)).toEqual(plain);
  });

  it('decryptHlsSegment rejects a non-16-byte key', () => {
    expect(() => decryptHlsSegment(Buffer.alloc(16), Buffer.alloc(8), null, 0)).toThrow(
      /invalid length 8/,
    );
  });

  it('rejects an AES-128 key URI that resolves to a private IP (SSRF)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        okRes({
          text: '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="http://169.254.169.254/key"\n#EXT-X-ENDLIST\nseg0.ts\n',
        }),
      ),
    );
    await expect(
      downloadManifestFallback('https://x/m.m3u8', '/tmp/out.mp4', {
        timeout: 5000,
        lookup: publicLookup,
      }),
    ).rejects.toThrow(/private|link-local/);
  });

  it('fails with network_error on an invalid AES-128 key length', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url) => {
        if (String(url).includes('key.bin')) {
          return okRes({ arrayBuffer: new ArrayBuffer(8) }); // not 16 bytes
        }
        if (String(url).endsWith('.m3u8')) {
          return okRes({
            text: '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="https://k.example/key.bin"\n#EXT-X-ENDLIST\nseg0.ts\n',
          });
        }
        return okRes({ arrayBuffer: new ArrayBuffer(16) });
      }),
    );
    await expect(
      downloadManifestFallback('https://x/m.m3u8', '/tmp/out.mp4', {
        timeout: 5000,
        lookup: publicLookup,
      }),
    ).rejects.toThrow(/invalid length 8/);
  });
});
