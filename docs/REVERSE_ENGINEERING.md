# SaveFamily app analysis (v0.6.0)

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
