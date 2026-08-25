/**
 * app.js — Stato applicativo e orchestrazione.
 *
 * Usa Api (comunicazione con il backend, vero o simulato) e UI
 * (rendering) senza mai toccare direttamente fetch() o il DOM:
 * il suo unico compito è coordinare "cosa succede quando".
 */

(function () {
  'use strict';

  const state = { files: [], stage: 'upload' };
  const ACCEPTED_EXT = ['pdf', 'jpg', 'jpeg', 'png', 'tif', 'tiff', 'heic'];
  const MAX_SIZE = 20 * 1024 * 1024;

  function uid() { return 'f' + Math.random().toString(36).slice(2, 9); }

  function generateBatchCode() {
    const d = new Date();
    const ymd = d.getFullYear() + String(d.getMonth() + 1).padStart(2, '0') + String(d.getDate()).padStart(2, '0');
    return 'RX-' + ymd + '-' + Math.random().toString(36).slice(2, 6).toUpperCase();
  }

  /* ============================================================
     FASE 1 — Caricamento
  ============================================================ */
  function addFiles(fileArray) {
    let added = 0;
    fileArray.forEach((file) => {
      const ext = file.name.split('.').pop().toLowerCase();
      if (!ACCEPTED_EXT.includes(ext)) {
        UI.log('File scartato (formato non supportato): ' + file.name, 'error');
        return;
      }
      if (file.size > MAX_SIZE) {
        UI.log('File scartato (supera 20 MB): ' + file.name, 'error');
        return;
      }
      if (state.files.some((f) => f.name === file.name && f.size === file.size)) {
        UI.log('File già presente, ignorato: ' + file.name, 'warn');
        return;
      }
      state.files.push({
        id: uid(), file, name: file.name, size: file.size,
        status: 'pending', substep: '', result: null, error: null,
      });
      added++;
    });
    if (added) UI.log(added + ' file aggiunti al lotto.', 'success');
    refreshView();
  }

  function removeFile(id) {
    state.files = state.files.filter((f) => f.id !== id);
    refreshView();
  }

  function refreshView() {
    UI.renderFileList(state.files, state.stage);
    UI.renderProcessList(state.files, state.stage);
    UI.updateButtons(state.stage, state.files.length > 0);
  }

  /* ============================================================
     FASE 2 — Preprocessing
  ============================================================ */
  async function runPreprocessing() {
    if (!state.files.length) return;
    state.stage = 'preprocessing';
    UI.updateStepper(state.stage);
    UI.el.btnPreprocess.disabled = true;
    UI.log('Preprocessing avviato per ' + state.files.length + ' file.', 'success');

    let okCount = 0;
    for (let i = 0; i < state.files.length; i++) {
      const f = state.files[i];
      f.status = 'progress';
      try {
        await Api.preprocessFile(f.file, (step) => {
          f.substep = step;
          UI.renderProcessList(state.files, state.stage);
        });
        f.status = 'done';
        okCount++;
        UI.log('Preprocessing completato: ' + f.name, 'success');
      } catch (err) {
        f.status = 'error';
        f.error = err.message || 'errore sconosciuto';
        UI.log('Errore preprocessing su ' + f.name + ' — ' + f.error, 'error');
      }
      UI.setProgress(i + 1, state.files.length, 'Preprocessing in corso');
      UI.renderProcessList(state.files, state.stage);
    }

    if (okCount === 0) {
      UI.updateStepper(state.stage, 1);
      state.stage = 'upload';
      UI.log('Nessun file elaborato correttamente. Verifica i file e riprova.', 'error');
      refreshView();
      return;
    }

    state.stage = 'preprocessed';
    UI.updateStepper(state.stage);
    UI.updateButtons(state.stage, state.files.length > 0);
    UI.setProgress(state.files.length, state.files.length, 'Preprocessing completato');
    UI.log('Preprocessing terminato: ' + okCount + '/' + state.files.length + ' file pronti per l\'esame.', 'success');
  }

  /* ============================================================
     FASE 3 — Esame prescrizioni
  ============================================================ */
  async function runExamination() {
    const targets = state.files.filter((f) => f.status === 'done');
    if (!targets.length) return;
    state.stage = 'examining';
    UI.updateStepper(state.stage);
    UI.el.btnExamine.disabled = true;
    UI.log('Esame prescrizioni avviato per ' + targets.length + ' file.', 'success');

    targets.forEach((f) => { f.status = 'progress'; });
    UI.renderProcessList(state.files, state.stage);

    let okCount = 0;
    for (let i = 0; i < targets.length; i++) {
      const f = targets[i];
      try {
        f.result = await Api.examineFile(f.file, (step) => {
          f.substep = step;
          UI.renderProcessList(state.files, state.stage);
        });
        f.status = 'done';
        okCount++;
        UI.log('Esame completato: ' + f.name + ' → ' + f.result.stato, 'success');
      } catch (err) {
        f.status = 'error';
        f.error = err.message || 'errore sconosciuto';
        UI.log('Errore esame su ' + f.name + ' — ' + f.error, 'error');
      }
      UI.setProgress(i + 1, targets.length, 'Esame in corso');
      UI.renderProcessList(state.files, state.stage);
    }

    state.stage = 'done';
    UI.updateStepper(state.stage);
    UI.setProgress(targets.length, targets.length, 'Esame completato');
    UI.log('Esame terminato: ' + okCount + '/' + targets.length + ' prescrizioni analizzate.', 'success');
    UI.showResults(state.files, Api.isLive());
  }

  /* ============================================================
     FASE 4 — Esportazione Excel
  ============================================================ */
  function downloadExcel() {
    const rows = state.files
      .filter((f) => f.status === 'done' && f.result)
      .map((f) => ({
        'Nome File': f.name,
        'Paziente': f.result.paziente,
        'Medico Prescrittore': f.result.medico,
        'Farmaco/i': f.result.farmaci,
        'Dosaggio': f.result.dosaggio,
        'Data Prescrizione': f.result.data_prescrizione,
        'Stato Validazione': f.result.stato,
        'Note': f.result.note,
      }));
    if (!rows.length) { UI.log('Nessun risultato da esportare.', 'warn'); return; }

    const ws = XLSX.utils.json_to_sheet(rows);
    ws['!cols'] = [{ wch: 26 }, { wch: 22 }, { wch: 20 }, { wch: 26 }, { wch: 16 }, { wch: 14 }, { wch: 16 }, { wch: 34 }];
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'Risultati');
    const filename = 'esame-prescrizioni_' + UI.el.batchCode.textContent + '.xlsx';
    XLSX.writeFile(wb, filename);
    UI.log('File Excel esportato: ' + filename, 'success');
  }

  /* ============================================================
     Reset
  ============================================================ */
  function resetAll() {
    state.files = [];
    state.stage = 'upload';
    UI.setBatchInfo(generateBatchCode(), new Date().toLocaleDateString('it-IT'));
    UI.resetView();
    UI.log('— Nuovo lotto avviato —');
    UI.updateStepper(state.stage);
    refreshView();
  }

  /* ============================================================
     Eventi
  ============================================================ */
  function bindEvents() {
    const { dropzone, fileInput, btnBrowse, fileList, btnPreprocess, btnExamine, btnReset, btnDownload } = UI.el;

    btnBrowse.addEventListener('click', () => fileInput.click());
    dropzone.addEventListener('click', () => fileInput.click());
    dropzone.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); }
    });
    dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
    dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
    dropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropzone.classList.remove('dragover');
      if (state.stage !== 'upload') return;
      addFiles(Array.from(e.dataTransfer.files));
    });
    fileInput.addEventListener('change', (e) => {
      addFiles(Array.from(e.target.files));
      fileInput.value = '';
    });
    fileList.addEventListener('click', (e) => {
      const btn = e.target.closest('.file-remove');
      if (btn) removeFile(btn.dataset.id);
    });

    btnPreprocess.addEventListener('click', runPreprocessing);
    btnExamine.addEventListener('click', runExamination);
    btnReset.addEventListener('click', resetAll);
    btnDownload.addEventListener('click', downloadExcel);
  }

  /* ============================================================
     Init
  ============================================================ */
  function init() {
    UI.setBatchInfo(generateBatchCode(), new Date().toLocaleDateString('it-IT'));
    UI.setModeBadge(Api.isLive());
    UI.updateStepper(state.stage);
    UI.updateButtons(state.stage, false);
    bindEvents();
    UI.log('Pronto. Carica i file delle prescrizioni per iniziare.');
  }

  init();
})();
