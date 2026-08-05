/* wizard-nuova-elaborazione.js
   Gestisce la navigazione tra i 4 step del form "Nuova elaborazione"
   (Sezione 7.2 del documento di progetto). Nessuna dipendenza esterna.
*/
(function () {
  const TOTALE_STEP = 4;
  let stepCorrente = 1;

  const inputFile = document.getElementById("input-file");
  const dropzone = document.getElementById("dropzone");
  const nomeFileEl = document.getElementById("nome-file-selezionato");
  const btnAvanti = document.getElementById("btn-avanti");
  const btnIndietro = document.getElementById("btn-indietro");
  const btnAvvia = document.getElementById("btn-avvia");
  const form = document.getElementById("form-wizard");

  const ETICHETTE_MODALITA = {
    full: "Completa",
    solo_preprocessing: "Solo preprocessing",
    da_ocr_in_poi: "Da OCR in poi",
  };

  function mostraStep(numero) {
    document.querySelectorAll(".wizard-panel").forEach((pannello) => {
      pannello.classList.toggle(
        "wizard-panel--attivo",
        Number(pannello.dataset.step) === numero
      );
    });

    document.querySelectorAll(".wizard-step").forEach((indicatore) => {
      const n = Number(indicatore.dataset.stepIndicatore);
      indicatore.classList.remove("wizard-step--attivo", "wizard-step--completato");
      if (n === numero) indicatore.classList.add("wizard-step--attivo");
      else if (n < numero) indicatore.classList.add("wizard-step--completato");
    });

    btnIndietro.style.visibility = numero === 1 ? "hidden" : "visible";
    btnAvanti.style.display = numero < 3 ? "inline-flex" : "none";
    btnAvvia.style.display = numero === 3 ? "inline-flex" : "none";

    // Lo step 4 (Monitoraggio) e' solo l'esito dopo l'invio: nascondiamo
    // i pulsanti di navigazione quando lo raggiungiamo.
    if (numero === 4) {
      btnIndietro.style.visibility = "hidden";
      btnAvanti.style.display = "none";
      btnAvvia.style.display = "none";
    }

    if (numero === 3) aggiornaRiepilogo();
    stepCorrente = numero;
  }

  function validaStepCorrente() {
    if (stepCorrente === 1) {
      if (!inputFile.files || inputFile.files.length === 0) {
        alert("Seleziona un file PDF prima di continuare.");
        return false;
      }
      const nome = inputFile.files[0].name.toLowerCase();
      if (!nome.endsWith(".pdf")) {
        alert("Il file deve essere in formato PDF.");
        return false;
      }
    }
    return true;
  }

  function aggiornaRiepilogo() {
    const fileSel = inputFile.files[0];
    document.getElementById("riepilogo-file").textContent = fileSel ? fileSel.name : "—";

    const modalitaSel = form.querySelector('input[name="modalita"]:checked');
    document.getElementById("riepilogo-modalita").textContent = modalitaSel
      ? ETICHETTE_MODALITA[modalitaSel.value] || modalitaSel.value
      : "—";
  }

  inputFile.addEventListener("change", () => {
    if (inputFile.files.length > 0) {
      nomeFileEl.textContent = inputFile.files[0].name;
      dropzone.classList.add("dropzone--attivo");
    }
  });

  // drag & drop sulla dropzone
  ["dragover", "dragenter"].forEach((evento) =>
    dropzone.addEventListener(evento, (e) => {
      e.preventDefault();
      dropzone.classList.add("dropzone--attivo");
    })
  );
  ["dragleave", "drop"].forEach((evento) =>
    dropzone.addEventListener(evento, (e) => {
      e.preventDefault();
      if (evento === "drop" && e.dataTransfer.files.length > 0) {
        inputFile.files = e.dataTransfer.files;
        nomeFileEl.textContent = inputFile.files[0].name;
      }
      dropzone.classList.remove("dropzone--attivo");
    })
  );

  btnAvanti.addEventListener("click", () => {
    if (!validaStepCorrente()) return;
    if (stepCorrente < 3) mostraStep(stepCorrente + 1);
  });

  btnIndietro.addEventListener("click", () => {
    if (stepCorrente > 1) mostraStep(stepCorrente - 1);
  });

  form.addEventListener("submit", () => {
    // Il POST reale (verso /jobs/nuovo) avviene con il submit nativo del form;
    // mostriamo lo step 4 solo come feedback immediato prima del redirect.
    mostraStep(4);
    btnAvvia.disabled = true;
  });

  mostraStep(1);
})();
