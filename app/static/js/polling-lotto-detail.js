/* polling-lotto-detail.js
   Nella pagina di dettaglio di un lotto:
   1. Aggiorna avanzamento via polling finché l'elaborazione automatica
      in corso non si conclude, poi ricarica la pagina.
   2. Gestisce l'apertura del modal di confronto/correzione OCR.
   3. Gestisce il filtro client-side per gravità difformità.
*/
(function () {

  // ─── 1. Polling avanzamento ───────────────────────────────────────────────

  // L'id del lotto si legge dall'intestazione della pagina (sempre presente),
  // non dal pannello di avanzamento: quel pannello dipende da
  // elaborazione_attiva, che al primissimo caricamento dopo la creazione
  // del lotto puo' essere ancora None — il background task che crea la
  // riga Elaborazione parte solo DOPO che la risposta HTTP e' stata
  // inviata (cosi' funzionano i BackgroundTasks di FastAPI), quindi la
  // primissima pagina puo' arrivare prima che quella riga esista ancora.
  // Il polling deve partire comunque, per accorgersi quando compare.
  const intestazione = document.querySelector(".main__header[data-lotto-id]");
  const lottoId = intestazione ? intestazione.dataset.lottoId : null;

  if (lottoId) {
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

        // Cercati ad ogni tick (non una volta sola all'avvio): possono non
        // esistere ancora nella primissima pagina, vedi commento sopra.
        const pannello = document.getElementById("pannello-avanzamento-dettaglio");

        if (dati.fase_attiva && !pannello) {
          // Il server dice che un'elaborazione e' partita, ma questa pagina
          // e' stata renderizzata prima che accadesse: ricarica per
          // ottenere il pannello, lo stepper e tutto il resto coerenti
          // con lo stato reale, invece di provare a ricostruirli a mano.
          window.location.reload();
          return;
        }
        if (!pannello) return; // nulla in corso, nulla da aggiornare

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

        if (barra) barra.style.width = `${dati.percentuale}%`;
        if (percentualeEl) percentualeEl.textContent = `${dati.percentuale}%`;
        if (etichetta) etichetta.textContent = dati.etichetta_stato;

        if (badgePausa && formPausa && formRiprendi) {
          const inPausa = dati.richiesta_controllo === "pausa";
          const inAnnullamento = dati.richiesta_controllo === "annulla";
          badgePausa.style.display = inPausa ? "inline-flex" : "none";
          // "annulla" e' terminale: niente pausa/riprendi mentre e' in corso
          // (evita che un click su "pausa" sovrascriva la richiesta di
          // annullamento, vedi metti_in_pausa in lotti.py).
          formPausa.style.display = (inPausa || inAnnullamento) ? "none" : "inline";
          formRiprendi.style.display = (inPausa && !inAnnullamento) ? "inline" : "none";
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

    aggiorna();
    setInterval(aggiorna, 2000);
  }


  // ─── 1b. Conferma "Annulla elaborazione" ──────────────────────────────────
  // Sostituisce il confirm() nativo del browser: il suo pulsante "Annulla"
  // (il Cancel di sistema, tradotto) si scontrava con l'azione "Annulla"
  // dell'app — sembrava dire "sì, annulla" ma voleva dire il contrario
  // ("annulla questo popup, non fare nulla"). Qui i due pulsanti sono
  // scritti per esteso, senza ambiguità.

  const formAnnulla = document.getElementById("form-annulla-elaborazione");
  const modalAnnulla = document.getElementById("modal-conferma-annulla");
  if (formAnnulla && modalAnnulla) {
    const btnConferma = document.getElementById("btn-annulla-conferma");
    const btnAnnulla = document.getElementById("btn-annulla-annulla");

    formAnnulla.addEventListener("submit", (e) => {
      e.preventDefault();
      modalAnnulla.style.display = "flex";
    });
    btnAnnulla.addEventListener("click", () => {
      modalAnnulla.style.display = "none";
    });
    btnConferma.addEventListener("click", () => {
      btnConferma.disabled = true;
      btnConferma.textContent = "Annullamento in corso…";
      formAnnulla.submit();
    });
    modalAnnulla.addEventListener("click", (e) => {
      if (e.target === modalAnnulla) modalAnnulla.style.display = "none";
    });
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


  // ─── 3. Filtro score OCR minimo ────────────────────────────────────────────
  // Mostra solo le prescrizioni con score OCR sotto la soglia scelta (o senza
  // score), cosi' l'operatore puo' concentrarsi su quelle da ricontrollare.

  window.filtraScoreOcr = function (sogliaStr) {
    const righe = document.querySelectorAll("#tabella-qualita tbody tr");
    const soglia = sogliaStr === "" ? null : parseFloat(sogliaStr);
    righe.forEach((riga) => {
      if (soglia === null || isNaN(soglia)) {
        riga.style.display = "";
        return;
      }
      const score = riga.dataset.score === "" ? null : parseFloat(riga.dataset.score);
      riga.style.display = (score === null || score < soglia) ? "" : "none";
    });
  };


  // ─── 4. Modal confronto/correzione OCR ───────────────────────────────────

  const ETICHETTE_CAMPO = {
    cognome_nome_assistito: "Cognome / Nome assistito",
    codice_fiscale: "Codice fiscale",
    codice_esenzione: "Codice esenzione",
    codice_atc: "Codice ATC",
    testo_prescrizione: "Testo prescrizione",
    metodo_estrattivo_olio: "Metodo estrattivo olio",
    forma_farmaceutica: "Forma farmaceutica",
    // data_prescrizione e data_invio omessi di proposito: vengono presi
    // sempre dall'Excel Regione, mai dalla lettura OCR (vedi lo stesso
    // elenco in lotti.py:ETICHETTE_CAMPO_OCR).
    data_etichetta_preparazione: "Data preparazione etichetta",
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
          const isVero = valStr === "true" || val === true;
          html += `<div class="toggle-vero-falso">
            <input type="radio" name="${campo}" id="ocr-${campo}-vero" value="true" ${isVero ? "checked" : ""}>
            <label for="ocr-${campo}-vero">Vero</label>
            <input type="radio" name="${campo}" id="ocr-${campo}-falso" value="false" ${!isVero ? "checked" : ""}>
            <label for="ocr-${campo}-falso">Falso</label>
          </div>`;
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


  // ─── 5. Modal segnalazione "etichetta mancante" ───────────────────────────
  // Stesso principio del modal Annulla sopra: niente confirm() nativo,
  // pulsanti scritti per esteso cosi' l'esito di ciascuna scelta e' chiaro
  // prima di confermare (specialmente "conferma" qui esclude in blocco
  // altre difformita', un effetto che va reso esplicito prima del click).

  const modalEtichetta = document.getElementById("modal-etichetta-mancante");
  if (modalEtichetta) {
    const formEtichetta = document.getElementById("form-etichetta-mancante");
    const titoloEtichetta = document.getElementById("modal-etichetta-mancante-titolo");
    const testoEtichetta = document.getElementById("modal-etichetta-mancante-testo");
    const nextEtichetta = document.getElementById("modal-etichetta-mancante-next");
    const btnConfermaEtichetta = document.getElementById("btn-etichetta-mancante-conferma");
    const btnAnnullaEtichetta = document.getElementById("btn-etichetta-mancante-annulla");

    window.apriModalEtichettaMancante = function (lottoId, prescrizioneId, azione) {
      formEtichetta.action = `/lotti/${lottoId}/prescrizioni/${prescrizioneId}/etichetta-mancante/${azione}`;
      nextEtichetta.value = `${window.location.pathname}?fase=5`;
      if (azione === "conferma") {
        titoloEtichetta.textContent = "Confermare: etichetta assente?";
        testoEtichetta.textContent = "Le difformità 11, 12, 13, 16 e 19 di questa prescrizione, se ancora da gestire, verranno escluse automaticamente: dipendono tutte da dati leggibili solo sull'etichetta.";
        btnConfermaEtichetta.className = "btn btn--primario";
        btnConfermaEtichetta.textContent = "Sì, l'etichetta manca";
      } else {
        titoloEtichetta.textContent = "Escludere la segnalazione?";
        testoEtichetta.textContent = "L'etichetta risulta presente: nessuna difformità verrà toccata, restano tutte da gestire singolarmente come al solito.";
        btnConfermaEtichetta.className = "btn btn--secondario";
        btnConfermaEtichetta.textContent = "Sì, l'etichetta è presente";
      }
      modalEtichetta.style.display = "flex";
    };
    btnAnnullaEtichetta.addEventListener("click", () => {
      modalEtichetta.style.display = "none";
    });
    modalEtichetta.addEventListener("click", (e) => {
      if (e.target === modalEtichetta) modalEtichetta.style.display = "none";
    });
  }

})();
