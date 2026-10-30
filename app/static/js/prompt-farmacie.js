/**
 * Prompt farmacie — disegno rettangoli (area etichetta + campi) su un'
 * anteprima PDF, per Impostazioni -> Prompt farmacie.
 *
 * Le coordinate salvate sono relative all'immagine (0.0-1.0): per questo
 * basta leggere la posizione del mouse rispetto al bounding rect
 * dell'immagine mostrata, senza bisogno delle sue dimensioni naturali in
 * pixel — funzionano identiche indipendentemente da come il browser
 * ridimensiona l'immagine in pagina.
 */
(function () {
  const wrap = document.getElementById("anteprima-wrap");
  const img = document.getElementById("anteprima-img");
  const overlay = document.getElementById("anteprima-overlay");
  const listaCampi = document.getElementById("lista-campi");
  const template = document.getElementById("template-riga-campo");
  const btnAggiungiCampo = document.getElementById("aggiungi-campo");
  const btnDisegnaEtichetta = document.querySelector("[data-disegna-etichetta]");
  const btnCancellaEtichetta = document.querySelector("[data-cancella-etichetta]");
  const statoRettEtichetta = document.getElementById("stato-rett-etichetta");
  const banner = document.getElementById("banner-modalita-disegno");
  const hiddenEtichetta = {
    x1: document.getElementById("rettangolo_etichetta_x1"),
    y1: document.getElementById("rettangolo_etichetta_y1"),
    x2: document.getElementById("rettangolo_etichetta_x2"),
    y2: document.getElementById("rettangolo_etichetta_y2"),
  };

  if (!wrap || !img) return;

  const COLORI = ["#C9895B", "#6B9080", "#2B3B35", "#B03A2E", "#4472C4", "#8E44AD", "#D4A017"];
  let target = null; // "etichetta" oppure la riga <div.riga-campo-profilo> del campo
  let disegnando = false;
  let startX = 0, startY = 0;

  function relCoordsDaEvento(e) {
    const rect = wrap.getBoundingClientRect();
    let x = (e.clientX - rect.left) / rect.width;
    let y = (e.clientY - rect.top) / rect.height;
    return [Math.min(1, Math.max(0, x)), Math.min(1, Math.max(0, y))];
  }

  function hiddenDiRiga(riga) {
    return {
      x1: riga.querySelector('[name="campo_x1"]'),
      y1: riga.querySelector('[name="campo_y1"]'),
      x2: riga.querySelector('[name="campo_x2"]'),
      y2: riga.querySelector('[name="campo_y2"]'),
    };
  }

  function leggiRettangolo(hidden) {
    const x1 = parseFloat(hidden.x1.value), y1 = parseFloat(hidden.y1.value);
    const x2 = parseFloat(hidden.x2.value), y2 = parseFloat(hidden.y2.value);
    if ([x1, y1, x2, y2].some((v) => isNaN(v))) return null;
    return [x1, y1, x2, y2];
  }

  function scriviRettangolo(hidden, x1, y1, x2, y2) {
    hidden.x1.value = x1.toFixed(4);
    hidden.y1.value = y1.toFixed(4);
    hidden.x2.value = x2.toFixed(4);
    hidden.y2.value = y2.toFixed(4);
  }

  function cancellaRettangolo(hidden) {
    hidden.x1.value = "";
    hidden.y1.value = "";
    hidden.x2.value = "";
    hidden.y2.value = "";
  }

  function nomeCampoRiga(riga) {
    const input = riga.querySelector('[name="campo_nome"]');
    return (input && input.value.trim()) || "(campo senza nome)";
  }

  function coloreRiga(riga) {
    const indice = Array.prototype.indexOf.call(listaCampi.children, riga);
    return COLORI[indice % COLORI.length];
  }

  function aggiornaStatoEtichetta() {
    const r = leggiRettangolo(hiddenEtichetta);
    statoRettEtichetta.textContent = r ? "Area disegnata" : "Nessuna area disegnata (ricade sulla fascia generica)";
  }

  function aggiornaStatoRiga(riga) {
    const el = riga.querySelector("[data-stato-rett-campo]");
    const r = leggiRettangolo(hiddenDiRiga(riga));
    el.textContent = r ? "Rettangolo disegnato" : "Nessun rettangolo disegnato";
  }

  function ridisegnaOverlay() {
    overlay.innerHTML = "";

    const rEtichetta = leggiRettangolo(hiddenEtichetta);
    if (rEtichetta) {
      overlay.appendChild(creaBoxOverlay(rEtichetta, "#A4C3B2", "Area etichetta", true));
    }

    Array.prototype.forEach.call(listaCampi.children, (riga) => {
      const r = leggiRettangolo(hiddenDiRiga(riga));
      if (r) {
        overlay.appendChild(creaBoxOverlay(r, coloreRiga(riga), nomeCampoRiga(riga), false));
      }
    });
  }

  function creaBoxOverlay(rettangolo, colore, etichettaTesto, tratteggiato) {
    const [x1, y1, x2, y2] = rettangolo;
    const box = document.createElement("div");
    box.className = "rett-overlay" + (tratteggiato ? " rett-overlay--etichetta" : "");
    box.style.left = (x1 * 100) + "%";
    box.style.top = (y1 * 100) + "%";
    box.style.width = ((x2 - x1) * 100) + "%";
    box.style.height = ((y2 - y1) * 100) + "%";
    box.style.borderColor = colore;
    const label = document.createElement("span");
    label.className = "rett-overlay__etichetta";
    label.style.background = colore;
    label.textContent = etichettaTesto;
    box.appendChild(label);
    return box;
  }

  function mostraBanner(testo) {
    banner.textContent = testo;
    banner.hidden = false;
  }

  function nascondiBanner() {
    banner.hidden = true;
  }

  function evidenziaRiga(riga) {
    Array.prototype.forEach.call(listaCampi.children, (r) => r.classList.remove("riga-campo-profilo--attiva"));
    if (riga && riga !== "etichetta") riga.classList.add("riga-campo-profilo--attiva");
  }

  function avviaDisegno(nuovoTarget) {
    target = nuovoTarget;
    wrap.classList.add("anteprima-etichetta-wrap--disegno");
    evidenziaRiga(nuovoTarget);
    if (nuovoTarget === "etichetta") {
      mostraBanner("Modalita' disegno attiva — trascina sull'immagine per l'area dell'etichetta");
    } else {
      mostraBanner(`Modalita' disegno attiva — trascina sull'immagine per il campo "${nomeCampoRiga(nuovoTarget)}"`);
    }
    wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  wrap.addEventListener("mousedown", (e) => {
    if (!target) return;
    e.preventDefault();
    disegnando = true;
    [startX, startY] = relCoordsDaEvento(e);
  });

  wrap.addEventListener("mousemove", (e) => {
    if (!disegnando) return;
    const [curX, curY] = relCoordsDaEvento(e);
    const x1 = Math.min(startX, curX), x2 = Math.max(startX, curX);
    const y1 = Math.min(startY, curY), y2 = Math.max(startY, curY);
    const hidden = target === "etichetta" ? hiddenEtichetta : hiddenDiRiga(target);
    scriviRettangolo(hidden, x1, y1, x2, y2);
    ridisegnaOverlay();
  });

  window.addEventListener("mouseup", () => {
    if (!disegnando) return;
    disegnando = false;
    wrap.classList.remove("anteprima-etichetta-wrap--disegno");
    nascondiBanner();
    evidenziaRiga(null);
    if (target === "etichetta") {
      aggiornaStatoEtichetta();
    } else if (target) {
      aggiornaStatoRiga(target);
      const inputNome = target.querySelector('[name="campo_nome"]');
      if (inputNome && !inputNome.value.trim()) inputNome.focus();
    }
    target = null;
  });

  btnDisegnaEtichetta.addEventListener("click", () => avviaDisegno("etichetta"));
  btnCancellaEtichetta.addEventListener("click", () => {
    cancellaRettangolo(hiddenEtichetta);
    aggiornaStatoEtichetta();
    ridisegnaOverlay();
  });

  function creaRigaCampo(dati) {
    const frammento = template.content.cloneNode(true);
    const riga = frammento.querySelector(".riga-campo-profilo");

    riga.querySelector('[name="campo_nome"]').value = (dati && dati.nome_campo) || "";
    riga.querySelector('[name="campo_parola_guida"]').value = (dati && dati.parola_guida) || "";

    const hidden = hiddenDiRiga(riga);
    if (dati && dati.x1 !== null && dati.x1 !== undefined) {
      scriviRettangolo(hidden, dati.x1, dati.y1, dati.x2, dati.y2);
    }

    riga.querySelector("[data-disegna-campo]").addEventListener("click", () => avviaDisegno(riga));
    riga.querySelector("[data-cancella-campo]").addEventListener("click", () => {
      cancellaRettangolo(hidden);
      aggiornaStatoRiga(riga);
      ridisegnaOverlay();
    });
    riga.querySelector("[data-rimuovi-campo]").addEventListener("click", () => {
      riga.remove();
      ridisegnaOverlay();
    });
    riga.querySelector('[name="campo_nome"]').addEventListener("input", ridisegnaOverlay);

    listaCampi.appendChild(riga);
    aggiornaStatoRiga(listaCampi.lastElementChild);
    return listaCampi.lastElementChild;
  }

  btnAggiungiCampo.addEventListener("click", () => {
    creaRigaCampo(null);
    ridisegnaOverlay();
  });

  // Precarica i campi gia' salvati (se la farmacia ha gia' un profilo)
  const scriptCampi = document.getElementById("campi-esistenti");
  if (scriptCampi) {
    try {
      const campiEsistenti = JSON.parse(scriptCampi.textContent || "[]");
      campiEsistenti.forEach((c) => creaRigaCampo(c));
    } catch (e) {
      console.error("Errore nel caricare i campi esistenti del profilo:", e);
    }
  }

  aggiornaStatoEtichetta();
  ridisegnaOverlay();
})();
