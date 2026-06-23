(() => {
  const panel = document.getElementById('chaos-status-panel');
  if (!panel) return;

  function isChaosStatusRequest(evt) {
    return Boolean(
      evt.detail?.elt?.closest('#chaos-status-panel') ||
        evt.detail?.target?.closest?.('#chaos-status-panel'),
    );
  }

  function renderChaosStatusFailure() {
    panel.dataset.chaosStatusFailed = 'true';
    panel.setAttribute('aria-busy', 'false');
    panel.innerHTML = `
      <div class="flex items-center gap-2 text-coral-400">
        <svg class="h-4 w-4 flex-shrink-0" aria-hidden="true"><use href="/static/icons/sprite.svg#icon-alert" /></svg>
        <span class="font-body text-sm">Could not reach chaos API</span>
      </div>
    `;
  }

  function clearChaosStatusFailure() {
    delete panel.dataset.chaosStatusFailed;
    panel.setAttribute('aria-busy', 'false');
  }

  document.body.addEventListener('htmx:beforeRequest', (evt) => {
    if (isChaosStatusRequest(evt)) {
      panel.setAttribute('aria-busy', 'true');
    }
  });

  document.body.addEventListener('htmx:responseError', (evt) => {
    if (isChaosStatusRequest(evt)) renderChaosStatusFailure();
  });

  document.body.addEventListener('htmx:sendError', (evt) => {
    if (isChaosStatusRequest(evt)) renderChaosStatusFailure();
  });

  document.body.addEventListener('htmx:afterRequest', (evt) => {
    if (!isChaosStatusRequest(evt)) return;
    if (evt.detail.successful === false) {
      renderChaosStatusFailure();
      return;
    }
    clearChaosStatusFailure();
  });

  document.body.addEventListener('htmx:afterSwap', (evt) => {
    if (isChaosStatusRequest(evt)) clearChaosStatusFailure();
  });
})();
