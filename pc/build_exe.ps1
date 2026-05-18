$ErrorActionPreference = "Stop"

if (!(Test-Path ".\.venv\Scripts\python.exe")) {
    python -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install --no-cache-dir -r requirements.txt
.\.venv\Scripts\python.exe -m PyInstaller `
    --onefile `
    --windowed `
    --name ClipSyncPC `
    --icon assets\clipsync.ico `
    --add-data "assets\clipsync_icon.png;assets" `
    --distpath dist `
    --workpath build `
    --specpath . `
    clipsync_pc.py
