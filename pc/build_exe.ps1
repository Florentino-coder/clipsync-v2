$ErrorActionPreference = "Stop"

if (!(Test-Path ".\.venv\Scripts\python.exe")) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3 -m venv .venv
    } else {
        python -m venv .venv
    }
}

.\.venv\Scripts\python.exe -m pip install --no-cache-dir -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-cache-dir pyinstaller==6.20.0

Remove-Item -Recurse -Force .\dist\ClipSyncPC -ErrorAction SilentlyContinue
Remove-Item -Force .\dist\ClipSyncPC.exe -ErrorAction SilentlyContinue

$extraData = @('--add-data', 'assets\clipsync_icon.png;assets')
if (Test-Path '.\chrome-extension\manifest.json') {
    $extraData += @('--add-data', 'chrome-extension;chrome-extension')
}

$hiddenImports = @(
    '--hidden-import', 'psutil',
    '--hidden-import', 'websockets',
    '--hidden-import', 'cryptography'
)

.\.venv\Scripts\python.exe -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name ClipSyncPC `
    --icon assets\clipsync.ico `
    @extraData `
    @hiddenImports `
    --distpath dist `
    --workpath build `
    --specpath . `
    clipsync_pc.py

.\.venv\Scripts\python.exe -m PyInstaller `
    --noconfirm `
    --clean `
    ClipSyncPC.spec
