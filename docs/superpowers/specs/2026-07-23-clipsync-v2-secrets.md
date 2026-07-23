# GitHub Secrets for Florentino-coder/clipsync-v2 ONLY

Do **not** add these to production `Florentino-coder/clipsync`.

Generated locally on this machine (not in git):

`C:\Users\fluk3\.clipsync-v2-secrets\`

Files:
- `clipsync-v2-test.jks` — test keystore
- `ANDROID_KEYSTORE_BASE64.txt` — paste into secret `ANDROID_KEYSTORE_BASE64`
- `passwords.txt` — contains:
  - `ANDROID_KEYSTORE_PASSWORD`
  - `ANDROID_KEY_ALIAS` = `clipsync-v2-test`
  - `ANDROID_KEY_PASSWORD`

## GitHub UI steps

1. Open https://github.com/Florentino-coder/clipsync-v2/settings/secrets/actions
2. New repository secret × 4 (names exact):
   - `ANDROID_KEYSTORE_BASE64`
   - `ANDROID_KEYSTORE_PASSWORD`
   - `ANDROID_KEY_ALIAS`
   - `ANDROID_KEY_PASSWORD`
3. Do **not** create `PUBLIC_RELEASE_TOKEN` on v2 (we disabled public apk publish)

## After secrets

Push code to `clipsync-v2` → Actions → release tag `slip-test-latest` → PC downloads from that URL.
