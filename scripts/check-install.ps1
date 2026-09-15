[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Eseguire prima scripts\install.ps1." }
& $python (Join-Path $PSScriptRoot "check_install.py")
exit $LASTEXITCODE
