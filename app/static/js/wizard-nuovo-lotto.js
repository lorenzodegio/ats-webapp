/* wizard-nuovo-lotto.js
   Wizard a 3 step per la creazione di un nuovo lotto mensile:
   Dati lotto -> Caricamento file -> Conferma. Nessuna dipendenza esterna.
*/
(function () {
  let stepCorrente = 1;
  const TOTALE_STEP = 3;

  const inputNome = document.getElementById("input-nome");
  const inputMese = document.getElementById("input-mese");
  const inputAnno = document.getElementById("input-anno");

  const inputPdf = document.getElementById("input-file-pdf");
  const dropzonePdf = document.getElementById("dropzone-pdf");
  const nomeFilePdfEl = document.getElementById("nome-file-pdf");

  const inputExcel = document.getElementById("input-file-excel");
  const dropzoneExcel = document.getElementById("dropzone-excel");
  const nomeFileExcelEl = document.getElementById("nome-file-excel");

  const btnAvanti = document.getElementById("btn-avanti");
  const btnIndietro = document.getElementById("btn-indietro");
  const btnAvvia = document.getElementById("btn-avvia");
  const form = document.getElementById("form-wizard");

  const NOMI_MESI = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
    "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"];

  function mostraStep(numero) {
    document.querySelectorAll(".wizard-panel").forEach((pannello) => {
      pannello.classList.toggle("wizard-panel--attivo", Number(pannello.dataset.step) === numero);
    });
    document.querySelectorAll("#wizard-steps [data-step-indicatore]").forEach((indicatore) => {
      const n = Number(indicatore.dataset.stepIndicatore);
      indicatore.classList.remove("wizard-step--attivo", "wizard-step--completato", "wizard-step--bloccato");
      if (n > TOTALE_STEP) {
        indicatore.classList.add("wizard-step--bloccato");
      } else if (n === numero) {
        indicatore.classList.add("wizard-step--attivo");
      } else if (n < numero) {
        indicatore.classList.add("wizard-step--completato");
      }
    });

    btnIndietro.style.visibility = numero === 1 ? "hidden" : "visible";
    btnAvanti.style.display = numero < TOTALE_STEP ? "inline-flex" : "none";
    btnAvvia.style.display = numero === TOTALE_STEP ? "inline-flex" : "none";

    if (numero === TOTALE_STEP) aggiornaRiepilogo();
    stepCorrente = numero;
  }

  function validaStepCorrente() {
    if (stepCorrente === 1) {
      if (!inputNome.value.trim()) {
        alert("Dai un nome al lotto prima di continuare.");
        return false;
      }
    }
    if (stepCorrente === 2) {
      if (!inputPdf.files || inputPdf.files.length === 0) {
        alert("Carica il PDF combinato delle prescrizioni prima di continuare.");
        return false;
      }
      if (!inputPdf.files[0].name.toLowerCase().endsWith(".pdf")) {
        alert("Il file prescrizioni deve essere in formato PDF.");
        return false;
      }
    }
    return true;
  }

  function aggiornaRiepilogo() {
    document.getElementById("riepilogo-nome").textContent = inputNome.value.trim() || "—";
    document.getElementById("riepilogo-periodo").textContent =
      `${NOMI_MESI[Number(inputMese.value)]} ${inputAnno.value}`;
    document.getElementById("riepilogo-pdf").textContent =
      inputPdf.files[0] ? inputPdf.files[0].name : "—";
    document.getElementById("riepilogo-excel").textContent =
      inputExcel.files[0] ? inputExcel.files[0].name : "Non caricato";
  }

  function collegaDropzone(input, dropzone, etichettaEl) {
    input.addEventListener("change", () => {
      if (input.files.length > 0) {
        etichettaEl.textContent = input.files[0].name;
        dropzone.classList.add("dropzone--attivo");
      }
    });
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
          input.files = e.dataTransfer.files;
          etichettaEl.textContent = input.files[0].name;
        }
        dropzone.classList.remove("dropzone--attivo");
      })
    );
  }

  collegaDropzone(inputPdf, dropzonePdf, nomeFilePdfEl);
  collegaDropzone(inputExcel, dropzoneExcel, nomeFileExcelEl);

  btnAvanti.addEventListener("click", () => {
    if (!validaStepCorrente()) return;
    if (stepCorrente < TOTALE_STEP) mostraStep(stepCorrente + 1);
  });

  btnIndietro.addEventListener("click", () => {
    if (stepCorrente > 1) mostraStep(stepCorrente - 1);
  });

  form.addEventListener("submit", () => {
    btnAvvia.disabled = true;
    btnAvvia.textContent = "Creazione in corso…";
  });

    document.querySelectorAll("#wizard-steps [data-step-indicatore]").forEach((indicatore) => {
    indicatore.style.cursor = "pointer";
    indicatore.addEventListener("click", () => {
      const n = Number(indicatore.dataset.stepIndicatore);
      if (n < 1 || n > TOTALE_STEP) return;
      if (n > stepCorrente && !validaStepCorrente()) return;
      mostraStep(n);
    });
  });

  mostraStep(1);
})();
