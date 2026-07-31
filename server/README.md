# ClipSync Relay on Render

Deploy this folder as a Render Web Service.

## Settings

- Runtime: Python
- Root directory: `server`
- Build command: `pip install -r requirements.txt`
- Start command: `python relay_server.py`

The relay reads Render's `PORT` environment variable automatically.

Relay WebSocket failover order:

```text
Primary: wss://clipsync-relay-ko3c.onrender.com
Backup:  wss://clipsync-relay.onrender.com
```

Clients open one WebSocket at a time. They stay on the active relay and try backup only after failure. Use the same order in:

- PC: `ClipSyncPC.exe --relay-url wss://clipsync-relay-ko3c.onrender.com`
- Flutter: `kRelayUrls` in `mobile/lib/clip_service.dart`
