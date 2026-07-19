# KI-Optimierungen — Stabilitätsanalyse & Abarbeitungsplan

> Erstellt am 2026-07-17 durch Code-Review (Claude) über das gesamte Repo.
> Anlass: Integration stürzt im Betrieb immer wieder ab.
> Vorgehen: Alle Befunde wurden gegen die gepinnte Bibliothek `eltako14bus==0.0.73` und die HA-Core-APIs verifiziert. **Noch nichts wurde am Code geändert** — dieses Dokument ist die Arbeitsgrundlage.

---

## 1. Zusammenfassung: Warum stürzt die Integration ab?

Es gibt **drei Haupt-Absturzmechanismen**, die zusammen das beobachtete Verhalten erklären:

1. **Der Empfangs-Thread stirbt still (K1).** Der Callback, der jedes Telegramm vom seriellen Bus verarbeitet, läuft ungeschützt im Bus-Thread. Die Bibliothek fängt dort nur `SerialException`/`IOError`. Jede andere Exception (korruptes Telegramm in `prettify()`, Adress-Arithmetik, Shutdown-Race) **beendet den Empfangs-Thread endgültig** → die Integration empfängt nichts mehr, bis HA neu gestartet wird.
2. **Blockierende Thread-Joins im Event Loop (K2).** `unload()`/`reconnect()` rufen `thread.join()` (teils ohne Timeout) direkt im HA-Event-Loop auf. Hängt der Bus-/TCP-Thread, **friert ganz Home Assistant ein** → der Supervisor-Watchdog killt HA → sieht aus wie ein Absturz.
3. **Reload zerstört den Zustand (K3).** `async_unload_entry` entlädt die Plattformen nie. Nach jedem Reload existieren alte Entities samt Listenern weiter: doppelte Events, `unique_id`-Kollisionen, Memory-Leaks — Integration bleibt bis zum Neustart defekt.

Dazu kommen viele Einzel-Crashes in Callbacks und Service-Aufrufen (Taster-Release, `set_temperature`, Cover-Position nach Neustart, Restore von Zählerständen …), die jeweils Funktionen dauerhaft lahmlegen oder das Log fluten.

---

## 2. Befunde nach Priorität

Legende: ☐ offen · ☑ erledigt · Aufwand: S (klein, <30 min) / M (mittel) / L (groß)

### P0 — KRITISCH (direkte Absturz-/Ausfallursachen)

- ☑ **K1 — Ungeschützter Empfangs-Callback im Bus-Thread** · Aufwand: S–M
  [gateway.py:378-420](custom_components/eltako/gateway.py#L378-L420) `_callback_receive_message_from_serial_bus`
  Kein try/except; jede unerwartete Exception beendet den Empfangs-Thread der eltakobus-Bibliothek endgültig. **Wahrscheinlichste Ursache für „Integration tot bis Neustart".**
  **Fix:** Gesamten Callback-Body in `try/except Exception` mit Logging kapseln; langfristig Verarbeitung in den Event Loop verlagern und im Bus-Thread nur ein minimales Übergabestück lassen.

- ☑ **K2 — Blockierende `join()`s im Event Loop** · Aufwand: M
  [gateway.py:352-360](custom_components/eltako/gateway.py#L352-L360) (`unload()`, `join()` **ohne Timeout**), [gateway.py:265-274](custom_components/eltako/gateway.py#L265-L274) (`reconnect()`, `join(10)`), [virtual_network_gateway.py:224-226](custom_components/eltako/virtual_network_gateway.py#L224-L226) (`stop_tcp_server`) — aufgerufen im Loop aus [eltako_integration_init.py:241-246](custom_components/eltako/eltako_integration_init.py#L241-L246) und [button.py:137-139](custom_components/eltako/button.py#L137-L139) (Reconnect-Button).
  **Fix:** Alle stop/join/reconnect-Aufrufe per `await hass.async_add_executor_job(...)`; `join()` immer mit Timeout + Ergebnis prüfen.

- ☑ **K3 — `async_unload_entry` entlädt keine Plattformen** · Aufwand: S–M
  [eltako_integration_init.py:241-246](custom_components/eltako/eltako_integration_init.py#L241-L246); Gegenstück Zeile 191. Auch der in [gateway.py:295-297](custom_components/eltako/gateway.py#L295-L297) registrierte Service wird nie deregistriert.
  **Fix:** `await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)` aufrufen und Ergebnis zurückgeben; Service mit `hass.services.async_remove` entfernen.

- ☑ **K4 — `TypeError` bei jedem Taster-Loslassen** · Aufwand: S
  [binary_sensor.py:353](custom_components/eltako/binary_sensor.py#L353): `LAST_RECEIVED_TELEGRAMS[ key, {…} ]` — `[key, default]` statt `.get(key, default)` → `TypeError: unhashable type: 'dict'` bei **jedem** Release-Telegramm; Release-Events + `push_duration` funktionieren nie. Zusätzlich Zeile 358: `b2(...)` statt `b2s(...)` → `NameError` im Fehlerpfad.
  **Fix:** `.get(b2s(self.dev_id), {...})`; `b2` → `b2s`; statt `raise` loggen und `return`.

- ☑ **K5 — Climate: `set_temperature` crasht auf frischen Installationen immer** · Aufwand: S
  [climate.py:156](custom_components/eltako/climate.py#L156) setzt `_attr_priority = ControllerPriority.AUTO.value` (= int 1, kein Enum) → [climate.py:283-284](custom_components/eltako/climate.py#L283-L284) liest `priority.description` → `AttributeError`. Weitere Pfade: `find_by_description()` liefert `None` bei unbekanntem Restore-String; `target_temperature=None` → `TypeError` beim Vergleich.
  **Fix:** Enum-Mitglied statt `.value` setzen; in `_send_command` defensiv auf Enum-Typ und `None`-Temperatur prüfen.

- ☑ **K6 — Setup-Fehler sind permanent statt Retry (`ConfigEntryNotReady` fehlt)** · Aufwand: M
  [eltako_integration_init.py:109-190](custom_components/eltako/eltako_integration_init.py#L109-L190): Fehlerpfade machen `return` ohne Wert (HA: „did not return boolean") oder `raise Exception(...)`. Serieller Port kurz nicht verfügbar → Setup scheitert endgültig bis zum HA-Neustart.
  **Fix:** Verbindungs-/Portfehler → `raise ConfigEntryNotReady` (HA wiederholt automatisch); Konfigurationsfehler → `ConfigEntryError`; sonst sauber `return False`.

### P1 — HOCH (Folgefehler, Leaks, eingefrorene Zustände)

- ☑ **H1 — Keine Fehlerbarriere um `value_changed`** · Aufwand: S
  [device.py:158-171](custom_components/eltako/device.py#L158-L171): `self.value_changed(msg)` und `data['esp2_msg']` ungeschützt. Eine fehlerhafte Entity erzeugt pro Telegramm einen Traceback, Statusupdates gehen verloren.
  **Fix:** `try/except Exception` um den Aufruf (Log mit dev_id + msg); `data.get('esp2_msg')` mit None-Check. Damit werden viele Einzelbugs (H3, M4, M6 …) von „Funktionsausfall" zu „geloggtem Fehler" herabgestuft.

- ☑ **H2 — Restore-Crashes nach jedem HA-Neustart (Sensoren verschwinden)** · Aufwand: S–M
  [sensor.py:458-488](custom_components/eltako/sensor.py#L458-L488) `load_value_initially`: `int("123.45")` (Meter speichern gerundete Floats!), State `"unavailable"` nicht abgefangen, `float("12,5")` schlägt fehl; `raise e` bricht `async_added_to_hass` ab → „Error adding entity".
  **Fix:** Jede Konvertierung absichern, `unavailable` wie `unknown` behandeln, `float()` für `total_increasing`, **niemals raisen** (nur loggen). Gleiches Muster in [light.py:78](custom_components/eltako/light.py#L78) / [switch.py:74](custom_components/eltako/switch.py#L74) (`raise e` entfernen).

- ☑ **H3 — Climate: ungeschützte HeaterMode-Konvertierung + OFF nie erkannt** · Aufwand: S
  [climate.py:413-427](custom_components/eltako/climate.py#L413-L427): `A5_10_06.HeaterMode(int.from_bytes(...))` außerhalb des try → `ValueError` bei fremden RPS-Telegrammen. Zeile 416 vergleicht `HeaterMode.OFF.value == msg.data` (int vs. bytes) → **immer False**, OFF-Zustand wird nie erkannt.
  **Fix:** Konvertierung in try/except mit Fallback `UNKNOWN`; Vergleich auf Enum-Ebene.

- ☑ **H4 — Cover: `TypeError` bei unbekannter Position (None)** · Aufwand: S *(inkl. `set_cover_position`, `set_cover_tilt_position` und value_changed-Tilt — der Tilt-Service wurde erst durchs adversariale Review nachgezogen)*
  [cover.py:199-203](custom_components/eltako/cover.py#L199-L203), [cover.py:325-336](custom_components/eltako/cover.py#L325-L336) (Services) und [cover.py:297-308](custom_components/eltako/cover.py#L297-L308) (`value_changed`, Tilt ohne None-Guard → Positionstracking friert ein).
  **Fix:** None-Guards analog zur bestehenden Positionslogik (Zeile 292/303) ergänzen; Services bei unbekannter Position sauber abbrechen oder Annahme treffen.

- ☑ **H5 — Cover: `time.sleep()` bis 255 s blockiert Executor-Threads** · Aufwand: M *(async + abbrechbarer Task; Review fand + behob: neues MOVE muss auf STOP des abgebrochenen Tasks warten)*
  [cover.py:333-349](custom_components/eltako/cover.py#L333-L349): synchroner Sleep zwischen Fahr- und Stopp-Telegramm. Mehrere Tilt-Kommandos gleichzeitig → Executor-Pool-Starvation, HA wird zäh; Stopp-Telegramm nach Unload geht an toten Dispatcher.
  **Fix:** Auf `async_set_cover_tilt_position` + `asyncio.sleep` umstellen (oder `hass.loop.call_later`), Timer bei `async_will_remove_from_hass` canceln.

- ☑ **H6 — `UnboundLocalError` im Exception-Handler killt Plattform-Setup** · Aufwand: S
  [light.py:52](custom_components/eltako/light.py#L52) und [select.py:45](custom_components/eltako/select.py#L45): except-Block loggt `dev_conf.id`/`dev_config.id`, das bei Fehlern in der ersten Iteration nie zugewiesen wurde → `UnboundLocalError` propagiert → **gesamte Plattform lädt nicht**.
  **Fix:** Im Log nur `entity_config`/`platform` ausgeben (wie in switch.py/cover.py) oder Variable vorher mit `None` initialisieren.

- ☑ **H7 — Listener-/Handler-Leaks (Reload wird nie sauber)** · Aufwand: M *(async_on_remove/config_entry.async_on_unload; Review fand + behob: `is`→`==` in 2 Remove-Methoden, Climate-Subscription-Timing)*
  - [climate.py:74-79](custom_components/eltako/climate.py#L74-L79): `hass.bus.async_listen` ohne Unsubscribe → nach Reload verarbeiten alte + neue Entity Events, `_send_command` sendet doppelt (Heizungs-Flattern).
  - [sensor.py:1072](custom_components/eltako/sensor.py#L1072) (`EventListenerInfoField.__init__`): Listener im Konstruktor, nie deregistriert.
  - [sensor.py:876/924/968](custom_components/eltako/sensor.py#L876), [binary_sensor.py:379](custom_components/eltako/binary_sensor.py#L379) i.V.m. [gateway.py:118-136](custom_components/eltako/gateway.py#L118-L136): Gateway-Handler-Listen werden nie bereinigt; Registrierung feuert sofort im Konstruktor (vor `async_added_to_hass`).
  **Fix:** Registrierung nach `async_added_to_hass` verschieben, `self.async_on_remove(...)`-Muster; Remove-Methoden im Gateway ergänzen.

- ☑ **H8 — Virtual Network Gateway: TCP-Server fragil (Leak/OOM/dauerhaft tot)** · Aufwand: L — **ERLEDIGT 2026-07-18 (v2.1.3)** *(alle Punkte a–d + Review-Befunde: Generation-Token pro Serverlauf gegen Zombie-Thread-Restart-Race, Lifecycle-Lock gegen Unload-vs-Reconnect-Race; Funktionstests in `tests/test_virtual_network_gateway.py`)*
  [virtual_network_gateway.py:86-200](custom_components/eltako/virtual_network_gateway.py#L86-L200):
  a) `bind()` ohne `SO_REUSEADDR` und ohne try/except → nach Reconnect `EADDRINUSE`, Thread stirbt, `_running` bleibt `True` → Server bis Neustart tot.
  b) Race: Client-Thread löscht Queue vor `connected_clients.remove()` → `KeyError` in `_forward_message` im Loop.
  c) Kein `settimeout`, `sendall` blockierend, Queues unbounded → hängende Clients = Thread-/Speicher-Leak bis OOM („stürzt nach Tagen ab").
  d) `stop_tcp_server` schließt Client-Sockets/-Threads nie; ~~`AttributeError` wenn Server nie lief~~ *(AttributeError-Teil bereits in Phase 1 mitgefixt: `tcp_thread=None`-Init + Guard + Join-Timeout)* ([virtual_network_gateway.py:224-226](custom_components/eltako/virtual_network_gateway.py#L224-L226)).
  **Fix:** SO_REUSEADDR + try/except um bind/DNS mit `_running.clear()` im finally; Lock bzw. Kopien + korrekte Reihenfolge im Cleanup; Socket-Timeouts + `Queue(maxsize=…)` mit Drop-Strategie; beim Stop alle Clients schließen; `tcp_thread = None` im `__init__`.

- ☑ **H9 — `validate_path` lässt den seriellen Port offen** · Aufwand: S
  [gateway.py:494-501](custom_components/eltako/gateway.py#L494-L501): `serial.serial_for_url(...)` wird nie geschlossen → Port bleibt belegt, Gateway-Verbindung kann fehlschlagen.
  **Fix:** Port im `finally` schließen (Context-Manager).

- ☑ **H10 — Cross-Loop: `asyncio.to_thread(asyncio.run, coro)`** · Aufwand: M — **GEPRÜFT: kein Fix, kein Bug (False Positive)**
  [gateway.py:250-252](custom_components/eltako/gateway.py#L250-L252) (via [button.py:166-168](custom_components/eltako/button.py#L166-L168), „Read memory of bus devices"): Der Verdacht war „attached to a different loop". **Verifikation gegen die Bibliothek:** Der genutzte `RS485SerialInterfaceV2` ist ein **Thread mit thread-sicheren Queues** und nutzt nur `asyncio.sleep` — er ist NICHT an einen Loop gebunden (im Gegensatz zur `asyncio.Protocol`-Variante `RS485SerialInterface`). Der Crash tritt also nicht auf. Zudem hält `to_thread` das minutenlange Auslesen von bis zu 255 Geräten korrekt vom HA-Loop fern; ein „Fix" (Ausführung im HA-Loop) wäre eine echte Regression. → **Absichtlich unverändert gelassen.**

- ☑ **H11 — Climate: Cooling-Mode-Konfiguration dreifach defekt** · Aufwand: M *(entity_config statt config; CONF_SENSOR statt String-Index; generelles Event + Handler-Filter nach switch_button; Cooling nicht hardware-getestet)*
  [climate.py:54-58](custom_components/eltako/climate.py#L54-L58), [climate.py:71-74](custom_components/eltako/climate.py#L71-L74) i.V.m. [binary_sensor.py:173](custom_components/eltako/binary_sensor.py#L173):
  a) prüft `config` (Gesamt-Config) statt `entity_config` → Zweig nie erreicht; b) `CONF_SENSOR [CONF_SWITCH_BUTTON]` → `TypeError`, äußeres except verwirft die **gesamte Climate-Entity**; c) Event-ID-Mismatch: Subscription mit Button-Suffix, gefeuert ohne → Handler nie aufgerufen, Kühlen fällt nach 15 min auf HEAT zurück.
  **Fix:** `entity_config` prüfen; Zugriff korrigieren; Event-IDs identisch aufbauen.

### P2 — MITTEL (latente Crashes, Kompatibilität, falsche Zustände)

- ☑ **M1 — Zuweisung an Property `native_value` statt `_attr_native_value`** — konsequent `_attr_native_value`. · S *(mit H7/Phase 3 erledigt)*
- ☑ **M2 — `ClimateEntityFeature.TURN_ON/TURN_OFF` fehlen** — Features + `async_turn_on/off` ergänzt. Review-Fix F1: `async_turn_off` sendet OFF direkt (nicht über die Toggle-Logik, die ein OFF-Gerät sonst wieder eingeschaltet hätte). · S
- ☑ **M3 — `kwargs['temperature']` → `KeyError`** — [climate.py:265](custom_components/eltako/climate.py#L265); `kwargs.get(ATTR_TEMPERATURE)` mit None-Guard. · S *(mit K5 erledigt)*
- ☑ **M4 — `UnboundLocalError` für Telegramme mit org ≠ 0x05/0x07 (Dimmer)** — `else: return` ergänzt. Regressionstest (1BS/org 0x06) in `test_dimmable_light.py`. · S *(Phase 6, 2026-07-19)*
- ☑ **M5 — Zuweisung an Property `hvac_modes` beim Restore** — Modi-Restore entfernt (werden im `__init__` gesetzt), widersprüchliche hvac_mode-Logik bereinigt. · S
- ☑ **M6 — `raise Exception` mitten im Message-Callback** — [binary_sensor.py:337](custom_components/eltako/binary_sensor.py#L337) (A5-30-03, unbekannter description_key); loggen + `return`. · S *(mit K4 erledigt)*
- ☑ **M7 — Non-frozen Dataclass-Subklasse von `SensorEntityDescription`** — `@dataclass(frozen=True, kw_only=True)` gesetzt; Regressionstest in `test_phase6_robustness.py`. · S *(Phase 6, 2026-07-19)*
- ☑ **M8 — `event.data['pressed_buttons']` ohne Guard** — `.get('pressed_buttons', [])`. · S *(Phase 6, 2026-07-19)*
- ☑ **M9 — `get_id_from_gateway_name` crasht bei abweichenden Namen** — Regex-Parsing mit `None`-Rückgabe (Aufrufer in init prüft auf None → ConfigEntryError); `get_device_config` per `.get(...)` gehärtet. Regressionstests in `test_phase6_robustness.py`. · M *(Phase 6, 2026-07-19)*
- ☑ **M10 — Config Flow: `TypeError` bei `None`-Gateway-Config + Substring-Matching** — None-Guard bei `find_gateway_config_by_id`; `validate_eltako_conf` ermittelt den Gerätetyp jetzt aus der YAML per Gateway-Id statt per Substring (`'lan' in 'Planung'`), inkl. Fix der zwei fehlerhaften Debug-Logs. Review-Nachtrag: `BAUD_RATE_DEVICE_TYPE_MAPPING`-Lookup per `.get()` gehärtet (unmapped `ftd14` → saubere Meldung statt `KeyError`, auch in `async_setup_entry`). · M *(Phase 6, 2026-07-19)*
- ☑ **M11 — VNG: `service_info`-`UnboundLocalError`, `zeroconf=None`-Pfad** — Variablen vorinitialisieren, Unregister nur nach erfolgreicher Registrierung. · S *(mit H8 erledigt, 2026-07-18)*
- ☑ **M12 — `datetime.py`: tote Plattform mit garantierten Crashes** — Datei entfernt (nie in `PLATFORMS`, mehrere sichere Crashes; Funktion existiert bereits als `GatewayLastReceivedMessage` in `sensor.py`). · S *(Phase 6, 2026-07-19)*
- ☑ **M13 — `device_info.model` crasht bei `dev_eep=None`** — Guard `self.dev_eep.eep_string if self.dev_eep else None`. · S *(Phase 6, 2026-07-19)*
- ☐ **M14 — Climate: toter Periodik-Task (auskommentiert)** — [climate.py:159-160/200-218](custom_components/eltako/climate.py#L159-L218); bei Reaktivierung Task-Leak/Race. Sauber implementieren (`entry.async_create_background_task` + Cancel) oder Code entfernen. · M

### P3 — NIEDRIG (Korrektheit, Hygiene, Zukunftssicherheit)

- ☑ **N1 — Invertierte Vergleichskette bei `invert_signal`** — [binary_sensor.py:276/286/293](custom_components/eltako/binary_sensor.py#L276): `a != b == 1` wird als `(a != b) and (b == 1)` ausgewertet → mit `invert_signal: true` dauerhaft falsche Zustände. Klammern setzen. · S *(erledigt; D5-00-01 zusätzlich auf korrekte Fenster-Semantik `contact==0 ⇒ offen` gefixt — war testgedeckt falsch herum)*
- ☑ **N2 — `_attr_should_poll = True` für reine Push-Entities** — auf `False`. · S *(Phase 7, 2026-07-19)*
- ☑ **N3 — Ungültige `device_class`-Strings** — BATTERY+Volt → VOLTAGE, `'window'` (Binary-Klasse) → None, `'rain'` → None (+ `""`-Unit → None). Regressionstest prüft alle Sensor-Beschreibungen. · S *(Phase 7, 2026-07-19)*
- ☑ **N4 — `manifest.json`: ungültiger Schlüssel `"panel_custom"`** — entfernt (+ vestigiale `frontend`-Dependency, da Panel-Feature tot). · S *(Phase 7, 2026-07-19)*
- ☑ **N5 — `cv.Number` ist kein öffentliches HA-API** — ersetzt durch öffentliche Validatoren: IDs → `cv.positive_int`, Port → `cv.port`, Delay/Timeouts → `cv.positive_float`, Temperaturen → `vol.Coerce(float)`. Reale `ha.yaml` + `test_config` verifiziert. · S *(Phase 7, 2026-07-19)*
- ☑ **N6 — Logging-Format-Fehler** — die echten Crashes (2× `%s`, 1 Argument) waren die config_flow-Zeilen (mit M10 behoben); fragile f-string-+-%-args-Mischung in init bereinigt. · S *(Phase 7, 2026-07-19)*
- ☑ **N7 — Service `send_message`: bare `except:`, EEP-Konstruktor außerhalb try, Copy-Paste-Fehlermeldung, `import inspect` in Funktion** — [gateway.py:301-341](custom_components/eltako/gateway.py#L301-L341). · S *(erledigt; zusätzlich: fehlende EEP-Parameter zerstören keine Enum-Defaults mehr — `priority=0`-Crash behoben)*
- ☑ **N8 — `LAST_RECEIVED_TELEGRAMS` als Klassen-Dict** — jetzt Instanz-Attribut. Behebt Multi-Gateway-Vermischung, das seit v2.1.3 betroffene Release-Event **und** (Review-Erkenntnis) die A5-30-03-Mehrkanal-Kollision (5 Instanzen teilten sich einen dev_id-Key). Regressionstest. · S *(Phase 7, 2026-07-19)*
- ☑ **N9 — Sensor-Kleinkram** — `msg.data[3]` mit Längencheck; Weather-Station respektiert Nutzernamen (`dev_name == dev_conf.name` war immer wahr); VOC/Language via `.get()` mit Schema-Defaults. · S *(Phase 7, 2026-07-19)*
- ☑ **N10 — Toter/riskanter Code entfernt** — auskommentierte Panel-Blöcke + zugehörige Imports (os, panel_custom, websocket_api, async_register_built_in_panel), doppelte `dev_id`-Property, `get_entity_from_hass` (+ `DATA_ENTITY_PLATFORM`-Import), tote `validate_ids_of_climate`, sowie die verwaiste `frontend/`-View (Review-Nachtrag). `import glob`/`inspect` in gateway.py sind genutzt (Docstring bereits an Zeile 1) → kein Handlungsbedarf. · M *(Phase 7, 2026-07-19)*

---

## 3. Abarbeitungsplan (Phasen)

Jede Phase einzeln umsetzen → Tests laufen lassen → committen. So bleibt jeder Schritt nachvollziehbar und rückrollbar.

### ☑ Phase 0 — Baseline schaffen (vor jedem Fix!) — **ERLEDIGT 2026-07-17**
1. ☑ venv erstellt (`.venv`, Python 3.14.6), Dependencies installiert (HA 2026.7.2, eltako14bus 0.0.73)
2. ☑ Baseline dokumentiert: **138 Tests → 6 Failures, 11 Errors** auf unverändertem Stand.
   - 11 Errors waren Test-Harness-Probleme (Python 3.14: `asyncio.get_event_loop()`; HA 2026.7: neue `ConfigEntry`-Pflichtparameter) → behoben in `tests/mocks.py`, `tests/test_gateway.py`
   - Metadata-Test verglich Paketnamen ohne PEP-503-Normalisierung (`esp2_gateway_adapter` vs. `esp2-gateway-adapter`) → behoben in `tests/test_metadata.py`
3. Hinweis: `tests/test_send_message_service.py` regeneriert bei jedem Lauf `docs/service-send-message/eep-params.md` (Nebenwirkung, ggf. später beheben)

### ☑ Phase 1 — Die Absturzursachen beseitigen (K1–K6) — **ERLEDIGT 2026-07-17**
| Schritt | Befund | Verifikation |
|---|---|---|
| ☑ 1.1 | K1: try/except um Empfangs-Callback | Code-Review; Log statt Thread-Tod |
| ☑ 1.2 | K2: joins in Executor (`async_unload`/`_unload_blocking`-Split, Reconnect-Button via Executor), Join-Timeouts + `is_alive()`-Guards | Testsuite grün |
| ☑ 1.3 | K3: `async_unload_platforms` + Service-Deregistrierung + Cleanup bei Setup-Fehlschlag | Testsuite grün |
| ☑ 1.4 | K4: `.get()`-Fix Taster-Release (+ `b2`→`b2s`, kein `raise` mehr) | vorher ERROR-Tests (F6-10-00, A5-07/08/30) jetzt grün |
| ☑ 1.5 | K5: Priority-Enum-Fix (+ Guards in `_send_command`, `async_handle_priority_events`) | `test_send_message_service` jetzt grün |
| ☑ 1.6 | K6: `ConfigEntryNotReady`/`ConfigEntryError` statt stiller `return`s | Code-Review |

**Testergebnis nach Phase 1: 138 Tests → 3 Failures, 0 Errors.** Die 3 verbleibenden Failures sind ein und dieselbe offene Produktentscheidung (siehe Abschnitt „Offene Produktentscheidungen").

### ☑ Phase 2 — Callbacks & Restore robust machen (H1–H4, H6) — **ERLEDIGT 2026-07-17**
- ☑ 2.1 H1: Fehlerbarriere in `device.py` (schützt vor allen weiteren Callback-Bugs)
- ☑ 2.2 H2: Restore-Parsing in `sensor.py` + `raise e` in light/switch entfernt
- ☑ 2.3 H3: HeaterMode-Konvertierung + OFF-Vergleich (+ `HVACAction.OFF`)
- ☑ 2.4 H4: Cover-None-Guards (`set_cover_position`, `set_cover_tilt_position`, value_changed-Tilt)
- ☑ 2.5 H6: UnboundLocalError in light/select-Setup

**Adversariales Review (3 Reviewer-Lens + Verifikation, via Workflow):** fand 2 echte Nachbesserungen, beide eingearbeitet:
- Sensor-Restore parste alle Werte als `float()` → Integer-Sensoren (PIR, Nachrichtenzähler) kamen als `"42.0"` statt `"42"` zurück. Jetzt: echte Ganzzahlen bleiben `int`.
- H4-None-Guard fehlte in `set_cover_tilt_position` (der Tilt-Service crashte weiter mit `TypeError`). Nachgezogen.

**Testergebnis nach Phase 2: 138 Tests → 3 Failures, 0 Errors** (unverändert die 3 Rocker-Switch-Failures = Produktentscheidung).

### ☑ Phase 3 — Lifecycle & Leaks (H5, H7, H9, H10) — **ERLEDIGT 2026-07-17**
- ☑ 3.1 H7: Gateway-Remove-Methoden + Registrierung in `async_added_to_hass` mit `async_on_remove` (sensor/binary_sensor); climate-Listener via `config_entry.async_on_unload`. Bonus: M1 (`native_value`→`_attr_native_value`) mitgefixt.
- ☑ 3.2 H5: Cover-Tilt auf async + abbrechbaren Task umgestellt (kein `time.sleep` mehr)
- ☑ 3.3 H9: validate_path schließt den Port (Context-Manager)
- ☑ 3.4 H10: geprüft — False Positive für den V2-Bus, absichtlich unverändert

**Adversariales Review (3 Lens + Verifikation, via Workflow):** 5 bestätigte Befunde → 3 distinkte, alle eingearbeitet:
- `is`→`==` in `remove_last_message_received_handler` / `remove_received_message_count_handler` (gebundene Methoden sind bei jedem Zugriff neu → Deregistrierung war ein No-op; Leak beim Deaktivieren der Diagnose-Sensoren).
- Cover-Tilt-Supersede: zweiter Tilt-Befehl während laufender Bewegung → neuer MOVE wurde vor dem STOP des abgebrochenen Tasks gesendet (Tilt = No-op). Fix: abgebrochenen Task awaiten, damit STOP FIFO-mäßig vor dem neuen MOVE liegt.
- Climate-Priority-Subscription-Timing: Registrierung wieder deterministisch im Setup (fängt select.py-Restore-Event) + leak-sicher via `config_entry.async_on_unload`.

**Testergebnis nach Phase 3: 138 Tests → 3 Failures, 0 Errors** (unverändert die 3 Rocker-Switch-Failures).

### ☑ Phase 4 — Virtual Network Gateway härten (H8, M11) — **ERLEDIGT 2026-07-18 (v2.1.3)**
- ☑ 4.1 bind/DNS-Fehlerbehandlung + SO_REUSEADDR + `_running`-Cleanup im finally
- ☑ 4.2 Snapshot-Iteration + Cleanup-Reihenfolge für Client-Listen und Queues
- ☑ 4.3 Socket-Timeouts (10 s) + bounded Queues (1000, drop-on-full) + Client-Cleanup beim Stop
- ☑ 4.4 M11: service_info/zeroconf-Guards
- *(VNG ist beim Nutzer in Benutzung — bestätigt 2026-07-18.)*

**Adversariales Review (8 Finder-Winkel + 1-Vote-Verify):** 4 CONFIRMED-Befunde, alle eingearbeitet:
- **Generation-Token:** Zombie-Server-Thread (Join-Timeout beim Stop) konnte das geteilte `_running` clearen und die Clients des NEU gestarteten Servers schließen → jede Generation bekommt jetzt ein eigenes Stop-Event; Shared-Cleanup nur, solange der Thread die aktuelle Generation ist.
- **Lifecycle-Lock + `_shutdown`-Flag:** paralleles `stop_tcp_server` (Unload vs. Reconnect-Button) konnte `AttributeError` werfen (Unload schlug fehl) bzw. den Server auf einem entladenen Entry wiederbeleben.
- **Chord-Event-IDs sortiert:** F6-02 meldet Zwei-Tasten-Akkorde in Kontakt-Schließreihenfolge → gleiche Kombination erzeugte mal `.._lt_rb`, mal `.._rb_lt`.
- Härtung: `serialize()` vor `sendall` (eine defekte Message trennt nicht mehr alle Clients), errno-Debug-Log im OSError-Pfad, Lazy-Logging auf Hot-Paths.
- REFUTED (geprüft, kein Fix nötig): Gateway-Info-Timeout-Stapelung (TCP-Sendepuffer), SO_REUSEADDR-Windows-Testbedenken (empirisch widerlegt), historische Chord-ID-Formel (beweisbar identisch).

### ☑ Phase 5 — Climate-Funktionsfehler (H11, M2, M5) — **ERLEDIGT 2026-07-17**
- ☑ H11 Cooling-Kette, ☑ M2 TURN_ON/OFF (+ Review-Fix F1), ☑ M5 Restore, ☑ M3 (schon Phase 1), ⏸️ M14 (Hardware nötig)
- Adversarial reviewt: F1 gefunden+behoben (turn_off schaltete OFF-Gerät ein). Cooling nicht hardware-getestet.

### ☑ K7 — Geräte über HA-UI löschbar — **ERLEDIGT 2026-07-17** (von dir dokumentiert)
- `async_remove_config_entry_device` (permissive `return True`) in `eltako_integration_init.py`. Optionaler Config-Abgleich = Folge-Task.

### ☑ K8 — LAN-Gateway: TCP-Verbindung härten — **ERLEDIGT 2026-07-17**
**Anlass:** Nutzer-Logs zeigten außerdem, dass die *eigentliche* „geht nicht"-Ursache ein Setup-Crash beim Parsen des Gateway-Namens war (`int("2)")`) — in v2.1.0 bereits behoben. Zusätzlich zur TCP-Robustheit:
- Die Integration übergab dem `TCP2SerialCommunicator` keine Timeouts → Lib-Defaults 60 s Reconnect + 60 s Keep-Alive → lange Ausfälle pro Abbruch.
- **Fix:** `reconnection_timeout` (Default **15 s**) und `tcp_keep_alive_timeout` (Default **30 s**) werden übergeben und sind pro Gateway per YAML konfigurierbar (`reconnection_timeout:` / `tcp_keep_alive_timeout:`).
- ~~**Offen/Upstream:** `esp2_gateway_adapter` 0.2.15 behandelt `recv()==b''` (Gegenstelle schließt sauber) als „Nachricht" statt als Disconnect → theoretische Endlosschleife ohne Reconnect.~~ **ERLEDIGT 2026-07-18 (v2.1.2):** Live am Gateway (192.168.177.15:2325, TCM515) reproduziert — Gateway schließt bei zweitem Client sauber (FIN), Symptom „nur HA-Neustart hilft" bestätigt. Prüfung ergab: Bug ist **auch in 0.2.21 noch vorhanden** (per Test gegen die ungepatchte Klasse verifiziert, `run()` unverändert). Fix in der Integration: `tcp2serial_hardened.py` (`HardenedTCP2SerialCommunicator`) — `recv()==b''` → `ConnectionResetError` → Reconnect-Pfad der Lib; zusätzlich Kernel-TCP-Keepalive (`SO_KEEPALIVE`). Lib auf **0.2.21** gebumpt (Daemon-Thread, ACK-Handling). Regressionstest: `tests/test_tcp_connection_hardening.py`. **Achtung:** `run()` ist eine gepatchte Kopie aus 0.2.21 → bei jedem künftigen Lib-Bump gegen Upstream-`run()` abgleichen. Upstream-Meldung an grimmpp/esp2_gateway_adapter weiterhin sinnvoll.

### ☑ Phase 6 — Mittlere Priorität abarbeiten (M4, M7–M10, M12, M13) — **ERLEDIGT 2026-07-19 (v2.1.4)**
- M1/M3/M6 waren bereits in früheren Phasen erledigt; M11 mit H8 (Phase 4). M14 bleibt offen (Hardware nötig).
- Umgesetzt: M4 (light org-Guard), M7 (frozen dataclass), M8 (event.data.get), M9 (Gateway-Id-Regex + get_device_config), M10 (Gerätetyp aus YAML statt Substring + Baud-Guard), M12 (tote datetime.py entfernt), M13 (model-None-Guard).
- Adversarial reviewt (3 Winkel): Kernänderungen bestätigt; 1 latenter `KeyError` (unmapped `ftd14`) in den angefassten Funktionen gehärtet. 6 neue Regressionstests. **152 Tests grün.**
- **Offen M14** — Climate: toter Periodik-Task (auskommentiert), Reaktivierung nur mit Hardware sinnvoll.

### ☑ Phase 7 — Aufräumen & Zukunftssicherheit (N1–N10) — **ERLEDIGT 2026-07-19 (v2.1.5)**
- N1/N7 bereits in früheren Phasen erledigt. N2–N6, N8–N10 in Phase 7.
- Korrektheit: N3 (device_class), N8 (Instanz-Dict, behebt Multi-Gateway + A5-30-03-Kollision), N9 (Weather-Name immer überschrieben).
- Hygiene/Zukunftssicherheit: N2 (should_poll), N5 (öffentliche Validatoren), N6 (Logging), N4 (Manifest/hassfest), N10 (toter Code + frontend/).
- Adversarial reviewt (2 Winkel): **0 Regressionen**; Review fand zusätzlich einen hängenden `InfoPageView`-Import (behoben) und bestätigte den A5-30-03-Bonus-Fix. 4 neue Regressionstests (Phase 6+7). **154 Tests grün.**

### ◑ Phase 8 — Abschluss-Verifikation (weitgehend erledigt 2026-07-19)
1. ☑ Kompletter Testlauf: **154 Tests grün, 0 Fehler** (Baseline-Failures aus Phase 0 alle behoben). Neue Regressionstests in Phase 4/6/7 ergänzt.
2. ☑ `hassfest`-Validierung **grün** (CI-Lauf auf v2.1.6 bestätigt). War vorbestehend rot wegen fehlender `zeroconf`-Dependency → in v2.1.6 deklariert.
3. ☑ Config-Ladepfad verifiziert: reale `ha.yaml` validiert end-to-end gegen `CONFIG_SCHEMA` (2 Gateways, 22 Geräte). Dabei Tippfehler im VNG-`device_type` der Beispiel-Config behoben und den zugehörigen Test repariert (rief CONFIG_SCHEMA auf falscher Ebene auf → validierte Gateways nie).
4. ☑ `changes.md` + `manifest.json`-Version durchgängig gepflegt (v2.1.0 … v2.1.6).
5. ☐ **Offen — nur vom Nutzer durchführbar:**
   - Manueller HA-Durchlauf am echten System: Start → Reload → Reconnect-Button → Taster drücken/loslassen → HA-Neustart (Restore) → Langzeit-Log.
   - **HACS-Action** (CI) rot wegen **Repo-Einstellungen** (kein Code): GitHub-Repo braucht (a) *Topics* und (b) aktivierte *Issues*. Über GitHub-Weboberfläche/-Settings setzen (braucht Repo-Admin-Rechte, nicht per Code lösbar).

### ☐ Restlicher offener Befund
- **M14** — Climate: toter Periodik-Task (auskommentiert). Reaktivierung nur mit echter Heiz-/Kühl-Hardware sinnvoll testbar. *(Siehe auch AC4 unten — dasselbe Thema, im Audit Runde 2 präzisiert.)*

---

## 6. Audit Runde 2 (2026-07-19) — Backlog der zurückgestellten Befunde

> Gesamt-Codebase-Audit (6 parallele Auditoren, 48 Kandidaten). Die ~20 sicheren, unit-testbaren Fixes wurden in **v2.2.0** umgesetzt. Die folgenden Befunde sind **Verhaltensänderungen** und warten auf das Hardware-/HA-Test-Setup. Priorität: ⚡ = hoher Nutzer-Impact.

### Climate (Cooling-Pfad, nie hardware-getestet — zusammen mit M14 angehen)
- ☐ **AC1 ⚡** — `change_temperature_values` erzwingt HEAT/HEATING bei jedem Telegramm; `HVACAction.COOLING` wird nirgends gesetzt → Cooling-Status flippt bei jedem Aktor-Telegramm zurück. Fix: auf `_get_mode()` verzweigen.
- ☐ **AC2** — THERMOSTAT-Priorität sendet faktisch „Solltemperatur 40 °C" (`int(40/40*255)=0xFF` in DB2). Intent lt. Kommentar `00-00-00-08`; eep.py dokumentiert Thermostat-Prio zudem als 0x0E. Mit Hardware klären, dann `target_temp=0` bzw. korrekten Prio-Code senden.
- ☐ **AC3** — HEAT↔COOL-Übergang ruft `async_set_hvac_mode` → sendet Normal-Mode (0x70) auch wenn Gerät OFF/Preset → schaltet ausgeschaltete Heizung ungefragt ein.
- ☐ **AC4** (= M14) — Auskommentierter Periodik-Task: (a) Cooling-Keep-alive via `cooling.sender` wird nie gesendet, (b) Cooling-Expiry wird nur bei eingehenden Events geprüft (FTS14EM aus → Status hängt ewig auf COOL), (c) Aktor-Watchdog-Refresh fehlt.
- ☐ **AC5** — Cooling-Expiry: Wanduhr (`time.time()`) statt `monotonic()` und Vergleich exakt = Repeat-Periode (15 min, null Toleranz) → Flattern bei verlorenem Telegramm/NTP-Sprung. Fix: monotonic + Marge (~1,5×).
- ☐ **AC6** — Eingehende A5-10-06-Telegramme überschreiben Zieltemperatur/Modus ohne Prioritätsprüfung (Thermostat-Stale-Wert schlägt HA-Sollwert); `HeaterMode.UNKNOWN` wird gespeichert und später als Kommando-Byte 0x00 re-gesendet.
- ☐ **AC7** — `async_set_hvac_mode`: OFF schreibt keinen State (UI zeigt alt), HEAT-Anforderung im COOL-Modus wird still ignoriert, OFF wirkt als Toggle (schaltet Aus wieder ein — nur `turn_off` wurde in M2/F1 gefixt).
- ☐ **AC8** — Thermostat-Telegramme werden mit dem Aktor-EEP dekodiert (`self.dev_eep` statt `self.thermostat.eep`) → z. B. A5-10-12-Thermostat liefert Unsinn ohne Warnung.

### Multi-Gateway (⚡ betrifft Setups mit >1 Gateway — beim Nutzer vorhanden!)
- ☐ **AM1 ⚡⚡** — `get_identifier` lässt die Gateway-Id weg, wenn `dev_id=None` → ALLE Gateways teilen sich das Dispatcher-Signal `eltako_send_message` (jedes Kommando geht auf **alle** Busse, N-fache Echos) und der Send-Service `eltako_send_message_service` wird vom letzten Gateway überschrieben. Fix (Breaking: Service-/Event-Umbenennung!): gw-Präfix auch bei `dev_id=None`.
- ☐ **AM2 ⚡** — 1BS-Telegramme (`EltakoWrapped1BS`, D5-00-01/FTS14EM) fehlen in der Adress-Rewrite-Liste des Gateways, `_message_received_callback` addiert dann die EIGENE base_id auf fremde lokale Adressen → bei 2 Bus-Gateways aktualisiert Gateway-B-Telegramm die Entity von Gateway A. Fix: Rewrite-Liste ergänzen + `data['gateway']`-Vergleich (TODO device.py:174).
- ⏸ **AM3** — unique_id verliert den `left`/`right`-Diskriminator (`b2s` nutzt nur `addr[0]`) → zwei Rocker-Hälften desselben Tasters kollidieren, eine Entity fehlt still. **ZURÜCKGESTELLT** (Nutzer-Entscheidung 2026-07-19): Fix ändert die id JEDER left/right-Entity → ohne (config-abhängige, komplexe) Migration verwaisen auch nicht-kollidierende Einzel-Entities; niedrige Priorität + seltene Config. Quelle lokalisiert: `CONF_ID_REGEX` erlaubt ` left/right`, switch.py/light.py filtern per `dev_id[1]`, unique_id ist keyless `eltako_gw_<id>_<addr>`.

### Gateway / Init
- ☑ **AG1 ⚡** — ESP2-Gateways außer FAM14 (FGW14-USB, FAM-USB, LAN_ESP2) fragen nie eine Base-Id ab; Schema-Default `00-00-00-00` + Gate `int(base_id)!=0` verwirft dann ALLE empfangenen Telegramme kommentarlos (Zähler zählt weiter!). **ERLEDIGT v2.9.0** (B4): Log-Warnung + **Reparatur-Hinweis** (`repairs`-Issue `missing_base_id`), wenn ein solcher Typ mit Base-Id `00-00-00-00` läuft; verschwindet bei gültiger Config/Reload.
- ☐ **AG1b** (Follow-up aus B4-Review) — Laufzeit-Fehlschlag der Base-Id-*Abfrage*: ESP3/LAN-Gateways, die zwar abfragen, aber nie eine Antwort erhalten, bleiben auf `00-00-00-00` und verwerfen still alle Telegramme — der AG1-Repair (Setup-Zeit, nur „fragt nie ab"-Typen) fängt das NICHT. Bräuchte einen verzögerten/Laufzeit-Check (z. B. wenn nach Connect + N s die Base-Id noch 0 ist → Repair). Eigenständiger Fehlermodus.
- ☐ **AG2** — Rewrite-Liste enthält Catch-all `EltakoMessage` → Payload von 0x8b-Bus-Management-Telegrammen wird verfälscht ans globale Event-Bus/VNG weitergereicht; `AddressExpression.add()` kann OverflowError werfen (still verschluckt).
- ☐ **AG3** — `send_message`-Service: kein Service-Schema, keine Sender-Id-Validierung, unkoerzierte YAML-Werte (String statt int → TypeError), Fehler erreichen den Aufrufer nie (Service wirkt immer erfolgreich).
- ☐ **AG4** — Sender-Id-Validierung vergleicht nur 3 Bytes → Base+Offset-Fenster (128 Ids) wird falsch geprüft (falsche Warnungen bei Boundary-Crossing, keine Warnung bei ungültigen Ids).
- ☐ **AG5** — Tote v1.3.4-Migrationslogik in init (hass.data ist bei Start immer leer); `async_get_base_ids_of_registered_gateway` würde bei Aufruf IndexError werfen (Geräte ohne connections).

### Sensor (unique_id-Änderungen = Registry-Migration nötig)
- ☑ **AS1 ⚡** — Tarif fehlt in der unique_id → Multi-Tarif-Zähler (`meter_tariffs: [1,2]`) erzeugen Kollisionen, Tarif ≥2 fehlt still. **ERLEDIGT v2.7.0** (B3): Tarif ist jetzt Teil der unique_id; erster Tarif behält die schlichte id (Bestandsschutz, keine Migration), Zusatz-Tarife bekommen `_tariff_<wert>`.
- ☑ **AS2** — VOC: `VOLATILE_ORGANIC_COMPOUNDS` (µg/m³) mit Einheit ppb → Statistik-Ablehnung; korrekt wäre `VOLATILE_ORGANIC_COMPOUNDS_PARTS`; Nicht-Total-Substanzen haben Einheit `''`. **ERLEDIGT v2.8.0** (B3): device_class aus Einheit abgeleitet (Total→`_PARTS`+ppb, Substanzen→kein device_class/unit).
- ☑ **AS3** — Lokalisierter Substanzname in der unique_id → Sprachwechsel verwaist Entities/Statistiken. **ERLEDIGT v2.8.0** (B3): sprachunabhängiger Key (name_en), lokalisierter Anzeigename; erste Entity-Registry-Migration (1.2→1.3) mit Mehrdeutigkeits-Ausschluss (Upstream-„Styren"-Bug) + Kollisions-Guard.
- ☐ **AS4** (Upstream eltakobus) — A5-10-03-Decode: `target_temp` ohne `+8`-Offset → Zieltemperatur 8 K zu niedrig und gestaucht (Encode symmetrisch falsch → Tests round-trippen grün).
- ☐ **AS5** — A5-07-01-PIR-Sensor publiziert Roh-Byte (0–255) als einheitslosen MEASUREMENT statt ≥128-Semantik (Designentscheidung).

### Cover / Switch
- ☐ **AV1** — Restore: HA-State `open` überschreibt die gespeicherte Position mit 100 (offen = irgendwas >0!) → falsche Fahrtrichtung nach Neustart bei Zwischenposition.
- ☐ **AV2** — F6-02-Schalter: nur obere Rocker-Hälfte (Aktionen 1/3) wird verarbeitet und togglet; untere Hälfte (0/2 = AUS, genau was `turn_off` sendet) wird ignoriert → invertierter HA-State bis zum nächsten Top-Press. Fix: 0/2 = explizit AUS, 1/3 = explizit AN.

### Config-Flow / VNG-Protokoll
- ☐ **AF1** — Kein `async_set_unique_id`/`_abort_if_unique_id_configured` → bei fehlgeschlagenem Setup kann derselbe Gateway doppelt angelegt werden (zweiter Entry überschreibt `gateway_<id>`, Unload zerlegt den jeweils anderen).
- ☐ **AN1** — VNG `send_gateway_info`: Gateway-Typ-Code = Enum-Listenposition+1 (Aliase kollabieren!, kein Empfänger dekodiert ihn, `get_by_index` ohne −1); Client übernimmt bei N Gateways nur die LETZTE Base-Id für alle Adressübersetzungen.
- ☐ **AN2** — Gesendete (nicht empfangene) Telegramme werden ohne Adressübersetzung ans VNG weitergereicht (`convert_bus_address_to_external_address` = toter TODO-Stub) → Mehrbus-Ambiguität bei Zweit-HA.
- ☐ **AN3** — `CONF_ID_REGEX` erlaubt ` left/right`-Suffix auch bei base_id/sender_id, wo er bedeutungslos ist → fehlerhafte Configs passieren die Validierung.

---

## 7. Agenda Runde 3 (2026-07-19) — neue Vorhaben V1–V3

> Vom Nutzer beauftragt am 2026-07-19. **Empfohlene Reihenfolge: V1 → V2 → V3.**
> Begründung: Erst alle Features in main konsolidieren (V1), damit Analyse und
> Weiterentwicklungsplan (V2) auf dem vollständigen Code aufsetzen; das neue
> BaseID-Schreib-Feature (V3) profitiert vom Analyse-Wissen und braucht ohnehin
> die Hardware-Session zum Testen (Schreib-Limit!). Die Hardware-Punkte aus
> Abschnitt 6 (AM1/AM2/AG1, AC1–AC8/M14) und der manuelle HA-Durchlauf lassen
> sich mit dem V3-Test in EINER Session bündeln.

### ◑ V1 — Branch-Konsolidierung: alle Features nach main überführen

**V1.1 Inventar + V1.2 Triage (2026-07-19):**

| Branch | Inhalt | Entscheidung |
|---|---|---|
| grimmpp-patch-5 | nur Version-Bump 1.5→1.5.1 (Lib-Pins 2024) | **verwerfen** (überholt) |
| grimmpp-patch-9 | Doku-Tippfehler „Gatways" | ☑ **übernommen** (Doku-Fix, v2.3.0) |
| feature-branch | `!=`-Klammerung (in main gefixt), raise-Entfernung, eltako14bus 0.0.82 | **überholt** (alle Fixes in main besser) |
| feature-branch-v1 | cover `.get()`-Restore, `async_forward_entry_setups` | **überholt** (in main via H-/Round-2-Fixes) |
| version2.1 | ⊂ version2 (0 eigene Commits) | **verwerfen** (Teilmenge) |
| version2 | 9 echte Features F1–F9 (Rest = veralteter Prä-v2.x-Stand) | **Feature-Extraktion**, siehe unten |

**Lib-Befund:** eltako14bus 0.0.82 ist funktional kompatibel (159 Funktionstests grün), behebt aber KEINEN der 4 dokumentierten Lib-Bugs (alle empirisch noch vorhanden). Bump nur nötig für F7 (Repeater braucht `send_repeater_mode*`).

**version2-Features (F1–F9), Disposition:**
- ☑ **F1 — optionales `area`-Feld** (Geräte-Area aus YAML): nach main portiert in v2.3.0, HA-nativ via `suggested_area` + **non-destruktive** Einmalzuweisung (überschreibt manuelle User-Area NICHT, anders als upstream).
- ☑ **F9 — `temperature_unit` optional** (Default °C): portiert v2.3.0.
- ☐ **F5 — Climate Display/State-Fixes** ⚡ (None-Defaults statt 0°, optimistische UI, **externe-Adress-Matching** — vermutliche Ursache „Climate aktualisiert nicht"): **Hardware-Session**, hoher Wert/Aufwand.
- ☐ **F2 — `room_sensor`** (externe HA-Temperatur an Aktor, ersetzt Fake-40°C): Hardware-Session; Debounce beim Portieren ergänzen.
- ☐ **F4 — `ACTUATOR_ACK` (0x0F) Default-Priorität ohne Thermostat**: upstreams empirische Antwort auf AC2 — Hardware-Session zum Bestätigen.
- ☐ **F3 — `off_temperature`** (Frostschutz-OFF): Hardware-Session, opt-in.
- ☐ **F6 — Prioritäts-Select nur bei Thermostat** + **F9 done**: UX; F6 entfernt Entity → Registry-Migration bedenken; Hardware-Session.
- ☐ **F7 — Repeater-Mode-Select** (Gateway als Repeater): braucht Lib-Bump 0.0.82; version2 hat Base-Id-Bug im `8B99`-Handler (nicht mitportieren!) + ESP3-Guard nötig. Zweite Welle.
- ☐ **F8 — Frontend-Panel + WebSocket-API**: experimentell/teils kaputt (`ShaddowDataManager` unvollständig, entfernte HA-API, ext. Paket `home_assistant_eltako_frontend`). **Zurückgestellt/verworfen** bis eigenständig entschieden.

**V1.3 offen:** Climate-Cluster (F2–F6) + F7 in der Hardware-Session portieren (mit AC1–AC8 bündeln — überlappender Code). **V1.4 offen:** obsolete Remote-Branches im FMDHET-Fork löschen (grimmpp-patch-5, feature-branch, feature-branch-v1, version2.1; version2 nach vollständiger Feature-Extraktion).

---

### ☐ V1 (Original-Beschreibung) — Branch-Konsolidierung: alle Features nach main überführen
**Ziel:** Kein Feature liegt mehr nur in einem Seitenzweig; main ist die einzige Quelle der Wahrheit.
**Ist-Stand (2026-07-19):** 6 Remote-Branches sind NICHT in main gemergt: `feature-branch`, `feature-branch-v1`, `grimmpp-patch-5`, `grimmpp-patch-9`, `version2`, `version2.1`. (Alle `stability-fixes-*`, `hassfest-zeroconf-dep` sowie `heating`, `integration-FLGTF55`, `EEP-A5-04-01-and-A5-07-01`, `feature/invert-sensor-signal`, `feature/testing`, `grimmpp-patch-1–4/6–8` sind bereits enthalten.)
**Schritte:**
1. Pro Branch: `git log main..origin/<branch>` + Diff sichten → Feature-Inventar erstellen (was ist drin, was davon fehlt in main tatsächlich noch — viele Patches sind vermutlich veraltet/überholt durch v2.x).
2. Je Branch entscheiden: **übernehmen** (rebase/cherry-pick auf main, Konflikte gegen v2.2.0-Stand lösen), **überholt** (Feature existiert in main in besserer Form → Branch als obsolet markieren) oder **verwerfen** (mit Begründung dokumentieren).
3. Übernommene Features: Tests ergänzen/anpassen, adversariales Review wie gehabt, dann `--no-ff`-Merge.
4. Funktionsprüfung: ohne HA-Setup per Unit-/Mock-Tests; hardwarenahe Features auf die Hardware-Session-Checkliste setzen.
5. Abschluss: obsolete Branches im Fork löschen (nur FMDHET, nichts Richtung grimmpp), damit die Branch-Liste den echten Zustand zeigt.
**Aufwand:** M–L (abhängig vom Inventar). **Kein Hardware-Zwang** für die Sichtung; einzelne Features ggf. doch.

### ☑ V2 — Komplettanalyse & Weiterentwicklungsplan („auf Herz und Nieren") — **ERLEDIGT 2026-07-19**
Ergebnis in **`ANALYSE-UND-ROADMAP.md`** (eigene Datei): Messungen (Coverage 53 %, ruff), Architektur (A1–A6, Kern: A5 Adressierung ohne Eigentümer = Wurzel AM1/AM2/AG1), EEP-Matrix (alle 25 Lib-EEPs verdrahtet; Lücken T1–T3), HA-Konformität (Availability/unique_id-Migration/entity_category/Diagnostics/Repairs; API-seitig bereits sauber), Teststrategie (28 Punkte, Ziel ~70 %), Abhängigkeits-Strategie (patchen statt forken), Doku/UX (D1–D9/U1–U3), und eine **5-Wellen-Roadmap** (A Quick-Wins → B Lifecycle → C Architektur → D Hardware-Features/V3 → E strategisch). Details siehe die Datei; Original-Beschreibung unten.

### ☐ V2 (Original-Beschreibung) — Komplettanalyse & Weiterentwicklungsplan
**Ziel:** Detaillierte Prüfung der GESAMTEN Integration auf Richtigkeit, Verbesserungen, Erweiterungen; Ergebnis als eigene Markdown-Datei (Vorschlag: `ANALYSE-UND-ROADMAP.md`) + priorisierter Weiterentwicklungsplan.
**Abgrenzung zu Audit R2 (Abschnitt 6):** R2 suchte gezielt latente *Bugs*. V2 ist breiter: Architektur, EEP-Abdeckung vs. Eltako-Produktpalette, Testabdeckung (Lücken: Config-Flow ungetestet!, Meter-Sensoren ungetestet), HA-Best-Practices (config entries vs. YAML, Entity-Naming, unique_id-Migrationspfad), Doku-Qualität, Upstream-Differenz zu grimmpp, Dependency-Strategie (4 dokumentierte Library-Bugs → Fork der Libs erwägen?).
**Schritte:**
1. Statische Komplettanalyse (Architektur-Review, HA-Konventions-Check, hassfest/HACS-Kür).
2. Dynamische Tests soweit ohne Hardware möglich: Testabdeckung messen (coverage), fehlende Testklassen ergänzen (Config-Flow!, Meter, VNG-Protokoll), Mock-Bus-Integrationstests.
3. EEP-/Geräte-Matrix: unterstützte vs. in Doku versprochene vs. real existierende Eltako-Geräte.
4. Findings-Datei schreiben (Struktur wie dieses Dokument: Befund → Priorität → Aufwand), inkl. Bewertung der Abschnitt-6-Backlog-Punkte im Gesamtkontext.
5. Roadmap ableiten: kurzfristig (Bugfixes/Backlog), mittelfristig (Features wie V3, UI-Konfiguration), langfristig (Architektur, Upstream-Beitrag).
**Aufwand:** L. **Kein Hardware-Zwang** (Hardware-Verifikation einzelner Findings → Session-Checkliste).

### ☐ V3 — Feature: Gateway-BaseID aus Home Assistant heraus SCHREIBEN
**Ziel:** BaseID nicht nur lesen (heute: `GatewayBaseId`-Sensor), sondern ändern — mit **verpflichtender Nutzer-Quittierung**, da EnOcean-Chips nur eine stark begrenzte Anzahl BaseID-Änderungen erlauben (ESP3 `CO_WR_IDBASE`: max. ~10 Schreibvorgänge pro Chip, danach permanent gesperrt!).
**Ist-Stand Technik (geprüft 2026-07-19):** Weder `esp2_gateway_adapter` noch `eltakobus` implementieren einen Schreibpfad — nur `send_base_id_request` (lesen). Nötig ist:
1. **Recherche/Design:** (a) ESP3-Gateways (USB300, MGW/EUL-LAN): `CO_WR_IDBASE` (Common Command 0x07) + RET-Code-Auswertung (u. a. „FLASH_HW_ERROR/zu oft geschrieben"); Rest-Zähler ist NICHT auslesbar → Warnung muss das deutlich sagen. (b) ESP2-Gateways (FAM14/FAM-USB): klären, ob BaseID überhaupt per Telegramm änderbar ist (FAM14: Drehschalter/PCT14 — vermutlich nicht) → Feature ggf. nur für ESP3-Typen anbieten und für andere sauber ausblenden.
2. **Library-Erweiterung:** `send_write_base_id(new_base_id)` + Antwort-Handling im Fork der Lib oder lokal in `tcp2serial_hardened.py`-Manier (gepinnte Kopie), da Upstream-Release nicht in unserer Hand.
3. **HA-UX mit Quittierung (Designentscheidung nötig):** Vorschlag: Text-Entity (neue BaseID eingeben, validiert gegen Adressformat) + Bestätigungs-Button, der erst nach gesetztem Text aktiv wird und die Warnung (Schreib-Limit!) in Name/Beschreibung trägt; alternativ Service `eltako.write_base_id` mit Pflichtparameter `confirm: true`. KEIN Schreiben ohne expliziten zweiten Schritt; Erfolg/Fehlschlag als Event + Log; nach Erfolg Re-Read der BaseID zur Verifikation.
4. **Sicherheitsnetz:** Rate-Limit im Code (z. B. 1 Schreibvorgang pro HA-Laufzeit ohne Neustart-Override), Validierung des erlaubten Adressbereichs (0xFF800000–0xFFFFFF80 bei ESP3), Abbruch bei identischer BaseID.
5. **Tests:** Mock-Kommunikator (Schreib-Telegramm + RET-Codes simulieren); am echten Gerät **höchstens 1–2 Schreibvorgänge** einplanen (Limit!) — fester Punkt der Hardware-Session.
**Aufwand:** M–L. **Hardware-Session zwingend** für den Abschlusstest.

---

## 3a. Offene Produktentscheidungen (bitte entscheiden)

1. ~~**Rocker-Switch: 1 oder 2 Events pro Telegramm?**~~ **ENTSCHIEDEN 2026-07-18 (v2.1.3): Variante (a), 2 Events.** Das button-spezifische Event (Suffix z. B. `_rt`) ist reaktiviert — Prä-v2.0.0-Automationen funktionieren wieder; die 3 Test-Failures sind Geschichte (Suite seither komplett grün). Akkord-IDs werden sortiert gefeuert (`_lt_rb`, nie `_rb_lt`), siehe Phase-4-Review.

2. **D5-00-01-Verhaltensänderung (bereits umgesetzt, bitte prüfen):**
   Fensterkontakt-Semantik war invertiert (geschlossenes Fenster wurde als „offen" angezeigt). Jetzt EnOcean-konform: `contact == 0` ⇒ offen ⇒ `is_on = True` (gedeckt durch `test_binary_sensor_D5_00_01`). **Wer das alte (falsche) Verhalten mit `invert_signal: true` kompensiert hat, muss das Flag entfernen.**

## 3b. Neu entdeckte Bibliotheks-Bugs (eltako14bus 0.0.73) — Kandidaten für Upstream-Issues

- ☑ `DefaultEnum.__repr__` ([util.py:103](https://github.com/grimmpp/eltako14bus)) shadowt das Builtin `repr` → `UnboundLocalError`, sobald ein `ControllerPriority`-Wert in einen f-string/`repr()` gerät. **GEPATCHT v2.10.0** (B5, `eltakobus_patches.py`). Der gateway.py-Workaround (Logging per `.name`) bleibt als Gürtel-und-Hosenträger.
- ⏸ `_TemperatureAndHumiditySensor3` (A5-04-03): Default `temperature=-20` lässt sich nicht encodieren (`ValueError: byte must be in range(0, 256)`) — `encode_message` rechnet ohne Offset. Workaround: Send-Message-Service füllt numerische Parameter weiterhin mit 0. **ZURÜCKGESTELLT (B5→Hardware-Session):** ändert an Hardware gesendete Telegramme, ohne Gerät nicht verifizierbar.
- ⏸ *(Audit R2, 2026-07-19)* **A5-10-03-Decode ohne `+8`-Offset** (= AS4): `target_temp = (data[1]/255)*22` statt `8 + …` → Zieltemperatur 8 K zu niedrig; Encode symmetrisch falsch, daher round-trippen Unit-Tests grün — nur echte Hardware zeigt es. **ZURÜCKGESTELLT (B5→Hardware-Session):** Climate, Decode+Encode müssen konsistent auf den Spec-Wert; ohne Gerät riskant.
- ☑ *(Audit R2, 2026-07-19)* **A5-30-01/-03: Learn-Bit-Encode/Decode-Asymmetrie**: `encode_message` schreibt `data[3] = learn_button` (ohne `<<3`), Decode liest Bit 3. Folge: vom Send-Message-Service erzeugte A5-30-Telegramme mit `learn_button=1` dekodieren als Teach-in und werden seit v2.2.0 von den (korrekten) Learn-Guards verworfen. **GEPATCHT v2.10.0** (B5, `eltakobus_patches.py`, Wrapper setzt Bit 3; Round-Trip-Test).

## 4. Hinweise

- **Größter Hebel zuerst:** Phase 1 + 2 beheben die eigentlichen Absturzmechanismen. Alles Weitere erhöht Robustheit und Wartbarkeit.
- Die Befunde K1/K2/K3 erklären auch „mysteriöse" Symptome: Integration empfängt nichts mehr (K1), HA friert komplett ein (K2), nach Config-Änderung doppelte Events/Entities (K3).
- Upstream-Sync: Dieses Repo ist ein Fork von `grimmpp/home-assistant-eltako`. Es lohnt sich, die Fixes als saubere, thematisch getrennte Commits zu machen — dann können sie ggf. auch upstream als PRs eingereicht werden.

## 5. Fortschritts-Log

| Datum | Schritt | Ergebnis |
|---|---|---|
| 2026-07-17 | Analyse abgeschlossen, Plan erstellt | Dieses Dokument |
| 2026-07-17 | Phase 0: venv (Py 3.14.6, HA 2026.7.2), Test-Harness gefixt (mocks/ConfigEntry/Metadata-Normalisierung) | Baseline: 138 Tests, 6 F / 11 E → nach Harness-Fix 6 F / 2 E |
| 2026-07-17 | Phase 1: K1–K6 umgesetzt; dazu M3, M6, N1 (+D5-Semantik), N7, M1-Teilfix (VNG `tcp_thread`) | **138 Tests, 3 F / 0 E** — Rest = Produktentscheidung Rocker-Events |
| 2026-07-17 | Branch `stability-fixes` angelegt; thematisch getrennte Commits | siehe `git log` |
| 2026-07-17 | Phase 1 nach GitHub gepusht + via PR #1 in FMDHET/main gemergt (nichts an grimmpp) | `origin/main` aktuell |
| 2026-07-17 | Phase 2: H1, H2, H3, H4, H6 auf Branch `stability-fixes-phase2` | 138 Tests, 3 F / 0 E |
| 2026-07-17 | Phase 2 adversarial reviewt (Workflow, 3 Lens + Verify); 2 bestätigte Nachbesserungen eingearbeitet | Sensor int/float, Cover-Tilt None-Guard |
| 2026-07-17 | Phase 2 nach GitHub gepusht + in FMDHET/main gemergt (`afc1937`) | `origin/main` aktuell |
| 2026-07-17 | Phase 3: H5, H7, H9 auf Branch `stability-fixes-phase3`; H10 geprüft (kein Fix) | 138 Tests, 3 F / 0 E |
| 2026-07-17 | Phase 3 adversarial reviewt (Workflow); 3 distinkte Befunde eingearbeitet | is→==, Tilt-Ordering, Climate-Timing |
| 2026-07-17 | Phase 3 gemergt (`55f6601`); **Release v2.1.0** (manifest, changes.md, Auto-Release-Workflow, Tag) | HACS zeigt saubere Version |
| 2026-07-17 | Nutzer-Logs analysiert → echte „geht nicht"-Ursache = Gateway-ID-Parse-Crash (`int("2)")`), in v2.1.0 bereits behoben | Diagnose |
| 2026-07-17 | Phase 5: H11, M2 (+F1), M5 + K7 (Geräte-Löschen); nach versehentlichem `git reset` aus Snapshot wiederhergestellt | 138 Tests, 3 F / 0 E |
| 2026-07-17 | K8: LAN-TCP-Reconnect 60s→15s / Keep-Alive 60s→30s, konfigurierbar | für Release v2.1.1 |
| 2026-07-18 | K8-Nachtrag (v2.1.2): EOF-Bug live am Gateway reproduziert (Single-Client-Verdrängung, FHEM als Zweit-Client identifiziert); Fix `tcp2serial_hardened.py` + Lib-Bump 0.2.21; am echten System verifiziert | **Release v2.1.2** |
| 2026-07-18 | Produktentscheidung Rocker: 2 Events reaktiviert → Suite erstmals komplett grün (139) | für Release v2.1.3 |
| 2026-07-18 | Phase 4: H8 a–d + M11 auf Branch `stability-fixes-phase4`; 6 neue VNG-Funktionstests | 145 Tests grün |
| 2026-07-18 | Phase 4 adversarial reviewt (8 Winkel, 1-Vote-Verify): 4 CONFIRMED eingearbeitet (Generation-Token, Lifecycle-Lock, sortierte Chord-IDs, Serialize-Härtung), 4 REFUTED | **146 Tests, 0 F** |
| 2026-07-19 | Phase 6: M4, M7–M10, M12, M13 auf Branch `stability-fixes-phase6`; datetime.py entfernt; 6 neue Regressionstests | 152 Tests grün |
| 2026-07-19 | Phase 6 adversarial reviewt (3 Winkel): Kernänderungen bestätigt, `ftd14`-KeyError gehärtet (config_flow + init) | **152 Tests, 0 F**, Release v2.1.4 |
| 2026-07-19 | Phase 7: N2–N6, N8–N10 auf Branch `stability-fixes-phase7`; totes frontend/ + Panel-Reste entfernt; 2 neue Regressionstests | 154 Tests grün |
| 2026-07-19 | Phase 7 adversarial reviewt (2 Winkel): 0 Regressionen; hängender InfoPageView-Import gefangen & behoben; reale ha.yaml gegen neues Schema verifiziert | **154 Tests, 0 F**, Release v2.1.5 |
| 2026-07-19 | hassfest via CI rot entdeckt (fehlende `zeroconf`-Dependency, vorbestehend) → deklariert | **Release v2.1.6, hassfest grün** |
| 2026-07-19 | Phase 8: Testlauf/hassfest/Config-Validierung ✅; ha.yaml-`device_type`-Tippfehler + Beispiel-Test-Bug behoben | offen nur: manueller HA-Test, HACS-Repo-Settings (Nutzer), M14 (Hardware) |
| 2026-07-19 | HACS-Action grün (Nutzer: Issues + Topics gesetzt); Translations (en/de) angelegt, Error-Slugs | CI komplett grün |
| 2026-07-19 | **Audit Runde 2**: Gesamt-Codebase (6 Auditoren, 48 Kandidaten) → 20 sichere Fixes umgesetzt, 26 Befunde in Backlog (Abschnitt 6), 2 neue Upstream-Bugs dokumentiert | 163 Tests, 3-fach verifiziert, **Release v2.2.0** |
| 2026-07-19 | **Agenda Runde 3** (Abschnitt 7): V1 Branch-Konsolidierung (6 ungemergte Branches inventarisiert), V2 Komplettanalyse+Roadmap, V3 BaseID-Schreiben (Schreibpfad fehlt in beiden Libs, CO_WR_IDBASE-Limit ~10×) | Reihenfolge V1→V2→V3 festgelegt |
| 2026-07-19 | **V1.1/1.2**: 6 Branches getriagt (4 verwerfen/überholt, version2 = 9 Features F1–F9); eltako14bus 0.0.82 als kompatibel-aber-bugfrei-lose geprüft | Triage-Tabelle in Abschnitt 7 |
| 2026-07-19 | **V1.3a**: F1 (area, non-destruktiv) + F9 (temp_unit optional) + Doku-Fix nach main portiert; 6 neue Tests | 169 Tests grün, **Release v2.3.0** |
| 2026-07-19 | **V2**: Komplettanalyse → `ANALYSE-UND-ROADMAP.md` (Coverage 53%, ruff, 5 Agenten, 5-Wellen-Roadmap) | Analyse-Deliverable |
| 2026-07-19 | **Roadmap-Welle A** (Start): ruff-Cleanup (31+6 Funde), Doku-Fixes (D4 switch_button ⚡, D1/D2/D3) | 171 Tests grün, ruff F-clean, **Release v2.3.1** |
| 2026-07-19 | **Roadmap-Welle A** (Rest): A3 entity_category, A4 config-entry unique_id/AF1, A5 A5-06-01 twilight/daylight-Sensoren + toter Konstante, A6 Harness + 15 P0-Tests | Coverage 53%→58% (button/config_flow/select 0%→60-67%), **186 grün, Release v2.4.0** |
