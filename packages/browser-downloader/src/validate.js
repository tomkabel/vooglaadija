// Input validation for the HTTP endpoint.
//
// Guards against:
//   (a) SSRF — only http/https schemes, reject file/data/javascript, and block
//       private/link-local IPs (127.x, 10.x, 192.168.x, 172.16-31.x, 169.254.x,
//       ::1, link-local, ULA) including hostnames that resolve to them.
//   (b) path traversal — output_dir must realpath under the configured base.
//   (c) malformed env timeouts — Number() must be a finite positive int, else
//       fall back to the default.
//   (d) extension whitelisting — only VIDEO_EXTS may ever be written to disk.

import { lookup as defaultLookup } from 'node:dns/promises';
import { realpath as defaultRealpath } from 'node:fs/promises';

export const VIDEO_EXTS = ['mp4', 'webm', 'ts', 'm4v', 'm4s'];

const CONTENT_TYPE_EXT = {
  'video/mp4': 'mp4',
  'video/webm': 'webm',
  'video/mp2t': 'ts',
  'video/x-m4v': 'm4v',
  'video/x-mpegurl': 'm3u8',
  'application/vnd.apple.mpegurl': 'm3u8',
  'application/x-mpegurl': 'm3u8',
  'application/dash+xml': 'mpd',
};

// Convert an IPv4-mapped IPv6 address (`::ffff:a.b.c.d` dotted-decimal or
// `::ffff:xxxx:yyyy` hex form) to its dotted-decimal IPv4 string, or null if
// `h` is not an IPv4-mapped address. Used to defeat SSRF via IPv4-mapped IPv6
// (e.g. `http://[::ffff:169.254.169.254]/` reaching the cloud metadata IP).
function ipv4FromMapped(h) {
  const prefix = '::ffff:';
  if (!h.startsWith(prefix)) {
    return null;
  }
  const rest = h.slice(prefix.length);
  if (rest.includes('.')) {
    return rest; // dotted-decimal form: ::ffff:169.254.169.254
  }
  const groups = rest.split(':');
  if (groups.length === 2) {
    // hex form: ::ffff:a9fe:a9fe  →  169.254.169.254
    const g1 = Number.parseInt(groups[0], 16);
    const g2 = Number.parseInt(groups[1], 16);
    if (Number.isNaN(g1) || Number.isNaN(g2)) {
      return null;
    }
    return `${(g1 >> 8) & 0xff}.${g1 & 0xff}.${(g2 >> 8) & 0xff}.${g2 & 0xff}`;
  }
  return null;
}

// Returns true for IPv4/IPv6 strings that are private, loopback, link-local,
// ULA, or the unspecified address. Hostnames (non-IP) return false and are
// resolved separately.
export function isPrivateIp(value) {
  if (!value || typeof value !== 'string') {
    return false;
  }
  // WHATWG URL serializes IPv6 hostnames bracketed (e.g. "[::1]").
  let v = value;
  if (v.startsWith('[') && v.endsWith(']')) {
    v = v.slice(1, -1);
  }
  if (v.includes(':')) {
    const h = v.toLowerCase();
    if (h === '::1' || h === '::') {
      return true;
    }
    if (h.startsWith('fe80:')) {
      return true; // link-local
    }
    if (h.startsWith('fc') || h.startsWith('fd')) {
      return true; // unique local fc00::/7
    }
    // IPv4-mapped IPv6 (::ffff:x.x.x.x / ::ffff:xxxx:yyyy) — strip and re-check
    // the embedded IPv4 against the private-IP rules (SSRF bypass defence).
    if (h.startsWith('::ffff:')) {
      const ipv4 = ipv4FromMapped(h);
      if (ipv4) {
        return isPrivateIp(ipv4);
      }
    }
    return false;
  }
  const parts = value.split('.');
  if (parts.length !== 4) {
    return false;
  }
  const a = Number(parts[0]);
  const b = Number(parts[1]);
  if (Number.isNaN(a) || Number.isNaN(b)) {
    return false;
  }
  if (a === 127 || a === 10 || a === 0) {
    return true;
  }
  if (a === 192 && b === 168) {
    return true;
  }
  if (a === 169 && b === 254) {
    return true;
  }
  if (a === 172 && b >= 16 && b <= 31) {
    return true;
  }
  if (a === 100 && b >= 64 && b <= 127) {
    return true; // CGNAT 100.64.0.0/10
  }
  if (a === 192 && b === 0) {
    return true; // 192.0.0.0/24 IETF protocol assignments
  }
  if (a === 198 && (b === 18 || b === 19)) {
    return true; // 198.18.0.0/15 benchmarking
  }
  if (a >= 224) {
    return true; // multicast + reserved + 255.255.255.255
  }
  return false;
}

export async function validateUrl(raw, { lookup = defaultLookup } = {}) {
  if (typeof raw !== 'string' || raw.length === 0) {
    throw new Error('url must be a non-empty string');
  }
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error('url is not a valid URL');
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new Error(`url scheme not allowed: ${parsed.protocol}`);
  }
  const host = parsed.hostname;
  if (isPrivateIp(host)) {
    throw new Error(`private/link-local host blocked: ${host}`);
  }
  let addrs;
  try {
    addrs = await lookup(host, { all: true });
  } catch {
    throw new Error(`host could not be resolved: ${host}`);
  }
  const list = Array.isArray(addrs) ? addrs : [addrs];
  for (const entry of list) {
    const addr = typeof entry === 'string' ? entry : entry.address;
    if (isPrivateIp(addr)) {
      throw new Error(`host resolves to private/link-local address: ${addr}`);
    }
  }
  return parsed;
}

export async function validateOutputDir(dir, { base, realpath = defaultRealpath } = {}) {
  if (typeof dir !== 'string' || dir.length === 0) {
    throw new Error('output_dir must be a non-empty string');
  }
  const baseDir = base || process.env.BD_OUTPUT_BASE || '/output';
  // The base MUST exist — never fall back to the raw string (which would let a
  // non-existent base silently disable the path-traversal guard).
  let resolvedBase;
  try {
    resolvedBase = await realpath(baseDir);
  } catch {
    throw new Error(`output base does not exist or is not accessible: ${baseDir}`);
  }
  let resolved;
  try {
    resolved = await realpath(dir);
  } catch {
    throw new Error('output_dir does not exist or is not accessible');
  }
  // Exact match OR a real child: `resolved.startsWith(resolvedBase + '/')`.
  // A bare `startsWith(resolvedBase)` would let `/output-evil` match `/output`.
  if (resolved !== resolvedBase && !resolved.startsWith(`${resolvedBase}/`)) {
    throw new Error(`output_dir must be under ${resolvedBase}`);
  }
  return resolved;
}

export function parseTimeout(value, defaultMs) {
  const n = Number(value);
  if (!(Number.isInteger(n) && n > 0)) {
    return defaultMs;
  }
  return n;
}

// Derive a whitelisted extension from a response URL and/or content-type.
// Never returns an unwhitelisted extension; falls back to 'mp4'.
export function pickExtension(urlOrPath, contentType) {
  if (typeof contentType === 'string') {
    const ct = contentType.toLowerCase().split(';')[0].trim();
    if (CONTENT_TYPE_EXT[ct]) {
      const mapped = CONTENT_TYPE_EXT[ct];
      if (mapped === 'm3u8' || mapped === 'mpd') {
        return 'mp4'; // manifests are re-muxed to mp4 by streamlink/ffmpeg
      }
      return mapped;
    }
  }
  if (typeof urlOrPath === 'string') {
    try {
      const u = new URL(urlOrPath);
      const ext = u.pathname.split('.').pop();
      if (ext && ext !== u.pathname) {
        const lower = ext.toLowerCase();
        if (VIDEO_EXTS.includes(lower)) {
          return lower;
        }
      }
    } catch {
      // not a URL — try plain path extension
      const ext = urlOrPath.split('.').pop();
      if (ext && ext !== urlOrPath) {
        const lower = ext.toLowerCase();
        if (VIDEO_EXTS.includes(lower)) {
          return lower;
        }
      }
    }
  }
  return 'mp4';
}

// Ensure an extension is whitelisted, else default to 'mp4'.
export function safeExt(ext) {
  if (typeof ext === 'string' && VIDEO_EXTS.includes(ext.toLowerCase())) {
    return ext.toLowerCase();
  }
  return 'mp4';
}
