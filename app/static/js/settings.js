(() => {
  const Vooglaadija = window.Vooglaadija;

  // ─── Username Save Form: Loading State ──────────────────────────────
  const usernameForm = document.getElementById('username-settings-form');
  const usernameSubmit = usernameForm?.querySelector('[data-username-submit]');
  const usernameSpinner = usernameForm?.querySelector('[data-username-spinner]');
  const usernameLabel = usernameForm?.querySelector('[data-username-label]');
  const usernameDefaultLabel = usernameLabel?.textContent || 'Save username';

  function setUsernameSaveLoading(isLoading) {
    if (!(usernameForm && usernameSubmit && usernameSpinner && usernameLabel)) return;

    usernameForm.setAttribute('aria-busy', String(isLoading));
    usernameSubmit.disabled = isLoading;
    usernameSubmit.setAttribute('aria-busy', String(isLoading));
    usernameSpinner.classList.toggle('hidden', !isLoading);
    usernameLabel.textContent = isLoading ? 'Saving…' : usernameDefaultLabel;
  }

  function isUsernameSettingsRequest(evt) {
    return Boolean(evt.detail?.elt?.closest('#username-settings-form'));
  }

  if (usernameForm) {
    usernameForm.addEventListener('htmx:beforeRequest', () => {
      setUsernameSaveLoading(true);
    });
    document.body.addEventListener('htmx:afterRequest', (evt) => {
      if (isUsernameSettingsRequest(evt)) {
        setUsernameSaveLoading(false);
      }
    });
    document.body.addEventListener('htmx:sendError', (evt) => {
      if (isUsernameSettingsRequest(evt)) {
        setUsernameSaveLoading(false);
      }
    });
  }

  // ─── Password Change Form: Match Validation ─────────────────────────
  const passwordForm = document.getElementById('password-change-form');
  if (passwordForm) {
    passwordForm.addEventListener('submit', (event) => {
      const newPassword = document.getElementById('new_password');
      const confirmPassword = document.getElementById('new_password_confirm');

      if (!(newPassword && confirmPassword)) return;

      if (newPassword.value !== confirmPassword.value) {
        event.preventDefault();
        Vooglaadija.toast.show('New password and confirmation do not match.', 'error');
      }
    });

    // Submit loading state
    const submitBtn = passwordForm.querySelector('button[type="submit"]');
    if (submitBtn) {
      passwordForm.addEventListener('htmx:beforeRequest', () => {
        submitBtn.disabled = true;
        submitBtn.textContent = 'Changing password\u2026';
      });
      document.body.addEventListener('htmx:afterRequest', (evt) => {
        if (evt.detail.elt?.closest('#password-change-form')) {
          submitBtn.disabled = false;
          submitBtn.textContent = 'Change password';
        }
      });
    }
  }

  // ─── New Password Strength Meter (Settings) ──────────────────────────
  const newPasswordInput = document.getElementById('new_password');
  const strengthFill = document.getElementById('new-password-strength-fill');
  const strengthText = document.getElementById('new-password-strength-text');

  if (newPasswordInput && strengthFill && strengthText) {
    function evaluatePasswordStrength(password) {
      const hasMinimumLength = password.length >= 8;
      const hasNumber = /\d/.test(password);
      const hasSpecialCharacter = /[^A-Za-z0-9]/.test(password);
      const checksPassed = [hasMinimumLength, hasNumber, hasSpecialCharacter].filter(
        Boolean,
      ).length;

      if (password.length === 0) {
        return { levelClass: '', label: '' };
      }

      if (!hasMinimumLength) {
        return { levelClass: 'is-weak', label: 'Weak password' };
      }

      if (checksPassed === 3) {
        return { levelClass: 'is-strong', label: 'Strong password' };
      }

      if (checksPassed === 2) {
        return { levelClass: 'is-medium', label: 'Medium strength password' };
      }

      return { levelClass: 'is-weak', label: 'Weak password' };
    }

    function updatePasswordStrength() {
      const strength = evaluatePasswordStrength(newPasswordInput.value);
      strengthFill.className = `password-strength-fill ${strength.levelClass}`;
      strengthText.className = `password-strength-text ${strength.levelClass}`;
      strengthText.textContent = strength.label;
    }

    newPasswordInput.addEventListener('input', updatePasswordStrength);
    updatePasswordStrength();
  }

  // ─── Delete Account Form ─────────────────────────────────────────────
  const deleteForm = document.getElementById('delete-account-form');
  if (deleteForm) {
    deleteForm.addEventListener('submit', (event) => {
      const confirmInput = document.getElementById('confirm_text');
      const confirmValue = confirmInput ? confirmInput.value.trim().toUpperCase() : '';
      if (confirmValue !== 'DELETE') {
        event.preventDefault();
        Vooglaadija.toast.show('Type "DELETE" to confirm account deletion.', 'warning');
        return;
      }
    });
  }
})();
