(() => {
  // ─── Custom Confirm Modal with Focus Trap ──────────────────────────
  var modal = document.getElementById('confirm-modal');
  var modalTitle = document.getElementById('modal-title');
  var modalDesc = document.getElementById('modal-desc');
  var modalCancel = document.querySelector('[data-modal-cancel]');
  var modalConfirm = document.querySelector('[data-modal-confirm]');
  var pendingConfirm = null;
  var lastFocusedEl = null;

  // Focusable elements within modal
  var modalFocusable = null;
  function getModalFocusable() {
    if (!modal) return [];
    return modal.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
  }

  function trapFocus(e) {
    var focusable = getModalFocusable();
    if (focusable.length === 0) return;
    var first = focusable[0];
    var last = focusable[focusable.length - 1];

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
    if (!evt.detail || !evt.detail.question) return;
    var elt = evt.detail.elt;
    if (!elt.getAttribute('hx-confirm')) return;
    evt.preventDefault();
    lastFocusedEl = document.activeElement;
    modalTitle.textContent = evt.detail.question;
    modalDesc.textContent = 'This action cannot be undone.';
    modal.classList.remove('hidden');
    pendingConfirm = evt.detail;
    // Focus the cancel button
    setTimeout(() => {
      var focusable = getModalFocusable();
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
  var skelTimer = setTimeout(() => {
    var list = document.getElementById('download-list');
    if (!list) return;
    list.classList.remove('download-list-loading');
    var skel = document.getElementById('download-skeleton');
    if (skel) skel.remove();
  }, 5000);

  function clearSkel() {
    clearTimeout(skelTimer);
    var list = document.getElementById('download-list');
    if (!list) return;
    list.classList.remove('download-list-loading');
    var skel = document.getElementById('download-skeleton');
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
    var rows = document.querySelectorAll('.download-row');
    var completed = 0,
      inProgress = 0,
      total = rows.length;
    rows.forEach((row) => {
      var badge = row.querySelector('.status-badge');
      if (!badge) return;
      var s = badge.textContent.trim().toLowerCase();
      if (s === 'completed') completed++;
      else if (s === 'processing' || s === 'pending') inProgress++;
    });
    var ce = document.getElementById('stat-completed');
    var ie = document.getElementById('stat-in-progress');
    var te = document.getElementById('stat-total');
    if (ce) ce.textContent = completed;
    if (ie) ie.textContent = inProgress;
    if (te) te.textContent = total;
  }

  // ─── Relative Time ───────────────────────────────────────────────────
  function formatRelativeTime(dateString) {
    if (!dateString) return 'Just now';
    var date = new Date(dateString);
    if (isNaN(date.getTime())) return 'Just now';
    var diff = Date.now() - date.getTime();
    var sec = Math.floor(diff / 1000);
    if (sec < 10) return 'Just now';
    if (sec < 60) return sec + 's ago';
    var min = Math.floor(sec / 60);
    if (min < 60) return min + 'm ago';
    var hr = Math.floor(min / 60);
    if (hr < 24) return hr + 'h ago';
    var day = Math.floor(hr / 24);
    if (day < 7) return day + 'd ago';
    return date.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  // ─── SSE ─────────────────────────────────────────────────────────────
  var sseSource = null;
  var htmxPending = new Set();

  document.body.addEventListener('htmx:afterOnLoad', (evt) => {
    var elt = evt.detail.elt;
    var row = elt && elt.closest ? elt.closest('[data-job-id]') : null;
    if (row && row.dataset.jobId) {
      htmxPending.add(row.dataset.jobId);
      setTimeout(() => {
        htmxPending.delete(row.dataset.jobId);
      }, 3000);
    }
    if (document.getElementById('download-rows')) {
      var container = document.getElementById('download-rows');
      var ids = {};
      var rows = container.querySelectorAll('.download-row[data-job-id]');
      for (var i = 0; i < rows.length; i++) {
        var id = rows[i].dataset.jobId;
        if (id && ids[id]) {
          rows[i].remove();
        } else if (id) {
          ids[id] = true;
        }
      }
      var optRows = container.querySelectorAll('[data-optimistic="true"]');
      for (var j = 0; j < optRows.length; j++) {
        optRows[j].remove();
      }
    }
  });

  function handleJobUpdate(event) {
    clearSkel();
    var data;
    try {
      data = JSON.parse(event.data);
    } catch (_) {
      return;
    }
    if (!data || !data.id) return;
    var row = document.querySelector('[data-job-id="' + CSS.escape(data.id) + '"]');
    if (row) {
      updateDownloadRow(row, data);
    } else {
      if (htmxPending.has(data.id)) return;
      var optRow = document.querySelector('[data-optimistic="true"]');
      if (optRow && optRow.querySelector('.url-text').textContent === data.url) return;
      row = createDownloadRow(data);
      insertRowSorted(row, data);
      row.classList.add('fade-in');
    }
    updateStats();
  }

  function handleProgressUpdate(event) {
    clearSkel();
    var data;
    try {
      data = JSON.parse(event.data);
    } catch (_) {
      return;
    }
    if (!data || !data.id) return;
    var row = document.querySelector('[data-job-id="' + CSS.escape(data.id) + '"]');
    if (!row) {
      var optRow = document.querySelector('[data-optimistic="true"]');
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
    var container =
      document.getElementById('download-rows') || document.getElementById('download-list');
    if (!container) return;
    var sk =
      data._sort_key != null
        ? data._sort_key
        : data.created_at
          ? new Date(data.created_at).getTime() / 1000
          : Date.now() / 1000;
    var existing = container.querySelectorAll('.download-row');
    var before = null;
    for (var i = 0; i < existing.length; i++) {
      var ek = Number.parseFloat(existing[i].dataset.sortKey || '0');
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
      var input = document.getElementById('new-download-url');
      var url = input ? input.value.trim() : '';
      if (!url) return;

      var existing = document.querySelector('[data-optimistic="true"]');
      if (existing) {
        if (existing.querySelector('.url-text').textContent === url) return;
        existing.remove();
      }

      var optId = 'opt-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
      var now = new Date();
      var optData = {
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
      var row = createDownloadRow(optData);
      row.dataset.optimistic = 'true';
      insertRowSorted(row, optData);
      row.classList.add('fade-in');
      updateStats();

      var replace = (event) => {
        var realData;
        try {
          realData = JSON.parse(event.data);
        } catch (_) {
          return;
        }
        if (!realData || !realData.url) return;
        var optRow = document.querySelector('[data-optimistic="true"]');
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
      var btn = this.querySelector('button[type="submit"]');
      if (btn) btn.disabled = true;
    });

  document.body.addEventListener('htmx:afterRequest', (evt) => {
    var form =
      evt.detail.elt && evt.detail.elt.tagName === 'FORM'
        ? evt.detail.elt
        : evt.detail.elt && evt.detail.elt.closest
          ? evt.detail.elt.closest('form')
          : null;
    if (form) {
      var btn = form.querySelector('button[type="submit"]');
      if (btn) btn.disabled = false;
    }
  });

  // ─── Inline URL Validation ──────────────────────────────────────────
  var urlInput = document.getElementById('new-download-url');
  var validationTimer = null;
  if (urlInput) {
    urlInput.addEventListener('input', () => {
      clearTimeout(validationTimer);
      validationTimer = setTimeout(() => {
        var val = urlInput.value.trim();
        var errorEl = document.getElementById('url-validation-error');
        if (!val) {
          if (errorEl) errorEl.remove();
          return;
        }
        var valid = /^https?:\/\/.+/.test(val);
        if (!valid) {
          if (!errorEl) {
            var err = document.createElement('p');
            err.id = 'url-validation-error';
            err.className = 'text-xs text-coral-400 font-body mt-1.5';
            err.textContent = 'Must start with http:// or https://';
            urlInput.parentElement.after(err);
          }
        } else {
          if (errorEl) errorEl.remove();
        }
      }, 300);
    });
  }

  document.body.addEventListener('htmx:beforeRequest', () => {
    var errorEl = document.getElementById('url-validation-error');
    if (errorEl) errorEl.remove();
  });

  // ─── SSE Health Monitor ──────────────────────────────────────────────
  var lastMsg = Date.now();
  var reconnectShown = false;
  var sseFailed = false;
  var SSE_TIMEOUT = 35000;

  var _origHandleUpdate = handleJobUpdate;
  handleJobUpdate = (event) => {
    lastMsg = Date.now();
    sseFailed = false;
    var banner = document.getElementById('sse-reconnect-banner');
    if (banner) {
      banner.remove();
      reconnectShown = false;
    }
    _origHandleUpdate(event);
  };

  setInterval(() => {
    var indicator = document.querySelector('.live-indicator');
    if (!indicator) return;
    var elapsed = Date.now() - lastMsg;
    if (elapsed > SSE_TIMEOUT) {
      indicator.className = 'live-indicator live-indicator--error';
      indicator.textContent = 'Reconnecting\u2026';
    } else {
      indicator.className = 'live-indicator live-indicator--active';
      indicator.textContent = 'Live';
    }
  }, 5000);

  setInterval(() => {
    var elapsed = Date.now() - lastMsg;
    if (elapsed > 60000 && !reconnectShown) {
      reconnectShown = true;
      sseFailed = elapsed > 120000;
      var banner = document.createElement('div');
      banner.id = 'sse-reconnect-banner';
      banner.className =
        'fixed bottom-4 right-4 z-50 flex items-center gap-3 bg-coral-500/90 backdrop-blur-sm text-white px-5 py-3.5 rounded-xl shadow-2xl border border-white/10 slide-up';
      banner.innerHTML =
        '<svg class="h-5 w-5 flex-shrink-0" aria-hidden="true"><use href="#icon-alert" /></svg>' +
        '<span class="text-sm font-medium">' +
        (sseFailed ? 'Connection lost \u2014 ' : 'Connection lost \u2014 updates paused') +
        '</span>' +
        '<button onclick="location.reload()" class="bg-white/20 hover:bg-white/30 active:bg-white/40 px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-all duration-200">' +
        (sseFailed ? 'Retry Connection' : 'Refresh') +
        '</button>';
      document.body.appendChild(banner);
      var check = setInterval(() => {
        if (Date.now() - lastMsg < 10000) {
          var b = document.getElementById('sse-reconnect-banner');
          if (b) b.remove();
          reconnectShown = false;
          sseFailed = false;
          clearInterval(check);
        }
      }, 3000);
    }
  }, 5000);

  // ─── Row Factory ─────────────────────────────────────────────────────
  function createDownloadRow(data) {
    var div = document.createElement('div');
    div.className = 'download-row';
    div.dataset.jobId = data.id;
    div.innerHTML = getRowHTML(data);
    htmx.process(div);
    return div;
  }

  function updateDownloadRow(row, data) {
    var badge = row.querySelector('.status-badge');
    if (badge) {
      var old = badge.textContent.trim().toLowerCase();
      var next = data.status.toLowerCase();
      if (old !== next) {
        badge.classList.remove('status-changed');
        void badge.offsetWidth;
        badge.classList.add('status-changed');
        setTimeout(() => {
          badge.classList.remove('status-changed');
        }, 600);
      }
      badge.textContent = data.status.charAt(0).toUpperCase() + data.status.slice(1);
      badge.className = 'status-badge status-' + data.status;
    }
    var ts = row.querySelector('.timestamp');
    if (ts && data.updated_at) ts.textContent = formatRelativeTime(data.updated_at);

    var dlBtn = row.querySelector('.download-btn');
    if (data.status === 'completed') {
      if (dlBtn) {
        dlBtn.style.display = 'inline-flex';
      } else {
        dlBtn = document.createElement('a');
        dlBtn.href = '/web/downloads/' + data.id + '/file';
        dlBtn.className = 'download-btn text-xs';
        dlBtn.download = '';
        dlBtn.target = '_blank';
        dlBtn.setAttribute('hx-boost', 'false');
        dlBtn.innerHTML =
          '<svg class="h-4 w-4" aria-hidden="true"><use href="#icon-download" /></svg> Save';
        dlBtn.style.display = 'inline-flex';
        var containers = row.querySelectorAll('.flex.items-center.gap-3');
        var c = containers[containers.length - 1];
        var del = c.querySelector('button[hx-delete]');
        if (del) c.insertBefore(dlBtn, del);
        else c.appendChild(dlBtn);
      }
    } else if (dlBtn) {
      dlBtn.style.display = 'none';
    }
  }

  function updateDownloadProgress(row, progress) {
    var bar = row.querySelector('.progress-bar');
    if (!bar) {
      var badge = row.querySelector('.status-badge');
      if (!badge) return;
      var wrap = document.createElement('div');
      wrap.className = 'progress-container';
      wrap.innerHTML =
        '<div class="progress-track"><div class="progress-bar" style="width:0%"></div></div>' +
        (progress.eta != null ? '<span class="progress-eta"></span>' : '');
      badge.parentNode.insertBefore(wrap, badge.nextSibling);
      bar = wrap.querySelector('.progress-bar');
    }
    if (bar && progress.percent != null) bar.style.width = Math.min(progress.percent, 100) + '%';
    var eta = row.querySelector('.progress-eta');
    if (eta && progress.eta != null) {
      var m = Math.floor(progress.eta / 60);
      var s = Math.round(progress.eta % 60);
      eta.textContent = m + 'm ' + s + 's';
    }
  }

  function getRowHTML(data) {
    var statusText = data.status.charAt(0).toUpperCase() + data.status.slice(1);
    var date = formatRelativeTime(data.created_at);
    return (
      '<div class="flex-1 min-w-0"><div class="flex items-center gap-3">' +
      '<div class="h-10 w-10 rounded-xl bg-white/[0.04] border border-white/[0.06] flex items-center justify-center flex-shrink-0">' +
      '<svg class="h-5 w-5 text-gray-500" aria-hidden="true"><use href="#icon-video" /></svg></div>' +
      '<div><p class="url-text">' +
      escapeHtml(data.title || data.url) +
      '</p>' +
      '<p class="timestamp">' +
      date +
      '</p></div></div></div>' +
      '<div class="flex items-center gap-3">' +
      '<span class="status-badge status-' +
      data.status +
      '">' +
      statusText +
      '</span>' +
      (data.status === 'completed'
        ? '<a href="/web/downloads/' +
          data.id +
          '/file" class="download-btn text-xs" download target="_blank" hx-boost="false">' +
          '<svg class="h-4 w-4" aria-hidden="true"><use href="#icon-download" /></svg> Save</a>'
        : '') +
      '<button hx-delete="/web/downloads/' +
      data.id +
      '" hx-target="closest .download-row" hx-swap="outerHTML" hx-confirm="Delete this download?" class="btn-danger" aria-label="Delete download">' +
      '<svg class="h-5 w-5" aria-hidden="true"><use href="#icon-trash" /></svg></button></div>'
    );
  }

  function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  window.VooglaadijaDashboard = {
    formatRelativeTime: formatRelativeTime,
    updateStats: updateStats,
  };
})();
