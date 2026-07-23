# ClipSync v2 Test Pipeline — Design / Plan

**Status:** Approved direction (2026-07-23)  
**Owner:** F  
**Constraint:** Production `Florentino-coder/clipsync` is live — **do not push, tag, or overwrite its releases.**

---

## Goal

Ship slip-auto-confirm **test builds** via GitHub Actions on a **separate** repo, then:

1. PC downloads APK from GitHub (PC net)
2. PC serves APK to phone over USB tether (local, no phone data)

---

## Isolation rules (hard)

| Action | Production `clipsync` | Test `clipsync-v2` |
|--------|----------------------|---------------------|
| `git push` slip branch | **Forbidden** | Allowed |
| Release tag `android-latest` | **Do not touch** | Use `slip-test-latest` only |
| Signing keystore | Production secrets stay | **New test keystore only** |
| Update `version.json` URLs | Keep production URLs | Point to v2 release assets |
| Relay `clipsync-relay.onrender.com` | Leave running | Reuse OK (new msgs ignored by old clients) |

---

## Signing

- **Choice:** Release-signed with **separate test keystore** (not production).
- Alias example: `clipsync-v2-test`
- Store as GitHub Actions secrets on **`clipsync-v2` only**:
  - `ANDROID_KEYSTORE_BASE64`
  - `ANDROID_KEYSTORE_PASSWORD`
  - `ANDROID_KEY_ALIAS`
  - `ANDROID_KEY_PASSWORD`
- Never copy production keystore into v2.
- Side effect: installing test APK may require uninstalling a production-signed ClipSync on the same `applicationId` — use test phone, or change `applicationId` suffix `.test` if needed later.

---

## CI on clipsync-v2

1. Workflow on `main` + `workflow_dispatch`
2. Build signed APK (+ optional PC EXE)
3. Publish/replace assets on release tag **`slip-test-latest`**
4. Stable names:
   - `ClipSync-slip.apk`
   - `ClipSyncPC.exe` (optional)

PC download URL (config):

```text
https://github.com/Florentino-coder/clipsync-v2/releases/download/slip-test-latest/ClipSync-slip.apk
```

---

## PC APK flow

1. Settings → **Download APK from GitHub** → `%APPDATA%\ClipSync\apk\`
2. Settings → **Share over USB tether** → `http://<pc-usb-ip>:8788/ClipSync-slip.apk`
3. Phone browser opens URL → install
4. Fallback: Open APK folder (MTP drag-drop)

---

## Rollout order

1. Generate test keystore locally (not committed)
2. Add secrets to **clipsync-v2** only
3. Add remote `v2` → push `feat/slip-auto-confirm` (or `main`) to v2 only
4. Run Actions → confirm APK on `slip-test-latest`
5. PC download + USB share test on **lab phone**
6. Production stays on current ClipSync until Gates pass

---

## Out of scope (for now)

- Merging into production `clipsync`
- Overwriting production APK/EXE releases
- Enabling `auto_confirm` for real shops
- Changing production relay deploy
