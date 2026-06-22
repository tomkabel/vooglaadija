(() => {
  // ─── Custom Confirm Modal with Focus Trap ──────────────────────────
  const modal = document.getElementById('confirm-modal');
  const modalTitle = document.getElementById('modal-title');
  const modalDesc = document.getElementById('modal-desc');
  const modalCancel = document.querySelector('[data-modal-cancel]');
  const modalConfirm = document.querySelector('[data-modal-confirm]');
  let pendingConfirm = null;
  let lastFocusedEl = null;

  // Focusable elements within modal
  const _modalFocusable = null;
  function getModalFocusable() {
    if (!modal) return [];
    return modal.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
  }

  function trapFocus(e) {
    const focusable = getModalFocusable();
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    if (e.key === 'Tab') {
      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else if (document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }

  document.body.addEventListener('htmx:confirm', (evt) => {
    if (!evt.detail?.question) return;
    const elt = evt.detail.elt;
    if (!elt.getAttribute('hx-confirm')) return;
    evt.preventDefault();
    lastFocusedEl = document.activeElement;
    modalTitle.textContent = evt.detail.question;
    modalDesc.textContent = 'This action cannot be undone.';
    modal.classList.remove('hidden');
    pendingConfirm = evt.detail;
    // Focus the cancel button
    setTimeout(() => {
      const focusable = getModalFocusable();
      if (focusable.length > 0) focusable[0].focus();
    }, 50);
    document.addEventListener('keydown', trapFocus);
    // Prevent body scroll
    document.body.style.overflow = 'hidden';
  });

  function closeModal(confirmed) {
    modal.classList.add('hidden');
    if (pendingConfirm) {
      pendingConfirm.issueRequest(confirmed);
      pendingConfirm = null;
    }
    document.removeEventListener('keydown', trapFocus);
    document.body.style.overflow = '';
    if (lastFocusedEl) lastFocusedEl.focus();
  }

  modalCancel.addEventListener('click', () => {
    closeModal(false);
  });
  modalConfirm.addEventListener('click', () => {
    closeModal(true);
  });

  modal.addEventListener('click', (evt) => {
    if (evt.target === modal) closeModal(false);
  });

  document.addEventListener('keydown', (evt) => {
    if (evt.key === 'Escape' && !modal.classList.contains('hidden')) closeModal(false);
  });

  // ─── Skeleton Loader ─────────────────────────────────────────────────
  const skelTimer = setTimeout(() => {
    const list = document.getElementById('download-list');
    if (!list) return;
    list.classList.remove('download-list-loading');
    const skel = document.getElementById('download-skeleton');
    if (skel) skel.remove();
  }, 5000);

  function clearSkel() {
    clearTimeout(skelTimer);
    const list = document.getElementById('download-list');
    if (!list) return;
    list.classList.remove('download-list-loading');
    const skel = document.getElementById('download-skeleton');
    if (skel) skel.remove();
  }

  document.body.addEventListener('htmx:sseOpen', () => {
    clearTimeout(skelTimer);
    setTimeout(clearSkel, 200);
  });

  document.body.addEventListener('htmx:sseError', () => {
    clearSkel();
  });
  document.body.addEventListener('htmx:afterSwap', () => {
    clearSkel();
    updateStats();
  });

  // ─── Stats ───────────────────────────────────────────────────────────
  function updateStats() {
    const rows = document.querySelectorAll('.download-row');
    let completed = 0;
    let inProgress = 0;
    const total = rows.length;
    for (const row of rows) {
      const badge = row.querySelector('.status-badge');
      if (!badge) continue;
      const s = badge.textContent.trim().toLowerCase();
      if (s === 'completed') completed++;
      else if (s === 'processing' || s === 'pending') inProgress++;
    }
    const ce = document.getElementById('stat-completed');
    const ie = document.getElementById('stat-in-progress');
    const te = document.getElementById('stat-total');
    if (ce) ce.textContent = completed;
    if (ie) ie.textContent = inProgress;
    if (te) te.textContent = total;
  }

  // ─── Relative Time ───────────────────────────────────────────────────
  function formatRelativeTime(dateString) {
    if (!dateString) return 'Just now';
    const date = new Date(dateString);
    if (Number.isNaN(date.getTime())) return 'Just now';
    const diff = Date.now() - date.getTime();
    const sec = Math.floor(diff / 1000);
    if (sec < 10) return 'Just now';
    if (sec < 60) return `${sec}s ago`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ago`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}h ago`;
    const day = Math.floor(hr / 24);
    if (day < 7) return `${day}d ago`;
    return date.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  // ─── SSE ─────────────────────────────────────────────────────────────
  let sseSource = null;
  const htmxPending = new Set();
  let lastMsg = Date.now();
  let reconnectShown = false;
  let sseFailed = false;
  let announcementTimer = null;
  let sseHealthIntervalId = null;
  const SSE_TIMEOUT = 35000;

  function announceDownloadUpdate(message) {
    const announcer = document.getElementById('download-announcer');
    const announcement = String(message || '').trim();
    if (!(announcer && announcement)) return;

    announcer.textContent = '';
    clearTimeout(announcementTimer);
    announcementTimer = setTimeout(() => {
      announcer.textContent = announcement;
    }, 50);
  }

  function getDownloadTitle(data, row) {
    const title = data.title || data.url || row.querySelector('.url-text')?.textContent;
    return String(title || '').trim();
  }

  function getVisibleDownloadTitle(row, data) {
    const title = row.querySelector('.url-text')?.textContent || data.title || data.url;
    return String(title || '').trim();
  }

  document.body.addEventListener('htmx:afterOnLoad', (evt) => {
    const elt = evt.detail.elt;
    const row = elt?.closest ? elt.closest('[data-job-id]') : null;
    if (row?.dataset.jobId) {
      htmxPending.add(row.dataset.jobId);
      setTimeout(() => {
        htmxPending.delete(row.dataset.jobId);
      }, 3000);
    }
    if (document.getElementById('download-rows')) {
      const container = document.getElementById('download-rows');
      const ids = {};
      const rows = container.querySelectorAll('.download-row[data-job-id]');
      for (let i = 0; i < rows.length; i++) {
        const id = rows[i].dataset.jobId;
        if (id && ids[id]) {
          rows[i].remove();
        } else if (id) {
          ids[id] = true;
        }
      }
      const optRows = container.querySelectorAll('[data-optimistic="true"]');
      for (let j = 0; j < optRows.length; j++) {
        optRows[j].remove();
      }
    }
  });

  function handleJobUpdate(event) {
    clearSkel();
    lastMsg = Date.now();
    sseFailed = false;
    const banner = document.getElementById('sse-reconnect-banner');
    if (banner) {
      banner.remove();
      reconnectShown = false;
    }
    let data;
    try {
      data = JSON.parse(event.data);
    } catch (_) {
      return;
    }
    if (!data?.id) return;
    let row = document.querySelector(`[data-job-id="${CSS.escape(data.id)}"]`);
    if (row) {
      updateDownloadRow(row, data);
    } else {
      if (htmxPending.has(data.id)) return;
      const optRow = document.querySelector('[data-optimistic="true"]');
      if (optRow && optRow.querySelector('.url-text').textContent === data.url) return;
      row = createDownloadRow(data);
      insertRowSorted(row, data);
      row.classList.add('fade-in');
      const title = getDownloadTitle(data, row);
      if (title) announceDownloadUpdate(`New download: ${title}`);
    }
    updateStats();
  }

  function handleProgressUpdate(event) {
    clearSkel();
    let data;
    try {
      data = JSON.parse(event.data);
    } catch (_) {
      return;
    }
    if (!data?.id) return;
    let row = document.querySelector(`[data-job-id="${CSS.escape(data.id)}"]`);
    if (!row) {
      const optRow = document.querySelector('[data-optimistic="true"]');
      if (optRow && data.url && optRow.querySelector('.url-text').textContent === data.url) return;
      row = createDownloadRow({
        id: data.id,
        url: data.url || '',
        title: null,
        status: 'processing',
        created_at: null,
        file_name: null,
        error: null,
        updated_at: null,
        progress: data.progress,
      });
      insertRowSorted(row, data);
      const title = getDownloadTitle(data, row);
      if (title) announceDownloadUpdate(`New download: ${title}`);
    }
    if (data.progress) updateDownloadProgress(row, data.progress);
  }

  function attachSSE(source) {
    if (!source) return;
    if (sseSource && sseSource !== source) {
      sseSource.removeEventListener('job_update', handleJobUpdate);
      sseSource.removeEventListener('progress_update', handleProgressUpdate);
    }
    source.addEventListener('job_update', handleJobUpdate);
    source.addEventListener('progress_update', handleProgressUpdate);
    sseSource = source;
  }

  document.body.addEventListener('htmx:sseOpen', (evt) => {
    attachSSE(evt.detail.source);
  });

  function insertRowSorted(row, data) {
    const container =
      document.getElementById('download-rows') || document.getElementById('download-list');
    if (!container) return;
    const sk =
      data._sort_key != null
        ? data._sort_key
        : data.created_at
          ? new Date(data.created_at).getTime() / 1000
          : Date.now() / 1000;
    const existing = container.querySelectorAll('.download-row');
    let before = null;
    for (let i = 0; i < existing.length; i++) {
      const ek = Number.parseFloat(existing[i].dataset.sortKey || '0');
      if (sk > ek) {
        before = existing[i];
        break;
      }
    }
    row.dataset.sortKey = String(sk);
    if (before) container.insertBefore(row, before);
    else container.appendChild(row);
  }

  // ─── Optimistic UI ───────────────────────────────────────────────────
  document
    .querySelector('form[hx-post="/web/downloads"]')
    ?.addEventListener('htmx:beforeRequest', () => {
      const input = document.getElementById('new-download-url');
      const url = input ? input.value.trim() : '';
      if (!url) return;

      const existing = document.querySelector('[data-optimistic="true"]');
      if (existing) {
        if (existing.querySelector('.url-text').textContent === url) return;
        existing.remove();
      }

      const optId = `opt-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
      const now = new Date();
      const optData = {
        id: optId,
        url: url,
        title: null,
        status: 'pending',
        file_name: null,
        error: null,
        created_at: now.toISOString(),
        updated_at: now.toISOString(),
        _sort_key: now.getTime() / 1000,
      };
      const row = createDownloadRow(optData);
      row.dataset.optimistic = 'true';
      insertRowSorted(row, optData);
      row.classList.add('fade-in');
      updateStats();

      const replace = (event) => {
        let realData;
        try {
          realData = JSON.parse(event.data);
        } catch (_) {
          return;
        }
        if (!realData?.url) return;
        const optRow = document.querySelector('[data-optimistic="true"]');
        if (!optRow) return;
        if (optRow.querySelector('.url-text').textContent === realData.url) {
          optRow.dataset.jobId = realData.id;
          optRow.removeAttribute('data-optimistic');
          updateDownloadRow(optRow, realData);
          if (sseSource) sseSource.removeEventListener('job_update', replace);
        }
      };
      if (sseSource) {
        sseSource.addEventListener('job_update', replace);
        setTimeout(() => {
          if (sseSource) sseSource.removeEventListener('job_update', replace);
        }, 30000);
      }
    });

  // ─── Submit button disabled state ───────────────────────────────────
  document
    .querySelector('form[hx-post="/web/downloads"]')
    ?.addEventListener('htmx:beforeRequest', function () {
      const btn = this.querySelector('button[type="submit"]');
      if (btn) btn.disabled = true;
    });

  document.body.addEventListener('htmx:afterRequest', (evt) => {
    const form =
      evt.detail.elt && evt.detail.elt.tagName === 'FORM'
        ? evt.detail.elt
        : evt.detail.elt?.closest
          ? evt.detail.elt.closest('form')
          : null;
    if (form) {
      const btn = form.querySelector('button[type="submit"]');
      if (btn) btn.disabled = false;
    }
  });

  // ─── Inline URL Validation ──────────────────────────────────────────
  const urlInput = document.getElementById('new-download-url');
  let validationTimer = null;
  if (urlInput) {
    urlInput.addEventListener('input', () => {
      clearTimeout(validationTimer);
      validationTimer = setTimeout(() => {
        const val = urlInput.value.trim();
        const errorEl = document.getElementById('url-validation-error');
        if (!val) {
          if (errorEl) errorEl.remove();
          return;
        }
        const valid = /^https?:\/\/.+/.test(val);
        if (valid) {
          if (errorEl) errorEl.remove();
        } else if (!errorEl) {
          const err = document.createElement('p');
          err.id = 'url-validation-error';
          err.className = 'text-xs text-coral-400 font-body mt-1.5';
          err.textContent = 'Must start with http:// or https://';
          urlInput.parentElement.after(err);
        }
      }, 300);
    });
  }

  document.body.addEventListener('htmx:beforeRequest', () => {
    const errorEl = document.getElementById('url-validation-error');
    if (errorEl) errorEl.remove();
  });

  // ─── SSE Health Monitor ──────────────────────────────────────────────
  function updateLiveIndicator(elapsed) {
    const indicator = document.querySelector('.live-indicator');
    if (!indicator) return;
    if (elapsed > SSE_TIMEOUT) {
      indicator.className = 'live-indicator live-indicator--error';
      indicator.textContent = 'Reconnecting\u2026';
    } else {
      indicator.className = 'live-indicator live-indicator--active';
      indicator.textContent = 'Live';
    }
  }

  function showReconnectBanner(elapsed) {
    if (!(elapsed > 60000)) return;

    const failed = elapsed > 120000;
    const wasReconnectShown = reconnectShown;
    const wasFailed = sseFailed;
    let banner = document.getElementById('sse-reconnect-banner');
    if (!banner) {
      banner = document.createElement('div');
      banner.id = 'sse-reconnect-banner';
      banner.className =
        'fixed bottom-4 right-4 z-50 flex items-center gap-3 bg-coral-500/90 backdrop-blur-sm text-white px-5 py-3.5 rounded-xl shadow-2xl border border-white/10 slide-up';
      document.body.appendChild(banner);
    }

    reconnectShown = true;
    sseFailed = failed;
    if (wasReconnectShown && wasFailed === failed && banner.dataset.sseFailed === String(failed)) {
      return;
    }
    banner.dataset.sseFailed = String(failed);
    banner.innerHTML = `<svg class="h-5 w-5 flex-shrink-0" aria-hidden="true"><use href="#icon-alert" /></svg><span class="text-sm font-medium">${failed ? 'Connection lost \u2014 ' : 'Connection lost \u2014 updates paused'}</span><button onclick="location.reload()" class="bg-white/20 hover:bg-white/30 active:bg-white/40 px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-all duration-200">${failed ? 'Retry Connection' : 'Refresh'}</button>`;
  }

  function removeReconnectBanner() {
    const banner = document.getElementById('sse-reconnect-banner');
    if (banner) banner.remove();
    reconnectShown = false;
    sseFailed = false;
  }

  function removeReconnectBannerIfRecovered() {
    if (!(Date.now() - lastMsg < 10000)) return;

    removeReconnectBanner();
  }

  function runSseHealthCheck() {
    const elapsed = Date.now() - lastMsg;
    updateLiveIndicator(elapsed);
    removeReconnectBannerIfRecovered();
    showReconnectBanner(elapsed);
  }

  function shouldRunSseHealthMonitor() {
    return Boolean(document.getElementById('sse-container'));
  }

  function startSseHealthMonitor() {
    if (sseHealthIntervalId !== null || !shouldRunSseHealthMonitor()) return;
    runSseHealthCheck();
    sseHealthIntervalId = setInterval(runSseHealthCheck, 5000);
  }

  function stopSseHealthMonitor() {
    if (sseHealthIntervalId === null) return;
    clearInterval(sseHealthIntervalId);
    sseHealthIntervalId = null;
  }

  function teardownSseHealthMonitor() {
    stopSseHealthMonitor();
    removeReconnectBanner();
  }

  function isDashboardSseElement(element) {
    return Boolean(
      element?.matches?.('#sse-container, #download-list') ||
        element?.querySelector?.('#sse-container, #download-list'),
    );
  }

  window.addEventListener('pagehide', stopSseHealthMonitor);
  window.addEventListener('pageshow', startSseHealthMonitor);
  window.addEventListener('beforeunload', stopSseHealthMonitor);
  document.body.addEventListener('htmx:beforeCleanupElement', (evt) => {
    if (isDashboardSseElement(evt.detail?.elt)) teardownSseHealthMonitor();
  });
  document.body.addEventListener('htmx:load', (evt) => {
    if (isDashboardSseElement(evt.detail?.elt)) startSseHealthMonitor();
  });
  startSseHealthMonitor();

  // ─── Row Factory ─────────────────────────────────────────────────────
  const statusBadgeTemplates = loadStatusBadgeTemplates();

  function loadStatusBadgeTemplates() {
    const script = document.getElementById('status-badge-templates');
    if (!script) return { known: {}, template: '' };
    try {
      const data = JSON.parse(script.textContent || '{}');
      return {
        known: data.known || {},
        template: data.template || '',
      };
    } catch (_err) {
      return { known: {}, template: '' };
    }
  }

  function normalizeStatus(status) {
    const raw = String(status || 'unknown').trim();
    return raw || 'unknown';
  }

  function statusClassSuffix(status) {
    return (
      normalizeStatus(status)
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '') || 'unknown'
    );
  }

  function getStatusBadgeHTML(status) {
    const normalized = normalizeStatus(status);
    const known = statusBadgeTemplates.known[normalized.toLowerCase()];
    if (known) return known;
    if (!statusBadgeTemplates.template) return '';
    return statusBadgeTemplates.template
      .replace('__STATUS_CLASS__', `status-${statusClassSuffix(normalized)}`)
      .replace('__STATUS_LABEL__', escapeHtml(normalized));
  }

  function createStatusBadge(status) {
    const wrapper = document.createElement('div');
    wrapper.innerHTML = getStatusBadgeHTML(status);
    return wrapper.firstElementChild;
  }

  function createDownloadRow(data) {
    const div = document.createElement('div');
    div.className = 'download-row';
    div.dataset.jobId = data.id;
    div.innerHTML = getRowHTML(data);
    htmx.process(div);
    return div;
  }

  function updateDownloadRow(row, data) {
    const badge = row.querySelector('.status-badge');
    let statusChanged = false;
    let nextStatus = normalizeStatus(data.status);
    if (badge) {
      const old = badge.textContent.trim().toLowerCase();
      nextStatus = normalizeStatus(data.status);
      const next = nextStatus.toLowerCase();
      const replacement = createStatusBadge(data.status);
      if (replacement) {
        if (old !== next) {
          statusChanged = true;
          replacement.classList.add('status-changed');
          setTimeout(() => {
            replacement.classList.remove('status-changed');
          }, 600);
        }
        badge.replaceWith(replacement);
      } else if (old !== next) {
        statusChanged = true;
        badge.classList.remove('status-changed');
        void badge.offsetWidth;
        badge.classList.add('status-changed');
        setTimeout(() => {
          badge.classList.remove('status-changed');
        }, 600);
      }
    }
    if (statusChanged) {
      const title = getVisibleDownloadTitle(row, data);
      const visibleStatus = row.querySelector('.status-badge')?.textContent?.trim() || nextStatus;
      if (title) announceDownloadUpdate(`${title} - ${visibleStatus}`);
    }
    const ts = row.querySelector('.timestamp');
    if (ts && data.updated_at) ts.textContent = formatRelativeTime(data.updated_at);

    let dlBtn = row.querySelector('.download-btn');
    if (normalizeStatus(data.status).toLowerCase() === 'completed') {
      if (dlBtn) {
        dlBtn.style.display = 'inline-flex';
      } else {
        dlBtn = document.createElement('a');
        dlBtn.href = `/web/downloads/${encodeURIComponent(String(data.id || ''))}/file`;
        dlBtn.className = 'download-btn text-xs';
        dlBtn.download = '';
        dlBtn.target = '_blank';
        dlBtn.setAttribute('hx-boost', 'false');
        dlBtn.innerHTML =
          '<svg class="h-4 w-4" aria-hidden="true"><use href="#icon-download" /></svg> Save';
        dlBtn.style.display = 'inline-flex';
        const containers = row.querySelectorAll('.flex.items-center.gap-3');
        const c = containers[containers.length - 1];
        const del = c.querySelector('button[hx-delete]');
        if (del) c.insertBefore(dlBtn, del);
        else c.appendChild(dlBtn);
      }
    } else if (dlBtn) {
      dlBtn.style.display = 'none';
    }
  }

  function updateDownloadProgress(row, progress) {
    let bar = row.querySelector('.progress-bar');
    if (!bar) {
      const badge = row.querySelector('.status-badge');
      if (!badge) return;
      const wrap = document.createElement('div');
      wrap.className = 'progress-container';
      wrap.innerHTML = `<div class="progress-track"><div class="progress-bar" style="width:0%"></div></div>${progress.eta != null ? '<span class="progress-eta"></span>' : ''}`;
      badge.parentNode.insertBefore(wrap, badge.nextSibling);
      bar = wrap.querySelector('.progress-bar');
    }
    if (bar && progress.percent != null) bar.style.width = `${Math.min(progress.percent, 100)}%`;
    const eta = row.querySelector('.progress-eta');
    if (eta && progress.eta != null) {
      const m = Math.floor(progress.eta / 60);
      const s = Math.round(progress.eta % 60);
      eta.textContent = `${m}m ${s}s`;
    }
  }

  function getRowHTML(data) {
    const date = formatRelativeTime(data.created_at);
    const jobId = String(data.id || '');
    const jobPathId = encodeURIComponent(jobId);
    const createdAt = data.created_at ? String(data.created_at) : '';
    const status = normalizeStatus(data.status);
    return `<div class="flex-1 min-w-0"><div class="flex items-center gap-3"><div class="h-10 w-10 rounded-xl bg-white/[0.04] border border-white/[0.06] flex items-center justify-center flex-shrink-0"><svg class="h-5 w-5 text-gray-500" aria-hidden="true"><use href="#icon-video" /></svg></div><div><p class="url-text" hx-disable title="${escapeHtml(data.title || data.url || '')}">${escapeHtml(data.title || data.url || '')}</p><p class="timestamp" data-timestamp="${escapeHtml(createdAt)}">${escapeHtml(date)}</p></div></div></div><div class="flex items-center gap-3">${getStatusBadgeHTML(status)}${
      status.toLowerCase() === 'completed'
        ? `<a href="/web/downloads/${jobPathId}/file" class="download-btn text-xs" download target="_blank" hx-boost="false"><svg class="h-4 w-4" aria-hidden="true"><use href="#icon-download" /></svg> Save</a>`
        : ''
    }<button hx-delete="/web/downloads/${jobPathId}" hx-target="closest .download-row" hx-swap="outerHTML" hx-confirm="Delete this download?" class="btn-danger" aria-label="Delete download"><svg class="h-5 w-5" aria-hidden="true"><use href="#icon-trash" /></svg></button></div>`;
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  window.VooglaadijaDashboard = {
    formatRelativeTime: formatRelativeTime,
    updateStats: updateStats,
  };
})();
