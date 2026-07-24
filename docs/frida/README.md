# Frida capture runbook — recovering the SaveFamily mTLS client identity

This directory contains everything needed to run the recovery step described in
[../SERVER_LOCKOUT.md](../SERVER_LOCKOUT.md). It **cannot** be done from a CI/dev
machine — it requires a rooted Android device or a rooted emulator actually
running the official SaveFamily app. Read the root-cause doc first.

## What you are extracting

1. **Native login constants** — `appid`, `version`, `flag` (now generated in
   `libnative-lib.so`, stubbed in the static APK).
2. **The mTLS client identity** — the client **certificate chain + private key**
   the app presents to `europe.myaqsh.com:11001-11003`. This is the actual blocker:
   the new API rejects anyone without it (`400 No required SSL certificate was sent`).
3. **The real login request** — host/port/path/params the current app uses.

## Prerequisites

- A **rooted** Android device or emulator (Google APIs image, *not* Play image, so
  it can be rooted). AVD example: `system-images;android-30;google_apis;x86_64`.
- The official **SaveFamily** app installed (`com.tgelec.savefamily`) and a working
  account to log in with.
- Host: `pip install frida-tools` (matching the frida-server version below).
- `frida-server` for the device ABI pushed and running as root:

  ```bash
  adb root && adb push frida-server-XX.X.X-android-arm64 /data/local/tmp/frida-server
  adb shell "chmod 755 /data/local/tmp/frida-server && /data/local/tmp/frida-server &"
  ```

## Run the capture

```bash
frida -U -f com.tgelec.savefamily -l capture_savefamily.js --no-pause
```

Then **log in inside the app**. Watch the console:

- `NativeUtils.getAppId()/getVersion()/getFlag() = ...` → copy these into
  [`../../custom_components/savefamily/core/protocol.py`](../../custom_components/savefamily/core/protocol.py)
  (`DEFAULT_APP_ID`, `DEFAULT_CLIENT_VERSION`, `DEFAULT_CLIENT_FLAG`).
- `[tls ] client certificate chain:` → save the `-----BEGIN CERTIFICATE-----`
  block(s) to `client-cert.pem`.
- `[tls ] client private key:` → save the `-----BEGIN PRIVATE KEY-----` block to
  `client-key.pem`. If instead you see *"non-extractable (hardware-backed)"*, the
  key lives in the Android KeyStore; check the `[ks ]` lines — the identity is
  probably loaded from a bundled `.p12`/`.bks` whose password is printed by the
  `KeyStore.load` hook, so pull that asset and open it with the printed password.
- `[http] ... POST https://...:1100x/...` → the real login endpoint and body.

### If the app dies on launch (anti-Frida / Baidu packer)

- Prefer spawn (`-f`) so hooks land before the packer restores classes.
- Try an **older** SaveFamily build first (fewer defenses) to get the client cert —
  the mTLS identity is usually stable across builds even if `version` changes.
- Consider `objection`/`frida-gadget` repackaging, or a magisk "MagiskFrida" module,
  or an anti-anti-frida script (hook `ptrace`, `/proc/self/status` TracerPid, etc.).

## Wire it into the integration

Once you have `client-cert.pem` + `client-key.pem` and the new endpoint:

1. In [`core/async_client.py`](../../custom_components/savefamily/core/async_client.py),
   build an `ssl.SSLContext` that loads the client identity and use it for the
   requests to the mTLS host:

   ```python
   import ssl
   ctx = ssl.create_default_context()
   ctx.load_cert_chain(certfile="client-cert.pem", keyfile="client-key.pem")
   # aiohttp: connector = aiohttp.TCPConnector(ssl=ctx)
   ```

2. Point the base URLs at the new port and update the login endpoint/params to
   match the captured request.
3. Update `DEFAULT_APP_ID` / `DEFAULT_CLIENT_VERSION` / `DEFAULT_CLIENT_FLAG` in
   `core/protocol.py` with the dumped values.
4. Make the client-certificate path configurable via the config flow, and relax
   the `SaveFamilyUpgradeRequiredError` → `ConfigEntryError` handling once login
   works again.

## Legality / scope

This is for restoring interoperability with **your own** account and **your own**
devices. Do not use captured credentials against accounts or devices you do not own.
