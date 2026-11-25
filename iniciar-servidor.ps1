# Arranca el servidor RoomCam en esta laptop (Windows).
# Uso: clic derecho > "Ejecutar con PowerShell", o en PowerShell:  .\iniciar-servidor.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "server")

if (-not (Test-Path ".venv")) {
    Write-Host "Preparando el servidor por primera vez (tarda un par de minutos)..."
    python -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
}

& .\.venv\Scripts\python.exe -m roomcam_server.local @args
Read-Host "Servidor detenido. Pulsa Enter para cerrar"
