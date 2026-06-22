(() => {
  const Vooglaadija = window.Vooglaadija;
  const DISMISS_ANIMATION_DELAY = 300;

  function removeAfterFade(element, exitClass) {
    setTimeout(() => {
      element.classList.add(exitClass);
      setTimeout(() => element.remove(), DISMISS_ANIMATION_DELAY);
    }, 5000);
  }

  function scheduleSuccessBoxDismiss(successBox) {
    if (!successBox || successBox.dataset.autoDismissScheduled === 'true') return;
    successBox.dataset.autoDismissScheduled = 'true';
    removeAfterFade(successBox, 'success-box-exit');
  }

  function scheduleSuccessBoxDismissals(root) {
    const container = root || document;
    if (container.matches?.('.success-box')) {
      scheduleSuccessBoxDismiss(container);
    }
    container.querySelectorAll?.('.success-box').forEach(scheduleSuccessBoxDismiss);
  }

  function showToast(message, type) {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type || 'info'}`;
    toast.setAttribute('role', 'alert');
    toast.setAttribute('aria-live', 'assertive');
    toast.setAttribute('aria-atomic', 'true');
    toast.textContent = message;

    container.appendChild(toast);

    removeAfterFade(toast, 'toast-exit');
  }

  document.querySelectorAll('.success-box').forEach(scheduleSuccessBoxDismiss);
  document.body.addEventListener('htmx:afterSwap', (evt) => {
    scheduleSuccessBoxDismissals(evt.detail.target);
  });

  window.Vooglaadija.toast = {
    ...(Vooglaadija.toast || {}),
    show: showToast,
  };
})();
