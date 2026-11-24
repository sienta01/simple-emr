/* Examination page: ICD-X and ICD-9-CM chips, copy-from-history, local draft, Ctrl+S. */
(() => {
  'use strict';
  const { $, api, toast, escapeHtml, debounce } = window.EMR;

  const form = $('#soap-form');
  if (!form) return;
  const readonly = form.hasAttribute('data-readonly');
  const fields = { s: 'subjective', o: 'objective', a: 'assessment', p: 'plan' };

  let dirty = false;
  let submitting = false;

  function flashEl(el) {
    el.classList.remove('flash');
    void el.offsetWidth;  // restart the animation
    el.classList.add('flash');
  }

  // --- Code chips (ICD-X on SOAP, ICD-9-CM on Tindakan) ---------------------
  const codeKey = (d) => `${(d.code || '').toUpperCase()}|${(d.name || '').toLowerCase()}`;

  function codeChips(root, hidden, url, onRender) {
    const box = $('[data-chips]', root);
    const input = $('.chips-text', root);
    const suggest = $('[data-suggest]', root);

    let items = [];
    try { items = JSON.parse(hidden.value || '[]'); } catch { items = []; }

    function render() {
      box.innerHTML = items.map((d, i) => `
        <span class="chip" title="${escapeHtml(`${d.code} ${d.name}`.trim())}">
          ${d.code ? `<b>${escapeHtml(d.code)}</b>` : ''}<span>${escapeHtml(d.name)}</span>
          ${readonly ? '' : `<button type="button" data-remove="${i}" aria-label="Hapus ${escapeHtml(d.code || d.name)}">×</button>`}
        </span>`).join('') || (readonly ? '<span class="muted">—</span>' : '');
      hidden.value = JSON.stringify(items);
      onRender?.(items);
    }

    function add(d) {
      if (!d || (!d.code && !d.name)) return false;
      if (items.some((x) => codeKey(x) === codeKey(d))) return false;
      items.push({ code: d.code || '', name: d.name || d.code });
      render();
      markDirty();
      return true;
    }

    box.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-remove]');
      if (!btn) return;
      items.splice(+btn.dataset.remove, 1);
      render();
      markDirty();
      input?.focus();
    });

    if (input) {
      let options = [];
      let active = -1;
      let seq = 0;

      const close = () => { suggest.hidden = true; input.setAttribute('aria-expanded', 'false'); active = -1; };

      const show = (q) => {
        const free = q ? [{ code: '', name: q, free: true }] : [];
        options = [...options.filter((o) => !o.free), ...free];
        if (!options.length) { close(); return; }
        suggest.innerHTML = options.map((o, i) => o.free
          ? `<div class="suggest-item suggest-free${i === active ? ' active' : ''}" role="option" data-i="${i}">Tambah sebagai teks bebas: “${escapeHtml(o.name)}”</div>`
          : `<div class="suggest-item${i === active ? ' active' : ''}" role="option" data-i="${i}"><b>${escapeHtml(o.code)}</b><span>${escapeHtml(o.name)}</span></div>`,
        ).join('');
        suggest.hidden = false;
        input.setAttribute('aria-expanded', 'true');
        suggest.querySelector('.active')?.scrollIntoView({ block: 'nearest' });
      };

      const lookup = debounce(async () => {
        const q = input.value.trim();
        const mine = ++seq;
        if (!q) { options = []; close(); return; }
        try {
          const data = await api(`${url}?q=${encodeURIComponent(q)}`);
          if (mine !== seq) return;
          options = data.results;
          active = 0;
          show(q);
        } catch (err) { toast(err.message, 'error'); }
      }, 180);

      const choose = (i) => {
        const o = options[i];
        if (!o) return;
        add({ code: o.code, name: o.name });
        input.value = '';
        options = [];
        close();
        input.focus();
      };

      input.addEventListener('input', lookup);
      input.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown' && !suggest.hidden) { e.preventDefault(); active = Math.min(active + 1, options.length - 1); show(input.value.trim()); }
        else if (e.key === 'ArrowUp' && !suggest.hidden) { e.preventDefault(); active = Math.max(active - 1, 0); show(input.value.trim()); }
        else if (e.key === 'Enter') {
          e.preventDefault();  // never submit the whole form from this box
          if (!suggest.hidden && active >= 0) choose(active);
        } else if (e.key === 'Escape') { close(); }
        else if (e.key === 'Backspace' && !input.value && items.length) { items.pop(); render(); markDirty(); }
      });
      suggest.addEventListener('mousedown', (e) => {
        const item = e.target.closest('[data-i]');
        if (item) { e.preventDefault(); choose(+item.dataset.i); }
      });
      input.addEventListener('blur', () => setTimeout(close, 120));
      root.addEventListener('click', (e) => { if (e.target === root || e.target === box) input.focus(); });
    }

    render();
    return {
      root,
      add,
      get items() { return items; },
      set(list) { items = list; render(); },
    };
  }

  const icdX = codeChips($('[data-icd]', form), form.elements.diagnoses, form.dataset.icdUrl);
  // The Tindakan tab sits outside the form; its codes travel in the form's hidden "procedures" field.
  const pxCount = $('[data-px-count]');
  const icd9 = codeChips($('[data-icd9]'), form.elements.procedures, form.dataset.icd9Url, (items) => {
    if (!pxCount) return;
    pxCount.textContent = items.length;
    pxCount.hidden = !items.length;
  });

  // After a failed save, open the tab that holds the first error.
  const errorPanel = $('.has-error, .alert-error')?.closest('[data-panel]')?.dataset.panel;
  if (errorPanel) $(`[data-tab="${errorPanel}"]`)?.click();

  // --- Copy from Riwayat ----------------------------------------------------
  const riwayat = JSON.parse($('#riwayat-data')?.textContent || '[]');
  const byId = Object.fromEntries(riwayat.map((v) => [String(v.id), v]));

  function appendTo(letter, text) {
    if (!text) return;
    const ta = form.elements[fields[letter]];
    const current = ta.value.replace(/\s+$/, '');
    if (current.includes(text.trim())) { flashEl(ta); return; }
    ta.value = current ? `${current}\n${text}` : text;
    flashEl(ta);
    markDirty();
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-copy]');
    if (btn && !readonly) {
      const v = byId[btn.closest('[data-visit]')?.dataset.visit];
      if (!v) return;
      const what = btn.dataset.copy;
      if (what === 's' || what === 'all') appendTo('s', v.s);
      if (what === 'o' || what === 'all') appendTo('o', v.o);
      if (what === 'a' || what === 'all') {
        appendTo('a', v.a);
        v.dx.forEach((d) => icdX.add(d));
        if (v.dx.length) flashEl(icdX.root);
      }
      if (what === 'p' || what === 'all') appendTo('p', v.p);
      if (what === 'px') {
        v.px.forEach((d) => icd9.add(d));
        flashEl(icd9.root);
      }
      toast('Disalin ke form “Saat ini”.', 'success', 1600);
      return;
    }
    // Clicking a history row expands the clamped text.
    const row = e.target.closest('.riwayat-row');
    if (row && !window.getSelection()?.toString()) row.closest('.riwayat-visit').classList.toggle('expanded');
  });

  if (readonly) return;

  // Saving from the Tindakan tab with S/O/A/P still empty: show the SOAP tab, so the browser
  // can point at the empty field instead of silently refusing to submit.
  form.addEventListener('invalid', () => $('[data-tab="soap"]')?.click(), true);

  // --- Local draft ----------------------------------------------------------
  const draftKey = `emr-soap-draft-${form.dataset.visit}`;
  const draftState = $('[data-draft-state]', form);
  const DRAFT_TTL = 3 * 24 * 3600 * 1000;

  const snapshot = () => ({
    s: form.elements.subjective.value, o: form.elements.objective.value,
    a: form.elements.assessment.value, p: form.elements.plan.value,
    dx: icdX.items, px: icd9.items, t: Date.now(),
  });
  const isEmpty = () => Object.values(fields).every((f) => !form.elements[f].value.trim())
    && !icdX.items.length && !icd9.items.length;

  const saveDraft = debounce(() => {
    try {
      localStorage.setItem(draftKey, JSON.stringify(snapshot()));
      const time = new Date().toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' });
      draftState.textContent = `Draf tersimpan di perangkat ini · ${time}`;
    } catch { /* storage full or disabled */ }
  }, 600);

  function markDirty() {
    dirty = true;
    saveDraft();
  }

  // Forget stale drafts of other visits.
  try {
    Object.keys(localStorage).filter((k) => k.startsWith('emr-soap-draft-')).forEach((k) => {
      const d = JSON.parse(localStorage.getItem(k) || '{}');
      if (!d.t || Date.now() - d.t > DRAFT_TTL) localStorage.removeItem(k);
    });
  } catch { /* ignore */ }

  try {
    const draft = JSON.parse(localStorage.getItem(draftKey) || 'null');
    if (draft && isEmpty() && (draft.s || draft.o || draft.a || draft.p || (draft.dx || []).length || (draft.px || []).length)) {
      form.elements.subjective.value = draft.s || '';
      form.elements.objective.value = draft.o || '';
      form.elements.assessment.value = draft.a || '';
      form.elements.plan.value = draft.p || '';
      icdX.set(draft.dx || []);
      icd9.set(draft.px || []);
      dirty = true;
      draftState.innerHTML = 'Draf yang belum disimpan dipulihkan · <button type="button" data-discard>Buang</button>';
    }
  } catch { /* ignore */ }

  draftState.addEventListener('click', (e) => {
    if (!e.target.closest('[data-discard]')) return;
    try { localStorage.removeItem(draftKey); } catch { /* ignore */ }
    Object.values(fields).forEach((f) => { form.elements[f].value = ''; });
    icdX.set([]);
    icd9.set([]);
    dirty = false;
    draftState.textContent = '';
  });

  Object.values(fields).forEach((f) => form.elements[f].addEventListener('input', markDirty));

  form.addEventListener('submit', () => {
    submitting = true;
    try { localStorage.removeItem(draftKey); } catch { /* ignore */ }
  });

  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  window.addEventListener('beforeunload', (e) => {
    if (dirty && !submitting) { e.preventDefault(); e.returnValue = ''; }
  });

  if (!form.elements.subjective.value) form.elements.subjective.focus();
})();
