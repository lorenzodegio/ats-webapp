<#
.SYNOPSIS
    Aggiorna il codice (git pull), le dipendenze (uv sync), applica le
    migrazioni Alembic e riavvia i servizi — con verifica finale su /health.

.DESCRIPTION
    Si ferma al primo errore (fail fast): se le migrazioni falliscono, i
    servizi NON vengono riavviati con un DB a meta' strada.

.PARAMETER Branch
    Branch da cui fare pull. Default: quello attualmente in uso (non forza
    un checkout su un branch diverso).
#>
param(
    [string]$Branch = ""
)

. (Join-Path $PSScriptRoot "common.ps1")

Test-Amministratore

Write-Step "Stato del repository prima dell'aggiornamento"
Push-Location $RepoRoot
try {
    $branchAttuale = (git rev-parse --abbrev-ref HEAD).Trim()
    $targetBranch = if ($Branch) { $Branch } else { $branchAttuale }
    Write-Host "    branch: $targetBranch"

    $modifiche = git status --porcelain
    if ($modifiche) {
        throw "Ci sono modifiche non committate nel repository — risolvile (commit/stash) prima di aggiornare, 'git pull' si rifiuterebbe altrimenti."
    }

    Write-Step "git pull ($targetBranch)"
    git fetch origin
    git checkout $targetBranch
    git pull origin $targetBranch

    Write-Step "Sincronizzo le dipendenze (uv sync, webapp + pipeline)"
    uv sync
    Push-Location (Join-Path $RepoRoot "docker")
    try { uv sync } finally { Pop-Location }

    Write-Step "Applico eventuali nuove migrazioni Alembic"
    $webappPython = Join-Path $WebappVenv "Scripts\python.exe"
    & $webappPython -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "alembic upgrade head ha restituito un errore (codice $LASTEXITCODE) — i servizi NON vengono riavviati." }
} finally {
    Pop-Location
}

Write-Step "Riavvio i servizi"
Invoke-ServiceControl -Nome $ServiceWebapp -Azione restart
# Ollama di norma non cambia da un aggiornamento del codice applicativo —
# riavviato solo se esplicitamente fermo (es. dopo un riavvio del server
# in cui per qualche motivo non fosse ripartito da solo).
$ollamaSvc = Get-Service -Name $ServiceOllama -ErrorAction SilentlyContinue
if ($ollamaSvc -and $ollamaSvc.Status -ne "Running") {
    Invoke-ServiceControl -Nome $ServiceOllama -Azione start
}

Write-Step "Verifico /health"
if (Test-Health) {
    Write-Host "OK — l'app risponde ed e' collegata al database." -ForegroundColor Green
} else {
    Write-Host "L'app NON risponde su /health dopo il riavvio — controlla i log in $LogsRoot e lo stato con .\status.ps1" -ForegroundColor Red
    exit 1
}
