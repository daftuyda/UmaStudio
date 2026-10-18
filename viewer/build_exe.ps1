# UmaStudio viewer -> dist\UmaStudio\UmaStudio.exe, its own window (Edge WebView2), no
# python needed to run it. builds in a private venv so the system python is left alone.
# video export still uses ffmpeg from PATH, or an ffmpeg.exe placed next to UmaStudio.exe.

$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
    $exe = Join-Path $PSScriptRoot "dist\UmaStudio\UmaStudio.exe"
    if (Get-Process UmaStudio -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }) {
        throw "UmaStudio is open - close it first (its files are in use)."
    }
    $py = ".build-venv\Scripts\python.exe"
    if (-not (Test-Path $py)) { python -m venv .build-venv }
    & $py -m pip install -q --disable-pip-version-check -r requirements.txt pyinstaller
    if ($LASTEXITCODE) { throw "pip install failed" }

    & $py -m PyInstaller --noconfirm --clean --windowed --name UmaStudio --icon umastudio.ico `
        --add-data "static;static" `
        --collect-all UnityPy --collect-all fmod_toolkit --collect-all texture2ddecoder `
        --collect-all etcpak --collect-all astc_encoder --collect-all archspec --collect-all webview `
        serve.py
    if ($LASTEXITCODE) { throw "PyInstaller failed" }
    Write-Host "Built $PSScriptRoot\dist\UmaStudio\UmaStudio.exe"
}
finally {
    Pop-Location
}
