# Frida capture runbook — recovering the SaveFamily mTLS client identity

*[Français ci-dessous](#procédure-de-capture-frida)*

> **Historical — not needed since v0.4.1.** The mTLS client certificate and the
> encrypted transport were ported from [yqt-smart-api](https://github.com/Niek/yqt-smart-api)
> and now ship with the integration (`core/client.pem`, `core/transport.py`). This
> runbook is kept in case the server changes its client identity again.
> It is also the way to confirm, on the decrypted SaveFamily code, the features
> described in [../REVERSE_ENGINEERING.md](../REVERSE_ENGINEERING.md).

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

---
---

# Procédure de capture Frida

Objectif : récupérer l'identité client mTLS de SaveFamily.

> **Historique, inutile depuis la v0.4.1.** Le certificat client mTLS et le
> transport chiffré ont été portés depuis [yqt-smart-api](https://github.com/Niek/yqt-smart-api)
> et sont livrés avec l'intégration (`core/client.pem`, `core/transport.py`). Cette
> procédure est conservée au cas où le serveur changerait encore d'identité client.
> Elle sert aussi à confirmer, sur le code SaveFamily déchiffré, les fonctions
> décrites dans [../REVERSE_ENGINEERING.md](../REVERSE_ENGINEERING.md).

Ce dossier contient tout le nécessaire pour l'étape de récupération décrite dans
[../SERVER_LOCKOUT.md](../SERVER_LOCKOUT.md). Elle **ne peut pas** se faire depuis
une machine de CI ou de développement : il faut un appareil Android rooté ou un
émulateur rooté qui exécute réellement l'app officielle SaveFamily. Lire d'abord
le document sur la cause.

## Ce que l'on extrait

1. **Les constantes natives de connexion** : `appid`, `version`, `flag` (générées
   dans `libnative-lib.so`, remplacées par des bouchons dans l'APK statique).
2. **L'identité client mTLS** : la **chaîne de certificats et la clé privée** que
   l'app présente à `europe.myaqsh.com:11001-11003`. C'est le vrai point bloquant :
   la nouvelle API rejette tout client qui ne l'a pas
   (`400 No required SSL certificate was sent`).
3. **La vraie requête de connexion** : hôte, port, chemin et paramètres utilisés
   par l'app actuelle.

## Prérequis

- Un appareil Android ou un émulateur **rooté** (image Google APIs, *pas* Play
  Store, pour pouvoir le rooter). Exemple d'AVD :
  `system-images;android-30;google_apis;x86_64`.
- L'app officielle **SaveFamily** installée (`com.tgelec.savefamily`) et un compte
  valide.
- Sur l'hôte : `pip install frida-tools` (même version que le frida-server ci-dessous).
- `frida-server` pour l'ABI de l'appareil, copié et lancé en root :

  ```bash
  adb root && adb push frida-server-XX.X.X-android-arm64 /data/local/tmp/frida-server
  adb shell "chmod 755 /data/local/tmp/frida-server && /data/local/tmp/frida-server &"
  ```

## Lancer la capture

```bash
frida -U -f com.tgelec.savefamily -l capture_savefamily.js --no-pause
```

Puis **se connecter dans l'app** et surveiller la console :

- `NativeUtils.getAppId()/getVersion()/getFlag() = ...` → recopier ces valeurs dans
  [`../../custom_components/savefamily/core/protocol.py`](../../custom_components/savefamily/core/protocol.py)
  (`DEFAULT_APP_ID`, `DEFAULT_CLIENT_VERSION`, `DEFAULT_CLIENT_FLAG`).
- `[tls ] client certificate chain:` → enregistrer le ou les blocs
  `-----BEGIN CERTIFICATE-----` dans `client-cert.pem`.
- `[tls ] client private key:` → enregistrer le bloc `-----BEGIN PRIVATE KEY-----`
  dans `client-key.pem`. Si le message indique *« non-extractable
  (hardware-backed) »*, la clé est dans le KeyStore Android : regarder les lignes
  `[ks ]`. L'identité est probablement chargée depuis un `.p12`/`.bks` embarqué
  dont le mot de passe est affiché par l'interception de `KeyStore.load` ;
  récupérer cet asset et l'ouvrir avec ce mot de passe.
- `[http] ... POST https://...:1100x/...` → le vrai endpoint et le corps de la
  requête de connexion.

### Si l'app se ferme au lancement (anti-Frida / packer Baidu)

- Préférer le lancement par Frida (`-f`) pour que les interceptions soient en
  place avant que le packer restaure les classes.
- Essayer d'abord une version **plus ancienne** de SaveFamily (moins de
  protections) pour obtenir le certificat client : l'identité mTLS est en général
  stable d'une version à l'autre, même si `version` change.
- Envisager un reconditionnement avec `objection`/`frida-gadget`, un module Magisk
  « MagiskFrida », ou un script anti-anti-Frida (interception de `ptrace`, du
  TracerPid de `/proc/self/status`, etc.).

## Brancher le résultat dans l'intégration

Une fois `client-cert.pem` + `client-key.pem` et le nouvel endpoint obtenus :

1. Dans [`core/async_client.py`](../../custom_components/savefamily/core/async_client.py),
   créer un `ssl.SSLContext` qui charge l'identité client et l'utiliser pour les
   requêtes vers l'hôte mTLS :

   ```python
   import ssl
   ctx = ssl.create_default_context()
   ctx.load_cert_chain(certfile="client-cert.pem", keyfile="client-key.pem")
   # aiohttp : connector = aiohttp.TCPConnector(ssl=ctx)
   ```

2. Faire pointer les URL de base vers le nouveau port et mettre à jour l'endpoint
   et les paramètres de connexion d'après la requête capturée.
3. Mettre à jour `DEFAULT_APP_ID` / `DEFAULT_CLIENT_VERSION` /
   `DEFAULT_CLIENT_FLAG` dans `core/protocol.py` avec les valeurs extraites.
4. Rendre le chemin du certificat client configurable dans l'assistant de
   configuration, et assouplir la gestion `SaveFamilyUpgradeRequiredError` →
   `ConfigEntryError` une fois la connexion rétablie.

## Légalité et périmètre

Cette procédure sert uniquement à rétablir l'interopérabilité avec **votre propre**
compte et **vos propres** appareils. N'utilisez pas les identifiants capturés
contre des comptes ou appareils qui ne vous appartiennent pas.
