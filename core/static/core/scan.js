/* Reads QR codes from the webcam into a <video>. Uses the browser's own BarcodeDetector where there is one
   (Chrome on Android and macOS), otherwise the bundled jsQR (Windows and Linux desktops), loaded on first use.
   The registration dialog (pendaftaran.js) uses it for the QR on the patient card. */
(() => {
  'use strict';
  const { escapeHtml, cameraError } = window.EMR;

  const SCAN_EVERY_MS = 120;
  const MAX_SIDE = 1280;  // frames are scaled down to this before jsQR reads them

  let jsqrLoading = null;
  function loadJsQR(url) {
    if (window.jsQR) return Promise.resolve(window.jsQR);
    if (!jsqrLoading) {
      jsqrLoading = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = url;
        script.onload = () => (window.jsQR ? resolve(window.jsQR) : reject(new Error('jsQR')));
        script.onerror = () => { jsqrLoading = null; reject(new Error('jsQR')); };
        document.head.appendChild(script);
      });
    }
    return jsqrLoading;
  }

  async function nativeDetector() {
    if (!('BarcodeDetector' in window)) return null;
    try {
      if (!(await window.BarcodeDetector.getSupportedFormats()).includes('qr_code')) return null;
      const detector = new window.BarcodeDetector({ formats: ['qr_code'] });
      return async (video) => (await detector.detect(video)).map((code) => code.rawValue);
    } catch {
      return null;
    }
  }

  async function jsqrDetector(url) {
    const jsQR = await loadJsQR(url);
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    return async (video) => {
      const scale = Math.min(1, MAX_SIDE / Math.max(video.videoWidth, video.videoHeight));
      const w = Math.round(video.videoWidth * scale);
      const h = Math.round(video.videoHeight * scale);
      if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
      ctx.drawImage(video, 0, 0, w, h);
      const code = jsQR(ctx.getImageData(0, 0, w, h).data, w, h, { inversionAttempts: 'dontInvert' });
      return code?.data ? [code.data] : [];
    };
  }

  class QrReader {
    /* onCode(text): called for every code seen; call pause() while handling it and resume() to go on.
       onState(state, message): 'starting' | 'scanning' | 'error' (message says why). */
    constructor({ video, cameraSelect, jsqrUrl, onCode, onState, storageKey = 'emr-scan-camera' }) {
      Object.assign(this, { video, cameraSelect, jsqrUrl, onCode, onState, storageKey });
      this.stream = null;
      this.detect = null;
      this.timer = null;
      this.paused = false;
      this.run = 0;  // every start() / stop() bumps it, so a late camera answer from an older start is dropped
      cameraSelect?.addEventListener('change', () => {
        try { localStorage.setItem(storageKey, cameraSelect.value); } catch { /* private mode */ }
        this.start(cameraSelect.value);
      });
    }

    async start(deviceId) {
      this.stop();
      const run = this.run;
      if (!navigator.mediaDevices?.getUserMedia) {
        // Browsers only allow the camera on HTTPS pages (and localhost).
        this.onState('error', 'Kamera tidak bisa dibuka karena aplikasi ini dibuka lewat HTTP: browser hanya '
          + 'mengizinkan kamera di alamat HTTPS (atau localhost). Pakai alat scan barcode USB, atau cari pasien '
          + 'dengan No. RM yang tertulis di kartu.');
        return;
      }
      this.onState('starting');
      try {
        this.detect ||= (await nativeDetector()) || (await jsqrDetector(this.jsqrUrl));
      } catch {
        if (run === this.run) this.onState('error', 'Pembaca QR gagal dimuat. Muat ulang halaman, lalu coba lagi.');
        return;
      }

      let remembered = null;
      try { remembered = localStorage.getItem(this.storageKey) || localStorage.getItem('emr-camera'); } catch { /* private mode */ }
      const video = { width: { ideal: 1280 }, height: { ideal: 720 } };
      if (deviceId) video.deviceId = { exact: deviceId };
      else if (remembered) video.deviceId = { ideal: remembered };
      else video.facingMode = { ideal: 'environment' };  // phones and tablets: the back camera
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
      } catch (err) {
        if (run === this.run) this.onState('error', cameraError(err));
        return;
      }
      if (run !== this.run) {  // closed or restarted while the browser asked for permission
        stream.getTracks().forEach((track) => track.stop());
        return;
      }

      this.stream = stream;
      const settings = stream.getVideoTracks()[0]?.getSettings() || {};
      // A webcam that faces the person is shown mirrored, so moving the card left moves it left on screen.
      this.video.classList.toggle('is-mirrored', settings.facingMode !== 'environment');
      this.video.srcObject = stream;
      await this.video.play().catch(() => { /* autoplay attribute takes over */ });
      await this.listCameras(settings.deviceId);
      if (run !== this.run) return;
      this.paused = false;
      this.onState('scanning');
      this.loop(run);
    }

    loop(run) {
      const tick = async () => {
        if (run !== this.run) return;
        if (!this.paused && this.video.readyState >= 2 && this.video.videoWidth) {
          try {
            const [text] = await this.detect(this.video);
            if (text && run === this.run && !this.paused) this.onCode(text);
          } catch { /* an unreadable frame: try the next one */ }
        }
        if (run === this.run) this.timer = setTimeout(tick, SCAN_EVERY_MS);
      };
      tick();
    }

    pause() { this.paused = true; }

    resume() { this.paused = false; }

    stop() {
      this.run += 1;
      clearTimeout(this.timer);
      this.stream?.getTracks().forEach((track) => track.stop());
      this.stream = null;
      this.video.srcObject = null;
    }

    // Device names are only readable once camera access is granted, so the list is filled after starting.
    async listCameras(activeId) {
      if (!this.cameraSelect) return;
      let cameras = [];
      try {
        cameras = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'videoinput');
      } catch { /* keep the current camera only */ }
      this.cameraSelect.innerHTML = cameras.map((d, i) => (
        `<option value="${escapeHtml(d.deviceId)}">${escapeHtml(d.label || `Kamera ${i + 1}`)}</option>`
      )).join('');
      if (cameras.some((d) => d.deviceId === activeId)) this.cameraSelect.value = activeId;
      this.cameraSelect.hidden = cameras.length < 2;
    }
  }

  window.EMR.QrReader = QrReader;
})();
