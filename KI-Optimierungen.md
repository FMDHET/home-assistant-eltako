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

- ☐ **H8 — Virtual Network Gateway: TCP-Server fragil (Leak/OOM/dauerhaft tot)** · Aufwand: L
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

- ☐ **H11 — Climate: Cooling-Mode-Konfiguration dreifach defekt** · Aufwand: M
  [climate.py:54-58](custom_components/eltako/climate.py#L54-L58), [climate.py:71-74](custom_components/eltako/climate.py#L71-L74) i.V.m. [binary_sensor.py:173](custom_components/eltako/binary_sensor.py#L173):
  a) prüft `config` (Gesamt-Config) statt `entity_config` → Zweig nie erreicht; b) `CONF_SENSOR [CONF_SWITCH_BUTTON]` → `TypeError`, äußeres except verwirft die **gesamte Climate-Entity**; c) Event-ID-Mismatch: Subscription mit Button-Suffix, gefeuert ohne → Handler nie aufgerufen, Kühlen fällt nach 15 min auf HEAT zurück.
  **Fix:** `entity_config` prüfen; Zugriff korrigieren; Event-IDs identisch aufbauen.

### P2 — MITTEL (latente Crashes, Kompatibilität, falsche Zustände)

- ☐ **M1 — Zuweisung an Property `native_value` statt `_attr_native_value`** — [sensor.py:901/948/985/1077](custom_components/eltako/sensor.py#L901); Fehler wird teils per `except AttributeError: pass` **stumm verschluckt** → Gateway-Sensoren bleiben leer. Konsequent `_attr_native_value` verwenden. · S
- ☐ **M2 — `ClimateEntityFeature.TURN_ON/TURN_OFF` fehlen** — [climate.py:117](custom_components/eltako/climate.py#L117); seit HA 2025.1 schlagen `climate.turn_on/off`-Services fehl. Features ergänzen. · S
- ☑ **M3 — `kwargs['temperature']` → `KeyError`** — [climate.py:265](custom_components/eltako/climate.py#L265); `kwargs.get(ATTR_TEMPERATURE)` mit None-Guard. · S *(mit K5 erledigt)*
- ☐ **M4 — `UnboundLocalError` für Telegramme mit org ≠ 0x05/0x07 (Dimmer)** — [light.py:175-187](custom_components/eltako/light.py#L175-L187); `decoded` nur bei 0x07 zugewiesen. `else: return` ergänzen. · S
- ☐ **M5 — Zuweisung an Property `hvac_modes` beim Restore** — [climate.py:169](custom_components/eltako/climate.py#L169); `_attr_hvac_modes` verwenden bzw. Modi-Restore entfernen (werden im `__init__` deterministisch gesetzt). · S
- ☑ **M6 — `raise Exception` mitten im Message-Callback** — [binary_sensor.py:337](custom_components/eltako/binary_sensor.py#L337) (A5-30-03, unbekannter description_key); loggen + `return`. · S *(mit K4 erledigt)*
- ☐ **M7 — Non-frozen Dataclass-Subklasse von `SensorEntityDescription`** — [sensor.py:82-84](custom_components/eltako/sensor.py#L82-L84); läuft nur noch über HA-Übergangs-Metaclass, künftig `TypeError` **beim Modul-Import** (ganze Sensor-Plattform). `@dataclass(frozen=True, kw_only=True)`. · S
- ☐ **M8 — `event.data['pressed_buttons']` ohne Guard** — [sensor.py:391-393](custom_components/eltako/sensor.py#L391-L393); fremde/manuell gefeuerte Events → `KeyError`. `.get(..., [])`. · S
- ☐ **M9 — `get_id_from_gateway_name` crasht bei abweichenden Namen** — [config_helpers.py:177-178](custom_components/eltako/config_helpers.py#L177-L178) (`IndexError`/`ValueError`, z. B. Float-Id `1.0`); plus harte Key-Zugriffe [config_helpers.py:87/98/117-125](custom_components/eltako/config_helpers.py#L117-L125) (`KeyError` statt Fehlermeldung). Regex-Parsing mit `None`-Rückgabe; `.get(...)` mit Defaults. · M
- ☐ **M10 — Config Flow: `TypeError` bei `None`-Gateway-Config + Substring-Matching** — [config_flow.py:99-104](custom_components/eltako/config_flow.py#L99-L104), [config_flow.py:144-151](custom_components/eltako/config_flow.py#L144-L151) (`'lan' in name` matcht z. B. „Planung"); None-Guard, Gerätetyp aus YAML per Id ermitteln. · M
- ☐ **M11 — VNG: `service_info`-`UnboundLocalError`, `zeroconf=None`-Pfad** — [virtual_network_gateway.py:172-197](custom_components/eltako/virtual_network_gateway.py#L172-L197); Variablen vorinitialisieren, Unregister nur nach erfolgreicher Registrierung. · S
- ☐ **M12 — `datetime.py`: tote Plattform mit garantierten Crashes** — [datetime.py:54/64/84-88](custom_components/eltako/datetime.py#L54): Handler-Registrierung vor `super().__init__`, `datetime.strptime` auf Modul, sync `set_value` an `hass.create_task` übergeben. Datei entfernen oder korrigieren, bevor jemand die Plattform aktiviert. · S
- ☐ **M13 — `device_info.model` crasht bei `dev_eep=None`** — [device.py:64-75](custom_components/eltako/device.py#L64-L75); Guard: `self.dev_eep.eep_string if self.dev_eep else None`. · S
- ☐ **M14 — Climate: toter Periodik-Task (auskommentiert)** — [climate.py:159-160/200-218](custom_components/eltako/climate.py#L159-L218); bei Reaktivierung Task-Leak/Race. Sauber implementieren (`entry.async_create_background_task` + Cancel) oder Code entfernen. · M

### P3 — NIEDRIG (Korrektheit, Hygiene, Zukunftssicherheit)

- ☑ **N1 — Invertierte Vergleichskette bei `invert_signal`** — [binary_sensor.py:276/286/293](custom_components/eltako/binary_sensor.py#L276): `a != b == 1` wird als `(a != b) and (b == 1)` ausgewertet → mit `invert_signal: true` dauerhaft falsche Zustände. Klammern setzen. · S *(erledigt; D5-00-01 zusätzlich auf korrekte Fenster-Semantik `contact==0 ⇒ offen` gefixt — war testgedeckt falsch herum)*
- ☐ **N2 — `_attr_should_poll = True` für reine Push-Entities** — [device.py:28](custom_components/eltako/device.py#L28); unnötige zyklische Last. Auf `False`. · S
- ☐ **N3 — Ungültige `device_class`-Strings** — [sensor.py:151/193/86-93](custom_components/eltako/sensor.py#L151) (`'window'`, `'rain'`, BATTERY mit Volt); HA validiert zunehmend hart. Korrigieren/weglassen. · S
- ☐ **N4 — `manifest.json`: ungültiger Schlüssel `"panel_custom"`** — [manifest.json](custom_components/eltako/manifest.json) (hassfest-Fehler). Entfernen. · S
- ☐ **N5 — `cv.Number` ist kein öffentliches HA-API** — [schema.py:58/252-274](custom_components/eltako/schema.py#L58): funktioniert nur durch internen Re-Export; zudem lässt es Floats durch (→ M9). `vol.Coerce(int)`/`cv.positive_int`. · S
- ☐ **N6 — Logging-Format-Fehler** — [config_flow.py:160-163](custom_components/eltako/config_flow.py#L160-L163), [eltako_integration_init.py:116](custom_components/eltako/eltako_integration_init.py#L116): erzeugen „--- Logging error ---"-Tracebacks, verdecken echte Fehler. · S
- ☑ **N7 — Service `send_message`: bare `except:`, EEP-Konstruktor außerhalb try, Copy-Paste-Fehlermeldung, `import inspect` in Funktion** — [gateway.py:301-341](custom_components/eltako/gateway.py#L301-L341). · S *(erledigt; zusätzlich: fehlende EEP-Parameter zerstören keine Enum-Defaults mehr — `priority=0`-Crash behoben)*
- ☐ **N8 — `LAST_RECEIVED_TELEGRAMS` als Klassen-Dict** — [binary_sensor.py:121](custom_components/eltako/binary_sensor.py#L121): über alle Gateways geteilt, nie geleert → Vermischung bei Multi-Gateway, Mini-Leak. Instanz-/Gateway-bezogen. · S
- ☐ **N9 — Sensor-Kleinkram** — [sensor.py:580](custom_components/eltako/sensor.py#L580) (`msg.data[3]` ohne Längencheck), [sensor.py:306](custom_components/eltako/sensor.py#L306) (Weather-Station ignoriert Nutzernamen), [sensor.py:362-363](custom_components/eltako/sensor.py#L362-L363) (VOC/Language harte Key-Zugriffe). · S
- ☐ **N10 — Toter/riskanter Code entfernen** — auskommentierte Panel-Blöcke ([eltako_integration_init.py:193-235](custom_components/eltako/eltako_integration_init.py#L193-L235), `register_static_path` wurde in HA 2025.7 entfernt!), `import glob` vor Docstring ([gateway.py:1](custom_components/eltako/gateway.py#L1)), doppelte `dev_id`-Property ([device.py:139/149](custom_components/eltako/device.py#L139)), `get_entity_from_hass` (ungenutzt, nutzt internes HA-API, [device.py:196-204](custom_components/eltako/device.py#L196-L204)), tote `validate_ids_of_climate` ([climate.py:91-96](custom_components/eltako/climate.py#L91-L96)). · M

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

### ☐ Phase 4 — Virtual Network Gateway härten (H8, M11)
- 4.1 bind/DNS-Fehlerbehandlung + SO_REUSEADDR + `_running`-Cleanup
- 4.2 Lock/Reihenfolge für Client-Listen und Queues
- 4.3 Socket-Timeouts + bounded Queues + Client-Cleanup beim Stop
- 4.4 M11: service_info/zeroconf-Guards
- *(Falls das virtuelle Gateway bei dir gar nicht in Benutzung ist, kann diese Phase nach hinten — bitte kurz bestätigen.)*

### ☐ Phase 5 — Climate-Funktionsfehler (H11, M2, M3, M5, M14)
- Cooling-Mode-Kette reparieren, TURN_ON/OFF-Features, set_temperature-Guards, Restore-Fix, Periodik-Task entscheiden (implementieren vs. entfernen)

### ☐ Phase 6 — Mittlere Priorität abarbeiten (M1, M4, M6–M10, M12, M13)
- Reihenfolge nach Gelegenheit; M7 (frozen dataclass) **vor dem nächsten großen HA-Upgrade**

### ☐ Phase 7 — Aufräumen & Zukunftssicherheit (N1–N10)
- Korrektheits-Fixes zuerst (N1!), dann Hygiene/toter Code

### ☐ Phase 8 — Abschluss-Verifikation
1. Kompletter Testlauf + ggf. neue Regressionstests für K1–K6
2. `hassfest`-Validierung (CI vorhanden: `.github/workflows/hassfest.yml`)
3. Manueller Test im echten HA: Start → Reload → Reconnect-Button → Taster drücken/loslassen → HA-Neustart (Restore) → Langzeitbeobachtung Log
4. `changes.md` + Versionsnummer in `manifest.json` pflegen

---

## 3a. Offene Produktentscheidungen (bitte entscheiden)

1. **Rocker-Switch: 1 oder 2 Events pro Telegramm?**
   Die Tests (`test_binary_sensor_F6_02_01/02.test_binary_sensor_rocker_switch`) erwarten **2 Events** pro Tastendruck: ein Event für den gesamten Schalter und ein button-spezifisches Event (Suffix z. B. `_rt`). Der Code feuert seit einer früheren Änderung nur noch **1 Event** — der button-spezifische Teil ist auskommentiert ([binary_sensor.py:237-241](custom_components/eltako/binary_sensor.py#L237-L241)). `changes.md` (v2.0.0) erwähnt „Button event ids changed => INCOMPATIBILTY".
   **Entscheidung nötig:** (a) button-spezifische Events wieder aktivieren (Tests werden grün, ggf. Doppel-Trigger in bestehenden Automationen) oder (b) Tests an das 1-Event-Verhalten anpassen. → Bis dahin bleiben 3 Test-Failures stehen.

2. **D5-00-01-Verhaltensänderung (bereits umgesetzt, bitte prüfen):**
   Fensterkontakt-Semantik war invertiert (geschlossenes Fenster wurde als „offen" angezeigt). Jetzt EnOcean-konform: `contact == 0` ⇒ offen ⇒ `is_on = True` (gedeckt durch `test_binary_sensor_D5_00_01`). **Wer das alte (falsche) Verhalten mit `invert_signal: true` kompensiert hat, muss das Flag entfernen.**

## 3b. Neu entdeckte Bibliotheks-Bugs (eltako14bus 0.0.73) — Kandidaten für Upstream-Issues

- `DefaultEnum.__repr__` ([util.py:103](https://github.com/grimmpp/eltako14bus)) shadowt das Builtin `repr` → `UnboundLocalError`, sobald ein `ControllerPriority`-Wert in einen f-string/`repr()` gerät. Workaround in `gateway.py` (Logging per `.name`).
- `_TemperatureAndHumiditySensor3` (A5-04-03): Default `temperature=-20` lässt sich nicht encodieren (`ValueError: byte must be in range(0, 256)`) — `encode_message` rechnet ohne Offset. Workaround: Send-Message-Service füllt numerische Parameter weiterhin mit 0.

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
