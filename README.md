# ClipSync

Copy text on your PC and sync it to an Android clipboard through a lightweight WebSocket relay.

## Deploy Relay

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Florentino-coder/clipsync)

## Download Android APK

The latest public APK is published from a separate public release repository:

[Download ClipSync.apk](https://github.com/Florentino-coder/clipsync/releases/download/android-latest/ClipSync.apk)

If the release asset is not ready yet, open the private repository Actions tab and check the latest `Build Android APK` run.

## Public APK Release Setup

This private repository builds the Android APK, then publishes only `ClipSync.apk` to the public `Florentino-coder/clipsync-apk` release.

One-time setup:

1. Create a public GitHub repository named `clipsync-apk` under `Florentino-coder`.
2. Initialize it with a README so the default `main` branch exists.
3. Create a fine-grained personal access token with `Contents: Read and write` access to `Florentino-coder/clipsync-apk`.
4. Add that token to this private repository as an Actions secret named `PUBLIC_RELEASE_TOKEN`.

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
