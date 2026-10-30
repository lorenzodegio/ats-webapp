<#
.SYNOPSIS
    Calibra num_thread/parallelismo per la fase di estrazione OCR,
    eseguendo benchmark.py (ambiente Python della pipeline) con le
    combinazioni indicate e producendo CSV + report Markdown.

.DESCRIPTION
    Wrapper sottile: tutta la logica di misura vive in benchmark.py (radice
    del repository, ambiente Python della pipeline - torch/easyocr/ollama).
    Questo script si limita a trovare l'interprete giusto e a passare i
    parametri. Non stampa mai contenuto dei documenti: solo conteggi, tempi
    e picchi di RAM (vedi benchmark.py).

    USA SOLO PDF anonimizzati/di prova, mai ricette reali con dati di
    pazienti.

.PARAMETER Ricette
    Cartella con i PDF di test (obbligatoria).

.PARAMETER Thread
    Valori di num_thread da confrontare, separati da virgola.
    Default: 20,30,38 (indicati nella specifica per i 40 vCPU del server).

.PARAMETER Parallelismo
    Valori di PIPELINE_PARALLELISMO da confrontare, separati da virgola.
    Default: 1,2.

.PARAMETER Excel
    File Excel Regione opzionale (solo per nome_farmacia via barcode).

.PARAMETER OutputDir
    Cartella dove salvare CSV e report Markdown. Default: backup\benchmark
    sotto la radice del repository.

.EXAMPLE
    .\benchmark.ps1 -Ricette "E:\apps\ocr-cannabis\data\pdf_test_anonimizzati"
#>
param(
    [Parameter(Mandatory)][string]$Ricette,
    [string]$Thread = "20,30,38",
    [string]$Parallelismo = "1,2",
    [string]$Excel = "",
    [string]$OutputDir = ""
)

. (Join-Path $PSScriptRoot "common.ps1")

if (-not (Test-Path $Ricette)) {
    throw "Cartella ricette non trovata: $Ricette"
}

$pipelinePythonDefault = Join-Path $PipelineVenv "Scripts\python.exe"
$pipelinePython = Get-DotEnvValue -Chiave "PIPELINE_PYTHON" -Default $pipelinePythonDefault
if (-not (Test-Path $pipelinePython)) {
    throw "Interprete della pipeline non trovato: $pipelinePython - esegui prima install.ps1 (uv sync) o imposta PIPELINE_PYTHON in .env."
}

if (-not $OutputDir) {
    $OutputDir = Join-Path $BackupRoot "benchmark"
}
if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$risultatiCsv = Join-Path $OutputDir "benchmark_risultati_$timestamp.csv"
$reportMd = Join-Path $OutputDir "benchmark_report_$timestamp.md"
$outputTemp = Join-Path $OutputDir "benchmark_output_$timestamp"

Write-Step "Eseguo benchmark.py ($Ricette, thread=$Thread, parallelismo=$Parallelismo)"
$argomenti = @(
    (Join-Path $RepoRoot "benchmark.py"),
    "--ricette", $Ricette,
    "--output", $outputTemp,
    "--thread", $Thread,
    "--parallelismo", $Parallelismo,
    "--risultati", $risultatiCsv,
    "--report", $reportMd
)
if ($Excel) {
    $argomenti += @("--excel", $Excel)
}

& $pipelinePython @argomenti
if ($LASTEXITCODE -ne 0) {
    throw "benchmark.py ha restituito un errore (codice $LASTEXITCODE) - vedi output sopra."
}

Write-Step "Benchmark completato"
Write-Host "CSV:      $risultatiCsv" -ForegroundColor Green
Write-Host "Report:   $reportMd" -ForegroundColor Green
