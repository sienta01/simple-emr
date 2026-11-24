/* Registration flow: search patient (or scan their card) -> (new patient | edit patient | NIK reminder) -> pick doctor
   -> back to the list. */
(() => {
  'use strict';
  const { $, $$, api, toast, escapeHtml, debounce } = window.EMR;

  const dlg = $('#daftar-dialog');
  if (!dlg) return;
  const nikDlg = $('#nik-dialog');
  const searchInput = $('#px-search', dlg);
  const resultsBody = $('#px-results', dlg);
  const statusBox = $('#px-status', dlg);
  const newForm = $('#px-new-form', dlg);
  const nikInput = $('#nik-input', nikDlg);
  const nikError = $('[data-nik-error]', nikDlg);
  const registerBtn = $('[data-action="register"]', dlg);
  const canEdit = Boolean(dlg.dataset.updateUrl);
  const cardUrl = dlg.dataset.cardUrl;  // absent without access to the patient database
  const cardPrefix = (dlg.dataset.cardPrefix || '').toUpperCase();
  const scanStep = $('[data-step="scan"]', dlg);
  const scanStatus = $('[data-scan-status]', dlg);

  const TITLES = {
    search: 'Form daftar pasien', scan: 'Scan kartu periksa', new: 'Pasien baru', edit: 'Ubah data pasien', doctor: 'Pilih dokter',
  };
  const LAST_DOCTOR_KEY = 'emr-last-doctor';

  let results = [];
  let active = -1;
  let selected = null;
  let editing = null;  // the found patient being corrected; null while creating a new one
  let searchSeq = 0;

  function showStep(step) {
    $$('[data-step]', dlg).forEach((s) => { s.hidden = !s.dataset.step.split(' ').includes(step); });
    $('[data-step-title]', dlg).textContent = TITLES[step];
    if (step === 'scan') {
      $('[data-back]', scanStep).focus();
      reader?.start();
    } else {
      reader?.stop();
    }
    if (step === 'search') searchInput.focus();
    if (step === 'new' || step === 'edit') $('input:not([disabled])', newForm)?.focus();
  }

  function setStatus(name) {
    $$('[data-status]', statusBox).forEach((el) => { el.hidden = el.dataset.status !== name; });
    statusBox.hidden = name === 'results';
  }

  function open() {
    selected = null;
    editing = null;
    results = [];
    active = -1;
    searchInput.value = '';
    resultsBody.innerHTML = '';
    setStatus('hint');
    newForm.reset();
    clearErrors();
    dlg.showModal();
    showStep('search');
  }

  $$('[data-open-daftar]').forEach((btn) => btn.addEventListener('click', open));
  // Keyboard shortcut for the front desk: F2 opens the registration form.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'F2' && !dlg.open) { e.preventDefault(); open(); }
  });

  // --- Search ---------------------------------------------------------------
  const runSearch = debounce(async () => {
    const q = searchInput.value.trim();
    const seq = ++searchSeq;
    if (isCard(q)) return;  // a USB scanner typed a card: Enter opens it (below)
    if (q.length < 2) {
      results = [];
      renderResults();
      setStatus('hint');
      return;
    }
    setStatus('loading');
    try {
      const data = await api(`${dlg.dataset.searchUrl}?q=${encodeURIComponent(q)}`);
      if (seq !== searchSeq) return;
      results = data.results;
      active = results.length ? 0 : -1;
      renderResults();
      setStatus(results.length ? 'results' : 'empty');
    } catch (err) {
      if (seq === searchSeq) { setStatus('hint'); toast(err.message, 'error'); }
    }
  }, 220);

  searchInput.addEventListener('input', runSearch);
  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && isCard(searchInput.value)) { e.preventDefault(); useCard(searchInput.value); return; }
    if (!results.length) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); active = Math.min(active + 1, results.length - 1); renderResults(); }
    if (e.key === 'ArrowUp') { e.preventDefault(); active = Math.max(active - 1, 0); renderResults(); }
    if (e.key === 'Enter' && active >= 0) { e.preventDefault(); pick(results[active]); }
  });

  function todayNote(p) {
    if (!p.today.length) return '';
    const text = p.today.map((v) => `No. ${v.no_antrian} · ${v.dokter} (${v.status_label.toLowerCase()})`).join('; ');
    return `<div class="today-chip">Sudah terdaftar hari ini: ${escapeHtml(text)}</div>`;
  }

  function renderResults() {
    resultsBody.innerHTML = results.map((p, i) => `
      <tr class="${i === active ? 'active' : ''}" data-index="${i}">
        <td class="mono">${escapeHtml(p.no_rm)}</td>
        <td><div class="cell-name">${escapeHtml(p.nama)}</div>
            <div class="cell-sub">${escapeHtml(p.jk_umur)} · ${escapeHtml(p.ttl)}</div>${todayNote(p)}</td>
        <td class="cell-addr">${escapeHtml(p.alamat)}</td>
        <td class="mono">${p.nik ? escapeHtml(p.nik) : '<span class="text-warn">belum ada</span>'}</td>
        <td class="col-actions">
          ${canEdit ? `<button type="button" class="btn btn-secondary btn-sm" data-edit="${i}" title="Perbaiki data pasien sebelum didaftarkan"><svg class="icon"><use href="#i-pencil"/></svg> Ubah</button>` : ''}
          <button type="button" class="btn btn-primary btn-sm" data-pick="${i}">Pilih</button>
        </td>
      </tr>`).join('');
    resultsBody.querySelector('tr.active')?.scrollIntoView({ block: 'nearest' });
  }

  resultsBody.addEventListener('click', (e) => {
    const row = e.target.closest('tr[data-index]');
    if (!row) return;
    if (e.target.closest('[data-pick]')) { pick(results[+row.dataset.index]); return; }
    if (e.target.closest('[data-edit]')) { openForm(results[+row.dataset.index]); return; }
    active = +row.dataset.index;
    renderResults();
  });
  resultsBody.addEventListener('dblclick', (e) => {
    const row = e.target.closest('tr[data-index]');
    if (row && !e.target.closest('button')) pick(results[+row.dataset.index]);
  });

  function pick(patient) {
    selected = patient;
    if (!patient.nik) askNik(patient);
    else goDoctor();
  }

  // --- NIK reminder ---------------------------------------------------------
  function askNik(patient) {
    $('[data-nik-name]', nikDlg).textContent = `${patient.nama} (RM ${patient.no_rm})`;
    nikInput.value = '';
    nikError.textContent = '';
    nikDlg.showModal();
    nikInput.focus();
  }

  nikInput.addEventListener('input', () => {
    nikInput.value = nikInput.value.replace(/\D/g, '').slice(0, 16);
    nikError.textContent = '';
  });

  $('#nik-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const nik = nikInput.value.trim();
    if (!/^\d{16}$/.test(nik)) { nikError.textContent = 'NIK harus 16 digit angka.'; return; }
    try {
      const url = dlg.dataset.nikUrl.replace('/0/', `/${selected.id}/`);
      const data = await api(url, { method: 'POST', data: { nik } });
      selected = { ...data.patient, today: selected.today };
      nikDlg.close();
      toast('NIK tersimpan.');
      goDoctor();
    } catch (err) {
      nikError.textContent = err.message;
    }
  });

  $('[data-nik-skip]', nikDlg).addEventListener('click', () => { nikDlg.close(); goDoctor(); });
  nikDlg.addEventListener('cancel', (e) => { e.preventDefault(); nikDlg.close(); goDoctor(); });

  // --- New patient / edit patient ------------------------------------------
  // One form serves both: empty for a new patient, or filled with a found patient's data to correct it.
  function openForm(patient = null) {
    editing = patient;
    newForm.reset();
    clearErrors();
    if (patient) {
      Object.entries(patient.fields).forEach(([name, value]) => {
        const input = newForm.elements[name];
        if (input) input.value = value ?? '';
      });
      $('[data-no-rm]', newForm).value = patient.no_rm;
      showStep('edit');
      return;
    }
    const q = searchInput.value.trim();
    // A typed name (not a number) is a good guess for the new patient's name.
    if (q && !/^\d+$/.test(q)) newForm.elements.nama.value = q.replace(/\b\w/g, (c) => c.toUpperCase());
    if (/^\d{16}$/.test(q)) newForm.elements.nik.value = q;
    showStep('new');
  }

  $$('[data-action="new-patient"]', dlg).forEach((btn) => btn.addEventListener('click', () => openForm()));

  newForm.elements.nik?.addEventListener('input', (e) => {
    e.target.value = e.target.value.replace(/\D/g, '').slice(0, 16);
  });

  function clearErrors() {
    $$('[data-error-for]', newForm).forEach((el) => { el.textContent = ''; });
    $$('.has-error', newForm).forEach((el) => el.classList.remove('has-error'));
  }

  function showErrors(errors) {
    let first = null;
    Object.entries(errors).forEach(([name, messages]) => {
      const holder = $(`[data-error-for="${name}"]`, newForm);
      if (holder) {
        holder.textContent = messages.join(' ');
        holder.closest('.field')?.classList.add('has-error');
        first = first || holder.closest('.field');
      } else {
        toast(messages.join(' '), 'error');
      }
    });
    first?.querySelector('input, select')?.focus();
  }

  newForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearErrors();
    const submit = $('button[type="submit"]', newForm);
    submit.disabled = true;
    try {
      const url = editing ? dlg.dataset.updateUrl.replace('/0/', `/${editing.id}/`) : dlg.dataset.createUrl;
      const data = await api(url, { method: 'POST', data: new FormData(newForm) });
      if (editing) {
        selected = { ...data.patient, today: editing.today };
        // "Ganti pasien" goes back to the results, which should show the corrected data.
        results = results.map((p) => (p.id === selected.id ? selected : p));
        renderResults();
        toast('Data pasien tersimpan.');
      } else {
        selected = data.patient;
        toast(`Pasien baru tersimpan — No. RM ${data.patient.no_rm}.`);
      }
      goDoctor();
    } catch (err) {
      if (err.body?.errors) showErrors(err.body.errors);
      else toast(err.message, 'error');
    } finally {
      submit.disabled = false;
    }
  });

  // --- Doctor ---------------------------------------------------------------
  // Same square as in the identity box: foto.js opens the photo dialog for it, so every patient
  // (new, corrected or returning) can be photographed right before registering.
  function photoSquare(p) {
    const has = Boolean(p.foto_url);
    const title = canEdit ? 'Foto pasien — klik untuk mengambil atau mengganti' : 'Lihat foto';
    return `
      <button type="button" class="id-photo sp-photo${has ? '' : ' is-empty'}" data-foto-open
              data-patient-id="${p.id}" data-patient-name="${escapeHtml(p.nama)}" title="${title}"${!has && !canEdit ? ' disabled' : ''}>
        <img data-foto-thumb alt="Foto ${escapeHtml(p.nama)}"${has ? ` src="${escapeHtml(p.foto_url)}"` : ' hidden'}>
        <span class="id-photo-empty" data-foto-thumb-empty${has ? ' hidden' : ''}><svg class="icon"><use href="#i-camera"/></svg>Foto</span>
      </button>`;
  }

  document.addEventListener('foto-change', (e) => {
    const { patientId, url } = e.detail;
    if (selected?.id === patientId) selected = { ...selected, foto_url: url };
    results = results.map((p) => (p.id === patientId ? { ...p, foto_url: url } : p));
  });

  function goDoctor() {
    const p = selected;
    const print = cardUrl ? `
      <a class="btn btn-secondary btn-sm sp-print" href="${escapeHtml(p.kartu_url)}?cetak=1" target="_blank" rel="noopener"
         title="Cetak kartu periksa (seukuran kartu ATM) dengan QR untuk pendaftaran berikutnya">
        <svg class="icon"><use href="#i-printer"/></svg> Cetak kartu</a>` : '';
    $('[data-selected-patient]', dlg).innerHTML = `
      ${photoSquare(p)}
      <div class="sp-rm"><small>No. RM</small><b>${escapeHtml(p.no_rm)}</b></div>
      <div class="sp-name">${escapeHtml(p.nama)}</div>${print}
      <div class="muted">${escapeHtml(p.jk_umur)} · ${escapeHtml(p.ttl)} · ${escapeHtml(p.alamat)}${p.nik ? '' : ' · <span class="text-warn">NIK belum ada</span>'}</div>`;
    const warn = $('[data-today-warning]', dlg);
    const waiting = (p.today || []).filter((v) => v.status === 'belum');
    warn.hidden = !waiting.length;
    warn.textContent = waiting.length
      ? `Pasien ini sudah menunggu hari ini: ${waiting.map((v) => `No. ${v.no_antrian} (${v.dokter})`).join(', ')}.`
      : '';

    const radios = $$('input[name="doctor"]', dlg);
    radios.forEach((r) => { r.checked = false; });
    let remembered = null;
    try { remembered = localStorage.getItem(LAST_DOCTOR_KEY); } catch { /* ignore */ }
    const preset = radios.length === 1 ? radios[0] : radios.find((r) => r.value === remembered);
    if (preset) preset.checked = true;
    showStep('doctor');
    (preset ? registerBtn : radios[0])?.focus();
  }

  $$('input[name="doctor"]', dlg).forEach((r) => r.addEventListener('dblclick', () => registerBtn.click()));

  registerBtn.addEventListener('click', async () => {
    const doctor = $('input[name="doctor"]:checked', dlg);
    if (!doctor) { toast('Pilih dokter terlebih dahulu.', 'error'); return; }
    registerBtn.disabled = true;
    try {
      const data = await api(dlg.dataset.registerUrl, { method: 'POST', data: { patient: selected.id, doctor: doctor.value } });
      try { localStorage.setItem(LAST_DOCTOR_KEY, doctor.value); } catch { /* ignore */ }
      window.location.href = data.redirect;
    } catch (err) {
      registerBtn.disabled = false;
      toast(err.message, 'error');
    }
  });

  // --- Patient card (QR "RM:<No. RM>") --------------------------------------------
  // Read by the webcam on the "Scan kartu" step, or typed by a USB barcode scanner (into the search box,
  // or anywhere on the page). Either way the patient is picked as if chosen from the search results.
  function isCard(text) {
    return Boolean(cardUrl && cardPrefix) && text.trim().toUpperCase().startsWith(cardPrefix);
  }

  let cardBusy = false;
  async function useCard(code, onError = (message) => toast(message, 'error')) {
    if (cardBusy) return false;
    cardBusy = true;
    try {
      const { patient } = await api(`${cardUrl}?kode=${encodeURIComponent(code.trim())}`);
      reader?.stop();
      searchInput.value = '';
      toast(`Kartu terbaca: ${patient.nama} (RM ${patient.no_rm}).`);
      pick(patient);
      return true;
    } catch (err) {
      onError(err.message);
      return false;
    } finally {
      cardBusy = false;
    }
  }

  function setScanStatus(text, kind = '') {
    if (!scanStatus) return;
    scanStatus.textContent = text;
    scanStatus.className = `scan-status${kind ? ` scan-status-${kind}` : ''}`;
  }

  function onScanState(state, message = '') {
    scanStep.dataset.state = state;
    const texts = { starting: 'Membuka kamera…', scanning: 'Mencari QR…' };
    setScanStatus(message || texts[state] || '', state === 'error' ? 'warn' : '');
  }

  // The same wrong code stays in front of the camera for a while: report it once, not ten times a second.
  let miss = { code: '', at: 0 };
  async function onScanCode(code) {
    if (code === miss.code && Date.now() - miss.at < 4000) return;
    if (!isCard(code)) {
      miss = { code, at: Date.now() };
      setScanStatus('QR ini bukan kartu periksa. Hadapkan QR di pojok kiri atas kartu.', 'error');
      return;
    }
    reader.pause();
    setScanStatus('Membaca kartu…');
    const ok = await useCard(code, (message) => {
      miss = { code, at: Date.now() };
      setScanStatus(message, 'error');
    });
    if (!ok) reader.resume();
  }

  const reader = scanStep && window.EMR.QrReader ? new window.EMR.QrReader({
    video: $('[data-scan-video]', scanStep),
    cameraSelect: $('[data-scan-camera]', scanStep),
    jsqrUrl: dlg.dataset.jsqrUrl,
    onCode: onScanCode,
    onState: onScanState,
  }) : null;

  $$('[data-action="scan"]', dlg).forEach((btn) => btn.addEventListener('click', () => showStep('scan')));
  dlg.addEventListener('close', () => reader?.stop());

  // USB barcode scanners "type" the QR text and press Enter, far faster than a person types. Outside a text
  // field (the search box handles its own), such a burst opens the registration form for that card.
  const isTyping = (el) => el?.isContentEditable
    || el?.matches?.('textarea, select, input:not([type="radio"]):not([type="checkbox"]):not([type="button"]):not([type="submit"])');
  let burst = '';
  let lastKey = 0;
  document.addEventListener('keydown', (e) => {
    if (!cardUrl || e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target)) return;
    if ($$('dialog[open]').some((d) => d !== dlg)) return;  // photo or NIK dialog on top
    const now = performance.now();
    if (now - lastKey > 60) burst = '';
    lastKey = now;
    if (e.key === 'Enter') {
      if (isCard(burst)) {
        e.preventDefault();
        if (!dlg.open) open();
        useCard(burst);
      }
      burst = '';
    } else if (e.key.length === 1) {
      burst += e.key;
    }
  });

  $$('[data-back]', dlg).forEach((btn) => btn.addEventListener('click', () => showStep(btn.dataset.back)));
})();
