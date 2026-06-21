(() => {
  /**
   * Initialize HTMX error handlers
   */
  function initHtmxErrorHandlers() {
    const AUTH_FORM_ENDPOINT_PREFIXES = ['/web/login', '/web/register'];

    function renderErrorNextToTarget(evt, responseHtml) {
      if (!responseHtml) return false;

      const sourceElement = evt.detail.elt;
      if (!sourceElement) return false;

      const targetSelector = sourceElement.getAttribute('hx-target');
      if (!targetSelector) return false;

      const targetElement = document.querySelector(targetSelector);
      if (!targetElement) return false;

      // Remove any previously-inserted server error that is a sibling of target.
      // Uses a data attribute marker so we only clean our own injected elements.
      const existing = targetElement.parentElement?.querySelector('[data-htmx-server-error="1"]');
      if (existing) existing.remove();

      // Parse the response as a document fragment so scripts are NOT executed.
      const wrapper = document.createElement('div');
      wrapper.setAttribute('data-htmx-server-error', '1');
      wrapper.innerHTML = responseHtml;

      // Insert AFTER the target element so the target's children (e.g. the
      // download rows in #download-rows) are preserved intact. The server-side
      // HTML fragment already includes proper error styling (error-box class).
      targetElement.insertAdjacentElement('afterend', wrapper);
      return true;
    }

    // Global HTMX error handler
    document.body.addEventListener('htmx:responseError', (evt) => {
      const xhr = evt.detail.xhr;
      const requestPath = evt.detail.pathInfo?.requestPath || '';
      const isAuthFormRequest = AUTH_FORM_ENDPOINT_PREFIXES.some((prefix) =>
        requestPath.startsWith(prefix),
      );
      const isWebFormRequest = requestPath.startsWith('/web/');

      // For web forms, render server-provided 4xx HTML errors inline.
      // This avoids duplicate generic toasts when backend returns an HTML error fragment.
      // The error is inserted as a sibling AFTER the hx-target, preserving the
      // target's children (e.g. download rows) intact. Use a data-attribute marker
      // so previous inline errors are cleaned up before inserting a new one.
      if (isWebFormRequest && xhr.status >= 400 && xhr.status < 500) {
        if (renderErrorNextToTarget(evt, xhr.responseText)) {
          if (xhr.status !== 401) {
            return;
          }
        }
      }

      switch (xhr.status) {
        case 401: {
          // Allow auth form endpoints to handle credential errors inline.
          if (isAuthFormRequest) {
            return;
          }
          // Token expired or invalid on protected endpoints - redirect to login.
          window.location.href = '/web/login?expired=1';
          break;
        }

        case 403:
          window.showToast('You do not have permission to perform this action', 'error');
          break;

        case 429: {
          const retryAfter = xhr.getResponseHeader('Retry-After');
          window.showToast(
            retryAfter
              ? `Rate limited. Try again in ${retryAfter}s`
              : 'Too many requests. Please wait before trying again.',
            'warning',
          );
          break;
        }

        default:
          if (xhr.status >= 500) {
            window.showToast('Server error. Please try again later.', 'error');
          } else if (xhr.status >= 400) {
            window.showToast('Request failed. Please check your input.', 'error');
          }
      }
    });

    // Handle htmx:afterRequest for form validation errors
    document.body.addEventListener('htmx:afterRequest', (evt) => {
      const xhr = evt.detail.xhr;

      // Handle 422 Unprocessable Entity (validation errors)
      if (xhr.status === 422) {
        // Try to parse error from response
        let errorMessage = null;
        try {
          const response = JSON.parse(xhr.responseText);
          // Check various error response shapes in order of specificity
          if (response.error?.message) {
            errorMessage = response.error.message;
          } else if (response.message) {
            errorMessage = response.message;
          } else if (response.error) {
            // response.error could be a string or object with other fields
            errorMessage =
              typeof response.error === 'string'
                ? response.error
                : response.error.error || response.error.detail || JSON.stringify(response.error);
          } else if (response.errors && Array.isArray(response.errors)) {
            // Field-level validation errors - join them
            errorMessage = response.errors.map((e) => e.message || JSON.stringify(e)).join('; ');
          } else if (response.detail) {
            errorMessage = response.detail;
          }
        } catch (_e) {
          // If not JSON, continue to HTML extraction below
        }

        if (errorMessage) {
          // Sanitize HTML by extracting text content using DOM
          const tempDiv = document.createElement('div');
          tempDiv.innerHTML = errorMessage; // Parse as HTML to strip tags
          const sanitized = tempDiv.textContent.trim(); // Extract plain text
          window.showToast(sanitized || 'Request failed. Please try again.', 'error');
        } else if (xhr.responseText) {
          // Fall back to extracting plain text from HTML response
          const tempDiv = document.createElement('div');
          tempDiv.innerHTML = xhr.responseText;
          const plainText = (tempDiv.textContent || tempDiv.innerText || '').trim();
          if (plainText) {
            window.showToast(plainText, 'error');
          } else {
            window.showToast('Request failed. Please try again.', 'error');
          }
        } else {
          // No response body - still show generic error
          window.showToast('Request failed. Please try again.', 'error');
        }
      }
    });
  }

  // Initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initHtmxErrorHandlers);
  } else {
    initHtmxErrorHandlers();
  }
})();
