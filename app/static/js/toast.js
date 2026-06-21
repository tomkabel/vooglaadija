(() => {
  const container = document.getElementById('toast-container');
  if (!container) return;

  function showToast(message, type) {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type || 'info'}`;
    toast.setAttribute('role', 'alert');
    toast.setAttribute('aria-live', 'assertive');
    toast.setAttribute('aria-atomic', 'true');
    toast.textContent = message;

    container.appendChild(toast);

    setTimeout(() => {
      toast.classList.add('toast-exit');
      setTimeout(() => toast.remove(), 300);
    }, 5000);
  }

  window.showToast = showToast;
})();
