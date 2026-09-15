$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Ambiente non installato. Eseguire prima scripts\install.ps1"
}
& $python -m local_meeting_assistant
exit $LASTEXITCODE
