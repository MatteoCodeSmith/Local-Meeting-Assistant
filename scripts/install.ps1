[CmdletBinding()]
param([switch]$Dev)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if ($env:OS -ne "Windows_NT" -or -not [Environment]::Is64BitOperatingSystem) {
    throw "BIONIC richiede Windows x64."
}
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCommand) {
    throw "Installa uv (https://docs.astral.sh/uv/getting-started/installation/), riapri PowerShell e riprova."
}
$venv = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    # No dependency on a personal/Codex runtime: uv locates or downloads Python 3.12.
    & $uvCommand.Source venv $venv --python 3.12
    if ($LASTEXITCODE -ne 0) { throw "Creazione ambiente Python 3.12 fallita." }
}
& $venvPython -c "import sys,struct; assert sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8, 'Richiesto Python 3.12 x64'"
if ($LASTEXITCODE -ne 0) { throw "Ambiente esistente incompatibile. Non e stato modificato: usa una nuova cartella del progetto." }
$installTarget = if ($Dev) { "${projectRoot}[dev]" } else { $projectRoot }
& $uvCommand.Source pip install --python $venvPython --editable $installTarget
if ($LASTEXITCODE -ne 0) { throw "Installazione dipendenze fallita. Controlla connessione e messaggio precedente." }
& $uvCommand.Source pip check --python $venvPython
if ($LASTEXITCODE -ne 0) { throw "Dipendenze incompatibili: controlla l'output precedente." }
Write-Host "App installata. Completa modelli e voci seguendo README.md, poi avvia scripts\run.ps1."
