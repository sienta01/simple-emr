/* Phone home page: find a patient to upload results for. */
(() => {
  'use strict';
  const { $, $$, api, toast, escapeHtml, debounce } = window.EMR;

  const input = $('[data-m-search]');
  if (!input) return;
  const results = $('[data-m-results]');
  const today = $$('[data-m-today]');
  let seq = 0;

  const run = debounce(async () => {
    const q = input.value.trim();
    const mine = ++seq;
    const searching = q.length >= 2;
    today.forEach((el) => { el.hidden = searching; });
    results.hidden = !searching;
    if (!searching) return;
    try {
      const data = await api(`${input.dataset.mSearch}?q=${encodeURIComponent(q)}`);
      if (mine !== seq) return;
      results.innerHTML = data.results.map((p) => `
        <a class="m-patient" href="${input.dataset.patientUrl.replace('/0/', `/${p.id}/`)}">
          <span class="m-patient-body">
            <span class="m-patient-name">${escapeHtml(p.nama)}</span>
            <span class="m-patient-sub">RM ${escapeHtml(p.no_rm)} · ${escapeHtml(p.jk_umur)} · ${escapeHtml(p.alamat)}</span>
          </span>
          <svg class="icon"><use href="#i-chevron-right"/></svg>
        </a>`).join('') || '<p class="empty">Pasien tidak ditemukan.</p>';
    } catch (err) {
      toast(err.message, 'error');
    }
  }, 250);

  input.addEventListener('input', run);
})();
