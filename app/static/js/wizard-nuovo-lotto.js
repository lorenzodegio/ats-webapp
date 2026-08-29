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

  const inputCartella = document.getElementById("input-cartella-pdf");
  const dropzoneCartella = document.getElementById("dropzone-cartella");
  const nomeCartellaEl = document.getElementById("nome-cartella-pdf");

  const btnModoCombinato = document.getElementById("btn-modo-combinato");
  const btnModoCartella = document.getElementById("btn-modo-cartella");
  const bloccoModoCombinato = document.getElementById("blocco-modo-combinato");
  const bloccoModoCartella = document.getElementById("blocco-modo-cartella");
  let modoCaricamento = "combinato"; // oppure "cartella"

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
      if (modoCaricamento === "combinato") {
        if (!inputPdf.files || inputPdf.files.length === 0) {
          alert("Carica il PDF combinato delle prescrizioni prima di continuare.");
          return false;
        }
        if (!inputPdf.files[0].name.toLowerCase().endsWith(".pdf")) {
          alert("Il file prescrizioni deve essere in formato PDF.");
          return false;
        }
      } else {
        if (pdfTrovatiInCartella().length === 0) {
          alert("Scegli una cartella che contenga almeno un file PDF.");
          return false;
        }
      }
      if (!inputExcel.files || inputExcel.files.length === 0) {
        alert("Carica l'Excel Regione prima di continuare: senza, l'analisi delle difformità non è affidabile.");
        return false;
      }
      const nomeExcel = inputExcel.files[0].name.toLowerCase();
      if (!nomeExcel.endsWith(".xlsx") && !nomeExcel.endsWith(".xls")) {
        alert("L'Excel Regione deve essere in formato .xlsx o .xls.");
        return false;
      }
    }
    return true;
  }

  function pdfTrovatiInCartella() {
    if (!inputCartella.files) return [];
    return Array.from(inputCartella.files).filter((f) => f.name.toLowerCase().endsWith(".pdf"));
  }

  function impostaModoCaricamento(modo) {
    modoCaricamento = modo;
    const combinato = modo === "combinato";
    bloccoModoCombinato.style.display = combinato ? "" : "none";
    bloccoModoCartella.style.display = combinato ? "none" : "";
    btnModoCombinato.classList.toggle("btn--primario", combinato);
    btnModoCombinato.classList.toggle("btn--secondario", !combinato);
    btnModoCartella.classList.toggle("btn--primario", !combinato);
    btnModoCartella.classList.toggle("btn--secondario", combinato);
    // Svuota la modalita' non attiva: non deve restare selezionato un file
    // "nascosto" che finirebbe comunque nel form al momento dell'invio.
    if (combinato) {
      inputCartella.value = "";
      nomeCartellaEl.textContent = "";
      dropzoneCartella.classList.remove("dropzone--attivo");
    } else {
      inputPdf.value = "";
      nomeFilePdfEl.textContent = "";
      dropzonePdf.classList.remove("dropzone--attivo");
    }
  }

  function aggiornaRiepilogo() {
    document.getElementById("riepilogo-nome").textContent = inputNome.value.trim() || "—";
    document.getElementById("riepilogo-periodo").textContent =
      `${NOMI_MESI[Number(inputMese.value)]} ${inputAnno.value}`;
    if (modoCaricamento === "combinato") {
      document.getElementById("riepilogo-pdf").textContent =
        inputPdf.files[0] ? inputPdf.files[0].name : "—";
    } else {
      const trovati = pdfTrovatiInCartella();
      document.getElementById("riepilogo-pdf").textContent =
        trovati.length ? `${trovati.length} file PDF (cartella)` : "—";
    }
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

  // Niente trascinamento per la cartella (richiede l'API DataTransferItem
  // per leggere le sottocartelle, non vale la complessita' in piu'): solo
  // click, che apre il selettore di cartelle nativo del sistema operativo.
  inputCartella.addEventListener("change", () => {
    const trovati = pdfTrovatiInCartella();
    if (trovati.length > 0) {
      nomeCartellaEl.textContent = `${trovati.length} file PDF trovati`;
      dropzoneCartella.classList.add("dropzone--attivo");
    } else {
      nomeCartellaEl.textContent = "Nessun PDF trovato in questa cartella";
      dropzoneCartella.classList.remove("dropzone--attivo");
    }
  });

  btnModoCombinato.addEventListener("click", () => impostaModoCaricamento("combinato"));
  btnModoCartella.addEventListener("click", () => impostaModoCaricamento("cartella"));

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
