$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$voiceEnv = Join-Path $projectRoot ".venv-voices"
$voicePython = Join-Path $voiceEnv "Scripts\python.exe"
$basePython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "Installare uv prima di procedere." }
if (-not (Test-Path -LiteralPath $basePython)) { throw "Eseguire prima scripts\install.ps1." }
if (-not (Test-Path -LiteralPath $voicePython)) {
    & uv venv $voiceEnv --python $basePython
    if ($LASTEXITCODE -ne 0) { throw "Creazione ambiente voci fallita." }
}
& uv pip install --python $voicePython "torch==2.8.0" "torchaudio==2.8.0" --index-url https://download.pytorch.org/whl/cpu
if ($LASTEXITCODE -ne 0) { throw "Installazione PyTorch CPU fallita." }
& uv pip install --python $voicePython "speechbrain==1.0.3" "silero-vad==6.2.1" "onnxruntime>=1.20,<2" "soundfile>=0.12,<1" "scipy>=1.12,<2"
if ($LASTEXITCODE -ne 0) { throw "Installazione componenti vocali fallita." }
& $voicePython (Join-Path $PSScriptRoot "download_voice_model.py")
if ($LASTEXITCODE -ne 0) { throw "Download checkpoint ECAPA fallito." }
Write-Host "Componente voci installato separatamente da Whisper. Nessuna registrazione elaborata."
