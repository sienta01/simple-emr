/* Ancillary results (Penunjang): upload, page order, notes, edit/rotate, delete, viewer, phone QR. */
(() => {
  'use strict';
  const { $, $$, api, toast, escapeHtml, csrfToken } = window.EMR;

  class Penunjang {
    constructor(root) {
      this.root = root;
      this.compact = root.hasAttribute('data-compact');
      this.maxBytes = (+root.dataset.maxMb || 25) * 1024 * 1024;
      this.items = [];
      this.filter = 'semua';
      this.grid = $('[data-grid]', root);
      this.empty = $('[data-empty]', root);
      this.queue = $('[data-queue]', root);
      this.uploading = 0;
      this.bindUpload();
      this.bindGrid();
      this.bindFilter();
      this.bindEdit();
      this.bindViewer();
      this.bindQr();
      this.load();
      document.addEventListener('visibilitychange', () => { if (!document.hidden) this.load(); });
    }

    url(kind, id) {
      return this.root.dataset[`${kind}Url`].replace('/0/', `/${id}/`);
    }

    async load() {
      if (this.uploading || this.dragId) return;
      try {
        const data = await api(this.root.dataset.listUrl);
        this.items = data.items;
        this.render();
      } catch (err) {
        toast(err.message, 'error');
      }
    }

    renumber() {
      const total = this.items.length;
      this.items.forEach((item, i) => { item.page = total - i; });
    }

    visible() {
      return this.filter === 'semua' ? this.items : this.items.filter((i) => i.kategori === this.filter);
    }

    render() {
      const items = this.visible();
      const canMove = !this.compact && this.filter === 'semua' && this.items.length > 1;
      this.grid.innerHTML = items.map((item, idx) => this.card(item, canMove, idx, items.length)).join('');
      this.empty.hidden = items.length > 0;
      this.empty.textContent = this.items.length ? 'Tidak ada halaman pada kategori ini.' : 'Belum ada hasil penunjang untuk pasien ini.';
      $$('[data-penunjang-count]').forEach((el) => { el.textContent = this.items.length; });
      $$('[data-kat]', this.root).forEach((btn) => {
        btn.dataset.label ??= btn.textContent.trim();
        const n = btn.dataset.kat === 'semua' ? this.items.length : this.items.filter((i) => i.kategori === btn.dataset.kat).length;
        btn.innerHTML = `${escapeHtml(btn.dataset.label)} <span class="n">${n}</span>`;
      });
    }

    card(item, canMove, idx, count) {
      const thumb = item.thumb_url
        ? `<img src="${escapeHtml(item.thumb_url)}" alt="Halaman ${item.page}" loading="lazy" draggable="false">`
        : '<div class="pj-pdf"><svg class="icon"><use href="#i-file"/></svg><span>PDF</span></div>';
      const move = canMove ? `
        <div class="pj-move">
          <button type="button" data-move="-1" title="Pindah ke kiri" aria-label="Pindah ke kiri"${idx === 0 ? ' disabled' : ''}><svg class="icon"><use href="#i-arrow-left"/></svg></button>
          <button type="button" data-move="1" title="Pindah ke kanan" aria-label="Pindah ke kanan"${idx === count - 1 ? ' disabled' : ''}><svg class="icon"><use href="#i-arrow-right"/></svg></button>
          <span class="pj-grip" data-grip title="Seret untuk memindahkan"><svg class="icon"><use href="#i-grip"/></svg></span>
        </div>` : '';
      return `
        <article class="pj-card" data-id="${item.id}">
          <div class="pj-thumb" data-view title="Klik untuk melihat">
            ${thumb}
            <span class="pj-page" title="Halaman ${item.page}">${item.page}</span>
            ${item.is_pdf && item.thumb_url ? '<span class="pj-type">PDF</span>' : ''}
            ${move}
          </div>
          <div class="pj-body">
            <div class="pj-meta"><span class="badge badge-kat-${escapeHtml(item.kategori)}">${escapeHtml(item.kategori_label)}</span><span title="${escapeHtml(item.uploaded_by)}">${escapeHtml(item.uploaded_at)}</span></div>
            <input class="input pj-note" data-note value="${escapeHtml(item.note)}" placeholder="Tambah catatan…" maxlength="500" aria-label="Catatan halaman ${item.page}">
            <div class="pj-foot">
              <span>${escapeHtml(item.size)}</span>
              <span class="pj-actions"><button type="button" data-edit>Ubah</button><button type="button" class="danger" data-delete>Hapus</button></span>
            </div>
          </div>
        </article>`;
    }

    find(id) { return this.items.find((i) => i.id === +id); }

    // --- Upload -------------------------------------------------------------
    bindUpload() {
      const zone = $('[data-dropzone]', this.root);
      $$('[data-pick]', this.root).forEach((btn) => btn.addEventListener('click', () => {
        $(`[data-input="${btn.dataset.pick}"]`, this.root).click();
      }));
      $$('[data-input]', this.root).forEach((input) => input.addEventListener('change', () => {
        this.uploadAll(Array.from(input.files));
        input.value = '';
      }));
      const hasFiles = (e) => Array.from(e.dataTransfer?.types || []).includes('Files');
      zone.addEventListener('dragover', (e) => { if (hasFiles(e)) { e.preventDefault(); zone.classList.add('dragover'); } });
      zone.addEventListener('dragleave', (e) => { if (!zone.contains(e.relatedTarget)) zone.classList.remove('dragover'); });
      zone.addEventListener('drop', (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        zone.classList.remove('dragover');
        this.uploadAll(Array.from(e.dataTransfer.files));
      });
    }

    kategori() {
      return $('input[name="upload-kategori"]:checked', this.root)?.value || 'lainnya';
    }

    async uploadAll(files) {
      if (!files.length) return;
      const kategori = this.kategori();
      this.uploading += 1;
      let ok = 0;
      for (const file of files) {
        if (await this.uploadOne(file, kategori)) ok += 1;
      }
      this.uploading -= 1;
      if (ok) toast(`${ok} halaman terunggah.`);
    }

    uploadOne(file, kategori) {
      const row = document.createElement('div');
      row.className = 'uq-item';
      row.innerHTML = `<span class="uq-name">${escapeHtml(file.name || 'foto')}</span><span class="uq-state">Menunggu…</span><div class="uq-bar"><span></span></div>`;
      this.queue.prepend(row);
      const state = $('.uq-state', row);
      const bar = $('.uq-bar span', row);
      const finish = (cls, text) => {
        row.classList.add(cls);
        state.textContent = text;
        if (cls === 'done') setTimeout(() => row.remove(), 2500);
        else row.addEventListener('click', () => row.remove(), { once: true });
      };

      if (file.size > this.maxBytes) {
        finish('error', `Terlalu besar (maks ${this.root.dataset.maxMb} MB)`);
        return Promise.resolve(false);
      }

      return new Promise((resolve) => {
        const fd = new FormData();
        fd.append('file', file);
        fd.append('kategori', kategori);
        if (this.root.dataset.visit) fd.append('visit', this.root.dataset.visit);
        const xhr = new XMLHttpRequest();
        xhr.open('POST', this.root.dataset.uploadUrl);
        xhr.setRequestHeader('X-CSRFToken', csrfToken());
        xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
        xhr.upload.addEventListener('progress', (e) => {
          if (!e.lengthComputable) return;
          const pct = Math.round((e.loaded / e.total) * 100);
          bar.style.width = `${pct}%`;
          state.textContent = pct < 100 ? `${pct}%` : 'Memproses…';
        });
        xhr.addEventListener('load', () => {
          let body = null;
          try { body = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
          if (xhr.status >= 200 && xhr.status < 300 && body?.item) {
            bar.style.width = '100%';
            finish('done', 'Selesai');
            this.items.unshift(body.item);
            this.renumber();
            this.render();
            resolve(true);
          } else {
            const msg = body?.error || (xhr.status === 403 ? 'Tidak ada akses' : `Gagal (${xhr.status})`);
            finish('error', `${msg} — ketuk untuk menutup`);
            resolve(false);
          }
        });
        xhr.addEventListener('error', () => { finish('error', 'Koneksi terputus'); resolve(false); });
        state.textContent = 'Mengunggah…';
        xhr.send(fd);
      });
    }

    // --- Grid: view, notes, move, delete ------------------------------------
    bindGrid() {
      this.grid.addEventListener('click', (e) => {
        const card = e.target.closest('.pj-card');
        if (!card) return;
        const item = this.find(card.dataset.id);
        const moveBtn = e.target.closest('[data-move]');
        if (moveBtn) { this.move(item, +moveBtn.dataset.move); return; }
        if (e.target.closest('[data-grip]')) return;
        if (e.target.closest('[data-edit]')) { this.openEdit(item); return; }
        if (e.target.closest('[data-delete]')) { this.remove(item); return; }
        if (e.target.closest('[data-view]')) this.view(item);
      });

      this.grid.addEventListener('change', async (e) => {
        const input = e.target.closest('[data-note]');
        if (!input) return;
        const item = this.find(input.closest('.pj-card').dataset.id);
        try {
          await api(this.url('update', item.id), { method: 'POST', data: { note: input.value.trim() } });
          item.note = input.value.trim();
          input.classList.add('saved');
          setTimeout(() => input.classList.remove('saved'), 1500);
        } catch (err) { toast(err.message, 'error'); }
      });
      this.grid.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && e.target.matches('[data-note]')) { e.preventDefault(); e.target.blur(); }
      });

      // Drag to reorder, started from the grip only so notes stay selectable.
      this.grid.addEventListener('pointerdown', (e) => {
        const grip = e.target.closest('[data-grip]');
        if (grip) grip.closest('.pj-card').draggable = true;
      });
      document.addEventListener('pointerup', () => {
        if (!this.dragId) $$('.pj-card[draggable="true"]', this.grid).forEach((c) => { c.draggable = false; });
      });
      this.grid.addEventListener('dragstart', (e) => {
        const card = e.target.closest?.('.pj-card');
        if (!card || !card.draggable) return;
        this.dragId = +card.dataset.id;
        card.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', card.dataset.id);
      });
      this.grid.addEventListener('dragover', (e) => {
        if (!this.dragId) return;
        e.preventDefault();
        const over = e.target.closest('.pj-card');
        const dragging = $('.pj-card.dragging', this.grid);
        if (!over || over === dragging) return;
        const rect = over.getBoundingClientRect();
        const before = e.clientX < rect.left + rect.width / 2;
        this.grid.insertBefore(dragging, before ? over : over.nextSibling);
      });
      this.grid.addEventListener('drop', (e) => { if (this.dragId) e.preventDefault(); });
      this.grid.addEventListener('dragend', () => {
        if (!this.dragId) return;
        const order = $$('.pj-card', this.grid).map((c) => +c.dataset.id);
        this.dragId = null;
        this.saveOrder(order);
      });
    }

    move(item, delta) {
      const order = this.items.map((i) => i.id);
      const from = order.indexOf(item.id);
      const to = from + delta;
      if (to < 0 || to >= order.length) return;
      order.splice(to, 0, order.splice(from, 1)[0]);
      this.saveOrder(order);
    }

    async saveOrder(order) {
      const before = this.items.map((i) => i.id).join(',');
      this.items = order.map((id) => this.find(id)).filter(Boolean);
      this.renumber();
      this.render();
      if (order.join(',') === before) return;
      try {
        await api(this.root.dataset.reorderUrl, { method: 'POST', json: { order } });
      } catch (err) {
        toast(err.message, 'error');
        this.load();
      }
    }

    async remove(item) {
      if (!window.confirm(`Hapus halaman ${item.page} (${item.kategori_label})?`)) return;
      try {
        await api(this.url('delete', item.id), { method: 'POST' });
        this.items = this.items.filter((i) => i.id !== item.id);
        this.renumber();
        this.render();
        toast(`Halaman ${item.page} dihapus.`);
      } catch (err) { toast(err.message, 'error'); }
    }

    // --- Filter -------------------------------------------------------------
    bindFilter() {
      $('[data-filter]', this.root).addEventListener('click', (e) => {
        const btn = e.target.closest('[data-kat]');
        if (!btn) return;
        this.filter = btn.dataset.kat;
        $$('[data-kat]', this.root).forEach((b) => b.classList.toggle('active', b === btn));
        this.render();
      });
    }

    // --- Edit (rotate, category, note) --------------------------------------
    bindEdit() {
      const dlg = $('[data-edit-dialog]', this.root);
      this.edit = { dlg, item: null, rotate: 0 };
      $$('[data-rotate]', dlg).forEach((btn) => btn.addEventListener('click', () => {
        this.edit.rotate = (((this.edit.rotate + +btn.dataset.rotate) % 360) + 360) % 360;
        const img = $('[data-edit-preview] img', dlg);
        if (img) img.style.transform = `rotate(${this.edit.rotate}deg)`;
      }));
      $('[data-edit-form]', dlg).addEventListener('submit', async (e) => {
        e.preventDefault();
        const { item } = this.edit;
        const rotate = { 0: 0, 90: 90, 180: 180, 270: -90 }[this.edit.rotate];
        const data = { kategori: $('[data-edit-kategori]', dlg).value, note: $('[data-edit-note]', dlg).value.trim() };
        if (rotate) data.rotate = rotate;
        try {
          const res = await api(this.url('update', item.id), { method: 'POST', data });
          Object.assign(item, res.item, { page: item.page });
          this.render();
          dlg.close();
          toast('Halaman diperbarui.');
        } catch (err) { toast(err.message, 'error'); }
      });
    }

    openEdit(item) {
      const { dlg } = this.edit;
      this.edit.item = item;
      this.edit.rotate = 0;
      $('[data-edit-page]', dlg).textContent = item.page;
      $('[data-edit-preview]', dlg).innerHTML = item.thumb_url
        ? `<img src="${escapeHtml(item.thumb_url)}" alt="">`
        : '<div class="pj-pdf"><svg class="icon"><use href="#i-file"/></svg><span>PDF</span></div>';
      $('[data-rotate-row]', dlg).hidden = item.is_pdf;
      $('[data-edit-kategori]', dlg).value = item.kategori;
      $('[data-edit-note]', dlg).value = item.note;
      dlg.showModal();
    }

    // --- Viewer -------------------------------------------------------------
    bindViewer() {
      const dlg = $('[data-viewer]', this.root);
      const stage = $('[data-viewer-stage]', dlg);
      this.viewer = { dlg, stage, img: $('[data-viewer-img]', dlg), index: 0, list: [] };
      $('[data-viewer-close]', dlg).addEventListener('click', () => dlg.close());
      $$('[data-viewer-step]', dlg).forEach((btn) => btn.addEventListener('click', () => this.step(+btn.dataset.viewerStep)));
      this.viewer.img.addEventListener('click', () => stage.classList.toggle('zoomed'));
      dlg.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowLeft') this.step(-1);
        if (e.key === 'ArrowRight') this.step(1);
      });
    }

    view(item) {
      if (item.is_pdf) { window.open(item.file_url, '_blank', 'noopener'); return; }
      this.viewer.list = this.visible().filter((i) => !i.is_pdf);
      this.viewer.index = this.viewer.list.findIndex((i) => i.id === item.id);
      this.showViewer();
      this.viewer.dlg.showModal();
    }

    step(delta) {
      const next = this.viewer.index + delta;
      if (next < 0 || next >= this.viewer.list.length) return;
      this.viewer.index = next;
      this.showViewer();
    }

    showViewer() {
      const { dlg, img, stage, list, index } = this.viewer;
      const item = list[index];
      stage.classList.remove('zoomed');
      img.src = item.file_url;
      $('[data-viewer-open]', dlg).href = item.file_url;
      $('[data-viewer-caption]', dlg).textContent = `Halaman ${item.page} · ${item.kategori_label} · ${item.uploaded_at}${item.note ? ` · ${item.note}` : ''}`;
      $('[data-viewer-step="-1"]', dlg).disabled = index === 0;
      $('[data-viewer-step="1"]', dlg).disabled = index === list.length - 1;
    }

    // --- Phone upload QR ----------------------------------------------------
    bindQr() {
      const btn = $('[data-show-qr]', this.root);
      const dlg = $('[data-qr-dialog]', this.root);
      if (!btn || !dlg) return;
      let timer = null;
      btn.addEventListener('click', () => {
        const img = $('img[data-qr-src]', dlg);
        if (!img.src) img.src = img.dataset.qrSrc;
        dlg.showModal();
        timer = setInterval(() => this.load(), 4000);  // show photos as they arrive from the phone
      });
      dlg.addEventListener('close', () => { clearInterval(timer); this.load(); });
    }
  }

  $$('[data-penunjang]').forEach((root) => new Penunjang(root));
})();
