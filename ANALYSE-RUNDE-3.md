# Analyse Runde 3 — Detail-Audit des gesamten Repos

> **Stand:** 2026-07-19/20, Baseline `v2.10.0` (main, 243 Unit-Tests grün, CI 7/7 grün)
> **Zweck:** Arbeitsgrundlage für die Umsetzung. Jeder Befund ist so beschrieben, dass er **ohne erneute Recherche** umgesetzt werden kann: Fundstelle, Failure-Szenario, Root Cause, konkreter Fix-Vorschlag, Test-Vorschlag, Aufwand.
> **Methode:** 6 parallele Finder-Agenten (Domänen-Split), Kandidaten anschließend verifiziert (Verdikte unten: CONFIRMED = Failure-Pfad im Code nachvollzogen bzw. empirisch reproduziert; PLAUSIBLE = realistisch, hängt an Laufzeit-Timing). Ground Truth: **installierte** HA 2026.7.x, eltako14bus 0.0.73, esp2_gateway_adapter 0.2.21 (alle in `.venv`).
> ⚠️ **Reduzierter Umfang:** Die Finder für Domäne 5 (init/config_flow/schema/config_helpers) und Domäne 6 (HA-Konformitäts-/Deprecation-Sweep, Tests-Fidelity, CI/Meta) brachen wegen des Org-Kostenlimits ab; es lief nur ein Inline-Mini-Pass (Ergebnis: `async_get_home_assistant_config` nutzt HAs `async_integration_yaml_config` → **kein** Blocking-I/O-Problem; `strenum`-Befund R3-26). **Ein vollständiger Pass über diese zwei Domänen steht noch aus** (Kandidaten: entity_id-Slug-Regeln bei manueller Zuweisung, Deprecation-Sweep aller HA-Imports, strings/translations-Parität, Workflows, Mock-Fidelity-Sweep).

---

## 0. Arbeitsanweisungen für die Umsetzung

**Umgebung/Kommandos:**
- Tests: `./.venv/Scripts/python.exe -m pytest tests/ -q` (Baseline: 243 grün; Py 3.14, HA 2026.7.x)
- Statische Analyse: `./.venv/Scripts/python.exe -m ruff check custom_components/eltako/` (Baseline-Rauschen: nur F403/F405 durch Star-Imports; **neue Dateien müssen komplett sauber sein**)
- Der Testlauf regeneriert `docs/service-send-message/eep-params.md` → **vor jedem Commit zurücksetzen**.

**Prozessregeln (etabliert, einhalten):**
1. Ein Thema pro Release; Branch → Fix → Tests → **adversariale Review (Finder + Verifier)** → Doku (`changes.md`, hier abhaken, ggf. `KI-Optimierungen.md`) → `manifest.json` bumpen → Merge `--no-ff` → Tag → Push.
2. **Explizit stagen, nie `git add -A`.** Nur FMDHET-Fork, nie grimmpp-Upstream.
3. CI-Verifikation über GitHub-REST-API (unauthentifiziert; kein `gh`).
4. unique_id-/Registry-Änderungen: Bestandsschutz zuerst (Muster AS1 „erster behält die id" / AS3 Registry-Migration mit Guards via `async_migrate_entry`).
5. Wert-/Offset-Änderungen an Sende-Telegrammen → **Hardware-Session**. (Decode-only-Patches sind ohne Gerät ok — B5-Policy.)
6. Lib-Patches über `eltakobus_patches.py` (Muster inkl. `_ORIGINALS`-Drift-Guards); bei Änderungen an `tcp2serial_hardened.run()` den Baseline-Hash-Test **nicht** anfassen (er pinnt Upstream, nicht unsere Kopie) — aber Modul-Docstring aktualisieren.

---

## 1. Scope & Methode

Domänen: (1) climate+cover, (2) light+switch+select+button, (3) sensor+binary_sensor, (4) gateway+tcp2serial_hardened+VNG, (5) init/config/schema/base ⚠️abgebrochen, (6) Querschnitt ⚠️abgebrochen. Jeder Finder musste gegen installierte Quellen verifizieren und konkrete Failure-Szenarien nennen; bekannter Backlog war als Ausschlussliste eingebettet. Verifikation recall-biased.

## 2. Ausschlüsse — bekannter offener Backlog (hier NICHT doppelt aufgeführt)

Bereits dokumentiert (Quelle `KI-Optimierungen.md` §6/§7, `ANALYSE-UND-ROADMAP.md`): Climate-Cluster AC1–AC8, M14, F2–F7; AM1/AM2/AG2/AG4/AN2 (Routing → Welle C); AG1b, AG3, AG5; AN1; AM3 ⏸; AN3; AS4 + A5-04-03-**Encode** (Lib, ⏸ Hardware); AS5; AV1/AV2; V3; B6/B7/B8.
**Doku-Fix nebenbei:** `KI-Optimierungen.md` führte AF1 fälschlich noch als offen — mit diesem Commit korrigiert.

---

## 3. Befunde (verifiziert, nach Schwere)

### 🔴 KRITISCH

#### R3-01 — TCP-Reconnect-Livelock: Keepalive-Timestamp wird beim (Re-)Connect nie zurückgesetzt · CONFIRMED · ✅ ERLEDIGT v2.11.0
- **Fundstelle:** [tcp2serial_hardened.py:67-112](custom_components/eltako/tcp2serial_hardened.py#L67-L112) (Kopie) + geerbtes `_check_timeout_on_application_level` (`esp3_tcp_com.py:192-197`)
- **Problem:** `last_message_received` wird nur beim Thread-Start (Z. 67) und nach `recv` (Z. 112) gesetzt. Der geerbte App-Level-Keepalive-Check (läuft **vor** jedem recv, Z. 89) schließt die Verbindung, wenn `time.time()-last_message_received > tcp_keep_alive_timeout` (Default 30 s) — und setzt den Timestamp dabei auf **0**. Nach jedem Ausfall > 30 s gilt: connect → fire(True) → Check schlägt sofort zu → close → Exception → fire(False) → 15 s warten → dasselbe. **`recv` wird nie erreicht, nichts kann den Timestamp je auffrischen: permanentes Connect/Close-Flattern bis HA-Neustart oder manueller Reconnect** (der baut einen neuen Bus, dessen Thread-Start Z. 67 den Timestamp frisch setzt — deshalb „hilft" der Reconnect-Button).
- **Failure-Szenario:** mgw-lan wird 60 s stromlos. Ab Rückkehr flappt die Verbindung alle ~15 s ewig; alle Entities flappen verfügbar/unverfügbar (B1), kein Telegramm kommt je an, 1 Traceback pro Zyklus. Der Single-Client-Tasmota-Slot wird dabei ständig belegt/freigegeben.
- **Root Cause:** Upstream-0.2.21-Bug, beim Kopieren mit übernommen: kein Timestamp-Reset im Connect-Block.
- **Fix:** Im `if self._ser is None:`-Block nach `_enable_tcp_keepalive` (vor `fire(True)`): `self.last_message_received = time.time()  # HARDENED: fresh grace period per connection`. Optional zusätzlich `_check_timeout_on_application_level` überschreiben mit Guard `if self._ser is None: return`.
- **Test:** Fake-Socketserver; `tcp_keep_alive_timeout=0.5`, `reconnection_timeout=0.1`; 1 Frame liefern, > 0,5 s schweigen (Check schließt), Server weiter annehmen: ohne Fix alterniert der Status-Recorder endlos, mit Fix bleibt die 2. Verbindung stehen.
- **Aufwand:** S · **Hinweis:** Upstream-Issue wert (grimmpp/esp2_gateway_adapter), auf explizite Nutzer-Freigabe.

### 🟠 HOCH

#### R3-02 — Zerteilte ESP3-Frames gehen verloren: `self._buffer = data` überschreibt den Reassembly-Rest · CONFIRMED · ✅ ERLEDIGT v2.11.0
- **Fundstelle:** [tcp2serial_hardened.py:110](custom_components/eltako/tcp2serial_hardened.py#L110); Upstream identisch (`esp3_tcp_com.py:164`); Serial-Variante macht es richtig (`esp3_serial_com.py:221` `extend`)
- **Problem:** `Packet.parse_msg` legt unvollständige Frame-Reste absichtlich zurück in `self._buffer`; der TCP-Pfad **ersetzt** den Buffer aber bei jedem `recv`, statt anzuhängen. Jeder über zwei TCP-Segmente verteilte Frame ist verloren (Kopf überschrieben, Rest scheitert am Sync/CRC).
- **Failure-Szenario:** Die Tasmota-Bridge forwardet UART-Bytes poll-getaktet → ~21-Byte-Funk-Frames landen regelmäßig in 2 Segmenten. Ergebnis: sporadisch „verschluckte" Tastendrücke/Sensor-Updates ohne Log-Hinweis.
- **Fix:** `self._buffer = list(self._buffer) + list(data)` (bzw. extend-kompatibel zur `parse_msg`-Rückgabe; `# HARDENED` markieren). KEEP_ALIVE-Kurzschluss (`b'IM2M'`) bleibt.
- **Test:** Frame in `bytes[:9]` + `bytes[9:]` über zwei recv-Returns einspeisen → genau 1 konvertierte Nachricht (heute 0); zusätzlich `IM2M` zwischen den Fragmenten → Kopf bleibt erhalten.
- **Aufwand:** S (gleicher Release wie R3-01 sinnvoll: beide in derselben Datei/run()).

#### R3-03 — „Received Messages per Session"-Sensor wird nie angelegt: `suggested_unit_of_measurement="Messages"` → ValueError · CONFIRMED (empirisch reproduziert) · ✅ ERLEDIGT v2.11.0
- **Fundstelle:** [sensor.py:1051-1055](custom_components/eltako/sensor.py#L1051-L1055)
- **Problem:** `suggested_unit_of_measurement="Messages"` mit `native_unit=None`/ohne device_class: HAs `SensorEntity._is_valid_suggested_unit` wirft `ValueError` — **reproduziert** gegen installierte HA 2026.7 (`unit_of_measurement RAISES: ValueError … suggest an incorrect unit of measurement: Messages.`). `entity_platform` bricht das Hinzufügen **dieser** Entity mit „Error adding entity"-Traceback bei jedem Start ab. Das Legacy-Feld `unit_of_measurement="count"` daneben ist tot (wird von SensorEntity nie gelesen). Betrifft auch die Live-Installation des Nutzers (Sensor fehlt still).
- **Fix:** Beide Unit-Felder ersatzlos streichen (Zähler braucht keine Einheit); `state_class=TOTAL_INCREASING` + icon bleiben.
- **Test:** Regressionstest: `s.unit_of_measurement` und Entity-Add-Pfad werfen nicht (heute: ValueError).
- **Aufwand:** S

#### R3-04 — Climate: Thermostat-Telegramme erreichen die Entity nie (AddressExpression in bytes-Liste) · CONFIRMED · ✅ ERLEDIGT v2.11.0
- **Fundstelle:** [climate.py:153](custom_components/eltako/climate.py#L153) vs. [device.py:58+268](custom_components/eltako/device.py#L58)
- **Problem:** `listen_to_addresses.append(self.thermostat.id)` hängt die **AddressExpression** an; der Filter vergleicht aber `adr[0] in listen_to_addresses` mit **bytes** (device.py:268). bytes == AddressExpression ist immer False → **jedes Telegramm des Raumthermostats wird vor `value_changed` verworfen** — für jede Adressform. Tests treffen es nicht, weil sie `value_changed` direkt aufrufen. (Erklärt sehr wahrscheinlich das bekannte Symptom „Climate aktualisiert nicht" — schärfer als der F5-Verdacht „externes Adress-Matching", und ohne Hardware fixbar. AC8 — falsches Decode-EEP — liegt dahinter und bleibt separat offen.)
- **Fix:** `self.listen_to_addresses.append(self.thermostat.id[0])`; zusätzlich für lokale Thermostat-IDs die Externalisierung spiegeln (`…id.add(self.gateway.base_id)[0]` wenn `is_local_address()` — wie device.py:52-58).
- **Test:** `cc._message_received_callback({'esp2_msg': A5_10_06(...).encode_message(b'\xff\xff\xff\x01')})` → `current_temperature` aktualisiert (heute: `value_changed` wird nie gerufen); plus `assert all(isinstance(a, bytes) for a in cc.listen_to_addresses)`.
- **Aufwand:** S
- **Review-Erweiterung (v2.11.0):** Der Fix des Empfangsfilters allein war ein Halb-Fix. `value_changed` (climate.py) verglich `msg.address` weiterhin gegen die **rohe** `dev_id`/`thermostat.id`, während das Gateway lokale Adressen vor dem Dispatch auf den globalen Bus **externalisiert** — bei lokaler Konfiguration (Normalfall) wurde daher jedes Telegramm intern erneut verworfen, **inkl. der eigenen Status-Telegramme des Aktors** (Hauptursache „Climate aktualisiert nicht", unabhängig vom Thermostat). Erweitert: beide Zweige (Aktor + Thermostat) vergleichen jetzt gegen `self._external_dev_id[0]` bzw. das gespeicherte `self._external_thermostat_id[0]`. Zusätzlich `None`-Guard, falls ein `room_thermostat`-Block ohne `id` konfiguriert ist (sonst `None.is_local_address()` → AttributeError in `__init__`). Kein Regressionsrisiko: für globale/externe Adressen ist der Vergleich identisch zu vorher.

#### R3-05 — `reconnect()`/Unload nicht serialisiert: verwaister laufender Bus hält den Single-Client-Slot · CONFIRMED · ✅ ERLEDIGT v2.11.0
- **Fundstelle:** [gateway.py:336-350](custom_components/eltako/gateway.py#L336) (`reconnect`), `_unload_blocking`
- **Problem:** Kein Lock um stop→join(10)→`_init_bus`→start (VNG hat für exakt das `_lifecycle_lock`, die Basisklasse nicht). Zwei überlappende `reconnect()` (Doppelklick auf den Button, Automation+User): A startet busA und weist zu, B weist danach busB zu und startet ihn → **busA läuft verwaist weiter** (Callbacks + Port/TCP-Slot), busB (auf den `self._bus` zeigt) bekommt beim Single-Client-Gateway nie einen Connect. Symptomatik: Telegramme kommen an (busA), aber `is_connected`=False (busB) → alles unavailable, Senden schlägt fehl, alle 15 s ein False-Event. Nur HA-Neustart löst es. Gleiche Race zwischen `reconnect()` und `_unload_blocking()` beim Reload.
- **Fix:** `self._lifecycle_lock = threading.Lock()` + `self._shutdown`-Flag in `EnOceanGateway.__init__`; Bodies von `reconnect()` und `_unload_blocking()` in `with self._lifecycle_lock:`; `reconnect()` No-op wenn `_shutdown` (Muster VNG).
- **Test:** `_init_bus` mit Fake-Bussen patchen, altes join langsam machen; 2 Threads mit Barrier → genau 1 Fake-Bus endet gestartet, jeder andere erhält stop(); Variante reconnect vs. unload → nach Unload ist der überlebende Bus gestoppt.
- **Aufwand:** M

### 🟡 MITTEL

#### R3-06 — Zombie-Bus feuert nach Generationswechsel ein letztes `connected=False` · CONFIRMED (per Inspektion) · ✅ ERLEDIGT v2.11.0
- **Fundstelle:** [gateway.py:242](custom_components/eltako/gateway.py#L242) (`set_status_changed_handler` bekommt in jeder Generation dieselbe Bound-Method, kein Generations-Guard); Bus-`run()`-Exits feuern unconditional False (z. B. tcp2serial_hardened.py:134)
- **Problem:** `join(10)` < `reconnection_timeout`-Sleep (15 s Default) → ein mitten im Sleep gestoppter Bus-Thread überlebt den join; wacht er später auf, feuert sein Exit-False **nach** dem True des neuen Busses. `GatewayConnectionState.value_changed` vertraut dem gepushten Wert (bekanntes B1-Follow-up) → Sensor klemmt auf „Disconnected", während Entities (reconcilen live) verfügbar bleiben — sich widersprechende UI. Zusätzlich cleart `connection_state_changed` den Memory-Read-Guard.
- **Fix:** Pro-Generation-Adapter in `_init_bus`: `bus.set_status_changed_handler(lambda st, b=bus: self._on_bus_status(b, st))`; `_on_bus_status` verwirft, wenn `b is not self._bus`. (Erledigt zugleich das B1-Follow-up „Sensor-Wert-Reconcile" an der Wurzel.)
- **Test:** Fake-Bus; nach Swap den ALTEN Handler mit False feuern → kein Gateway-Handler aufgerufen; NEUER Handler → Zustellung.
- **Aufwand:** S–M · sinnvoll im selben Release wie R3-05.

#### R3-07 — Sensor-Plattform: alle A5-Decode-Entities ohne Teach-In-Guard · CONFIRMED (Temperature gesichtet; Muster-Klassen identisch) · ✅ ERLEDIGT v2.12.0
- **Fundstelle:** [sensor.py:784](custom_components/eltako/sensor.py#L784) (Temperature) sowie Humidity (~918), Illumination (~806), BatteryVoltage (~864), Pir (~564), Voltage (~590), Twilight (~824), Daylight (~841), TargetTemperature (~891)
- **Problem:** `value_changed` übernimmt Decodes ungeprüft; Meter/WeatherStation im selben File und die binary_sensor-Branches (A4-Fix) guarden. Ein LRN-Teach-In (z. B. A5-04-02: `10-10-0D-80`) publiziert −15,8 °C / 6 % → Statistik-Spike, Frostschutz-Automation feuert.
- **Fix:** Am Anfang jedes betroffenen `value_changed`: `if getattr(decoded, "learn_button", 1) == 0: return`; für EEPs ohne `learn_button`-Attribut Roh-Bit prüfen: `if len(msg.data) > 3 and not (msg.data[3] & 0x08): return`.
- **Test:** Pro Klasse: Teach-In-Telegramm → `native_value` bleibt None; Daten-Telegramm → Wert (Muster: `test_round2_audit_fixes.test_a5_07_01_ignores_teach_in`).
- **Aufwand:** M (viele Klassen, mechanisch)
- **Umsetzung (v2.12.0):** einheitlicher Roh-Bit-Guard `_is_4bs_teach_in(msg)` (DB0.3) in allen 9 Sensor-`value_changed`. Review bestätigte: für A5-04-xx/A5-07-01/A5-08-01 identisch zu `learn_button==0`; für A5-06-01/A5-10-06/A5-10-12 (kein `learn_button`) ist der Roh-Bit-Check die einzige einheitliche Option und deckt sich mit dem Encode der Bibliothek (Daten-Telegramme setzen DB0.3; alle A5-10-06-Prioritätscodes haben Bit 3). **Hardware-Vorbehalt (LOW):** Der (ungenutzte) A5-10-03-Encoder der Bibliothek lässt data[3]=0x00; ein A5-10-03-Sende-/Loopback-Pfad existiert heute nicht (nur Empfang), daher zur Laufzeit nicht erreichbar — die DB0.3-Annahme für A5-10-03 in der Hardware-Session gegen echtes Gerät bestätigen.

#### R3-08 — Lib-Bug (NEU): A5-04-03-Decode rechnet `msg.data[1] * 265` statt `* 256` · CONFIRMED (Lib-Quelle) · ✅ ERLEDIGT v2.12.0
- **Fundstelle:** `.venv/.../eltakobus/eep.py:1075` (`raw_temp = msg.data[1] * 265 + msg.data[2]`)
- **Problem:** Zahlendreher: +0,70 °C (MSB=1), +1,41 °C (MSB=2), +2,11 °C (MSB=3) + Sprungstelle an jeder 256er-Grenze. **Decode-only** → fällt unter die B5-Policy „ohne Gerät verifizierbar" (anders als der zurückgestellte A5-04-03-**Encode**-Offset). Achtung: `tests/test_sensor_A5_04_03.py` backt den falschen Wert ein (22.8125 statt spec-korrekt 21.40625 für `99-02-12-09`) → Test mit fixen.
- **Fix:** Decode-Wrapper in `eltakobus_patches.py` (Muster `_patch_a5_30_learn_bit_encode`, idempotenter `_ORIGINALS`-Capture): Temperatur mit `*256` neu berechnen; Drift-Guard: Original liefert weiterhin den 265er-Wert. Upstream-Issue-Kandidat.
- **Aufwand:** S–M

#### R3-09 — F6-01-01-Einzeltaster feuern nie das Button-Event, obwohl ein „Event Id"-Feld dafür angelegt wird · CONFIRMED · ✅ ERLEDIGT v2.12.0
- **Fundstelle:** [binary_sensor.py:258-267](custom_components/eltako/binary_sensor.py#L258-L267) (frühes `return` vor dem gemeinsamen Fire-Block) vs. [sensor.py:427-436](custom_components/eltako/sensor.py#L427-L436) („Event Id"-StaticInfoField wird auch für F6_01_01 erzeugt)
- **Problem/Fix:** Das `return` überspringt `hass.bus.fire` + Push-Duration-Buchhaltung. Fix: `return` durch Fall-through ersetzen (pressed_buttons bleibt leer → nur Basis-Event, kein button-spezifisches). Alternative (Event bewusst nicht feuern) hieße: „Event Id"-Feld für F6_01_01 nicht mehr anlegen — Fall-through ist die konsistente Wahl.
- **Test:** Neuer `test_binary_sensor_F6_01_01.py`: Push+Release → 2 Events auf HassMock-Bus inkl. `push_duration_in_sec` (heute: `fired_events` leer).
- **Aufwand:** S

#### R3-10 — Sensor-Restore liest den einheiten-KONVERTIERTEN State-String in `native_value` (H2-Rest) · CONFIRMED (Design) · ✅ ERLEDIGT v2.13.0
- **Fundstelle:** [sensor.py:502-529](custom_components/eltako/sensor.py#L502) (`load_value_initially` parst `latest_state.state`)
- **Problem:** `State.state` ist nach HAs Einheitenkonvertierung (°F/mph/gal bei Imperial-Installationen oder User-Override). Der Wert wird als nativ interpretiert → Doppel-Konvertierung nach jedem Neustart (22,9 °C → gespeichert 73,2 → angezeigt 163,8 °F bis zum nächsten Telegramm); bei TOTAL_INCREASING (Wasser/Gas) Statistik-Spike + „Meter-Reset". Für die (metrische) Nutzer-Installation gering, generell aber falsch.
- **Fix:** `EltakoSensor` von `RestoreSensor` ableiten; in `async_added_to_hass` `(await self.async_get_last_sensor_data()).native_value` bevorzugen; String-Parser nur als Fallback (Text-Sensoren wie WindowHandle behalten den String-Pfad).
- **Test:** Restore aus `State('sensor.x','73.2',{'unit_of_measurement':'°F',...})` → native ≈ 22,9 (heute 73,2).
- **Aufwand:** M

#### R3-11 — A5-30-01: `invert_signal`/`device_class` des Kontakts werden auf die „Low Battery"-Entity angewendet · CONFIRMED · ✅ ERLEDIGT v2.12.0
- **Fundstelle:** [binary_sensor.py:66-74 + 319-324](custom_components/eltako/binary_sensor.py#L319) (auch A5-30-03 „Status of Wake", Z. ~356-360)
- **Problem:** `invert_signal: true` (normale NC/NO-Wahl für den Kontakt) invertiert stillschweigend den Batterie-Alarm (ON bei voller Batterie, OFF bei leerer → Notification feuert nie); die User-device_class (z. B. `window`) landet ebenfalls auf der Batterie-Entity.
- **Fix:** Für `low_battery`: `device_class=BinarySensorDeviceClass.BATTERY` hart setzen, `invert_signal` nie anwenden (direkt `decoded.low_battery`); „wake" analog ohne Inversion. (Beim Anfassen: öffentliches `contact_closed` statt `_contact_closed` verwenden.)
- **Test:** A5-30-01-Paar mit `invert_signal=True`; Telegramm „Batterie ok" → low_battery `is_on=False` (heute True) + device_class `battery`; „Batterie leer" → True.
- **Aufwand:** S

#### R3-12 — Sender-ID-Validierung ist für alle Aktoren ein No-op (`sender_id` vs. `_sender_id`) · CONFIRMED · ✅ ERLEDIGT v2.12.0
- **Fundstelle:** [device.py:146-157](custom_components/eltako/device.py#L146) (`hasattr(self, "sender_id")`) vs. `_sender_id` in light.py/switch.py/cover.py/climate.py (nur `TeachInButton` nutzt den öffentlichen Namen)
- **Problem:** `validate_actuators_dev_and_sender_id` — am Ende jedes Plattform-Setups genau dafür da — validiert für Lights/Switches/Cover/Climate **nichts**: Falsche Sender-IDs (klassischer Konfigurationsfehler bei Transceiver-Gateways) erzeugen nie die vorgesehene Warnung.
- **Fix:** In `validate_sender_id` Fallback `sender_id = getattr(self, "_sender_id", None)` ergänzen (oder `sender_id`-Property auf Basisklasse, die `_sender_id` liefert). **Nebenwirkung prüfen:** Entities ohne jeden Sender (Sensoren, Gateway-Felder) müssen weiter still True liefern.
- **Test:** EltakoSwitch auf Transceiver-GatewayMock mit Sender außerhalb des Base-ID-Fensters → `gateway.validate_sender_id` wird aufgerufen/warnt (heute: nie aufgerufen).
- **Aufwand:** S

#### R3-13 — Switch/SwitchableLight: WARNING-Spam für erwartbare Begleit-Telegramme derselben Adresse · CONFIRMED · ✅ ERLEDIGT v2.12.0
- **Fundstelle:** [switch.py:150-156](custom_components/eltako/switch.py#L150-L156); analog `EltakoSwitchableLight` (light.py ~305-313). `EltakoDimmableLight` hat den M4-Filter, diese nicht.
- **Problem:** Kein RORG-Vorfilter → jedes org≠0x05-Telegramm (z. B. die **dokumentierte** FSR14M-2x-Doppel-Config: gleiche Adresse als Switch + A5-12-01-Meter) wirft `WrongOrgError` und loggt pro Telegramm eine WARNING. Dauerspam bei empfohlener Konfiguration.
- **Fix:** M4-Muster spiegeln: bei M5-38-08/F6-02-xx-Dev-EEPs `if msg.org != 0x05: LOGGER.debug(...); return` vor dem Decode (bzw. WrongOrgError separat auf DEBUG).
- **Test:** EltakoSwitch (M5-38-08) + Regular4BSMessage von eigener Adresse → kein WARNING-Log, State unverändert.
- **Aufwand:** S

#### R3-14 — Dimmbares Licht: `turn_on` ohne Helligkeit sendet immer 100 % statt der letzten Helligkeit · CONFIRMED ⚠️ Verhaltensänderung · ✅ ERLEDIGT v2.13.0 (Nutzerwahl: Schalt-EIN cmd 0x01 → Aktor-Memory; Hardware-Verifikation offen)
- **Fundstelle:** [light.py:105](custom_components/eltako/light.py#L105) (`kwargs.get(ATTR_BRIGHTNESS, 255)`)
- **Problem:** Einfaches Einschalten (Dashboard-Toggle, Automation ohne brightness) überschreibt die FUD14-Dimm-Memory mit 100 % — nachts volle Helligkeit statt der letzten/Memory-Stufe. (HA-Konvention und Eltako-Tasterverhalten: letzte Helligkeit.)
- **Fix-Optionen:** (a) `kwargs.get(ATTR_BRIGHTNESS, self._attr_brightness or 255)`; (b) ohne explizite brightness ein Switching-ON (cmd 0x01) senden → Aktor nutzt seine Memory. **Vor Umsetzung Nutzer-Präferenz einholen** (sichtbare Verhaltensänderung).
- **Test:** Gedimmt-Status einspeisen (z. B. 45 %), `turn_on()` ohne kwargs → gesendetes Payload trägt 45 % bzw. cmd 0x01 (heute 100 %).
- **Aufwand:** S

#### R3-15 — Vom Send-Service gesendete Telegramme werden doppelt auf den globalen Bus dispatcht · CONFIRMED (Scope korrigiert) · ✅ ERLEDIGT v2.13.0 (GLOBAL nur zentral im _callback; AM1/AN2 = Welle C)
- **Fundstelle:** [gateway.py:417-421](custom_components/eltako/gateway.py#L417) (`send_message`: event_id **und** GLOBAL) + [gateway.py:463](custom_components/eltako/gateway.py#L463) (`_callback_send_message_to_serial_bus`: GLOBAL erneut)
- **Scope-Korrektur ggü. Finder:** Entity-Kommandos (light/switch/…) nutzen `EltakoEntity.send_message` (device.py:217, nur event_id) → **nicht** betroffen. Betroffen sind Aufrufer von `gateway.send_message` = der **Send-Message-Service** (und direkte Nutzer): bei aktivem Bus 2× GLOBAL → VNG-Clients erhalten den Frame doppelt, Entities verarbeiten ihn doppelt; bei inaktivem Bus 1× — die Inkonsistenz zeigt, dass eine der beiden Stellen ein Leftover ist.
- **Fix:** GLOBAL-Dispatch aus `_callback_send_message_to_serial_bus` entfernen (dann deckt `send_message` auch den Disconnected-Fall) — **oder** aus `send_message`; nur eine Stelle darf global publizieren. Entity-Pfad beachten: dessen einziges GLOBAL kommt heute aus dem `_callback` — bei Variante „aus _callback entfernen" muss `EltakoEntity.send_message` das GLOBAL-Dispatch übernehmen (sonst verlieren VNG/andere Entities die Entity-Kommandos!). Sauberste Lösung: GLOBAL-Dispatch zentral in `_callback` belassen und stattdessen aus `gateway.send_message` entfernen.
- **Test:** Recorder auf `ELTAKO_GLOBAL_EVENT_BUS_ID`; `gateway.send_message(msg)` bei aktivem Fake-Bus → genau 1 Dict (heute 2); Entity-Send → weiterhin genau 1.
- **Aufwand:** S · Vorsicht: Wechselwirkung mit AM1/AN2 (Welle C) — dort mit einplanen.

#### R3-16 — VNG: `is_connected`/„Connected"-Sensor dauerhaft False (Dummy-Bus + zu frühes True) · CONFIRMED · ✅ ERLEDIGT v2.12.0
- **Fundstelle:** [virtual_network_gateway.py:38ff](custom_components/eltako/virtual_network_gateway.py#L38) (super().__init__ baut nie gestarteten RS485-Bus); True-Fire beim Serverstart passiert in `async_setup` **vor** dem Plattform-Forward (eltako_integration_init.py) — die danach registrierten Handler (Connected-Sensor, B1-Availability) bekommen als Immediate-Notify das `is_active()`des Dummy-Busses = False, und ein späteres True kommt nie.
- **Problem:** Der „Connected"-Sensor der VNG zeigt ab Start dauerhaft „Disconnected", obwohl Clients bedient werden; `is_connected`/Diagnostics melden False. Reconnect-Button „heilt" scheinbar (neue Server-Generation feuert True, wenn Handler existieren). (Bereits im B1-Review als Rand-Fall notiert; hier mit konkretem Nutzer-Impact — der Nutzer betreibt eine VNG.)
- **Fix:** `is_connected`-Override in `VirtualNetworkGateway` → `self._running.is_set()`; Immediate-Notify der Basisklasse über einen Hook (`_current_connection_state()`) leiten, den VNG überschreibt. Optional Dummy-Bus für VirtualNetworkAdapter gar nicht bauen.
- **Test:** VNG starten (Fake-Loop), nach True-Fire `add_connection_state_changed_handler(recorder)` → Immediate-Notify True (heute False); `vng.is_connected is True` solange `_running` gesetzt.
- **Aufwand:** S–M

### 🟢 NIEDRIG

#### R3-17 — Cover-Restore friert `opening`/`closing` dauerhaft ein · CONFIRMED · ✅ ERLEDIGT v2.13.0
[cover.py:112-119](custom_components/eltako/cover.py#L112-L119): STATE_OPENING/CLOSING werden 1:1 restauriert; die Fahrt endete aber, während HA aus war — kein Telegramm/Timeout cleart je das Flag. Fix: als „gestoppt, Endlage unbekannt" restaurieren (`is_opening=is_closing=False`, `is_closed` aus Position ableiten); bestehende Tests `test_initial_loading_opening/closing` anpassen (asserten heute das Einfrieren). Aufwand S. *(Verwandt mit AV1, aber eigenständige Branches.)*

#### R3-18 — Cover-Tilt: 2 Rest-Fenster, in denen ein verspätetes STOP eine neue Fahrt anhält · PLAUSIBLE · ✅ ERLEDIGT v2.13.0
[cover.py ~404]: Post-Sleep-STOP wird nicht gegen Supersession re-validiert; `self._tilt_task`-Zuweisung nach eagerem Task-Start. Fix: Generationszähler (`_move_generation`), STOP nur bei unveränderter Generation + `self._tilt_task is asyncio.current_task()`. Aufwand M; Test mit kontrollierbarem Sleep-Future.

#### R3-19 — Dimmbares Licht mit F6-Sender: brightness wird still verworfen, UI bestätigt sie optimistisch · PLAUSIBLE · ✅ ERLEDIGT v2.13.0
[light.py ~118-141]: F6-Branch kann brightness nicht transportieren; Fast-Status schreibt sie trotzdem. Fix: bei F6-Sender + expliziter brightness einmalig warnen (oder ColorMode.ONOFF), Fast-Status-Block für F6 die brightness überspringen. Aufwand S.

#### R3-20 — Gerät in `sensor:` UND `binary_sensor:` → doppelte unique_id, HA verwirft die zweite Entity mit ERROR · CONFIRMED (per Inspektion) · ✅ ERLEDIGT v2.12.0
[binary_sensor.py:38ff]: Der Doppel-Scan über beide Sektionen (dokumentiertes Muster, z. B. F6-10-00) erzeugt zwei EltakoBinarySensor mit identischer id → „…does not generate unique IDs"-ERROR pro Start; welche überlebt, hängt von der Reihenfolge ab. Fix: seen-Set über (dev_id, description_key) über beide Pässe, binary_sensor-Sektion bevorzugen. Aufwand S.

#### R3-21 — `validate_actuators_dev_and_sender_id` läuft auf der Sensor-Plattform → Warn-Spam für korrekte dezentrale Sensoren · CONFIRMED · ✅ ERLEDIGT v2.12.0
[sensor.py:479] vs. binary_sensor.py:88 (überspringt bewusst). Funk-Sensoren (Wetterstation 05-…, FT55 FE-…) scheitern an beiden dev_id-Checks → ~1 Warnung pro Entity pro Start; auf Transceivern warnen auch die Gateway-Felder (dev_id 00-00-00-00). Fix: Aufruf entfernen (wie binary_sensor) oder auf Entities mit Sender beschränken. Aufwand S. *(Nach R3-12-Fix würde der Spam sonst sogar zunehmen — R3-12 und R3-21 zusammen umsetzen!)*

#### R3-22 — VNG-Accept-Loop: transienter accept-Fehler feuert False (nie wieder True) und retried ohne Backoff · PLAUSIBLE · ✅ ERLEDIGT v2.13.0
[virtual_network_gateway.py ~252-254]. Fix: im except keinen Connection-State feuern (Listener lebt ja), `time.sleep(1)`-Backoff. Aufwand S.

#### R3-23 — VNG `handle_client` liest nie vom Client-Socket: Client→VNG-Bytes stauen sich (TCP-Backpressure bis zum Freeze des Client-Bus-Threads) · CONFIRMED (einseitig per Code) / Konsequenz PLAUSIBLE · ✅ ERLEDIGT v2.13.0 (non-blocking drain; Review-Fix: `select`-`ValueError` beim Shutdown → break)
[virtual_network_gateway.py ~153]: Nur Write + Keepalive, kein `recv`. Kommandos einer Zweit-HA sind stille No-ops (deckt sich mit AN2), und ungelesene Bytes füllen den Kernel-Buffer, bis der **Client** beim Senden blockiert. Fix (minimal): pro Loop-Iteration non-blocking drainen und verwerfen (debug-loggen); vollwertig: 14-Byte-ESP2-Frames parsen und an das passende Gateway weiterleiten (= Teil von AN2/Welle C). Aufwand S (drain) / M–L (bridge).

#### R3-24 — `_callback_send_message_to_serial_bus` ohne `@callback`: HA fährt den Dispatcher-Target im Executor → Press/Release können sich umsortieren · PLAUSIBLE · ✅ ERLEDIGT v2.13.0
[gateway.py:453]: Plain-Funktion ⇒ HassJobType.Executor ⇒ zwei schnelle dispatcher_send-Aufrufe laufen konkurrent auf Pool-Threads; die `hass.create_task`-Reihenfolge ist nicht garantiert. Fix: `@callback` (homeassistant.core) annotieren — Body ist komplett non-blocking; läuft dann inline in Dispatch-Reihenfolge. Test: JobType-Assertion + 50× alternierende Sends → strikte Alternation. Aufwand S.

#### R3-25 — Test mutiert das geteilte `DEFAULT_GENERAL_SETTINGS`-Dict (+ mutable Default-Arg in GatewayMock) · CONFIRMED · ✅ ERLEDIGT v2.13.0 (Review-Fund: auch Produktivcode `get_general_settings_from_configuration` gab das geteilte Objekt zurück → Copy-on-return)
[tests/test_dimmable_light.py:23] (`settings = DEFAULT_GENERAL_SETTINGS` + in-place-Set) + [tests/mocks.py:130] (Default-Arg). Jede spätere GatewayMock läuft mit `fast_status_change=True` → Baseline ist ordnungsabhängig. Fix: `dict(DEFAULT_GENERAL_SETTINGS)` im Test; `general_settings=None` + Copy im Mock-`__init__`. Aufwand S.

#### R3-26 — `strenum`-Fremdpaket statt stdlib `enum.StrEnum` · CONFIRMED · ✅ ERLEDIGT v2.13.0
[const.py:3] `from strenum import StrEnum`; manifest pinnt „StrEnum". Ab Py 3.11 stdlib-identisch verfügbar; das Fremdpaket ist ein unnötiges Supply-Chain-/Wartungs-Risiko. Fix: `from enum import StrEnum` + Requirement aus manifest.json entfernen (HA ≥ 2024 setzt Py ≥ 3.12 voraus). Aufwand S.

---

## 4. Ergänzende Hinweise

- **R3-01/02 gehören zusammen** (eine Datei, ein Release, gemeinsame Fake-Socket-Testbasis). Beide sind zugleich Upstream-Bugs in esp2_gateway_adapter ≤ 0.2.21 → Issue-Kandidaten (nur auf explizite Nutzer-Freigabe).
- **R3-04 vor der Hardware-Session fixen**: Er blockiert vermutlich jede Climate-Diagnose; AC8/F5 lassen sich erst danach sinnvoll am Gerät bewerten.
- **R3-12 + R3-21 im selben Release** (sonst erzeugt die reparierte Validierung neuen Spam).
- **R3-06 erledigt das offene B1-Follow-up** („GatewayConnectionState.value_changed vertraut dem Arg") an der Wurzel gleich mit — dort dokumentiert in `ANALYSE-UND-ROADMAP.md` unter Welle B.
- Domänen 5/6 (init/config/schema + HA-Konformitäts-Sweep) **unvollständig auditiert** — als eigenes Arbeitspaket nachholen (s. Kopfnotiz).

## 5. Umsetzungsplan (Wellen)

| Welle | Inhalt | Charakter |
|---|---|---|
| **R3-A** ✅ **ERLEDIGT (v2.11.0)** | R3-01 + R3-02 (tcp2serial), R3-03 (Unit-ValueError), R3-04 (Climate-Listen), R3-05 + R3-06 (Lifecycle-Lock + Generations-Guard) | kritisch/hoch, ohne Hardware, kleiner Diff |
| **R3-B** ✅ **ERLEDIGT (v2.12.0)** | R3-07 (Learn-Guards), R3-08 (Lib-Decode-Patch), R3-09 (F6-01-01-Event), R3-11 (Low-Battery-Invert), R3-12 + R3-21 (Validierung), R3-13 (WARNING-Filter), R3-16 (VNG is_connected), R3-20 (Dual-Listing-Dedupe) | mittel, mechanisch, gut testbar |
| **R3-C** ✅ **ERLEDIGT (v2.13.0)** | R3-10 (RestoreSensor), R3-17/18 (Cover-Restore/Tilt), R3-14 ⚠️ (Nutzer gefragt → Schalt-EIN/Aktor-Memory), R3-15 (Doppel-Dispatch entfernt; AM1/AN2 in Welle C), R3-19, R3-22/23 (VNG), R3-24 (@callback), R3-25/26 (Hygiene) | niedrig/Design, teils Verhaltensänderung |
| **R3-D** | Vollständiger Nachhol-Pass Domänen 5+6 | Audit-Rest |
