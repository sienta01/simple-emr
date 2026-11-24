/* Patient card print page: paper size (remembered per PC), long names / addresses shrunk to fit,
   and the print dialog opened right away when the page was opened from a "Cetak kartu" button. */
(() => {
  'use strict';

  const PAPER_KEY = 'emr-kartu-kertas';
  const PAGES = {
    kartu: '@page { size: 85.6mm 53.98mm; margin: 0; }',
    a4: '@page { size: A4 portrait; margin: 12mm; }',
  };
  const root = document.documentElement;
  const pageStyle = document.getElementById('page-size');
  const radios = Array.from(document.querySelectorAll('input[name="kertas"]'));

  function setPaper(value, persist) {
    const paper = PAGES[value] ? value : 'kartu';
    root.dataset.kertas = paper;
    pageStyle.textContent = PAGES[paper];
    radios.forEach((r) => { r.checked = r.value === paper; });
    if (persist) {
      try { localStorage.setItem(PAPER_KEY, paper); } catch { /* private mode: this page only */ }
    }
  }

  let saved = null;
  try { saved = localStorage.getItem(PAPER_KEY); } catch { /* private mode */ }
  setPaper(saved, false);
  radios.forEach((r) => r.addEventListener('change', () => setPaper(r.value, true)));

  // data-fit="1": one line; "2": up to two lines; "1,2": one line if it fits at 88% size or more, else two.
  // The text shrinks step by step until it fits (to 80% at the last step); what still does not fit ends in "…".
  function fit(el) {
    el.classList.remove('is-clamped', 'is-multiline');
    el.style.fontSize = '';
    const base = parseFloat(getComputedStyle(el).fontSize);
    const steps = (el.dataset.fit || '1').split(',').map(Number);
    let overflows = () => false;
    steps.forEach((lines, i) => {
      if (i > 0 && !overflows()) return;
      el.classList.toggle('is-multiline', lines > 1);
      el.style.setProperty('--lines', lines);
      overflows = () => (lines > 1
        ? el.scrollHeight > lines * parseFloat(getComputedStyle(el).lineHeight) + 0.5
        : el.scrollWidth > el.clientWidth + 0.5);
      const min = base * (i === steps.length - 1 ? 0.8 : 0.88);
      let size = base;
      el.style.fontSize = '';
      while (overflows() && size > min) {
        size -= 0.25;
        el.style.fontSize = `${size}px`;
      }
    });
    el.classList.toggle('is-clamped', overflows());
  }

  const card = document.querySelector('.kartu');
  const rows = document.querySelector('.k-data');
  const note = document.querySelector('.k-note');

  // True when wrapped lines push the "Kartu ini harap dibawa…" box into the bottom margin or off the card.
  function cardOverflows() {
    const limit = card.clientHeight - parseFloat(getComputedStyle(card).paddingBottom);
    return note.offsetTop + note.offsetHeight > limit + 0.5;
  }

  function fitAll() {
    rows.style.fontSize = '';
    document.querySelectorAll('.kartu [data-fit]').forEach(fit);
    // A two-line name and a two-line address together: the data rows shrink together (to 85% at most).
    const base = parseFloat(getComputedStyle(rows).fontSize);
    let size = base;
    while (cardOverflows() && size > base * 0.85) {
      size -= 0.25;
      rows.style.fontSize = `${size}px`;
      rows.querySelectorAll('[data-fit]').forEach(fit);
    }
  }
  fitAll();

  const print = () => { fitAll(); window.print(); };
  document.querySelector('[data-print]')?.addEventListener('click', print);
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'p') { e.preventDefault(); print(); }
  });

  if (document.body.hasAttribute('data-autoprint')) {
    // Fonts first, or the print could be laid out with the fallback font.
    (document.fonts?.ready || Promise.resolve()).then(() => setTimeout(print, 120));
  }
})();
