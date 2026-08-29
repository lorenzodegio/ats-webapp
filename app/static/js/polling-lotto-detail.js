/* polling-lotto-detail.js
   Nella pagina di dettaglio di un lotto:
   1. Aggiorna avanzamento via polling finché l'elaborazione automatica
      in corso non si conclude, poi ricarica la pagina.
   2. Gestisce l'apertura del modal di confronto/correzione OCR.
   3. Gestisce il filtro client-side per gravità difformità.
*/
(function () {

  // ─── 1. Polling avanzamento ───────────────────────────────────────────────

  const pannello = document.getElementById("pannello-avanzamento-dettaglio");
  if (pannello) {
    const lottoId = pannello.dataset.lottoId;
    const barra = document.getElementById("barra-progresso-dettaglio");
    const percentualeEl = document.getElementById("percentuale-dettaglio");
    const etichetta = document.getElementById("etichetta-fase-dettaglio");
    const badgePausa = document.getElementById("badge-pausa");
    const formPausa = document.getElementById("form-pausa");
    const formRiprendi = document.getElementById("form-riprendi");
    const statoMacchina = document.getElementById("stato-macchina");
    const statoTitolo = document.getElementById("stato-macchina-titolo");
    const statoMessaggio = document.getElementById("stato-macchina-messaggio");
    const corpoLog = document.getElementById("tabella-log-dettaglio-corpo");
    const contenitoreLog = document.getElementById("contenitore-log-dettaglio");
    const CLASSE_BADGE_LIVELLO = { error: "badge--errore", warning: "badge--attesa", info: "badge--in-coda" };

    function escapeHtml(testo) {
      const div = document.createElement("div");
      div.textContent = testo;
      return div.innerHTML;
    }

    async function aggiorna() {
      try {
        const risposta = await fetch(`/lotti/${lottoId}/stato`);
        if (!risposta.ok) return;
        const dati = await risposta.json();

        if (barra) barra.style.width = `${dati.percentuale}%`;
        if (percentualeEl) percentualeEl.textContent = `${dati.percentuale}%`;
        if (etichetta) etichetta.textContent = dati.etichetta_stato;

        if (badgePausa && formPausa && formRiprendi) {
          const inPausa = dati.richiesta_controllo === "pausa";
          badgePausa.style.display = inPausa ? "inline-flex" : "none";
          formPausa.style.display = inPausa ? "none" : "inline";
          formRiprendi.style.display = inPausa ? "inline" : "none";
        }

        if (statoMacchina) {
          const inPausa = dati.richiesta_controllo === "pausa";
          statoMacchina.classList.toggle("stato-macchina--pausa", inPausa);
          if (statoTitolo) statoTitolo.textContent = inPausa ? "In pausa" : "Lavorazione automatica";
          if (statoMessaggio) statoMessaggio.textContent = dati.messaggio_operatore || "Elaborazione in corso.";
        }

        if (corpoLog && Array.isArray(dati.log_righe)) {
          corpoLog.innerHTML = dati.log_righe.map((riga) => `
            <tr>
              <td>${riga.ora}</td>
              <td><span class="badge ${CLASSE_BADGE_LIVELLO[riga.livello] || "badge--in-coda"}">${riga.livello}</span></td>
              <td>${escapeHtml(riga.messaggio)}</td>
            </tr>
          `).join("");
          if (contenitoreLog) contenitoreLog.scrollTop = contenitoreLog.scrollHeight;
        }

        if (!dati.fase_attiva) {
          // l'elaborazione automatica si è conclusa (completata o eccezione)
          window.location.reload();
        }
      } catch (err) {
        console.error("Polling dettaglio lotto fallito:", err);
      }
    }

    setInterval(aggiorna, 2000);
  }


  // ─── 2. Filtro gravità difformità ─────────────────────────────────────────

  window.filtraDifformita = function (gravita) {
    const righe = document.querySelectorAll("#tabella-difformita tbody tr");
    righe.forEach((riga) => {
      if (!gravita || riga.dataset.gravita === gravita) {
        riga.style.display = "";
      } else {
        riga.style.display = "none";
      }
    });
  };


  // ─── 3. Modal confronto/correzione OCR ───────────────────────────────────

  const ETICHETTE_CAMPO = {
    cognome_nome_assistito: "Cognome / Nome assistito",
    codice_fiscale: "Codice fiscale",
    codice_esenzione: "Codice esenzione",
    codice_atc: "Codice ATC",
    testo_prescrizione: "Testo prescrizione",
    metodo_estrattivo_olio: "Metodo estrattivo olio",
    forma_farmaceutica: "Forma farmaceutica",
    data_prescrizione: "Data prescrizione",
    data_etichetta_preparazione: "Data preparazione etichetta",
    data_invio: "Data invio / emissione",
    etichetta_data_scadenza: "Data scadenza etichetta",
    timbro_medico: "Timbro medico",
    firma_medico: "Firma medico",
    etichetta_nome_cognome_medico: "Nome medico (etichetta)",
    etichetta_nome_cognome_paziente: "Nome paziente (etichetta)",
    etichetta_prezzo_sost: "Prezzo sost. (etichetta)",
    etichetta_prezzo_on: "Prezzo on. (etichetta)",
    etichetta_prezzo_rec: "Prezzo rec. (etichetta)",
    etichetta_prezzo_iva: "IVA (etichetta)",
    etichetta_prezzo_tot: "Totale (etichetta)",
    totale_prescrizione: "Totale prescrizione",
    etichetta_thc: "THC (etichetta)",
    nome_farmacia: "Nome farmacia",
    etichetta_avvertenze: "Avvertenze (etichetta)",
  };

  const CAMPI_BOOLEANI = new Set(["timbro_medico", "firma_medico"]);
  const CAMPI_AREA = new Set(["testo_prescrizione", "etichetta_avvertenze"]);

  let _lottoIdCorrente = null;
  let _prescrizioneIdCorrente = null;

  window.apriModalOcr = async function (lottoId, prescrizioneId, barcode) {
    _lottoIdCorrente = lottoId;
    _prescrizioneIdCorrente = prescrizioneId;

    const modal = document.getElementById("modal-ocr");
    const rawEl = document.getElementById("modal-ocr-raw");
    const campiEl = document.getElementById("modal-ocr-campi");
    const barcodeEl = document.getElementById("modal-ocr-barcode");
    const pdfLink = document.getElementById("modal-ocr-pdf-link");
    const esitoEl = document.getElementById("modal-ocr-esito");

    barcodeEl.textContent = barcode;
    rawEl.textContent = "Caricamento…";
    campiEl.innerHTML = "<p style='color:var(--text-secondary); font-size:13px;'>Caricamento…</p>";
    esitoEl.style.display = "none";
    pdfLink.href = `/lotti/${lottoId}/prescrizioni/${prescrizioneId}/pdf`;
    modal.style.display = "block";
    document.body.style.overflow = "hidden";

    try {
      const resp = await fetch(`/lotti/${lottoId}/prescrizioni/${prescrizioneId}/ocr`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const dati = await resp.json();

      rawEl.textContent = JSON.stringify(dati.raw || {}, null, 2);

      const corretto = dati.corretto || {};
      let html = "";
      for (const [campo, etichetta] of Object.entries(ETICHETTE_CAMPO)) {
        const val = corretto[campo];
        const valStr = val != null ? String(val) : "";
        const label = etichetta;

        html += `<div style="margin-bottom:10px;">
          <label style="display:block; font-size:11px; font-weight:600; color:var(--text-secondary); margin-bottom:3px;">${label}</label>`;

        if (CAMPI_BOOLEANI.has(campo)) {
          const checked = valStr === "true" || val === true ? "checked" : "";
          html += `<input type="checkbox" name="${campo}" id="ocr-${campo}" ${checked}
            style="width:18px; height:18px; cursor:pointer;">`;
        } else if (CAMPI_AREA.has(campo)) {
          html += `<textarea name="${campo}" rows="3"
            style="width:100%; padding:6px 8px; border:1px solid var(--border-color); border-radius:6px; font-size:12px; font-family:var(--font-ui); resize:vertical;">${valStr}</textarea>`;
        } else {
          html += `<input type="text" name="${campo}" value="${valStr.replace(/"/g, '&quot;')}"
            style="width:100%; padding:6px 8px; border:1px solid var(--border-color); border-radius:6px; font-size:12px; font-family:var(--font-ui);">`;
        }
        html += `</div>`;
      }
      campiEl.innerHTML = html;

    } catch (e) {
      rawEl.textContent = `Errore: ${e.message}`;
      campiEl.innerHTML = `<p style='color:#c0392b;'>Impossibile caricare i dati OCR.</p>`;
    }
  };

  window.chiudiModalOcr = function () {
    document.getElementById("modal-ocr").style.display = "none";
    document.body.style.overflow = "";
    _lottoIdCorrente = null;
    _prescrizioneIdCorrente = null;
  };

  // Chiudi modal cliccando fuori dal pannello
  document.getElementById("modal-ocr")?.addEventListener("click", function (e) {
    if (e.target === this) chiudiModalOcr();
  });

  // Submit form OCR
  document.getElementById("form-ocr-correggi")?.addEventListener("submit", async function (e) {
    e.preventDefault();
    if (!_lottoIdCorrente || !_prescrizioneIdCorrente) return;

    const btn = document.getElementById("btn-salva-ocr");
    const esitoEl = document.getElementById("modal-ocr-esito");
    btn.disabled = true;
    btn.textContent = "Salvataggio…";

    const formData = new FormData(this);

    try {
      const resp = await fetch(
        `/lotti/${_lottoIdCorrente}/prescrizioni/${_prescrizioneIdCorrente}/ocr`,
        { method: "POST", body: formData }
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      esitoEl.textContent = "✅ Dati salvati correttamente.";
      esitoEl.style.cssText = "margin-top:12px; font-size:13px; color:#27ae60; display:block;";
      btn.textContent = "Salvato";
      setTimeout(() => chiudiModalOcr(), 1200);
    } catch (err) {
      esitoEl.textContent = `❌ Errore durante il salvataggio: ${err.message}`;
      esitoEl.style.cssText = "margin-top:12px; font-size:13px; color:#c0392b; display:block;";
      btn.disabled = false;
      btn.textContent = "Salva correzioni";
    }
  });

})();
