(() => {
  const Vooglaadija = window.Vooglaadija;

  const toolbar = document.querySelector('[data-bulk-toolbar]');
  const selectAll = document.querySelector('[data-select-all]');
  const countLabel = document.querySelector('[data-bulk-count]');
  const deleteBtn = document.querySelector('[data-bulk-delete]');

  function getCheckboxes() {
    return Array.from(document.querySelectorAll('[data-bulk-checkbox]'));
  }

  function getChecked() {
    return getCheckboxes().filter((cb) => cb.checked);
  }

  function updateBulkUI() {
    if (!deleteBtn) return;
    const checkboxes = getCheckboxes();
    const checked = getChecked();
    const checkedCount = checked.length;
    const total = checkboxes.length;

    if (countLabel) countLabel.textContent = `${checkedCount} selected`;
    deleteBtn.disabled = checkedCount === 0;

    if (selectAll) {
      selectAll.checked = total > 0 && checkedCount === total;
      selectAll.indeterminate = checkedCount > 0 && checkedCount < total;
    }
  }

  function toggleAll(checked) {
    for (const cb of getCheckboxes()) cb.checked = checked;
    updateBulkUI();
  }

  if (selectAll) {
    selectAll.addEventListener('change', () => toggleAll(selectAll.checked));
  }

  const rowsContainer = document.getElementById('download-rows');
  if (rowsContainer) {
    rowsContainer.addEventListener('change', (evt) => {
      if (evt.target && evt.target.matches('[data-bulk-checkbox]')) updateBulkUI();
    });
  }

  document.body.addEventListener('htmx:afterSwap', () => updateBulkUI());
  document.body.addEventListener('htmx:afterRequest', (evt) => {
    if (evt.detail?.elt && evt.detail.elt.matches?.('[data-bulk-delete]')) {
      updateBulkUI();
    }
  });

  document.body.addEventListener('bulk-delete-complete', (evt) => {
    const detail = evt.detail || {};
    const deleted = Array.isArray(detail.deleted) ? detail.deleted : [];
    for (const id of deleted) {
      const row = document.querySelector(`[data-job-id="${CSS.escape(String(id))}"]`);
      if (row) row.remove();
    }
    updateBulkUI();
    if (typeof Vooglaadija?.toast?.show === 'function') {
      const skipped = Array.isArray(detail.skipped) ? detail.skipped.length : 0;
      const requested = Array.isArray(detail.requested) ? detail.requested : deleted.length + skipped;
      if (skipped > 0) {
        Vooglaadija.toast.show(
          `Deleted ${deleted.length} of ${requested} selected downloads (${skipped} skipped).`,
          'info',
        );
      } else {
        Vooglaadija.toast.show(`Deleted ${deleted.length} download${deleted.length === 1 ? '' : 's'}.`, 'success');
      }
    }
  });

  updateBulkUI();
})();
