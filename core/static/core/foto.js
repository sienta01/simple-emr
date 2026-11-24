/* Patient photo: enlarged view, webcam snapshot or file upload, delete.
   Any [data-foto-open][data-patient-id] square opens the dialog for that patient (identity box,
   registration dialog). After a change the square updates itself and "foto-change" fires on document. */
(() => {
  'use strict';
  const { $, $$, api, toast, escapeHtml, cameraError } = window.EMR;

  const dlg = $('#foto-dialog');
  if (!dlg) return;

  const current = $('[data-foto-current]', dlg);
  const placeholder = $('[data-foto-placeholder]', dlg);
  const video = $('[data-foto-video]', dlg);
  const shot = $('[data-foto-shot]', dlg);
  const cameraSelect = $('[data-foto-camera]', dlg);
  const msg = $('[data-foto-msg]', dlg);
  const fileInput = $('[data-foto-file]', dlg);
  const captureInput = $('[data-foto-capture]', dlg);
  const deleteBtn = $('[data-foto-delete]', dlg);
  const maxBytes = (+dlg.dataset.maxMb || 25) * 1024 * 1024;

  const SHOT_SIDE = 800;  // same as PHOTO_SIDE on the server
  const CAMERA_KEY = 'emr-camera';

  let stream = null;
  let busy = false;
  let opener = null;  // the square that opened the dialog; it names the patient

  const hasPhoto = () => current.hasAttribute('src');
  const patientUrl = (key) => dlg.dataset[key].replace('/0/', `/${opener.dataset.patientId}/`);

  // idle: the stored photo (or placeholder) | live: webcam preview | shot: the snapshot to save
  function setMode(mode) {
    $$('[data-foto-mode]', dlg).forEach((el) => { el.hidden = el.dataset.fotoMode !== mode; });
    current.hidden = mode !== 'idle' || !hasPhoto();
    placeholder.hidden = mode !== 'idle' || hasPhoto();
    video.hidden = mode !== 'live';
    shot.hidden = mode !== 'shot';
    cameraSelect.hidden = mode !== 'live' || cameraSelect.options.length < 2;
    if (deleteBtn) deleteBtn.hidden = !hasPhoto();
  }

  function showMsg(text) {
    msg.textContent = text || '';
    msg.hidden = !text;
  }

  function setSrc(img, url) {
    if (url) img.src = url;
    else img.removeAttribute('src');
  }

  function showPhoto(url) {
    setSrc(current, url);
    const thumb = $('[data-foto-thumb]', opener);
    setSrc(thumb, url);
    thumb.hidden = !url;
    $('[data-foto-thumb-empty]', opener).hidden = !!url;
    opener.classList.toggle('is-empty', !url);
    // On document, not the square: the page may have re-rendered the square meanwhile.
    document.dispatchEvent(new CustomEvent('foto-change', { detail: { patientId: +opener.dataset.patientId, url } }));
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-foto-open]');
    if (!btn || btn.disabled) return;
    opener = btn;
    $('[data-foto-name]', dlg).textContent = btn.dataset.patientName;
    current.alt = `Foto ${btn.dataset.patientName}`;
    setSrc(current, $('[data-foto-thumb]', btn).getAttribute('src'));
    showMsg('');
    setMode('idle');
    dlg.showModal();
  });
  dlg.addEventListener('close', stopCamera);

  // --- Webcam ---------------------------------------------------------------
  async function startCamera(deviceId) {
    if (!navigator.mediaDevices?.getUserMedia) {
      // Browsers only allow the camera on HTTPS pages (and localhost). Phones can still use the camera app.
      if (window.matchMedia('(pointer: coarse)').matches) captureInput.click();
      else showMsg('Kamera tidak bisa dibuka karena aplikasi ini dibuka lewat HTTP: browser hanya mengizinkan kamera di alamat HTTPS (atau localhost). Pakai "Unggah foto", atau minta admin memasang HTTPS.');
      return;
    }
    stopCamera();
    showMsg('');
    let remembered = null;
    try { remembered = localStorage.getItem(CAMERA_KEY); } catch { /* private mode */ }
    const constraints = { width: { ideal: 1280 }, height: { ideal: 720 } };
    if (deviceId) constraints.deviceId = { exact: deviceId };
    else if (remembered) constraints.deviceId = { ideal: remembered };
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: constraints, audio: false });
    } catch (err) {
      showMsg(cameraError(err));
      setMode('idle');
      return;
    }
    if (!dlg.open) { stopCamera(); return; }  // closed while the browser asked for permission
    video.srcObject = stream;
    await listCameras(stream.getVideoTracks()[0]?.getSettings().deviceId);
    setMode('live');
    $('[data-foto-snap]', dlg).focus();
  }

  function stopCamera() {
    stream?.getTracks().forEach((track) => track.stop());
    stream = null;
    video.srcObject = null;
  }

  // Device names are only readable once camera access is granted, so the list is filled after starting.
  async function listCameras(activeId) {
    let cameras = [];
    try {
      cameras = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'videoinput');
    } catch { /* keep the current camera only */ }
    cameraSelect.innerHTML = cameras.map((d, i) => (
      `<option value="${escapeHtml(d.deviceId)}">${escapeHtml(d.label || `Kamera ${i + 1}`)}</option>`
    )).join('');
    if (cameras.some((d) => d.deviceId === activeId)) cameraSelect.value = activeId;
  }

  cameraSelect.addEventListener('change', () => {
    try { localStorage.setItem(CAMERA_KEY, cameraSelect.value); } catch { /* private mode */ }
    startCamera(cameraSelect.value);
  });

  $('[data-foto-camera-start]', dlg)?.addEventListener('click', () => startCamera());
  $('[data-foto-cancel]', dlg)?.addEventListener('click', () => { stopCamera(); setMode('idle'); });

  $('[data-foto-snap]', dlg)?.addEventListener('click', () => {
    const w = video.videoWidth;
    const h = video.videoHeight;
    if (!w || !h) return;  // camera not ready yet
    // Keep the centre square: the same part the preview shows.
    const side = Math.min(w, h);
    const out = Math.min(side, SHOT_SIDE);
    shot.width = out;
    shot.height = out;
    shot.getContext('2d').drawImage(video, (w - side) / 2, (h - side) / 2, side, side, 0, 0, out, out);
    setMode('shot');
    $('[data-foto-save]', dlg).focus();
  });

  $('[data-foto-retake]', dlg)?.addEventListener('click', () => {
    setMode('live');
    $('[data-foto-snap]', dlg).focus();
  });

  $('[data-foto-save]', dlg)?.addEventListener('click', () => {
    shot.toBlob((blob) => { if (blob) upload(blob, 'foto.jpg'); }, 'image/jpeg', 0.92);
  });

  // --- Upload / delete ------------------------------------------------------
  $('[data-foto-pick]', dlg)?.addEventListener('click', () => fileInput.click());
  [fileInput, captureInput].forEach((input) => input?.addEventListener('change', () => {
    const file = input.files[0];
    input.value = '';
    if (file) upload(file, file.name);
  }));

  function setBusy(on) {
    busy = on;
    $$('.modal-actions button', dlg).forEach((b) => { b.disabled = on; });
  }

  async function upload(file, name) {
    if (busy) return;
    if (file.size > maxBytes) { showMsg(`File terlalu besar (maks ${dlg.dataset.maxMb} MB).`); return; }
    const fd = new FormData();
    fd.append('foto', file, name);
    setBusy(true);
    try {
      const data = await api(patientUrl('uploadUrl'), { method: 'POST', data: fd });
      showPhoto(data.foto_url);
      dlg.close();
      toast('Foto pasien tersimpan.');
    } catch (err) {
      showMsg(err.message);
    } finally {
      setBusy(false);
    }
  }

  deleteBtn?.addEventListener('click', async () => {
    if (busy || !window.confirm('Hapus foto pasien ini?')) return;
    setBusy(true);
    try {
      await api(patientUrl('deleteUrl'), { method: 'POST' });
      showPhoto('');
      setMode('idle');
      toast('Foto pasien dihapus.');
    } catch (err) {
      showMsg(err.message);
    } finally {
      setBusy(false);
    }
  });
})();
