import { describe, expect, it, vi } from 'vitest';

import { detectBlob, pushBlobUrl } from '../src/tier2-dom.js';

function makeTier2Page({ drmSequence = [false], block = false, blobUrl = null, payload = null }) {
  let drmIdx = 0;
  const evaluate = vi.fn(async (fn) => {
    const src = fn.toString();
    if (src.includes('createObjectURL')) {
      return undefined; // HOOK_SRC patch
    }
    if (src.includes('__bd_drm')) {
      const v =
        drmIdx < drmSequence.length ? drmSequence[drmIdx] : drmSequence[drmSequence.length - 1];
      drmIdx += 1;
      return v;
    }
    if (src.includes('document.title')) {
      return block;
    }
    if (src.includes('querySelector')) {
      return blobUrl;
    }
    if (src.includes('arrayBuffer')) {
      return payload;
    }
    return undefined;
  });
  const page = { evaluate, $: vi.fn(async () => null) };
  return { page, evaluate };
}

const calledSelector = (evaluate) =>
  evaluate.mock.calls.some(([fn]) => fn.toString().includes('querySelector'));

describe('tier2-dom — pushBlobUrl helper (cap + non-Blob guard)', () => {
  it('records Blob inputs only (non-Blob / MediaSource guard)', () => {
    const arr = [];
    pushBlobUrl(arr, new Blob(['x']), 'blob:a');
    expect(arr).toEqual(['blob:a']);
    // a plain object (MediaSource-like) must NOT be recorded
    pushBlobUrl(arr, { sourceBuffers: [] }, 'blob:b');
    expect(arr).toEqual(['blob:a']);
  });

  it('evicts the oldest entry beyond the cap', () => {
    const arr = [];
    for (let i = 0; i < 5; i += 1) {
      pushBlobUrl(arr, new Blob(['x']), `blob:${i}`, 3);
    }
    expect(arr).toEqual(['blob:2', 'blob:3', 'blob:4']);
  });
});

describe('tier2-dom — terminal conditions BEFORE polling (no no_media_found masking)', () => {
  it('throws drm_detected when EME is engaged before polling', async () => {
    const { page, evaluate } = makeTier2Page({ drmSequence: [true] });
    await expect(detectBlob(page, { timeout: 1000 })).rejects.toMatchObject({
      code: 'drm_detected',
    });
    expect(calledSelector(evaluate)).toBe(false); // never polled for blobs
  });

  it('throws anti_bot_block on block-page indicators before polling', async () => {
    const { page, evaluate } = makeTier2Page({ block: true, drmSequence: [false] });
    await expect(detectBlob(page, { timeout: 1000 })).rejects.toMatchObject({
      code: 'anti_bot_block',
    });
    expect(calledSelector(evaluate)).toBe(false);
  });
});

describe('tier2-dom — blob detection + lifecycle', () => {
  it('returns bytes when a blob URL is found', async () => {
    const { page } = makeTier2Page({
      blobUrl: 'blob:https://x/abc',
      payload: { bytes: [65, 66, 67], type: 'video/mp4', size: 3 },
      drmSequence: [false],
    });
    const result = await detectBlob(page, { timeout: 1000 });
    expect(result).toMatchObject({ kind: 'bytes', ext: 'mp4' });
    expect(result.buffer.toString()).toBe('ABC');
  });

  it('rejects oversized blob bodies (size cap backstop)', async () => {
    const { page } = makeTier2Page({
      blobUrl: 'blob:https://x/big',
      payload: { bytes: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0], type: 'video/mp4', size: 10 },
      drmSequence: [false],
    });
    await expect(detectBlob(page, { timeout: 1000, bodyCap: 5 })).rejects.toMatchObject({
      code: 'network_error',
    });
  });

  it('rejects a blob whose content-length exceeds the cap BEFORE materializing (tooLarge)', async () => {
    // `tooLarge: true` with no `bytes` simulates the in-page content-length
    // check rejecting before Array.from(new Uint8Array(ab)) runs.
    const { page } = makeTier2Page({
      blobUrl: 'blob:https://x/big',
      payload: { tooLarge: true, type: 'video/mp4', size: 999_999_999 },
      drmSequence: [false],
    });
    await expect(detectBlob(page, { timeout: 1000, bodyCap: 1024 })).rejects.toMatchObject({
      code: 'network_error',
    });
  });

  it('throws drm_detected when EME engages DURING polling (late DRM)', async () => {
    const { page, evaluate } = makeTier2Page({
      blobUrl: null,
      drmSequence: [false, true], // before-poll false, then true during poll
    });
    await expect(detectBlob(page, { timeout: 1000 })).rejects.toMatchObject({
      code: 'drm_detected',
    });
    expect(calledSelector(evaluate)).toBe(true); // it did poll before DRM engaged
  });

  it('throws no_media_found when the timeout expires with no blob', async () => {
    const { page } = makeTier2Page({
      blobUrl: null,
      payload: null,
      drmSequence: [false, false, false],
    });
    const p = detectBlob(page, { timeout: 200 });
    await expect(p).rejects.toMatchObject({ code: 'no_media_found' });
  });
});
