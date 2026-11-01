# SaveFamily app analysis (v0.6.0)

*[Français ci-dessous](#analyse-de-lapplication-savefamily-v060)*

How the features added in v0.6.0 were derived, and how much of each one has been
checked. Protocol basics (mutual TLS, encrypted envelope, signature) are in
[yqt-smart-api's REVERSE_ENGINEERING.md](https://github.com/Niek/yqt-smart-api/blob/main/REVERSE_ENGINEERING.md)
and apply unchanged.

## Sources

| APK | Version | Notes |
| --- | --- | --- |
| `com.tgelec.savefamily` | 1.4.0 (41) | Packed with Baidu Protect (`com.sagittarius.v6.StubApplication`, `assets/baiduprotect*.jar`). The real DEX is decrypted at runtime; the shipped `baiduprotect*.i.dex` are empty skeletons. Only the stub DEX (activity names) and resources are readable statically. |
| `com.tgelec.yqtsmart` | 1.1.5 (16) | Same `com.tgelec.aqsh` code base on the same backend, not packed. All endpoint, command and model details below come from this APK. |

The SaveFamily stub DEX declares the same feature activities as YQT SMART
(`SOSActivity`, `WorkModeActivity`, `LookForWatchActivity`, `PhotoWallActivity`,
`SafeZoneActivity`, `MonitorActivity`, `RemoteShutdownActivity`, `TrailActivity`,
`MessageCenterActivity`, ...), which is why the YQT findings are assumed to apply.
Confirming that against the decrypted SaveFamily DEX needs a runtime dump (rooted
device + Frida), see [frida/](frida/).

The DEX files were read with small stdlib-only scripts (Retrofit annotations,
per-method `const-string`/`invoke` cross references, model fields and static
values); no third-party decompiler was used.

## Findings used by the integration

| Feature | Request | Evidence |
| --- | --- | --- |
| Alarms | `GET /app/{sid}/S10APP/v2_findAlarmInfo` (`did`, `did_id`, `flag=0`, `count=20`, `createtime=`) | Endpoint legacy-live upstream. Type codes from `AlarmInfo.TYPE_*`: 1 SOS, 2 low battery, 3 left zone, 4 entered zone, 5 removed, 6 fall, 7/8 home-school arrival/departure, 9 auth code, 10 mode change. |
| GSM signal | `POST /S10APP/findDeviceGSMSignal` (`user_id`, `did`) | APK only. `GSMSingalResponse.signal` is an int; its scale is unknown. |
| Settings | `GET /app/{sid}/S10APP/v2_findSetInfo` | Already used for DND. `SettingResponse.parseSetting` treats `*open == 2` as enabled. |
| Work mode | `v2_sendOrder` `D1` (`param1=<seconds>`, `dev_model`), then `GET v2_upSetInfo` (`id`, `upinterval`) | APK. `WorkModeActivity` uses 60 / 600 / 3600 s; 600 is `Setting.DEFAULT_UPINTERVAL`. The app sends the command first and persists on success. |
| SOS numbers | `v2_sendOrder` `D7` (`param1..5`), then `GET v2_upSetInfo` (`id`, `sosnumber1..5`) | APK, same command-then-persist order (`sos/a.B2`). |
| Find watch | `v2_sendOrder` `D12` with `dev_model` | APK (`LookForWatchActivity` → `a4/a.w3`). |
| Remote photo | `v2_sendOrder` `D75` | APK (`PhotoWallPresenter.S3`, `D134` is the front camera variant). |
| Restart / power off | `v2_sendOrder` `D2` / `D17` with `dev_model` | APK (`RemoteShutdownActivity` presenter). `D160` (factory reset) is deliberately not exposed. |
| Listen in | `v2_sendOrder` `D18` (`param1=<phone>`) | APK (`MonitorActivity`). |
| Safe zones | `GET /app/{sid}/S10APP/v2_new_findRailInfo` (`did`, `did_id`, `loginname`) | APK. `SafeZone.STATUS_OPEN = 1`, radius 200–2000 m. |
| Photo wall | `GET v2_findPictrueDoorInfo` (`did`, `max_id=0`), `GET v2_downloadPictrueDoor` (`did`, basename) | Legacy-live upstream; download over the encrypted transport is unverified. |
| Position history | `GET /app/{sid}/S10APP/v2_findPositionRecordByOneday` (`ksdate`, `jsdate` as `yyyy-MM-dd HH:mm`, `checkWifiOpen`) | APK. The app formats phone-local times without a UTC conversion; `checkWifiOpen=1` is a guess. |

Only the `D3` location request and the DND read/write had been confirmed live
before this version. Everything else above is static analysis, to be confirmed
with a real account (enable debug logging; failures of the new reads are logged
and do not break location updates).

## Seen in the APK, not implemented

- SMS alert switches (`batteryopen`/`sosopen`/`removeopen` + `centernumber`):
  the app also sends `D5`/`D10`/`D19`/`D28`, and which command maps to which
  setting is not established.
- Device switches (`v2_findDeviceSwitch` / `v2_upDeviceSwitch`, `type_status`
  as `type-status` pairs): most `DeviceSwitch.TYPE_*` meanings are unknown.
- Alarm clocks (`v2_findClockInfo`/`v2_upClockInfo`, `dev_clockV2`), class
  schedule, scheduled power on/off (`onoff_*`), phone book and whitelist,
  volume (`D239`), hearts reward (`D23`), time sync (`D280`), chat
  (multipart, unverified on the current transport), video calls, app store and
  watch faces.

---
---

# Analyse de l'application SaveFamily (v0.6.0)

Comment les fonctions ajoutées en v0.6.0 ont été obtenues, et jusqu'où chacune a
été vérifiée. Les bases du protocole (TLS mutuel, enveloppe chiffrée, signature)
sont décrites dans le
[REVERSE_ENGINEERING.md de yqt-smart-api](https://github.com/Niek/yqt-smart-api/blob/main/REVERSE_ENGINEERING.md)
et s'appliquent telles quelles.

## Sources

| APK | Version | Remarques |
| --- | --- | --- |
| `com.tgelec.savefamily` | 1.4.0 (41) | Protégé par Baidu Protect (`com.sagittarius.v6.StubApplication`, `assets/baiduprotect*.jar`). Le vrai DEX n'est déchiffré qu'à l'exécution ; les `baiduprotect*.i.dex` livrés sont des squelettes vides. Seuls le DEX d'amorce (noms des écrans) et les ressources sont lisibles en statique. |
| `com.tgelec.yqtsmart` | 1.1.5 (16) | Même base de code `com.tgelec.aqsh`, même serveur, non protégé. Tous les détails d'endpoints, de commandes et de modèles ci-dessous viennent de cet APK. |

Le DEX d'amorce de SaveFamily déclare les mêmes écrans que YQT SMART
(`SOSActivity`, `WorkModeActivity`, `LookForWatchActivity`, `PhotoWallActivity`,
`SafeZoneActivity`, `MonitorActivity`, `RemoteShutdownActivity`, `TrailActivity`,
`MessageCenterActivity`…), d'où l'hypothèse que les résultats YQT s'appliquent.
Le confirmer sur le DEX SaveFamily déchiffré demande un dump à l'exécution
(appareil rooté + Frida), voir [frida/](frida/).

Les fichiers DEX ont été lus avec de petits scripts Python sans dépendance
(annotations Retrofit, références croisées `const-string`/`invoke` par méthode,
champs et valeurs statiques des modèles) ; aucun décompilateur tiers n'a été utilisé.

## Résultats utilisés par l'intégration

| Fonction | Requête | Niveau de preuve |
| --- | --- | --- |
| Alarmes | `GET /app/{sid}/S10APP/v2_findAlarmInfo` (`did`, `did_id`, `flag=0`, `count=20`, `createtime=`) | Endpoint testé en réel (ancien transport) en amont. Codes de type issus de `AlarmInfo.TYPE_*` : 1 SOS, 2 batterie faible, 3 sortie de zone, 4 entrée dans une zone, 5 montre retirée, 6 chute, 7/8 arrivée/départ maison-école, 9 code d'autorisation, 10 changement de mode. |
| Signal GSM | `POST /S10APP/findDeviceGSMSignal` (`user_id`, `did`) | APK uniquement. `GSMSingalResponse.signal` est un entier ; son échelle est inconnue. |
| Réglages | `GET /app/{sid}/S10APP/v2_findSetInfo` | Déjà utilisé pour le Ne pas déranger. `SettingResponse.parseSetting` considère `*open == 2` comme activé. |
| Mode de fonctionnement | `v2_sendOrder` `D1` (`param1=<secondes>`, `dev_model`), puis `GET v2_upSetInfo` (`id`, `upinterval`) | APK. `WorkModeActivity` utilise 60 / 600 / 3600 s ; 600 est `Setting.DEFAULT_UPINTERVAL`. L'app envoie d'abord la commande, puis enregistre la valeur en cas de succès. |
| Numéros SOS | `v2_sendOrder` `D7` (`param1..5`), puis `GET v2_upSetInfo` (`id`, `sosnumber1..5`) | APK, même ordre commande puis enregistrement (`sos/a.B2`). |
| Faire sonner la montre | `v2_sendOrder` `D12` avec `dev_model` | APK (`LookForWatchActivity` → `a4/a.w3`). |
| Photo à distance | `v2_sendOrder` `D75` | APK (`PhotoWallPresenter.S3` ; `D134` est la variante caméra frontale). |
| Redémarrer / éteindre | `v2_sendOrder` `D2` / `D17` avec `dev_model` | APK (présentateur de `RemoteShutdownActivity`). `D160` (remise à zéro d'usine) n'est volontairement pas exposé. |
| Écoute à distance | `v2_sendOrder` `D18` (`param1=<téléphone>`) | APK (`MonitorActivity`). |
| Zones de sécurité | `GET /app/{sid}/S10APP/v2_new_findRailInfo` (`did`, `did_id`, `loginname`) | APK. `SafeZone.STATUS_OPEN = 1`, rayon de 200 à 2000 m. |
| Mur de photos | `GET v2_findPictrueDoorInfo` (`did`, `max_id=0`), `GET v2_downloadPictrueDoor` (`did`, nom de fichier seul) | Testé en réel (ancien transport) en amont ; le téléchargement via le transport chiffré n'est pas vérifié. |
| Historique des positions | `GET /app/{sid}/S10APP/v2_findPositionRecordByOneday` (`ksdate`, `jsdate` au format `yyyy-MM-dd HH:mm`, `checkWifiOpen`) | APK. L'app envoie l'heure locale du téléphone sans conversion UTC ; `checkWifiOpen=1` est une supposition. |

Avant cette version, seules la demande de position `D3` et la lecture/écriture du
Ne pas déranger avaient été confirmées en réel. Tout le reste du tableau vient de
l'analyse statique et reste à confirmer avec un vrai compte (activer les logs de
debug ; les échecs des nouvelles lectures sont journalisés et ne bloquent pas la
position).

## Vu dans l'APK, non implémenté

- Alertes SMS (`batteryopen`/`sosopen`/`removeopen` + `centernumber`) : l'app
  envoie aussi `D5`/`D10`/`D19`/`D28`, et la correspondance commande ↔ réglage
  n'est pas établie.
- Interrupteurs de la montre (`v2_findDeviceSwitch` / `v2_upDeviceSwitch`,
  `type_status` sous forme de paires `type-statut`) : la plupart des
  `DeviceSwitch.TYPE_*` ont une signification inconnue.
- Réveils (`v2_findClockInfo`/`v2_upClockInfo`, `dev_clockV2`), emploi du temps,
  allumage/extinction programmés (`onoff_*`), répertoire et liste blanche, volume
  (`D239`), cœurs de récompense (`D23`), synchronisation de l'heure (`D280`),
  messagerie (multipart, non vérifiée sur le transport actuel), appels vidéo,
  magasin d'applications et cadrans.
