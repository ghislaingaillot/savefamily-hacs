# SaveFamily Connect backend

*[Français ci-dessous](#backend-savefamily-connect)*

## Classic and Connect

SaveFamily runs two separate platforms, each with its own app:

| | Classic | Connect |
|---|---|---|
| App | SaveFamily Classic (`com.tgelec.savefamily`) | SaveFamily Connect (`com.savefamily.app`, Grupo SaveFamily SL) |
| Backend | LinksField/tgelec, `*.myaqsh.com` | `https://api-platform.pro.savefamily.app/gateway/api` |
| Protocol | Mutual TLS, AES-CBC envelope, signed form parameters | Plain JSON over HTTPS |
| Login | Account + password, per region | Email + password, then a code sent by email |

A watch belongs to one platform only. A Connect watch cannot be reached through the Classic client: every `myaqsh.com` region rejects the account, and the Classic app itself says the watch works with the newer app. That is why the config flow starts by asking which app the watch uses.

## How the Connect API was mapped

The Connect app is written in Flutter (Dart compiled ahead of time in `libapp.so`), with no certificate pinning, no mutual TLS and no bundled client certificate. The endpoint paths come from the strings in the app binary and from the JavaScript of the SaveFamily web portal (`portal.savefamily.app`), which calls the same API. Every request below was then checked live against a real account and watch.

## Authentication

1. `POST /auth/login` with `{email, password}` returns a short-lived 2FA session `token` and the `availableMethods` (on the account tested: `EMAIL`, the default).
2. `POST /auth/2fa/request-code` with `{token, methodType}` emails a code. The response body is a bare `OK`, not JSON.
3. `POST /auth/twofa/login` with `{token, code, methodType, platform: "web", rememberMe: true}` returns `{accessToken, refreshToken, tokenType, expiresIn, sessionExpiresAt}`. `platform` must be lowercase `web`; `WEB` is rejected with 403.
4. Authenticated requests send the access token as the `token` cookie. The gateway ignores an `Authorization: Bearer` header on its own; the integration sends both.
5. `POST /auth/refresh` with the JSON body `{refreshToken}` returns the same shape as step 3. Without that body the server answers 500.

The access token lasts 15 minutes (`expiresIn: 900`). The refresh token is **rotated on every refresh**, each time with a new 30-day window. So after one login with the emailed code, the integration keeps the session alive indefinitely, as long as Home Assistant keeps polling and stores each new refresh token. If Home Assistant is off for more than 30 days, or the refresh token is rejected, the entry asks to reauthenticate: password, then a new emailed code. The server answers a rejected refresh token with HTTP 401; the integration treats any 4xx other than 408 and 429 the same way. The server does not invalidate a refresh token when it rotates it: in tests, a token already used still refreshed 10 minutes after the rotation, and reusing it did not end the session. A token that had never been used was refused a few minutes after a newer one had been issued from an older token, so the integration always stores the newest one at once. A refresh and the storage of its new token run together and are not interrupted by an unload or a shutdown, which wait for them.

The account API key feature (`/users/{id}/api-key`) is reserved for admin roles; a normal family account gets 401, so it cannot replace the login above.

## Data

- `GET /devices/` returns `{total, items, page, pages}`; `?page=N` selects a page, and the integration reads them all. Each item has `id` (UUID, used in other paths), `identificator` (the number shown in the app), `imei`, `battery`, `carrierName` (the child's name), `type` (model, e.g. `savewatch-2`), `protocol`, `lastConnection` (epoch ms), plus `settings`, `capabilities` and `flags`.
- `GET /devices/{id}/positions/latest` returns `{item}` with `latitude`, `longitude`, `hpe` (accuracy in metres), `steps`, `type` (`GPS`, `WIFI`, …), `positionDate` (epoch ms), `address`, and `networks` (nearby Wi-Fi SSIDs and BSSIDs).
- `GET /devices/{id}/positions?limit=N` returns the position history, paginated (not used yet).
- `wss://api-platform.pro.savefamily.app/websocket` pushes live updates (not used yet; the integration polls).

## In the integration

- `custom_components/savefamily/connect/client.py` implements the steps above and maps devices and positions to the same `SaveFamilyWatch` / `SaveFamilyWatchState` dataclasses as Classic, so the device tracker, battery, steps, last fix, stale-location and online entities work unchanged. The watch's device id is its `identificator`.
- The config entry stores the email, `backend: connect` and the current refresh token. **The password is not stored**; the setup form keeps it in memory only so that "Send me a new code" can request another code if the first one expires. The entry data is updated on every token rotation, so Connect entries do not reload when their data changes.
- A 401/403 on the device list triggers one token refresh and, if it persists, a reauthentication. A 401/403 on one watch's position does not: that watch keeps its last known state and the others still update.
- `networks`, `address` and `message` are dropped from positions before they reach Home Assistant, so nearby Wi-Fi networks never end up in entity attributes or the recorder. A latest position that is missing, has no coordinates or is at (0, 0) keeps the previous one, as on Classic.
- Connect entries load only `device_tracker`, `sensor` and `binary_sensor`. Classic-only features (settings, Do Not Disturb, SOS numbers, work mode, remote commands, photo wall, alarms, position history action) are not available on Connect yet; their actions answer with a clear "not supported on SaveFamily Connect" error. An action whose target reaches Connect watches only through an area, floor or label skips them; naming a Connect watch, or reaching only Connect watches, gives that error.

## Not done yet

- Alarms and SOS events, geofences, SOS contacts and position history over the Connect API.
- WebSocket push instead of polling.
- Requesting a fresh position from the watch.

---

# Backend SaveFamily Connect

## Classic et Connect

SaveFamily exploite deux plateformes distinctes, chacune avec son application :

| | Classic | Connect |
|---|---|---|
| Application | SaveFamily Classic (`com.tgelec.savefamily`) | SaveFamily Connect (`com.savefamily.app`, Grupo SaveFamily SL) |
| Backend | LinksField/tgelec, `*.myaqsh.com` | `https://api-platform.pro.savefamily.app/gateway/api` |
| Protocole | TLS mutuel, enveloppe AES-CBC, paramètres de formulaire signés | JSON simple sur HTTPS |
| Connexion | Compte + mot de passe, par région | Email + mot de passe, puis un code envoyé par email |

Une montre n'appartient qu'à une seule plateforme. Une montre Connect est inaccessible au client Classic : toutes les régions `myaqsh.com` refusent le compte, et l'application Classic indique elle-même que la montre fonctionne avec la nouvelle application. C'est pourquoi la configuration commence par demander quelle application utilise la montre.

## Comment l'API Connect a été cartographiée

L'application Connect est écrite en Flutter (Dart compilé à l'avance dans `libapp.so`), sans épinglage de certificat, sans TLS mutuel et sans certificat client embarqué. Les chemins des points d'accès proviennent des chaînes du binaire de l'application et du JavaScript du portail web SaveFamily (`portal.savefamily.app`), qui appelle la même API. Chaque requête ci-dessous a ensuite été vérifiée en direct avec un vrai compte et une vraie montre.

## Authentification

1. `POST /auth/login` avec `{email, password}` renvoie un `token` de session 2FA de courte durée et les `availableMethods` (sur le compte testé : `EMAIL`, par défaut).
2. `POST /auth/2fa/request-code` avec `{token, methodType}` envoie un code par email. Le corps de la réponse est un simple `OK`, pas du JSON.
3. `POST /auth/twofa/login` avec `{token, code, methodType, platform: "web", rememberMe: true}` renvoie `{accessToken, refreshToken, tokenType, expiresIn, sessionExpiresAt}`. `platform` doit être `web` en minuscules ; `WEB` est refusé avec une 403.
4. Les requêtes authentifiées envoient le jeton d'accès dans le cookie `token`. La passerelle ignore un en-tête `Authorization: Bearer` seul ; l'intégration envoie les deux.
5. `POST /auth/refresh` avec le corps JSON `{refreshToken}` renvoie la même forme qu'à l'étape 3. Sans ce corps, le serveur répond 500.

Le jeton d'accès dure 15 minutes (`expiresIn: 900`). Le jeton de rafraîchissement **change à chaque rafraîchissement**, avec à chaque fois une nouvelle fenêtre de 30 jours. Après une seule connexion avec le code reçu par email, l'intégration maintient donc la session indéfiniment, tant que Home Assistant continue d'interroger le serveur et conserve chaque nouveau jeton. Si Home Assistant reste éteint plus de 30 jours, ou si le jeton est refusé, l'entrée demande une nouvelle authentification : mot de passe, puis un nouveau code par email. Le serveur répond à un jeton refusé par une HTTP 401 ; l'intégration traite de même toute 4xx autre que 408 et 429. Le serveur n'invalide pas un jeton quand il le renouvelle : lors des tests, un jeton déjà utilisé fonctionnait encore 10 minutes après le renouvellement, et le réutiliser ne mettait pas fin à la session. Un jeton jamais utilisé a été refusé quelques minutes après qu'un jeton plus récent a été émis à partir d'un plus ancien : l'intégration enregistre donc toujours le plus récent immédiatement. Un rafraîchissement et l'enregistrement de son nouveau jeton s'exécutent ensemble et ne sont pas interrompus par un déchargement ou un arrêt, qui les attendent.

La fonction de clé d'API du compte (`/users/{id}/api-key`) est réservée aux rôles administrateurs ; un compte familial normal reçoit une 401, elle ne peut donc pas remplacer la connexion ci-dessus.

## Données

- `GET /devices/` renvoie `{total, items, page, pages}` ; `?page=N` choisit une page, et l'intégration les lit toutes. Chaque élément contient `id` (UUID, utilisé dans les autres chemins), `identificator` (le numéro affiché dans l'application), `imei`, `battery`, `carrierName` (le prénom de l'enfant), `type` (modèle, par ex. `savewatch-2`), `protocol`, `lastConnection` (epoch en ms), ainsi que `settings`, `capabilities` et `flags`.
- `GET /devices/{id}/positions/latest` renvoie `{item}` avec `latitude`, `longitude`, `hpe` (précision en mètres), `steps`, `type` (`GPS`, `WIFI`, …), `positionDate` (epoch en ms), `address` et `networks` (SSID et BSSID des réseaux Wi-Fi proches).
- `GET /devices/{id}/positions?limit=N` renvoie l'historique des positions, paginé (pas encore utilisé).
- `wss://api-platform.pro.savefamily.app/websocket` pousse les mises à jour en direct (pas encore utilisé ; l'intégration interroge le serveur).

## Dans l'intégration

- `custom_components/savefamily/connect/client.py` met en œuvre les étapes ci-dessus et convertit appareils et positions vers les mêmes dataclasses `SaveFamilyWatch` / `SaveFamilyWatchState` que Classic : le suivi de position, la batterie, les pas, la dernière position, la position périmée et l'état en ligne fonctionnent sans changement. L'identifiant de la montre est son `identificator`.
- L'entrée de configuration conserve l'email, `backend: connect` et le jeton de rafraîchissement courant. **Le mot de passe n'est pas conservé** ; le formulaire de configuration le garde seulement en mémoire pour que « M'envoyer un nouveau code » puisse demander un autre code si le premier expire. Les données de l'entrée sont mises à jour à chaque rotation du jeton ; les entrées Connect ne se rechargent donc pas quand leurs données changent.
- Une 401/403 sur la liste des appareils déclenche un rafraîchissement du jeton et, si elle persiste, une nouvelle authentification. Une 401/403 sur la position d'une montre, non : cette montre garde son dernier état connu et les autres continuent d'être mises à jour.
- `networks`, `address` et `message` sont retirés des positions avant d'arriver dans Home Assistant : les réseaux Wi-Fi proches ne se retrouvent jamais dans les attributs des entités ni dans l'historique. Une dernière position absente, sans coordonnées ou en (0, 0) garde la précédente, comme sur Classic.
- Les entrées Connect ne chargent que `device_tracker`, `sensor` et `binary_sensor`. Les fonctions propres à Classic (réglages, Ne pas déranger, numéros SOS, mode de fonctionnement, commandes à distance, mur de photos, alarmes, action d'historique des positions) ne sont pas encore disponibles sur Connect ; leurs actions répondent par une erreur claire « non pris en charge sur SaveFamily Connect ». Une action dont la cible n'atteint des montres Connect que par une pièce, un étage ou une étiquette les ignore ; nommer une montre Connect, ou n'atteindre que des montres Connect, donne cette erreur.

## Reste à faire

- Alarmes et événements SOS, zones de sécurité, contacts SOS et historique des positions via l'API Connect.
- Notifications WebSocket au lieu de l'interrogation périodique.
- Demander une nouvelle position à la montre.
