# SaveFamily server lockout (status 3 — "The current app is unavailable. Please upgrade the app.")

## Symptom

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
actually sends, then port it here.

1. Install the official **SaveFamily** app on a rooted Android device or emulator
   and log in successfully.
2. Attach [Frida](https://frida.re/) and dump the native credentials:

   ```javascript
   Java.perform(() => {
     const N = Java.use('com.tgelec.aqsh.utils.NativeUtils');
     console.log('appid   =', N.getAppId());
     console.log('version =', N.getVersion());
     console.log('flag    =', N.getFlag());
   });
   ```

3. Capture the actual login traffic (mitmproxy / Frida SSL-pinning bypass) to learn:
   - the **host and port** used (expected: `:11001`–`:11003`),
   - the **endpoint** and full parameter set,
   - the **client certificate + private key** presented in the TLS handshake.
4. Once the client certificate/key and the new request format are known, the API
   client (`custom_components/savefamily/core/async_client.py`) must be extended
   to talk to the mTLS endpoint with that certificate. The current
   `appid`/`version`/`flag` constants in `core/protocol.py` are then whatever
   step 2 reported.

Track the upstream reference project
[Niek/yqt-smart-api](https://github.com/Niek/yqt-smart-api/issues/7), which hit
the identical wall on the same day — a community fix there will likely reveal the
new parameters for everyone.
