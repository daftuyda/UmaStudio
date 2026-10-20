# UmaStudio viewer -> dist\UmaStudio\UmaStudio.exe, its own window (Edge WebView2), no
# python needed to run it. builds in a private venv so the system python is left alone.
# video export still uses ffmpeg from PATH, or an ffmpeg.exe placed next to UmaStudio.exe.

param(
    [switch]$RunTests,
    [string]$Tag = ""
)

$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
    $releaseArgs = @()
    if ($Tag) { $releaseArgs = @("--tag", $Tag) }
    $version = & python package_release.py --version-only @releaseArgs
    if ($LASTEXITCODE) { throw "Release version validation failed" }
    $exe = Join-Path $PSScriptRoot "dist\UmaStudio\UmaStudio.exe"
    if (Get-Process UmaStudio -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }) {
        throw "UmaStudio is open - close it first (its files are in use)."
    }
    $py = ".build-venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        python -m venv .build-venv
        if ($LASTEXITCODE) { throw "Could not create the build environment" }
    }
    & $py -m pip install -q --disable-pip-version-check -r requirements.txt pyinstaller==6.22.3
    if ($LASTEXITCODE) { throw "pip install failed" }

    if ($RunTests) {
        & $py -m unittest discover -s tests -v
        if ($LASTEXITCODE) { throw "Python tests failed" }
        Get-ChildItem tests/*.test.mjs | ForEach-Object {
            & node --experimental-vm-modules $_.FullName
            if ($LASTEXITCODE) { throw "JavaScript tests failed: $($_.Name)" }
        }
    }

    & $py -m PyInstaller --noconfirm --clean --windowed --name UmaStudio --icon umastudio.ico `
        --add-data "static;static" `
        --collect-all UnityPy --collect-all fmod_toolkit --collect-all texture2ddecoder `
        --collect-all etcpak --collect-all astc_encoder --collect-all archspec --collect-all webview `
        serve.py
    if ($LASTEXITCODE) { throw "PyInstaller failed" }
    & $py package_release.py @releaseArgs
    if ($LASTEXITCODE) { throw "Release packaging failed" }
    if ($env:GITHUB_OUTPUT) {
        "version=$version" | Out-File -FilePath $env:GITHUB_OUTPUT -Encoding utf8 -Append
    }
    Write-Host "Built $PSScriptRoot\dist\UmaStudio\UmaStudio.exe"
    Write-Host "Packaged $PSScriptRoot\dist\UmaStudio-$version.zip"
}
finally {
    Pop-Location
}
