# Analyse & Roadmap — home-assistant-eltako (FMDHET-Fork)

> Stand: 2026-07-19, Code-Basis **v2.3.0**, Home Assistant 2026.7.2, Testsuite **171 grün**.
> Diese Datei ist das Ergebnis von **V2** (Komplettanalyse) aus `KI-Optimierungen.md`, Abschnitt 7.
> Abgrenzung: Einzelne **Bugs** stehen bereits im Backlog (`KI-Optimierungen.md`, Abschnitte 2/6/7 — K/H/M/N/A-Serien). Dieses Dokument ist die **höhere Flughöhe**: Architektur, Abdeckung, HA-Konformität, Teststrategie, Abhängigkeiten, Doku — plus eine priorisierte **Roadmap**.

## Inhalt
1. Methodik & Messungen
2. Architektur
3. EEP- und Geräteabdeckung
4. Home-Assistant-Konformität & Modernisierung
5. Teststrategie & Abdeckung
6. Abhängigkeits- & Upstream-Strategie
7. Dokumentation & UX
8. **Roadmap** (kurz-/mittel-/langfristig)

---

## 1. Methodik & Messungen

**Vorgehen:** statische Analyse (ruff), Testabdeckungsmessung (coverage.py über die 171-Test-Suite), sowie sechs fokussierte Detail-Analysen (Architektur, EEP-Matrix, HA-Konformität, Teststrategie, Abhängigkeiten, Doku). Dynamische Verifikation einzelner Befunde ist — wo ohne echte Hardware/HA-Instanz möglich — per Mock-Tests erfolgt; der Rest ist für die Hardware-Session vorgemerkt.

### 1.1 Testabdeckung (coverage.py, Zeilen)

Gesamt: **53 %** (1471 von 3147 Statements ungetestet).

| Modul | Coverage | |
|---|---:|---|
| `button.py` | **0 %** | komplett ungetestet |
| `config_flow.py` | **0 %** | komplett ungetestet |
| `select.py` | **0 %** | komplett ungetestet |
| `eltako_integration_init.py` | 17 % | Setup/Unload/Migration kaum abgedeckt |
| `sensor.py` | 43 % | Meter/Weather/VOC-Pfade ungetestet |
| `gateway.py` | 46 % | Send-Service, Base-Id, Adressübersetzung |
| `climate.py` | 49 % | Cooling-Pfad kaum abgedeckt |
| `light.py` | 62 % | |
| `device.py` | 64 % | |
| `cover.py` | 64 % | |
| `switch.py` | 65 % | |
| `binary_sensor.py` | 66 % | |
| `config_helpers.py` | 68 % | |
| `virtual_network_gateway.py` | 72 % | |
| `tcp2serial_hardened.py` | 77 % | |
| `const.py` / `schema.py` / `__init__.py` | 93–100 % | |

→ Details & Testplan in Abschnitt 5.

### 1.2 Statische Analyse (ruff)

Relevante echte Funde (das Gros der 906 Meldungen ist `import *`-Rauschen F403/F405 — siehe Abschnitt 2/HA-Konformität):
- **29× F401** ungenutzte Imports (u. a. `binary_sensor.json`, `config_flow.CONF_ID/CONF_NAME/selector`, `config_helpers.b2a`, `cover.DOMAIN/MANUFACTURER`, `device.datetime/prettify/HomeAssistant`, `init` 4×, `gateway.locking/CONF_MAC`, `light.entity_registry`, `schema.numbers.Number/Real`).
- **6× F841** ungenutzte lokale Variablen — darunter **`binary_sensor.py:220 pushed_duration`** (berechnet, nie verwendet → verdächtig, evtl. verlorene Zuweisung an das Event-Feld) und mehrere `except … as e` ohne Nutzung.
- **1× F811** Redefinition, **1× F541** leerer f-string.
→ Größtenteils sicher auto-fixbar (`ruff --fix`); als **Quick-Win** in der Roadmap (Abschnitt 8, Q-Serie).

---

<!-- ARCHITEKTUR -->
## 2. Architektur

Die Integration ist für ein gewachsenes Community-Projekt bemerkenswert solide: Sie folgt dem HA-Plattformmodell sauber (je Domäne eine Datei, gemeinsame Basis `EltakoEntity`), ist mit ~170 Tests inklusive EEP-spezifischer und phasenweiser Regressionstests ungewöhnlich gut abgesichert, und die dokumentierte Härtung des Lebenszyklus (Executor-Joins mit Timeout, `async_unload`/`_unload_blocking`-Split, Fehlerbarrieren, VNG-Generation-Tokens + Lifecycle-Lock) ist durchdacht. Die folgenden Befunde betreffen daher nicht die Stabilität im Kleinen (adressiert), sondern die **strukturellen Nähte**, an denen die Multi-Gateway- und Threading-Backlog-Punkte (AM1/AM2/AG1) immer wieder aufschlagen — weil sie eine gemeinsame Wurzel haben.

### A1 — `EnOceanGateway` ist eine God-Class (~6 Verantwortungen)
Bus-I/O + Lebenszyklus, Device-Registry-Schreibzugriff, Service-Registrierung (mit EEP-Reflection), Bus-Speicher-Auslesen, Adressübersetzung und vier handgeschriebene Observer-Register in einer 603-Zeilen-Klasse. **Wirkung:** hohe Testfläche; die Observer-Register waren exakt der Ort der H7-Leaks (HA-Dispatcher von Hand nachgebaut ohne dessen Cleanup). **Richtung:** in `BusConnection` / `GatewayRegistration` / Routing zerlegen; Observer-Register durch pro-Gateway-Dispatcher-Signale ersetzen; Adress-Validierung als typ-abhängige Strategie herauslösen. **Aufwand: L.**

### A2 — `EltakoEntity` vermischt Adressmathematik, Routing & Registry-Schreibzugriff
Externe-Adress-Berechnung im Konstruktor, Selbst-Filterung eingehender Telegramme (mit erneutem `add(base_id)`), Registry-Mutation in der Basisklasse. **Wirkung:** Adress-Semantik an zwei Stellen (Gateway *und* Entity) → Wurzel von AM2; Registry-Schreibzugriff pro Entity statt pro Gerät. **Richtung:** Entity nur noch aufgelöste Telegramme zustellen; Adressmathematik raus; Area-Zuweisung in den Setup (einmal je Gerät). **Aufwand: M** (fällt mit A5 zusammen).

### A3 — Bibliotheks-Grenze: die kopierte `run()` ist eine tickende Drift-Uhr
`tcp2serial_hardened.py` kopiert die komplette `run()`-Schleife aus `esp2_gateway_adapter 0.2.21`, um zwei Zeilen zu patchen, und greift über Proxies auf name-mangled Privatattribute zu. Vier dokumentierte `eltako14bus`-Bugs werden umgangen, nicht behoben. **Wirkung:** Der Pin verhindert nur Versionswechsel, nicht inhaltliche `run()`-Drift beim nächsten Bump — einzige Absicherung ist menschliche Disziplin. **Richtung, gestuft:** (1) Zwei-Zeilen-Fix upstreamen + Hook-Refactor vorschlagen (`_on_recv`), damit eine echte Subklasse ohne Kopie reicht — **S**; (2) sofort **Drift-Guard-Test** (`inspect.getsource(run)` gegen eingecheckten Hash → CI-rot bei Abweichung) — **S**; (3) Eskalation: **Fork beider Libs unter FMDHET** — **M** + Sync-Last. → Details Abschnitt 6.

### A4 — Threading-Contract zentral benannt, aber nur auf dem Nebenpfad angewandt
`_schedule_handler` (`call_soon_threadsafe`) etabliert *einen* dokumentierten Bus-Thread→Loop-Übergang — angewandt nur auf die vier Diagnose-Handler. Der **Hot-Path** dispatcht direkt aus Fremd-Threads (`dispatcher_send`/`create_task` im Bus-/Executor-Thread). Der VNG betreibt ein **zweites, eigenes** Threading-Modell in einer Subklasse, die fast alle Lebenszyklus-Methoden der Basis überschreibt (LSP-Spannung). **Wirkung:** Der Contract gilt für Zähler, nicht für die Telegramm-Zustellung — funktioniert eher durch HA-Interna-Glück als per Vertrag. **Richtung:** Empfangs-Callback zum **dünnen Producer** machen (nur Frame übernehmen, an den Loop übergeben; `prettify`/Rewrite/Dispatch im Loop) — der bereits im K1-Kommentar formulierte Zielzustand; VNG-Modell explizit trennen/dokumentieren. **Aufwand: M.**

### A5 — Adressierung/Routing hat keinen Eigentümer (Wurzel der Multi-Gateway-Befunde) ⚡
Es gibt zwei Empfangs-Busse: das pro-Gateway-Signal `SIGNAL_RECEIVE_MESSAGE` (**kein Consumer abonniert es** — nur Definition + Dispatch) und den globalen Bus, den *jede* Entity abonniert und selbst nach Adresse filtert. `get_identifier` fügt die Gateway-Id nur bei Geräteadresse `00-00-00-00` ein → für gateway-skopierte Signale mit `source_id=None` fällt der Diskriminator weg (**Wurzel AM1**). Empfang ist hinter `int(base_id)!=0` gated, ohne Fallback für ESP2-Gateways, die ihre Base-Id nie abfragen (**Wurzel AG1** — Zähler zählt, Telegramme verschwinden lautlos). Ausgehende Telegramme gehen unübersetzt an Bus/VNG (`convert_bus_address_to_external_address` = toter Stub → **AN2**). **Das ist kein Bündel Einzelbugs, sondern ein fehlendes Architekturelement.** **Richtung:** (1) Empfang auf das existierende **pro-Gateway-Topic** routen, Entities abonnieren *ihr* Gateway-Topic statt des Firehose → keine Entity sieht fremde Telegramme, Rück-Übersetzung nutzt konstruktionsbedingt die richtige Base-Id (löst AM2 + tote Signalbahn); (2) `get_identifier` die Gateway-Identität als explizites Argument geben (löst AM1; breaking → versioniert); (3) Base-Id als validierten Pflichtteil + „drop until base_id"-Gate beobachtbar machen (löst AG1). **Aufwand: L — höchster struktureller Hebel.**

### A6 — Config-Modell: hybrid mit stringly-typed Join-Key
Config-Entry trägt nur `{gateway_description, serial_path}`; alle Geräte in `configuration.yaml`. Bindung Entry↔YAML = aus dem Anzeigenamen geparster Integer (`… (Id: 2)`). Kein Options-Flow, kein `async_set_unique_id`/Abort (AF1). **Wirkung:** fragiler String-Join (M9/AF1), Reload erzwingt YAML-Editieren, Config-Flow ungetestet. **Richtung (gezielt, nicht „alles UI"):** (a) `async_set_unique_id`+Abort — **S**, behebt AF1; (b) Options-Flow für die YAML-Tuning-Felder (Timeouts, Message-Delay) — **M**; (c) langfristig YAML→Entry-Import, dann optional UI-Geräteverwaltung — **XL**.

### Rangierte Architektur-Empfehlungen
1. **Routing auf pro-Gateway-Topics + Adressierung in eine Schicht (A5, tw. A2).** Höchster Hebel: behebt AM1/AM2/AG1/AN2 an der Wurzel. Breaking → versioniert. **L.**
2. **Threading-Contract vereinheitlichen (A4).** Empfangs-Callback = dünner Producer; alle Übergänge über den zentralen Pfad; VNG trennen. Gekoppelt an (1). **M.**
3. **Bibliotheks-Grenze entschärfen (A3).** Upstream-PR + Hook-Refactor; sofort Drift-Guard-Test; FMDHET-Fork als Eskalation. Bester Aufwand/Risiko. **S/M.**
4. **`EnOceanGateway` entflechten (A1).** Aufteilung + Dispatcher-Signale statt Observer-Register (killt eine Leak-Klasse strukturell). **M–L.**
5. **Config-Flow-Härtung kurzfristig (A6).** `async_set_unique_id`+Abort (AF1) + Options-Flow; Config-Flow-Tests. **S→M.**

---

## 3. EEP- und Geräteabdeckung

Basis: `schema.py`-Whitelists + Plattform-Branches + gepinnte `eltako14bus==0.0.73` (25 konkrete EEP-Klassen). **Wichtig:** Library und Integration werden vom selben Autor (grimmpp) 1:1 co-entwickelt — **alle 25 Library-EEP-Klassen sind verdrahtet**, es gibt also **keinen Klassen-Gap**.

**Verdrahtete EEPs (Auszug):** F6-01-01/02-01/02-02/10-00 (Taster/Fenster), D5-00-01 (Kontakt), A5-07-01/08-01 (Bewegung/Präsenz+Helligkeit), A5-30-01/30-03 (Digitaleingänge/Wasser), A5-04-01/02/03 (Temp/Feuchte), A5-06-01 (Helligkeit), A5-09-0C (VOC), A5-10-03/10-06/10-12 (Heizung/Regler), A5-12-01/02/03 (Strom/Gas/Wasser-Zähler), A5-13-01 (Wetterstation), A5-38-08/M5-38-08 (Dimmer/Relais), G5/H5-3F-7F (Rollladen). Sender: A5-38-08, F6-02-01/02, H5-3F-7F, A5-10-06. Teach-In-Buttons: A5-10-06/10-12/38-08, H5-3F-7F. (Volle Matrix im Analyse-Transkript.)

**Lücken = Kandidaten für Features (nicht Bugs):**
- **T1 (Quick-Win, keine Library-Änderung):** A5-06-01 `twilight`/`daylight` als eigene Sensoren aufsplitten (Library dekodiert sie bereits, expliziter TODO `sensor.py:391`) — **S**. Toter `CONF_EEP_SUPPORTED_SENSOR_ROCKER_SWITCH` (`schema.py:46`, nirgends referenziert) entfernen — **S**.
- **T2 (Ausbau vorhandener EEPs):** A5-10-03 (FTR78S) und A5-10-12 (FUTH) als **vollwertige Climate-Entities** statt nur Sensoren (heute akzeptiert climate nur A5-10-06) — **M**.
- **T3 (außerhalb der Library → groß):** D2-01 VLD (schaltbare Steckdosen), A5-20-01 (Ventil-Stellantrieb MD10), A5-14/A5-02/F6-03 — je **L** (Library-Erweiterung Voraussetzung).

**Doku/Reality-Abweichungen:** A5-08-01 und F6-01-01 als *binary_sensor* sowie die Priority-`select`-Entity sind implementiert, aber in der README nicht gelistet; die Cooling-Doku suggeriert breitere Sender-Unterstützung als das Schema (F6-02-01/02) hergibt. → in Abschnitt 7 aufgenommen.

---

## 4. Home-Assistant-Konformität & Modernisierung

Geprüft gegen HA **2026.7.2**. **Bei den harten API-Themen ist v2.3.0 bereits sauber:** `async_forward_entry_setups`/`async_unload_platforms`, `async_remove_config_entry_device`, `DeviceInfo` ohne `suggested_area`, moderne Entity-Features (Climate TURN_ON/OFF, Light ColorMode), Enum-basierte Descriptions; **keine** `hass.helpers.*`/`hass.components.*`-Altlasten, keine entfernten APIs. Der Rückstand liegt im **Integrations-Lifecycle** und im **Config-Paradigma**, nicht in Reparaturen.

- **Config-Modell (§1):** „schlechteste beider Welten" — Geräte nur via YAML, dazu ein Config-Entry, der nur auf diese YAML zeigt (Doppelpflege). Kein OptionsFlow. **Richtung:** kurzfristig OptionsFlow für `general_settings`+Gateway-Tuning (**M**); strategisch `ConfigSubentry`-Flow pro Gerät (in 2026.7 vorhanden) als Weg zu UI-Konfiguration (**L**).
- **Availability (§3d) ⚡:** `available` wird nie gesetzt → bei USB-Abzug/TCP-Drop bleiben alle Entities „verfügbar" mit eingefrorenen Werten. Der Verbindungszustand ist bereits bekannt (`GatewayConnectionState`). **Fix:** in der Basisklasse `EltakoEntity` an den Connection-State-Handler koppeln — **ein Eingriff, alle Plattformen (M, hoher Nutzen).**
- **Config-Entry-`unique_id` (§4) = AF1:** Flow ruft nie `async_set_unique_id`/`_abort_if_unique_id_configured` → Doppel-Setup möglich. **S.**
- **`entity_category` (§3b):** fehlt komplett; Diagnose-Entities/Buttons gehören in `DIAGNOSTIC`/`CONFIG`. **S.**
- **`async_migrate_entry` + Entity-Registry-Migration (§3c):** existiert nicht → **Fundament, das für ALLE `unique_id`-Backlog-Fixes fehlt** (sonst verwaisen Korrekturen bestehende Entities). **M — vor jeder unique_id-Änderung.**
- **Fehlende Features:** `diagnostics.py` (M), `repairs`/`issue_registry` (M — Paradefall: AG1 „Base-Id nie gesetzt" sichtbar machen), `system_health.py` (S, niedrig).
- **Kleinkram:** `hass.create_task`→`entry.async_create_background_task` (`gateway.py:462`); `select.py:66 self.name`→`_attr_name`; harte `entity_id`-Konstruktion (`device.py:55`) HA überlassen (gekoppelt an Migration).
- **Quality-Scale:** faktisch legacy/no-score; **Bronze-Äquivalent erreichbar** mit den S/M-Punkten (unique_id, Availability, entity_category, Diagnostics); Silver+ erfordert UI-Konfiguration (ConfigSubentry).

---

## 5. Teststrategie & Abdeckung

Stil beibehalten: **Pure-Unit, ohne echtes HA/Hardware** (direkte Instanziierung, gemockte `schedule_update_ha_state`, reale EEP-Kodierung). Ziel **53 % → ~70 %**.

**Harness-Erweiterung zuerst** (`tests/mocks.py`, rückwärtskompatibel): `HassMock` um `data`, `async_add_executor_job` (führt synchron aus), `async_create_task` (fehlt aktuell!), `services`, `config_entries`; neue `FakeServiceRegistry` + `FakeConfigEntries`; `ConfigEntryMock` um `data`/`domain`/`async_on_unload` erweitern.

**Priorisierte Wellen:**
- **P0 (sofort, kein Harness-Umbau):** `test_select.py` (Restore-Fallback inkl. `unavailable`, select↔climate-Event-Kontrakt) · `test_button.py` (Teach-In-Payloads, Reconnect via Executor) · `test_sensor_meter_A5_12.py` (**0x8F-Guard, ungetestet**). Hebt select/button von 0 % → ~80–90 %.
- **P1 (nach Harness):** `test_config_flow.py` (Abbruch bei 0 Gateways, Detect-Erfolg, `validate_eltako_conf`-Zweige/M10, Fehler-Nicht-Clobbern, AF1 als `expectedFailure`) · `test_init_setup.py` (alle `ConfigEntryError`-Zweige, `ConfigEntryNotReady`, Unload/Service-Deregistrierung K3). Das sind die risikoreichsten 0-%/17-%-Module.
- **P2:** gateway RX-Callback + Adressübersetzung + send_message; Weather/VOC/PIR/Voltage-`value_changed`.

**Risiko-Hotspots** (Begründung „Hoch"): select-Restore-Fix feuert sonst evtl. `unavailable` als Heizungs-Priorität auf den Bus; select↔climate-Event-Kontrakt bricht lautlos bei einseitiger `get_bus_event_type`-Änderung; config_flow 0 % bei offenem AF1 + frischem M10; 0x8F-Guard schützt vor Fehl-Messwerten.

---

## 6. Abhängigkeits- & Upstream-Strategie

3 gepinnte PyPI-Libs (`eltako14bus==0.0.73`, `esp2_gateway_adapter==0.2.21`, `enocean`), alle von grimmpp. Ein kritischer Adapter-Bug ist bereits als gepinnte `run()`-Kopie gelöst (`tcp2serial_hardened.py`). 4 `eltako14bus`-Bugs bestehen (auch in 0.0.82) — sitzen aber in **isolierten Einzelmethoden**.

**Empfehlung: Vendoren/Patchen in der Integration — Libs (noch) NICHT forken.** Begründung: beide Libs sind auf PyPI und werden von HA-Core sauber installiert (hält hassfest/HACS/CI grün, keine Build-Toolchain-Annahme); ein Fork bräuchte PyPI-Publish oder fragile git-URL (Dauerlast > Nutzen für Ein-Personen-Fork); die Fixfläche ist winzig.

**Konkrete Schritte:**
1. **`eltakobus_patches.py`** anlegen (analog `tcp2serial_hardened.py`): idempotent + versionsgeguardet die 4 Methoden patchen — `DefaultEnum.__repr__` (Shadow), A5-10-03 `+8`-Offset (=AS4), A5-30 Learn-Bit-`<<3`, A5-04-03 Encode-Offset.
2. **Regressionstest je Patch** (echtes Byte-Layout) — so gebaut, dass er **anschlägt, sobald Upstream selbst fixt** (→ Patch dann entbehrlich).
3. **Bump-Checkliste** erweitern: bei jedem Lib-Bump `tcp2serial_hardened.run()` **und** die Patch-Liste re-diffen. Zusätzlich **Drift-Guard-Test** (`inspect.getsource(run)` gegen eingecheckten Hash → CI-rot bei Abweichung).
4. **eltako14bus bei 0.0.73 belassen**; erst mit F7/Repeater auf 0.0.82 bumpen (bringt sonst nichts), dann Patches re-verifizieren.
5. **Fork-Trigger** dokumentieren (nicht umsetzen): erst wenn V3 neue Lib-Methoden braucht *und* die Patchfläche über ~2–3 Methoden wächst, dann Fork als `eltako14bus-fmdhet` auf PyPI.

---

## 7. Dokumentation & UX

- **D4 ⚡ (stiller Konfig-Fehler):** Docs nutzen `switch-button` (Bindestrich), Schema erwartet `switch_button` (Unterstrich) → matcht nie, fällt still auf Default → Kühlmodus-Taste ignoriert. In `update_home_assistant_configuration.md` + `heating-and-cooling/readme.md` korrigieren. **S, hoher Impact.**
- **D1:** `docs/heating-and-cooling/readme.md` trägt Roh-Header „# NEED TO BE REVISED!!!" — entfernen/überarbeiten. **S.**
- **D2:** neues `area:`-Feld (v2.3.0) nirgends dokumentiert. **S.**
- **D3:** `update_home_assistant_configuration.md` veraltet — „nur ESP2 unterstützt" (falsch, ESP3/USB300/MGW-LAN da), Tippfehler `gam14`→`fam14`, unvollständige EEP-Liste. **S–M.**
- **D5:** LAN-Felder `address`/`port` + K8-Timeouts (`reconnection_timeout`/`tcp_keep_alive_timeout`), `thermostat:`, `show_dev_id_in_dev_name`, `time_tilts` fehlen im Referenzdoc (betrifft den mgw-lan-Nutzer direkt). **M.**
- **D6 (Nutzerentscheidung):** README/Manifest zeigen durchgehend auf **grimmpp**, nicht FMDHET (HACS-Button, Badges, `documentation`/`issue_tracker`/`codeowners`) → Install/„Problem melden" landet im Upstream. Auf FMDHET umstellen? **S.**
- **D7/D8:** Brand-Icon fehlt (kanonisch via `home-assistant/brands`, kein grimmpp-Beitrag; niedrige Prio); `hacs.json` minimal → `homeassistant`-Mindestversion ergänzen. **S.**
- **D9:** Konfig-„Wahrheit" liegt dreifach (README / Referenzdoc / ha.yaml) und driftet — `ha.yaml` (wird per Test validiert) zur Single Source erklären. **M.**
- **U1/U3:** UI-„Hinzufügen" erwartet, dass das Gateway schon in YAML steht (sonst Abbruch) — YAML-first-Ablauf explizit dokumentieren; vollständige `device_type`-Werteliste ergänzen (viele undokumentierte Aliase). **S.**

---

## 8. Roadmap

Konsolidiert aus dieser Analyse **plus** dem bestehenden Backlog (`KI-Optimierungen.md`: K/H/M/N erledigt; offen A-Serie AC/AM/AG/AS/AV/AF/AN + V3 BaseID). Reihenfolge nach **Nutzen/Aufwand** und **Hardware-Bedarf**. „⚡" = hoher Nutzer-Impact.

### Welle A — Quick-Wins ohne Hardware (nächste Releases, klein & sicher) — ☑ ERLEDIGT (v2.3.1 + v2.4.0, 2026-07-19)
| # | Maßnahme | Ref | Aufwand |
|---|---|---|---|
| A1 | Ruff-Aufräumung: 29 ungenutzte Imports, 6 Variablen (inkl. `pushed_duration`-Prüfung), F811/F541 | §1.2 | S |
| A2 | Doku-Fixes: `switch_button`-Tippfehler (⚡ stiller Fehler), „NEED TO BE REVISED"-Header, `area:` dokumentieren, ESP2-Falschaussage/`gam14` | D1–D4 | S |
| A3 | `entity_category=DIAGNOSTIC/CONFIG` an Gateway-/Statistik-Entities + Buttons | HA §3b | S |
| A4 | Config-Entry-`unique_id` + `_abort_if_unique_id_configured` (**AF1**) | HA §4 | S |
| A5 | EEP-Quick-Wins: A5-06-01 twilight/daylight-Split; toten Rocker-Konstante entfernen | §3 T1 | S |
| A6 | Test-Welle P0: `test_select.py`, `test_button.py`, `test_sensor_meter_A5_12.py` (0x8F) + Harness-Erweiterung | §5 | S–M |

### Welle B — Lifecycle-Modernisierung & Testfundament (mittelfristig, ohne Hardware)
| # | Maßnahme | Ref | Aufwand |
|---|---|---|---|
| B1 | ☑ **Availability** an Gateway-Verbindung koppeln (Basisklasse) ⚡ — **ERLEDIGT v2.5.0** (2026-07-19) | HA §3d | M |
| B2 | ☑ `async_migrate_entry` + Config-Entry-Versionierung (1.2) etablieren + unique_id-Backfill (AF1) — **ERLEDIGT v2.6.0** (2026-07-19); Entity-Registry-Migrationen folgen in B3 | HA §3c | M |
| B3 | unique_id-Backlog nach B2 ausrollen: ☑ Tarif in Zähler-Id (**AS1, v2.7.0**), ☑ VOC-Sprach-/Klassen-Id (**AS2/AS3, v2.8.0**), ☐ left/right-Diskriminator (AM3) | Backlog AS/AM | M |
| B4 | `repairs`-Issue für Base-Id-/Gateway-Fehler (**AG1** sichtbar machen) + `diagnostics.py` | HA §4 | M |
| B5 | `eltakobus_patches.py` (4 Lib-Bugs) + Drift-Guard-Test für `tcp2serial_hardened.run()` | §6 | S–M |
| B6 | Test-Welle P1: `test_config_flow.py`, `test_init_setup.py` → ~70 % Coverage | §5 | M |
| B7 | OptionsFlow für `general_settings` + Gateway-Tuning (erster Schritt aus der YAML-Doppelpflege) | HA §1 | M |
| B8 | Doku-Vollrevision D5/D9/U1/U3 (LAN-Felder, Single-Source `ha.yaml`, device_type-Tabelle) | §7 | M |

*B1-Follow-up (klein, optional):* `GatewayConnectionState.value_changed` meldet den angezeigten Verbindungs-Wert noch aus dem Notify-Argument — dieselbe Out-of-order-Flap-Staleness, die B1 für die **Verfügbarkeit** bereits behoben hat (Reconcile auf `gateway.is_connected`). Bewusst **nicht** in v2.5.0 mitgezogen, um test-fixiertes Bestandsverhalten nicht ungefragt zu ändern; bei Bedarf identischer 1-Zeilen-Fix + Anpassung von `test_binary_sensor_GatewayConnectionState.py`. Die Snapshot-Iteration des Handler-Fan-outs (Bus-Thread-Sicherheit) kommt dem Sensor bereits zugute.

### Welle C — Architektur-Refactoring (größer, weiter draußen)
| # | Maßnahme | Ref | Aufwand |
|---|---|---|---|
| C1 | **Routing auf pro-Gateway-Topics + Adressierung als eigene Schicht** — behebt AM1/AM2/AG1/AN2 an der Wurzel; Empfangs-Callback zum dünnen Producer (auch A4-Threading). Breaking → versioniert + Migrationsnotiz | Arch A5/A4 ⚡ | L |
| C2 | `EnOceanGateway` entflechten (`BusConnection`/`GatewayRegistration`/Router); Observer-Register → Dispatcher-Signale | Arch A1 | M–L |
| C3 | Threading-Contract vereinheitlichen; VNG-Modell via Komposition entkoppeln | Arch A4 | M |

### Welle D — Neue Features (überwiegend Hardware-Session)
| # | Maßnahme | Ref | HW? |
|---|---|---|---|
| D1 | **V3: Gateway-BaseID schreiben** mit Pflicht-Quittierung + Rate-Limit (CO_WR_IDBASE, ~10× Limit) | Agenda V3 | **ja** |
| D2 | Climate-Cluster: `room_sensor` (F2), `off_temperature` (F3), ACTUATOR_ACK-Default (F4), Display-Fixes (F5, u. a. externe-Adress-Matching ⚡), Priority-Select-Gating (F6) + Cooling-Bugs AC1–AC8/M14 | V1-F2–F6 / AC | **ja** |
| D3 | A5-10-03 / A5-10-12 als vollwertige Climate-Entities | §3 T2 | ja |
| D4 | F7 Repeater-Mode-Select (Lib-Bump 0.0.82; Base-Id-Bug NICHT mitportieren; ESP3-Guard) | V1-F7 | ja |
| D5 | **Manueller HA-Gesamtdurchlauf** (Start→Reload→Reconnect→Taster→Neustart/Restore→Langzeit-Log) — Phase-8-Abschluss | Phase 8 | **ja** |

### Welle E — Strategisch (optional, langfristig)
| # | Maßnahme | Ref | Aufwand |
|---|---|---|---|
| E1 | `ConfigSubentry`-Geräteverwaltung (UI statt YAML) → Weg zu Quality-Scale Silver+ | HA §1 | XL |
| E2 | T3-EEPs (VLD-Steckdosen, Ventil-Stellantrieb) — Library-Erweiterung/Fork nötig | §3 T3 | L–XL |
| E3 | Libs unter FMDHET forken — nur falls Fork-Trigger (§6/5) eintritt | §6 | L |

**Empfohlener nächster Schritt:** Welle A (klein, sicher, ohne Hardware) direkt abarbeiten; Welle B als nächstes größeres Paket. Welle C (Architektur) bewusst planen — höchster struktureller Hebel, aber breaking. Welle D bündeln, sobald das Hardware-Setup wieder steht.

