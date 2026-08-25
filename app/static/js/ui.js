/**
 * ui.js — Livello di presentazione (DOM).
 *
 * Contiene solo funzioni di rendering e i riferimenti agli elementi
 * della pagina: nessuna chiamata di rete, nessuna logica di stato.
 * app.js decide COSA mostrare; ui.js decide COME mostrarlo.
 */

const UI = (function () {
  const $ = (id) => document.getElementById(id);

  const el = {
    dropzone: $('dropzone'),
    fileInput: $('fileInput'),
    btnBrowse: $('btnBrowse'),
    fileList: $('fileList'),
    fileCount: $('fileCount'),
    btnPreprocess: $('btnPreprocess'),
    btnExamine: $('btnExamine'),
    btnReset: $('btnReset'),
    btnDownload: $('btnDownload'),
    progressFill: $('progressFill'),
    progressText: $('progressText'),
    progressPct: $('progressPct'),
    processFileList: $('processFileList'),
    logPanel: $('logPanel'),
    resultsSection: $('results-section'),
    resultsBody: $('resultsBody'),
    resultsMeta: $('resultsMeta'),
    steps: Array.from(document.querySelectorAll('.step')),
    modeBadge: $('modeBadge'),
    batchCode: $('batchCode'),
    batchDate: $('batchDate'),
  };

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }

  function nowStamp() {
    return new Date().toLocaleTimeString('it-IT', { hour12: false });
  }

  function log(msg, level) {
    const line = document.createElement('div');
    line.className = 'log-line' + (level ? ' ' + level : '');
    line.textContent = '[' + nowStamp() + '] ' + msg;
    el.logPanel.appendChild(line);
    el.logPanel.scrollTop = el.logPanel.scrollHeight;
  }

  function setBatchInfo(code, date) {
    el.batchCode.textContent = code;
    el.batchDate.textContent = date;
  }

  function setModeBadge(isLive) {
    el.modeBadge.textContent = isLive ? 'Modalità live' : 'Modalità demo';
    el.modeBadge.classList.toggle('live', isLive);
  }

  function renderFileList(files, stage) {
    if (stage === 'upload') {
      el.fileCount.textContent = files.length
        ? files.length + ' file pronti per il preprocessing.'
        : 'Nessun file caricato.';
      el.fileList.innerHTML = '';
      files.forEach((f) => {
        const li = document.createElement('li');
        li.className = 'file-row';
        li.innerHTML =
          '<span class="fname">' + escapeHtml(f.name) + '</span>' +
          '<span class="fsize">' + formatSize(f.size) + '</span>' +
          '<button type="button" class="file-remove" data-id="' + f.id + '" aria-label="Rimuovi ' + escapeHtml(f.name) + '">×</button>';
        el.fileList.appendChild(li);
      });
    } else {
      el.fileList.innerHTML = '';
    }
  }

  function renderProcessList(files, stage) {
    el.processFileList.innerHTML = '';
    if (stage === 'upload') return;
    files.forEach((f) => {
      const li = document.createElement('li');
      li.className = 'process-row';
      let badge;
      if (f.status === 'pending') badge = '<span class="status-badge status-pending">in coda</span>';
      else if (f.status === 'error') badge = '<span class="status-badge status-error">errore</span>';
      else if (f.status === 'done') badge = '<span class="status-badge status-done">completato</span>';
      else badge = '<span class="status-badge status-progress"><span class="spinner"></span>' + escapeHtml(f.substep || 'in corso') + '</span>';
      li.innerHTML = '<span class="fname">' + escapeHtml(f.name) + '</span>' + badge;
      el.processFileList.appendChild(li);
    });
  }

  function updateStepper(stage, errorAt) {
    const order = ['upload', 'preprocessing', 'examining', 'done'];
    const currentIndex = order.indexOf(stage === 'preprocessed' ? 'preprocessing' : stage);
    el.steps.forEach((stepEl, i) => {
      stepEl.classList.remove('active', 'done', 'error');
      if (errorAt === i) {
        stepEl.classList.add('error');
      } else if (i < currentIndex || (i === currentIndex && stage === 'preprocessed')) {
        stepEl.classList.add('done');
      } else if (i === currentIndex) {
        stepEl.classList.add('active');
      }
    });
    if (stage === 'done') {
      el.steps[3].classList.remove('active');
      el.steps[3].classList.add('done');
    }
  }

  function updateButtons(stage, hasFiles) {
    el.btnPreprocess.disabled = !(stage === 'upload' && hasFiles);
    el.btnExamine.disabled = stage !== 'preprocessed';
  }

  function setProgress(done, total, label) {
    const pct = total ? Math.round((done / total) * 100) : 0;
    el.progressFill.style.width = pct + '%';
    el.progressPct.textContent = pct + '%';
    el.progressText.textContent = label;
  }

  function showResults(files, isLive) {
    el.resultsSection.hidden = false;
    el.resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

    const done = files.filter((f) => f.status === 'done' && f.result);
    const errored = files.filter((f) => f.status === 'error');
    el.resultsMeta.textContent = done.length + ' prescrizioni analizzate' +
      (errored.length ? ', ' + errored.length + ' non elaborate' : '') +
      (isLive ? '.' : ' (dati di esempio — modalità demo).');

    el.resultsBody.innerHTML = '';
    if (!done.length) {
      const tr = document.createElement('tr');
      tr.innerHTML = '<td colspan="8" class="empty-note">Nessun risultato disponibile.</td>';
      el.resultsBody.appendChild(tr);
      el.btnDownload.disabled = true;
      return;
    }
    el.btnDownload.disabled = false;

    done.forEach((f) => {
      const r = f.result;
      const statoClass = r.stato === 'Valida' ? 'stato-valida' : r.stato === 'Da verificare' ? 'stato-verifica' : 'stato-incompleta';
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td>' + escapeHtml(f.name) + '</td>' +
        '<td>' + escapeHtml(r.paziente) + '</td>' +
        '<td>' + escapeHtml(r.medico) + '</td>' +
        '<td>' + escapeHtml(r.farmaci) + '</td>' +
        '<td>' + escapeHtml(r.dosaggio) + '</td>' +
        '<td>' + escapeHtml(r.data_prescrizione) + '</td>' +
        '<td><span class="stato-pill ' + statoClass + '">' + escapeHtml(r.stato) + '</span></td>' +
        '<td>' + escapeHtml(r.note) + '</td>';
      el.resultsBody.appendChild(tr);
    });
  }

  function resetView() {
    el.resultsSection.hidden = true;
    el.resultsBody.innerHTML = '';
    setProgress(0, 0, 'In attesa');
  }

  return {
    el, log, setBatchInfo, setModeBadge, renderFileList, renderProcessList,
    updateStepper, updateButtons, setProgress, showResults, resetView, escapeHtml,
  };
})();
