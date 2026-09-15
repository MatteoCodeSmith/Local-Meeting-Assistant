$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $projectRoot ".venv\Scripts\pythonw.exe"
if (-not (Test-Path -LiteralPath $pythonw)) {
    throw "Ambiente non installato. Eseguire prima scripts\install.ps1"
}
Start-Process -FilePath $pythonw -ArgumentList "-m", "local_meeting_assistant" -WorkingDirectory $projectRoot -WindowStyle Hidden

