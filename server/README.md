# ClipSync Relay on Render

Deploy this folder as a Render Web Service.

## Settings

- Runtime: Python
- Root directory: `server`
- Build command: `pip install -r requirements.txt`
- Start command: `python relay_server.py`

The relay reads Render's `PORT` environment variable automatically.

After deployment, use the public Render URL as a WebSocket URL:

```text
wss://YOUR-RENDER-SERVICE.onrender.com
```

Use that value in:

- PC: `ClipSyncPC.exe --relay-url wss://YOUR-RENDER-SERVICE.onrender.com`
- Flutter: `const kRelayUrl = 'wss://YOUR-RENDER-SERVICE.onrender.com';`
