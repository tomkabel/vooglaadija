import { mkdir, mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  VIDEO_EXTS,
  isPrivateIp,
  parseTimeout,
  pickExtension,
  safeExt,
  validateOutputDir,
  validateUrl,
} from '../src/validate.js';

describe('validate — isPrivateIp', () => {
  it('blocks loopback, private, and link-local IPv4', () => {
    expect(isPrivateIp('127.0.0.1')).toBe(true);
    expect(isPrivateIp('10.0.0.1')).toBe(true);
    expect(isPrivateIp('192.168.1.1')).toBe(true);
    expect(isPrivateIp('172.16.0.1')).toBe(true);
    expect(isPrivateIp('172.31.0.1')).toBe(true);
    expect(isPrivateIp('169.254.169.254')).toBe(true);
    expect(isPrivateIp('0.0.0.0')).toBe(true);
  });

  it('allows public IPv4', () => {
    expect(isPrivateIp('8.8.8.8')).toBe(false);
    expect(isPrivateIp('172.32.0.1')).toBe(false);
  });

  it('blocks ::1 and link-local / ULA IPv6', () => {
    expect(isPrivateIp('::1')).toBe(true);
    expect(isPrivateIp('fe80::1')).toBe(true);
    expect(isPrivateIp('fd12::1')).toBe(true);
    expect(isPrivateIp('fc00::1')).toBe(true);
  });

  it('returns false for hostnames and malformed values', () => {
    expect(isPrivateIp('example.com')).toBe(false);
    expect(isPrivateIp('')).toBe(false);
  });

  it('blocks IPv4-mapped IPv6 (dotted-decimal + hex forms) — SSRF bypass', () => {
    // dotted-decimal form
    expect(isPrivateIp('::ffff:169.254.169.254')).toBe(true);
    expect(isPrivateIp('::ffff:127.0.0.1')).toBe(true);
    expect(isPrivateIp('::ffff:10.1.2.3')).toBe(true);
    // hex form (::ffff:a9fe:a9fe  ==  169.254.169.254)
    expect(isPrivateIp('::ffff:a9fe:a9fe')).toBe(true);
    expect(isPrivateIp('::ffff:a9fe:a90a')).toBe(true); // 169.254.169.10
    // a public mapped address is allowed
    expect(isPrivateIp('::ffff:8.8.8.8')).toBe(false);
    expect(isPrivateIp('::ffff:0808:0808')).toBe(false); // 8.8.8.8 in hex
    // uppercase prefix is handled
    expect(isPrivateIp('::FFFF:169.254.169.254')).toBe(true);
  });
});

describe('validate — validateUrl', () => {
  it('accepts http and https public URLs', async () => {
    const lookup = async () => [{ address: '93.184.216.34' }];
    const u = await validateUrl('https://example.com/video.mp4', { lookup });
    expect(u.protocol).toBe('https:');
  });

  it('rejects non-http(s) schemes (file/data/javascript)', async () => {
    const lookup = async () => [{ address: '1.1.1.1' }];
    await expect(validateUrl('file:///etc/passwd', { lookup })).rejects.toThrow();
    await expect(validateUrl('data:text/html,<x>', { lookup })).rejects.toThrow();
    await expect(validateUrl('javascript:alert(1)', { lookup })).rejects.toThrow();
  });

  it('rejects private/link-local IP literals (SSRF)', async () => {
    const lookup = async () => [{ address: '1.1.1.1' }];
    await expect(validateUrl('http://127.0.0.1/', { lookup })).rejects.toThrow();
    await expect(validateUrl('http://169.254.169.254/latest', { lookup })).rejects.toThrow();
    await expect(validateUrl('http://10.0.0.1/', { lookup })).rejects.toThrow();
    await expect(validateUrl('http://[::1]/', { lookup })).rejects.toThrow();
  });

  it('rejects IPv4-mapped IPv6 reaching a private IP (SSRF bypass)', async () => {
    // No DNS lookup needed — the literal is rejected before resolution.
    const lookup = async () => [{ address: '1.1.1.1' }];
    await expect(validateUrl('http://[::ffff:169.254.169.254]/', { lookup })).rejects.toThrow(
      /private|link-local/,
    );
    await expect(validateUrl('http://[::ffff:a9fe:a9fe]/', { lookup })).rejects.toThrow(
      /private|link-local/,
    );
    // A mapped public address is allowed (resolves fine).
    const pub = await validateUrl('http://[::ffff:8.8.8.8]/', { lookup });
    expect(pub.protocol).toBe('http:');
  });

  it('rejects a hostname that resolves to a private IP (DNS-rebinding SSRF)', async () => {
    const lookup = async () => [{ address: '127.0.0.1' }];
    await expect(validateUrl('https://internal.evil/', { lookup })).rejects.toThrow();
  });

  it('rejects a hostname that cannot be resolved', async () => {
    const lookup = async () => {
      throw new Error('ENOTFOUND');
    };
    await expect(validateUrl('https://nope.invalid/', { lookup })).rejects.toThrow();
  });

  it('rejects non-string / empty input', async () => {
    await expect(
      validateUrl('', { lookup: async () => [{ address: '1.1.1.1' }] }),
    ).rejects.toThrow();
    await expect(
      validateUrl(null, { lookup: async () => [{ address: '1.1.1.1' }] }),
    ).rejects.toThrow();
  });
});

describe('validate — validateOutputDir', () => {
  it('accepts a directory under the base', async () => {
    const base = await mkdtemp(join(tmpdir(), 'bd-out-'));
    const sub = join(base, 'sub');
    await mkdir(sub);
    const realpath = async (p) => p; // identity for test
    const resolved = await validateOutputDir(sub, { base, realpath });
    expect(resolved.startsWith(base)).toBe(true);
  });

  it('rejects a path outside the base (path traversal)', async () => {
    const base = await mkdtemp(join(tmpdir(), 'bd-out-'));
    // `evil` looks like a child of the base but resolves outside it.
    const inside = join(base, 'evil');
    const realpath = async (p) => (p === inside ? '/etc' : p);
    await expect(validateOutputDir(inside, { base, realpath })).rejects.toThrow(/under/);
    // A `..` segment must also be rejected after resolution.
    await expect(
      validateOutputDir(join(base, '..', 'elsewhere'), { base, realpath: async (p) => p }),
    ).rejects.toThrow(/under/);
  });

  it('rejects a non-existent directory', async () => {
    const base = await mkdtemp(join(tmpdir(), 'bd-out-'));
    await expect(validateOutputDir(join(base, 'does-not-exist'), { base })).rejects.toThrow(
      /output_dir does not exist/,
    );
  });

  it('rejects a sibling path that shares the base prefix (e.g. /output-evil vs /output)', async () => {
    const base = '/tmp/bd-out-sibling';
    const sibling = `${base}-evil`; // shares the prefix but is NOT a child
    const realpath = async (p) => p; // identity
    await expect(validateOutputDir(sibling, { base, realpath })).rejects.toThrow(/under/);
  });

  it('rejects an out-of-base path BEFORE touching the filesystem (path-injection guard)', async () => {
    const base = await mkdtemp(join(tmpdir(), 'bd-out-'));
    const seen = [];
    const realpath = async (p) => {
      seen.push(p);
      return p;
    };
    await expect(validateOutputDir('/etc/passwd', { base, realpath })).rejects.toThrow(/under/);
    // Only the base was resolved: the attacker-supplied path never reached the
    // filesystem sink, so a lexically out-of-base value cannot be stat'ed.
    expect(seen).toEqual([base]);
  });

  it('collapses `..` lexically before the filesystem is touched', async () => {
    const base = await mkdtemp(join(tmpdir(), 'bd-out-'));
    const seen = [];
    const realpath = async (p) => {
      seen.push(p);
      return p;
    };
    await expect(
      validateOutputDir(`${base}/sub/../../escaped`, { base, realpath }),
    ).rejects.toThrow(/under/);
    expect(seen).toEqual([base]);
  });

  it('still rejects a symlink inside the base that resolves outside it', async () => {
    const base = await mkdtemp(join(tmpdir(), 'bd-out-'));
    const inside = join(base, 'link');
    // Lexically fine, but realpath escapes — the post-realpath check must catch it.
    const realpath = async (p) => (p === inside ? '/etc' : p);
    await expect(validateOutputDir(inside, { base, realpath })).rejects.toThrow(/under/);
  });

  it('rejects when the output base itself does not exist (no realpath fallback)', async () => {
    const realpath = async () => {
      throw new Error('ENOENT');
    };
    await expect(
      validateOutputDir('/output/sub', { base: '/nonexistent-base', realpath }),
    ).rejects.toThrow(/output base/);
  });
});

describe('validate — parseTimeout', () => {
  it('accepts finite positive integers', () => {
    expect(parseTimeout('5000', 30_000)).toBe(5000);
    expect(parseTimeout(7000, 30_000)).toBe(7000);
  });

  it('falls back to default on NaN / non-numeric', () => {
    expect(parseTimeout('abc', 30_000)).toBe(30_000);
    expect(parseTimeout(undefined, 30_000)).toBe(30_000);
    expect(parseTimeout(null, 30_000)).toBe(30_000);
  });

  it('falls back to default on non-positive or non-integer', () => {
    expect(parseTimeout(0, 30_000)).toBe(30_000);
    expect(parseTimeout(-5, 30_000)).toBe(30_000);
    expect(parseTimeout(5.5, 30_000)).toBe(30_000);
    expect(parseTimeout('5.5', 30_000)).toBe(30_000);
  });
});

describe('validate — extension whitelist', () => {
  it('VIDEO_EXTS contains the allowed set only', () => {
    expect(VIDEO_EXTS).toEqual(['mp4', 'webm', 'ts', 'm4v', 'm4s']);
  });

  it('pickExtension maps content-types', () => {
    expect(pickExtension('https://x/v', 'video/mp4')).toBe('mp4');
    expect(pickExtension('https://x/v', 'video/webm')).toBe('webm');
    expect(pickExtension('https://x/v', 'video/mp2t')).toBe('ts');
  });

  it('pickExtension maps HLS/DASH content-types to mp4 (re-muxed)', () => {
    expect(pickExtension('https://x/v', 'application/vnd.apple.mpegurl')).toBe('mp4');
    expect(pickExtension('https://x/v', 'application/dash+xml')).toBe('mp4');
  });

  it('pickExtension never returns an unwhitelisted extension (defaults mp4)', () => {
    expect(pickExtension('https://x/page.html', 'text/html')).toBe('mp4');
    expect(pickExtension('https://x/clip.mov', '')).toBe('mp4');
    expect(pickExtension('https://x/clip.mp4?token=1', '')).toBe('mp4');
  });

  it('safeExt whitelists and lowercases, else mp4', () => {
    expect(safeExt('MP4')).toBe('mp4');
    expect(safeExt('webm')).toBe('webm');
    expect(safeExt('html')).toBe('mp4');
    expect(safeExt('')).toBe('mp4');
  });
});
