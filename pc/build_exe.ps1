$ErrorActionPreference = "Stop"

if (!(Test-Path ".\.venv\Scripts\python.exe")) {
    python -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install --no-cache-dir -r requirements.txt

Remove-Item -Recurse -Force .\dist\ClipSyncPC -ErrorAction SilentlyContinue
Remove-Item -Force .\dist\ClipSyncPC.exe -ErrorAction SilentlyContinue

.\.venv\Scripts\python.exe -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name ClipSyncPC `
    --icon assets\clipsync.ico `
    --add-data "assets\clipsync_icon.png;assets" `
    --distpath dist `
    --workpath build `
    --specpath . `
    clipsync_pc.py

.\.venv\Scripts\python.exe -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name ClipSyncPC `
    --icon assets\clipsync.ico `
    --add-data "assets\clipsync_icon.png;assets" `
    --distpath dist `
    --workpath build `
    --specpath . `
    clipsync_pc.py
