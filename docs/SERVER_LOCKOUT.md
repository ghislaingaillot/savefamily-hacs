# SaveFamily server lockout (status 3 — "The current app is unavailable. Please upgrade the app.")

*[Français ci-dessous](#blocage-du-serveur-savefamily-statut-3)*

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

In v0.4.0 this did not restore connectivity, but it stopped the silent retry loop
and told the user why. Since v0.4.1 login works again (see the top of this
document); a `status 3` now means the server protocol changed again and the
integration needs an update.

## Recovery path (historical — not needed since v0.4.1)

> This section documents the plan considered before the upstream fix. The mTLS
> client identity now ships with the integration (`core/client.pem`), so none of
> these steps are required.

Before v0.4.1, the only way to make the integration work again was to capture what
the live app actually sends, then port it here. A ready-to-run Frida harness and a full runbook
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

---
---

# Blocage du serveur SaveFamily (statut 3)

Message du serveur : *« The current app is unavailable. Please upgrade the app. »*

## Résolu en v0.4.1

Le projet de référence [yqt-smart-api](https://github.com/Niek/yqt-smart-api) a
retrouvé le nouveau protocole (commit
[`5ee1a46`](https://github.com/Niek/yqt-smart-api/commit/5ee1a46)) : l'API réelle
est passée sur des endpoints à TLS mutuel (`:11001`–`:11003`), avec une enveloppe
JSON chiffrée en AES-CBC autour des paramètres signés habituels. L'intégration
embarque désormais le certificat client correspondant (`core/client.pem`) et le
transport de chiffrement/déchiffrement (`core/transport.py`), portés directement
depuis ce correctif. Il n'a finalement pas été nécessaire d'extraire
`appid`/`version`/`flag` de l'APK ; la suite de ce document retrace l'enquête.

## Symptôme (v0.3.x et antérieures)

Home Assistant n'arrive pas à configurer l'intégration et réessaie sans fin avec :

```
status=3: API version rejected by server (update the integration):
The current app is unavailable. Please upgrade the app.
```

Depuis la **v0.4.0**, l'intégration ne réessaie plus indéfiniment dans ce cas :
elle lève une erreur permanente et explicative (voir « Comportement de
l'intégration » ci-dessous).

## Cause (vérifiée sur le serveur réel le 2026-07-24)

Ce n'est **pas un bug de l'intégration**, et **changer une constante de version
ne suffit pas**. Le backend LinksField/tgelec derrière SaveFamily (`*.myaqsh.com`)
a fermé le chemin de connexion rétro-ingénié. Constats sur
`https://europe.myaqsh.com:8093/app/public/S10APP/v2_new_userLogin2` :

1. **Le champ `version` est le seul verrou, et toutes les valeurs sont refusées.**
   - `version <= 1.0.1` → `status 3`, message *« The old version have stopped… »*,
     avec une URL de mise à jour (`com.tgelec.yqtsmart`).
   - **toute autre valeur** (`1.0.2`, `1.0.3`, `1.1.5`, `1.3.9`, `2.0.0`, le
     versionCode `40` de l'APK, des dates…) → `status 3`, message *« The current
     app is unavailable. Please upgrade the app. »*, avec un champ `version` **vide**.
   - Le 2026-07-06, `version=1.0.2` fonctionnait encore ; le blocage a été durci ensuite.
2. **`appid` est totalement ignoré** à ce stade. `aaagg11145` (la valeur livrée
   par l'intégration), une chaîne vide ou n'importe quoi donnent la même réponse.
   `aaagg11145` est en fait l'identifiant de l'app **YQT SMART**
   (`com.tgelec.yqtsmart`), pas de SaveFamily : les deux sont des marques blanches
   du même backend.
3. **La signature n'est pas en cause.** Un `sign` volontairement faux donne le même
   `status 3`, et une requête `version=1.0.1` correctement signée est bien analysée
   et contrôlée : `sign` / `sign_flag=KHDIW` sont donc toujours acceptés.
4. **L'API réelle est passée derrière du TLS mutuel.** Les ports `11001`, `11002`
   et `11003` des mêmes serveurs répondent `HTTP 400 "No required SSL certificate
   was sent"` (nginx `ssl_verify_client on`). L'app officielle à jour s'y
   authentifie avec un **certificat client**.

### Pourquoi le certificat client ne peut pas être extrait en statique

L'APK actuel (`com.tgelec.savefamily` 1.3.9, versionCode 40) est :

- **protégé par Baidu** (`assets/baiduprotect*.jar` / `*.i.dex`) : le vrai code
  Dalvik et ses chaînes sont chiffrés et ne sont restaurés qu'à l'exécution ;
- ses identifiants de connexion sont générés en **code natif** : `libnative-lib.so`
  exporte `NativeUtils.getAppId()`, `getVersion()`, `getFlag()`, ainsi que
  `encrypt()`/`decrypt()` et une clé littérale `ThisIsMySecretNativeKey123`. Dans
  le `.so` livré, ces fonctions sont des **bouchons** du packer (`getFlag` =
  `mov w0,#0; ret` ; `getAppId`/`getVersion` renvoient une chaîne vide) : les
  vraies valeurs sont fournies à l'exécution ;
- les assets de l'APK ne contiennent que le CA **serveur** (`assets/ca.crt`,
  `CN=linksfield.net`, expiré le 2025-04-15), **aucun certificat ni clé client**.

L'identité client nécessaire à la nouvelle API mTLS n'existe donc que dans l'app
officielle en cours d'exécution. La récupérer demande de l'**instrumentation
dynamique**, pas de l'analyse statique.

## Comportement de l'intégration (v0.4.0+)

- Le `status 3` correspond maintenant à une `SaveFamilyUpgradeRequiredError` dédiée.
- Pendant la configuration ou l'interrogation, le coordinateur lève une
  `ConfigEntryError` (échec *permanent*) au lieu d'une boucle infinie de
  `ConfigEntryNotReady`.
- L'assistant de configuration affiche l'erreur `upgrade_required` avec un lien
  vers ce document.

En v0.4.0, cela ne rétablissait pas la connexion, mais arrêtait la boucle
silencieuse et expliquait la cause à l'utilisateur. Depuis la v0.4.1, la connexion
fonctionne à nouveau (voir le début du document) ; un `status 3` signifie
désormais que le protocole du serveur a encore changé et que l'intégration doit
être mise à jour.

## Piste de récupération (historique, inutile depuis la v0.4.1)

> Cette section décrit le plan envisagé avant le correctif amont. L'identité
> client mTLS est maintenant livrée avec l'intégration (`core/client.pem`) : aucune
> de ces étapes n'est nécessaire.

Avant la v0.4.1, la seule façon de refaire fonctionner l'intégration était de
capturer ce qu'envoie réellement l'app, puis de le porter ici. Un script Frida
prêt à l'emploi et une procédure complète sont fournis dans
[`docs/frida/`](frida/README.md) :

- [`frida/capture_savefamily.js`](frida/capture_savefamily.js) : intercepte les
  fonctions natives `NativeUtils.getAppId/getVersion/getFlag`, extrait la **chaîne
  de certificats et la clé privée du client mTLS** (via la couche Java
  `X509KeyManager`/`KeyStore`, avec repli sur `libssl` natif) et journalise la
  vraie requête de connexion.
- [`frida/README.md`](frida/README.md) : prérequis (appareil ou émulateur rooté,
  `frida-server`), exécution, enregistrement de `client-cert.pem` /
  `client-key.pem`, et intégration de l'identité dans `core/async_client.py`.

Résumé des étapes :

1. Sur un appareil ou émulateur rooté, installer l'app officielle **SaveFamily**,
   lancer
   `frida -U -f com.tgelec.savefamily -l docs/frida/capture_savefamily.js --no-pause`,
   puis se connecter pour déclencher la poignée de main TLS et la requête de connexion.
2. Recopier les `appid`/`version`/`flag` affichés dans `core/protocol.py`.
3. Enregistrer le certificat et la clé privée affichés dans `client-cert.pem` /
   `client-key.pem` (ou récupérer le keystore embarqué dont le mot de passe est affiché).
4. Adapter `core/async_client.py` pour dialoguer avec l'endpoint mTLS
   (`:11001`–`:11003`) avec ce certificat (`ssl.SSLContext.load_cert_chain`),
   mettre à jour les URL et l'endpoint de connexion d'après la requête capturée,
   rendre le chemin du certificat configurable, et lever le blocage
   `ConfigEntryError` une fois la connexion rétablie.

Suivre le projet de référence
[Niek/yqt-smart-api](https://github.com/Niek/yqt-smart-api/issues/7), qui a heurté
le même mur le même jour : un correctif communautaire y révélera probablement les
nouveaux paramètres pour tout le monde.
