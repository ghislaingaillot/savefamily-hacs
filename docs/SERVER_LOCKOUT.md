# SaveFamily server lockout (status 3 — "The current app is unavailable. Please upgrade the app.")

## Resolved in v0.4.1

The upstream reference project [yqt-smart-api](https://github.com/Niek/yqt-smart-api)
worked out the new protocol (commit
[`5ee1a46`](https://github.com/Niek/yqt-smart-api/commit/5ee1a46)): the live API moved
to mutual-TLS endpoints (`:11001`–`:11003`) with an AES-CBC encrypted JSON envelope
wrapping the usual signed form parameters. This integration now ships the matching
client certificate (`core/client.pem`) and the encrypt/decrypt transport
(`core/transport.py`), ported directly from that fix. No `appid`/`version`/`flag`
extraction from the APK was needed in the end — see the rest of this document for
historical context on the investigation.

## Symptom (v0.3.x and earlier)

Home Assistant fails to set up the integration and retries endlessly with:

```
status=3: API version rejected by server (update the integration):
The current app is unavailable. Please upgrade the app.
```

Since **v0.4.0** the integration no longer retries forever on this condition: it
raises a permanent, explanatory error instead (see "Behaviour in the integration"
below).

## Root cause (verified against the live server on 2026-07-24)

This is **not a bug in the integration** and **cannot be fixed by changing a
version constant**. The LinksField/tgelec backend behind SaveFamily
(`*.myaqsh.com`) closed the reverse-engineered login path. Findings from live
probing of `https://europe.myaqsh.com:8093/app/public/S10APP/v2_new_userLogin2`:

1. **The `version` string is the only gate, and every value is now rejected.**
   - `version <= 1.0.1` → `status 3`, message *"The old version have stopped…"*,
     with an upgrade URL in the response (`com.tgelec.yqtsmart`).
   - **every other value** (`1.0.2`, `1.0.3`, `1.1.5`, `1.3.9`, `2.0.0`, the APK
     versionCode `40`, date strings, …) → `status 3`, message *"The current app
     is unavailable. Please upgrade the app."*, with an **empty** `version` field.
   - On 2026-07-06 `version=1.0.2` still worked; the block was tightened after that.
2. **`appid` is completely ignored** at this stage. `aaagg11145` (the value this
   integration shipped), an empty string, and random junk all produce identical
   responses. Note `aaagg11145` is actually the **YQT SMART** app's id
   (`com.tgelec.yqtsmart`), not SaveFamily's — the two are white-labels of the
   same OEM backend.
3. **The signature is not the problem.** A deliberately corrupted `sign` yields
   the same `status 3`, and a normally-signed `version=1.0.1` request is fully
   parsed and version-checked — so `sign` / `sign_flag=KHDIW` are still accepted.
4. **The live API moved behind mutual TLS.** Ports `11001`, `11002`, `11003` on
   the same hosts answer `HTTP 400 "No required SSL certificate was sent"`
   (nginx `ssl_verify_client on`). The updated official app authenticates there
   by presenting a **client certificate**.

### Why the client certificate can't be extracted statically

The current APK (`com.tgelec.savefamily` 1.3.9, versionCode 40) is:

- **Baidu-packed** (`assets/baiduprotect*.jar` / `*.i.dex`) — the real Dalvik
  code and its string pool are encrypted at rest and only restored at runtime.
- Its login credentials are generated in **native code**: `libnative-lib.so`
  exports `NativeUtils.getAppId()`, `getVersion()`, `getFlag()`, plus
  `encrypt()`/`decrypt()` and a key literal `ThisIsMySecretNativeKey123`. In the
  shipped `.so` these getters are packer **stubs** (`getFlag` = `mov w0,#0; ret`;
  `getAppId`/`getVersion` return an empty `.rodata` string) — the real values are
  supplied at runtime.
- The APK assets contain only the **server** CA (`assets/ca.crt`,
  `CN=linksfield.net`, expired 2025-04-15) — **no client certificate/key**.

So the client identity needed for the new mTLS API exists only inside the running
official app. Recovering it requires **dynamic instrumentation**, not static
analysis.

## Behaviour in the integration (v0.4.0+)

- `status 3` now maps to a dedicated `SaveFamilyUpgradeRequiredError`.
- During setup / polling the coordinator raises `ConfigEntryError` (a *permanent*
  failure) instead of an endless `ConfigEntryNotReady` retry loop.
- The config flow shows the `upgrade_required` error with a link to this document.

This does not restore connectivity — nothing can, from code alone — but it stops
the silent retry loop and tells the user exactly why.

## Recovery path (requires a rooted device / emulator running the official app)

The only way to make the integration work again is to capture what the live app
actually sends, then port it here. A ready-to-run Frida harness and a full runbook
are provided in [`docs/frida/`](frida/README.md):

- [`frida/capture_savefamily.js`](frida/capture_savefamily.js) — hooks the native
  `NativeUtils.getAppId/getVersion/getFlag` getters, dumps the **mTLS client
  certificate chain + private key** (via the Java `X509KeyManager`/`KeyStore`
  layer, with a native `libssl` fallback), and logs the real login request.
- [`frida/README.md`](frida/README.md) — prerequisites (rooted device/emulator,
  `frida-server`), how to run it, how to save `client-cert.pem` / `client-key.pem`,
  and how to wire the identity into `core/async_client.py`.

Summary of the steps:

1. On a rooted device/emulator, install the official **SaveFamily** app and run
   `frida -U -f com.tgelec.savefamily -l docs/frida/capture_savefamily.js --no-pause`,
   then log in so the TLS handshake and login request fire.
2. Copy the printed `appid`/`version`/`flag` into `core/protocol.py`.
3. Save the printed client certificate + private key to `client-cert.pem` /
   `client-key.pem` (or pull the bundled keystore whose password the hook prints).
4. Extend `core/async_client.py` to talk to the mTLS endpoint
   (`:11001`–`:11003`) with that certificate (`ssl.SSLContext.load_cert_chain`),
   update the base URLs / login endpoint to the captured request, make the cert
   path configurable, and relax the `ConfigEntryError` block once login works.

Track the upstream reference project
[Niek/yqt-smart-api](https://github.com/Niek/yqt-smart-api/issues/7), which hit
the identical wall on the same day — a community fix there will likely reveal the
new parameters for everyone.
