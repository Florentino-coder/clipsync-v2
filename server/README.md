# ClipSync Relay on Render

Deploy this folder as a Render Web Service.

## Settings

- Runtime: Python
- Root directory: `server`
- Build command: `pip install -r requirements.txt`
- Start command: `python relay_server.py`

The relay reads Render's `PORT` environment variable automatically.

Current relay WebSocket URL:

```text
wss://clipsync-relay.onrender.com
```

Use that value in:

- PC: `ClipSyncPC.exe --relay-url wss://clipsync-relay.onrender.com`
- Flutter: `const kRelayUrl = 'wss://clipsync-relay.onrender.com';`
