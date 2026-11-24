/* Shared helpers for every page. Page scripts use window.EMR. */
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const csrfToken = () => $('meta[name="csrf-token"]')?.content || '';

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);

  const debounce = (fn, ms) => {
    let timer;
    return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
  };

  class ApiError extends Error {
    constructor(message, status, body) { super(message); this.status = status; this.body = body; }
  }

  function firstError(errors) {
    if (!errors) return null;
    const first = Object.values(errors)[0];
    return Array.isArray(first) ? first[0] : first;
  }

  function sessionExpired() {
    toast('Sesi Anda berakhir. Silakan masuk kembali.', 'error');
    setTimeout(() => window.location.reload(), 1500);
  }

  async function api(url, { method = 'GET', data, json } = {}) {
    const opts = { method, credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } };
    if (method !== 'GET') opts.headers['X-CSRFToken'] = csrfToken();
    if (json !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(json);
    } else if (data instanceof FormData) {
      opts.body = data;
    } else if (data) {
      opts.body = new URLSearchParams(data);
    }
    let res;
    try {
      res = await fetch(url, opts);
    } catch {
      throw new ApiError('Tidak dapat terhubung ke server. Periksa jaringan.', 0);
    }
    if (res.redirected && new URL(res.url).pathname.startsWith('/login')) {
      sessionExpired();
      throw new ApiError('Sesi berakhir.', 401);
    }
    const isJson = (res.headers.get('content-type') || '').includes('application/json');
    const body = isJson ? await res.json() : null;
    if (!res.ok) {
      const msg = (body && (body.error || firstError(body.errors)))
        || (res.status === 403 ? 'Anda tidak memiliki akses untuk tindakan ini.' : `Terjadi kesalahan (${res.status}).`);
      throw new ApiError(msg, res.status, body);
    }
    return body;
  }

  function toast(message, type = 'success', ms = 3800) {
    const box = $('#toasts');
    if (!box) return;
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = message;
    box.appendChild(el);
    dismissLater(el, ms);
  }

  function dismissLater(el, ms) {
    setTimeout(() => {
      el.classList.add('leaving');
      setTimeout(() => el.remove(), 300);
    }, ms);
  }

  function anyDialogOpen() {
    return $$('dialog[open]').length > 0;
  }

  // Why getUserMedia failed, for the photo dialog and the card scanner.
  const CAMERA_ERRORS = {
    NotAllowedError: 'Akses kamera ditolak. Izinkan kamera untuk situs ini di pengaturan browser, lalu coba lagi.',
    SecurityError: 'Akses kamera ditolak. Izinkan kamera untuk situs ini di pengaturan browser, lalu coba lagi.',
    NotFoundError: 'Tidak ada kamera yang terdeteksi di perangkat ini.',
    OverconstrainedError: 'Kamera yang dipilih tidak ditemukan. Pilih kamera lain.',
    NotReadableError: 'Kamera sedang dipakai aplikasi lain. Tutup aplikasi itu, lalu coba lagi.',
  };
  const cameraError = (err) => CAMERA_ERRORS[err?.name] || `Kamera tidak dapat dibuka (${err?.name || err?.message}).`;

  window.EMR = { $, $$, api, toast, escapeHtml, debounce, ApiError, csrfToken, anyDialogOpen, cameraError };

  // Theme: "auto" follows the device; "light" / "dark" are remembered per browser.
  // core/_theme_init.html applies the saved choice before first paint.
  const THEMES = ['auto', 'light', 'dark'];
  const THEME_LABELS = { auto: 'Otomatis', light: 'Terang', dark: 'Gelap' };
  const THEME_ICONS = { auto: 'monitor', light: 'sun', dark: 'moon' };
  const currentTheme = () => document.documentElement.dataset.theme || 'auto';

  function applyTheme(theme, persist = true) {
    if (theme === 'auto') delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
    if (persist) {
      try {
        if (theme === 'auto') localStorage.removeItem('emr-theme');
        else localStorage.setItem('emr-theme', theme);
      } catch { /* private mode: the choice lasts for this page only */ }
    }
    $$('[data-theme-set]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.themeSet === theme)));
    $$('[data-theme-cycle]').forEach((b) => {
      b.title = `Tema: ${THEME_LABELS[theme]}`;
      b.querySelector('use')?.setAttribute('href', `#i-${THEME_ICONS[theme]}`);
    });
  }

  document.addEventListener('click', (e) => {
    const set = e.target.closest('[data-theme-set]');
    if (set) applyTheme(set.dataset.themeSet);
    if (e.target.closest('[data-theme-cycle]')) {
      const next = THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length];
      applyTheme(next);
      toast(`Tema: ${THEME_LABELS[next]}`, 'success', 1500);
    }
  });
  // Keep other open tabs of the app in step.
  window.addEventListener('storage', (e) => {
    if (e.key === 'emr-theme') applyTheme(e.newValue || 'auto', false);
  });
  applyTheme(currentTheme(), false);

  // Django messages rendered by the server fade out on their own.
  $$('#toasts .toast').forEach((el) => dismissLater(el, 5000));

  // Sidebar: collapse on desktop, slide-in drawer on small screens.
  $('[data-sidebar-toggle]')?.addEventListener('click', () => {
    const collapsed = document.documentElement.classList.toggle('sidebar-collapsed');
    try { localStorage.setItem('emr-sidebar', collapsed ? 'collapsed' : 'open'); } catch { /* private mode */ }
  });
  $('[data-sidebar-open]')?.addEventListener('click', () => document.body.classList.add('sidebar-open'));
  $('[data-sidebar-close]')?.addEventListener('click', () => document.body.classList.remove('sidebar-open'));

  // Close buttons inside dialogs.
  document.addEventListener('click', (e) => {
    const closer = e.target.closest('[data-close]');
    if (closer) closer.closest('dialog')?.close();
  });

  // Queue rows open the examination page; RM rows open on double click / Enter.
  document.addEventListener('click', (e) => {
    const row = e.target.closest('tr[data-href]');
    if (!row || e.target.closest('a, button, input, select, textarea')) return;
    window.location.href = row.dataset.href;
  });
  document.addEventListener('dblclick', (e) => {
    const row = e.target.closest('tr[data-dblhref]');
    if (row) window.location.href = row.dataset.dblhref;
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const row = e.target.closest?.('tr[data-href], tr[data-dblhref]');
    if (row && e.target === row) window.location.href = row.dataset.href || row.dataset.dblhref;
  });

  // Date picker in the queue toolbars.
  $$('[data-date-nav]').forEach((input) => {
    input.addEventListener('change', () => {
      if (!input.value) return;
      const url = new URL(window.location.href);
      url.searchParams.set('tanggal', input.value);
      window.location.href = url.toString();
    });
  });

  // Queue lists stay live: a cheap fingerprint is polled every few seconds and the table is
  // reloaded only when a visit was added, called, examined or cancelled.
  $$('[data-autorefresh]').forEach((box) => {
    const opts = { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } };
    let version = box.dataset.version || '';
    let busy = false;
    const check = async () => {
      if (busy || document.hidden || anyDialogOpen()) return;
      busy = true;
      try {
        const res = await fetch(box.dataset.versionUrl, opts);
        if (!res.ok || res.redirected) return;
        const { v } = await res.json();
        if (v === version) return;
        const page = await fetch(box.dataset.autorefresh, opts);
        if (page.ok && !page.redirected) {
          box.innerHTML = await page.text();
          version = v;
        }
      } catch { /* network hiccup: try again next tick */ } finally {
        busy = false;
      }
    };
    setInterval(check, 4000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
  });

  // Search boxes that filter while typing: the server renders only the results (partial=1),
  // and the address bar follows so reload / back keep the search.
  // <template data-live-update="#sel"> in the results refreshes another element (e.g. the count).
  $$('form[data-live-search]').forEach((form) => {
    const target = $(form.dataset.liveSearch);
    const input = $('input[type="search"]', form);
    const reset = $('[data-live-reset]', form);
    if (!target || !input) return;
    const opts = { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } };
    const query = () => {
      const params = new URLSearchParams(new FormData(form));
      const q = input.value.trim();
      if (q) params.set(input.name, q); else params.delete(input.name);
      return params.toString();
    };
    let shown = query();
    let controller = null;
    const run = async () => {
      const qs = query();
      if (reset) reset.hidden = !input.value.trim();
      controller?.abort();
      if (qs === shown) return;
      const mine = controller = new AbortController();
      target.setAttribute('aria-busy', 'true');
      try {
        const res = await fetch(`${window.location.pathname}?${qs}${qs ? '&' : ''}partial=1`, { ...opts, signal: mine.signal });
        if (res.redirected && new URL(res.url).pathname.startsWith('/login')) { sessionExpired(); return; }
        if (!res.ok) throw new Error(String(res.status));
        target.innerHTML = await res.text();
        $$('template[data-live-update]', target).forEach((t) => {
          $(t.dataset.liveUpdate)?.replaceChildren(t.content.cloneNode(true));
          t.remove();
        });
        shown = qs;
        history.replaceState(null, '', qs ? `?${qs}` : window.location.pathname);
      } catch (err) {
        if (err.name !== 'AbortError') toast('Pencarian gagal. Periksa jaringan.', 'error');
      } finally {
        if (controller === mine) target.removeAttribute('aria-busy');
      }
    };
    input.addEventListener('input', debounce(run, 250));
    form.addEventListener('submit', (e) => { e.preventDefault(); run(); });
    reset?.addEventListener('click', (e) => {
      e.preventDefault();
      input.value = '';
      input.focus();
      run();
    });
  });

  // Cancel a registration from the Pendaftaran list.
  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-cancel-visit]');
    if (!btn) return;
    if (!window.confirm(`Batalkan pendaftaran ${btn.dataset.name}?`)) return;
    btn.disabled = true;
    try {
      await api(btn.dataset.cancelVisit, { method: 'POST' });
      toast(`Pendaftaran ${btn.dataset.name} dibatalkan.`);
      setTimeout(() => window.location.reload(), 600);
    } catch (err) {
      btn.disabled = false;
      toast(err.message, 'error');
    }
  });

  // Tabs on detail pages (SOAP / Penunjang); the active tab survives reloads via the URL hash.
  $$('[data-tabs]').forEach((tabs) => {
    const buttons = $$('[data-tab]', tabs);
    const show = (name) => {
      if (!buttons.some((b) => b.dataset.tab === name)) return;
      buttons.forEach((b) => {
        const on = b.dataset.tab === name;
        b.classList.toggle('active', on);
        b.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      $$('[data-panel]').forEach((p) => { p.hidden = p.dataset.panel !== name; });
    };
    buttons.forEach((b) => b.addEventListener('click', () => {
      show(b.dataset.tab);
      history.replaceState(null, '', `#${b.dataset.tab}`);
    }));
    show(window.location.hash.slice(1) || buttons[0]?.dataset.tab);
  });

  // Allergy field in the identity box saves itself.
  const NO_ALLERGY = ['', '-', 'tidak', 'tidak ada', 'tak ada', 'none', 'nihil'];
  $$('[data-alergi-url]').forEach((input) => {
    let saved = input.value.trim();
    const state = input.parentElement.querySelector('[data-save-state]');
    const box = input.closest('.identity');
    const flag = () => box?.classList.toggle('has-allergy', !NO_ALLERGY.includes(input.value.trim().toLowerCase()));
    const setState = (text, cls = '') => { if (state) { state.textContent = text; state.className = `save-state ${cls}`; } };
    const save = async () => {
      const value = input.value.trim();
      if (value === saved) return;
      setState('Menyimpan…');
      try {
        await api(input.dataset.alergiUrl, { method: 'POST', data: { alergi: value } });
        saved = value;
        setState('Tersimpan', 'ok');
        setTimeout(() => setState(''), 2500);
      } catch (err) {
        setState('Gagal', 'err');
        toast(err.message, 'error');
      }
    };
    input.addEventListener('input', () => { flag(); setState(''); });
    input.addEventListener('change', save);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
      if (e.key === 'Escape') { input.value = saved; flag(); input.blur(); }
    });
  });
})();
