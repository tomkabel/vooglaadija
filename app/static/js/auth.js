(() => {
  const Vooglaadija = window.Vooglaadija;

  // Public pages that don't need auth
  const PUBLIC_PAGES = ['/web/login', '/web/register', '/web/health'];

  /**
   * Refresh access token
   */
  async function refreshAccessToken() {
    try {
      const csrfMeta = document.querySelector('meta[name="csrf-token"]');
      const csrfToken = csrfMeta ? csrfMeta.getAttribute('content') : '';

      const response = await fetch('/api/v1/auth/refresh', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': csrfToken,
        },
        credentials: 'include', // Include cookies automatically
      });

      if (!response.ok) throw new Error('Refresh failed');
      return true;
    } catch (error) {
      console.error('Token refresh failed:', error);
      return false;
    }
  }

  /**
   * Initialize authentication on page load
   */
  async function initAuth() {
    // Check for logged out session message FIRST
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('logged_out') === '1') {
      Vooglaadija.toast.show('You have been logged out successfully.', 'info');
      window.history.replaceState({}, '', window.location.pathname);
    }

    // Check for expired session message
    if (urlParams.get('expired') === '1') {
      Vooglaadija.toast.show('Your session has expired. Please log in again.', 'info');
      window.history.replaceState({}, '', window.location.pathname);
    }

    // Check for deleted account message
    if (urlParams.get('account_deleted') === '1') {
      Vooglaadija.toast.show('Your account has been deleted.', 'info');
      window.history.replaceState({}, '', window.location.pathname);
    }

    // Check if we're on a public page (no auth needed) - AFTER query param handling
    if (PUBLIC_PAGES.includes(window.location.pathname)) return;
  }

  // Run on every page load
  initAuth();

  window.Vooglaadija.auth = {
    refreshAccessToken,
  };
})();
