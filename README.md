# ClipSync

Copy text on your PC and sync it to an Android clipboard through a lightweight WebSocket relay.

## Deploy Relay

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Florentino-coder/clipsync)

## Download Android APK

The latest APK is published as a GitHub release:

[Download ClipSync.apk](https://github.com/Florentino-coder/clipsync/releases/download/android-latest/ClipSync.apk)

If the release asset is not ready yet, open the repository Actions tab and check the latest `Build Android APK` run.

Current relay WebSocket URL:

```text
wss://clipsync-relay.onrender.com
```

Use it with the PC client:

```powershell
.\ClipSyncPC.exe --relay-url wss://clipsync-relay.onrender.com
```

For Android, set this value in `mobile/lib/clip_service.dart`:

```dart
const kRelayUrl = 'wss://clipsync-relay.onrender.com';
```
