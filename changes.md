# Changes and Feature List

## Version 2.13.0 — Audit-Runde 3, Welle R3-C: Design- & Verhaltens-Feinschliff
Dritte Umsetzungswelle aus `ANALYSE-RUNDE-3.md`: elf Design-/Niedrig-Befunde, adversarial reviewt.
* **Dimmbares Licht — Einschalten ohne Helligkeit nutzt die Dimm-Memory des Aktors (R3-14):** Einfaches Einschalten (Dashboard-Toggle, Automation ohne `brightness`) sendet jetzt ein reines Einschalt-Telegramm (Schaltbefehl statt Dimm-auf-100 %) → der FUD14 stellt seine zuletzt gespeicherte Dimmstufe wieder her, statt nachts auf 100 % zu springen. *(Verhalten auf ausdrücklichen Nutzerwunsch; die reale Aktor-Reaktion wird in der Hardware-Session verifiziert.)*
* **Dimmbares Licht mit Rocker-Sender (R3-19):** Ein dimmbares Licht mit F6-Rocker-Sender kann keine Helligkeit übertragen — eine angeforderte Helligkeit wird jetzt einmalig gewarnt und nicht mehr optimistisch vorgetäuscht.
* **Cover-Restore friert nicht mehr ein (R3-17):** Ein während eines HA-Neustarts unterbrochener „öffnet/schließt"-Zustand wurde 1:1 wiederhergestellt und nie geleert (kein Telegramm/Timeout) → das Cover hing dauerhaft auf „öffnet/schließt". Beim Neustart wird jetzt als **gestoppt** wiederhergestellt (Endlage aus der letzten Position abgeleitet).
* **Cover-Tilt — kein verspätetes STOP mehr (R3-18):** Ein während der Tilt-Fahrt gestartetes neues Kommando konnte durch das verspätete STOP der alten Tilt-Sequenz angehalten werden. Ein Generationszähler verwirft jetzt ein überholtes STOP.
* **Sensoren restaurieren den nativen Wert (R3-10):** Nach einem Neustart wurde der **angezeigte** (ggf. einheiten-konvertierte) Zustand als nativer Wert interpretiert → bei imperialen Installationen / Einheiten-Overrides eine Doppelkonvertierung. Sensoren leiten jetzt von `RestoreSensor` ab und restaurieren den einheiten-unabhängigen nativen Wert (String-Parser nur noch als Fallback).
* **Send-Service dispatcht nicht mehr doppelt (R3-15):** Über den Sende-Service verschickte Telegramme wurden bei aktivem Bus **zweimal** auf den globalen Bus gelegt (VNG-Clients bekamen den Frame doppelt, Entities verarbeiteten ihn doppelt). Jetzt genau einmal, zentral.
* **Sende-Callback in Dispatch-Reihenfolge (R3-24):** Das Sende-Callback läuft jetzt als HA-`@callback` inline auf dem Event-Loop → zwei schnelle aufeinanderfolgende Sendungen (z. B. Tastendruck + Loslassen) können sich nicht mehr über Pool-Threads umsortieren.
* **VNG robuster (R3-22, R3-23):** Ein transienter `accept()`-Fehler meldet nicht mehr fälschlich „getrennt" und hat jetzt einen kurzen Backoff; eingehende Client-Bytes werden nun geleert/verworfen, damit sie nicht den Kernel-Puffer füllen und den Sendepfad des Clients blockieren (ein Client-Verbindungsabbau wird dadurch auch schneller erkannt).
* **Aufräumen (R3-25, R3-26):** Kein In-place-Mutieren des geteilten `general_settings`-Standard-Dicts mehr — die Konfigurations-Hilfsfunktion gibt jetzt eine Kopie zurück (verhindert, dass eine spätere Änderung wie `enable_teach_in_buttons` den modulweiten Default für alle Standard-Gateways verändert), plus Test-Hygiene (kein mutable Default-Argument in GatewayMock). Umstieg auf die stdlib-`StrEnum` (Fremdpaket `StrEnum` aus den Abhängigkeiten entfernt — unnötiges Supply-Chain-/Wartungs-Risiko).
* Tests: 12 neue Tests; Suite **294 grün**. Adversarial reviewt (2 Finder + Selbst-Verifikation; Finder-Befunde behoben: R3-23 `select`-`ValueError` beim Shutdown, R3-25 Produktivcode-Copy).
* *Hinweis (R3-14, nur bei aktiviertem `fast_status_change`):* Nach `aus → einschalten` zeigt die UI kurz „an bei 0 %", bis das Status-Telegramm des Aktors mit der wiederhergestellten Dimmstufe eintrifft — selbstkorrigierend (bewusster „nicht raten"-Kompromiss).

## Version 2.12.0 — Audit-Runde 3, Welle R3-B: Sensor-, Taster- & Validierungs-Fixes
Zweite Umsetzungswelle aus `ANALYSE-RUNDE-3.md`: neun verifizierte Mittel-Befunde, mechanisch und gut testbar, adversarial reviewt.
* **Teach-in-Telegramme verfälschen keine Sensorwerte mehr (R3-07):** Alle A5-Sensoren (Temperatur, Feuchte, Helligkeit/Dämmerung/Tageslicht, Bewegung, Spannung, Batteriespannung, Zieltemperatur) ignorieren jetzt Teach-in-Telegramme (LRN-Bit). Bisher wurde ein Teach-in als Messwert interpretiert und publizierte Unsinn (z. B. −15,8 °C / 6 %) → Statistik-Spitzen, ggf. ausgelöste Automationen.
* **A5-04-03-Temperatur wird korrekt dekodiert (R3-08):** Ein Zahlendreher in der Bibliothek (`* 265` statt `* 256`) verschob die dekodierte Temperatur (Sprung an jeder 256er-Grenze). In der Integration gepatcht (mit Drift-Schutz), ohne die Bibliothek zu forken.
* **F6-01-01-Einzeltaster feuern ihr Button-Event (R3-09):** Ein vorzeitiges `return` übersprang das Feuern des Events (und die Tastendauer-Buchhaltung), obwohl ein „Event Id"-Feld dafür angelegt wurde. Das Event wird jetzt zuverlässig ausgelöst.
* **Batterie-Alarm des A5-30-01-Kontakts wird nicht mehr invertiert (R3-11):** Die `invert_signal`-Einstellung des Kontakts (NC/NO) invertierte still auch die „Low Battery"-Entity (meldete ON bei voller Batterie → Benachrichtigung feuerte nie) und übertrug die Kontakt-`device_class` auf die Batterie-Entity. Die Batterie-Entity ist jetzt fest `battery` und wird nie invertiert; „Status of Wake" (A5-30-03) ebenfalls nicht mehr. *Hinweis für Bestandsinstallationen mit `invert_signal: true`:* die „Low Battery"- und „Status of Wake"-Werte kehren sich jetzt um (vorher invertiert) — Automationen, die auf dem alten (falschen) Wert basierten, ggf. anpassen. Die Kontakt- und Digitaleingang-Entities bleiben unverändert invertierbar.
* **Sender-ID-Validierung greift jetzt für Aktoren (R3-12):** Die vorgesehene Warnung bei falsch konfigurierter Sender-ID (klassischer Fehler bei Transceiver-Gateways) war für Lichter/Schalter/Cover/Climate ein stiller No-op (Verwechslung `sender_id`/`_sender_id`). Jetzt korrekt aufgelöst.
* **Kein Warn-Spam auf der Sensor-Plattform (R3-21):** Die Aktor-Validierung lief fälschlich auch für dezentrale Funk-Sensoren (Wetterstation, FT55) und Gateway-Felder → ~1 Warnung pro Sensor pro Start. Auf der Sensor-Plattform entfernt (wie bei den Binärsensoren).
* **Kein Warn-Spam bei der dokumentierten FSR14M-2x-Doppelkonfiguration (R3-13):** Ein Schalter / schaltbares Licht (M5-38-08, RPS) verwarf begleitende A5-12-01-Zähler-Telegramme (4BS) derselben Adresse mit einer WARNING pro Telegramm. Nicht-RPS-Telegramme werden jetzt vor dem Dekodieren still gefiltert.
* **VNG „Connected"-Sensor stimmt (R3-16):** Der Verbindungs-Sensor der Virtual Network Gateway zeigte dauerhaft „Disconnected", obwohl Clients bedient wurden (er las einen nie gestarteten Dummy-Bus). Er spiegelt jetzt den TCP-Server-Zustand.
* **Kein „doppelte unique_id"-Fehler bei Doppel-Listung (R3-20):** Ein Gerät, das sowohl unter `binary_sensor:` als auch `sensor:` steht (dokumentiertes Muster, z. B. F6-10-00), erzeugte zwei Binärsensor-Entities mit gleicher ID → HA verwarf die zweite mit einem Fehler. Wird jetzt dedupliziert.
* Tests: 20 neue Tests; Suite **282 grün**. Adversarial reviewt (2 Finder + Selbst-Verifikation).

## Version 2.11.0 — Audit-Runde 3, Welle R3-A: Verbindungs- & Lifecycle-Stabilität
Erste Umsetzungswelle aus dem Detail-Audit (`ANALYSE-RUNDE-3.md`): sechs verifizierte Kritisch-/Hoch-Befunde, alle ohne Hardware, adversarial reviewt.
* **LAN-Gateway erholt sich wieder nach längeren Ausfällen (R3-01, kritisch):** Nach einem Verbindungsausfall länger als das Keep-alive-Timeout (Standard 30 s) verfiel das LAN-Gateway in permanentes Verbindungs-Flattern (verbinden → sofort wieder getrennt, alle ~15 s; kein Telegramm kam mehr an) — nur ein HA-Neustart oder manueller Reconnect half. Ursache: Der Keep-alive-Zeitstempel wurde beim (Wieder-)Verbinden nie zurückgesetzt, sodass die geerbte Timeout-Prüfung die frische Verbindung sofort wieder schloss. Behoben — jede Verbindung bekommt jetzt eine frische Schonfrist.
* **Über zwei TCP-Segmente zerteilte Telegramme gehen nicht mehr verloren (R3-02, hoch):** Der TCP-Empfangspuffer wurde bei jedem `recv` überschrieben statt ergänzt; ein Funk-Telegramm, das in zwei TCP-Paketen ankam (bei der Tasmota-Bridge regelmäßig), wurde dadurch still verschluckt (verschluckte Tastendrücke/Sensor-Updates ohne Log-Hinweis). Der Puffer wird jetzt korrekt zusammengesetzt.
* **Sensor „Received Messages per Session" erscheint wieder (R3-03, hoch):** Der Diagnose-Zähler wurde wegen einer ungültigen Einheit („Messages") bei jedem Start mit einem Fehler abgewiesen und **fehlte still**. Die Einheit ist entfernt (ein Zähler braucht keine) — der Sensor wird wieder angelegt.
* **Climate aktualisiert wieder (R3-04, hoch):** Gleich zwei Ursachen sorgten dafür, dass die Climate-Entity bei der üblichen (lokalen) Konfiguration **nie** die Ist-Temperatur übernahm. (1) Die Raumthermostat-Adresse wurde in der falschen Form (als Adress-Objekt statt als Adress-Bytes) registriert, sodass jedes Thermostat-Telegramm schon vom Empfangsfilter verworfen wurde. (2) Selbst durchgelassene Telegramme wurden intern erneut verworfen, weil `value_changed` gegen die **rohe** statt die vom Gateway **externalisierte** Adresse verglich — das betraf auch die **eigenen Status-Telegramme des Aktors** (der Hauptlieferant der Ist-Temperatur). Beides behoben: Adress-Bytes + Base-ID-Externalisierung, und der interne Abgleich erfolgt jetzt gegen die externalisierten Adressen (Aktor **und** Thermostat). Damit ist die Climate-Telegramm-Zustellung entsperrt (Voraussetzung für die Climate-Diagnose in der Hardware-Session; das Decode-EEP der Kühlanzeige/AC8 bleibt separater Hardware-Punkt). Zusätzlich abgesichert: ein `room_thermostat`-Block ohne `id` bricht die Einrichtung nicht mehr ab.
* **Reconnect und Entladen können sich nicht mehr überholen (R3-05, hoch):** Zwei überlappende Reconnects (Doppelklick / Automation + Nutzer) oder ein Reconnect während des Entladens konnten einen laufenden Bus **verwaist** zurücklassen (er belegte den Single-Client-Slot des Gateways, während der neue Bus nie verband → alles „nicht verfügbar", nur HA-Neustart half). Beide Abläufe sind jetzt serialisiert; nach dem Entladen wird kein Bus mehr wiederbelebt.
* **Kein widersprüchlicher Verbindungsstatus mehr nach Reconnect (R3-06):** Ein alter, gerade ersetzter Bus-Thread konnte nachträglich ein „getrennt" melden, das den Verbindungs-Sensor fälschlich auf „Disconnected" klemmte, obwohl die Verbindung stand. Status-Meldungen sind jetzt an ihre Bus-Generation gebunden; veraltete werden verworfen (behebt zugleich das offene B1-Follow-up an der Wurzel).
* Tests: 16 neue Tests (`test_tcp_connection_hardening.py`, `test_climate.py`, `test_sensor_received_messages.py`, `test_gateway_lifecycle.py`); Suite **259 grün**. Adversarial reviewt (2 Finder + Selbst-Verifikation; R3-04 wurde in der Review um den `value_changed`-Adressabgleich erweitert).

## Version 2.10.0 — Roadmap-Welle B (B5): Bibliotheks-Bugfixes in der Integration
* **Bekannte `eltako14bus`-Fehler werden jetzt in der Integration korrigiert** (`eltakobus_patches.py`), ohne die gepinnte Bibliothek zu forken. Zwei hardware-unabhängige Fehler sind behoben:
  * **Absturz beim Formatieren von Enum-Werten:** `DefaultEnum.__repr__` warf einen `UnboundLocalError`, sobald ein Enum-Wert (z. B. eine Regler-Priorität) in einen Log-/Text-Ausdruck geriet. Behoben.
  * **A5-30 Learn-Bit falsch codiert:** Über den Sende-Service erzeugte A5-30-Telegramme (Digitaleingänge) schrieben das Learn-Bit ins falsche Bit → sie wurden beim Dekodieren fälschlich als Teach-in erkannt und von den (korrekten) Learn-Guards verworfen. Encode setzt das Bit jetzt an die richtige Stelle (Bit 3), passend zum Decode.
* **Drift-Schutz:** Jeder Patch hat einen Test, der prüft, dass die gepinnte Bibliothek den ursprünglichen Fehler noch aufweist — behebt ein künftiges Bibliotheks-Update den Fehler selbst, schlägt der Test fehl und der Patch wird neu bewertet (statt still doppelt zu wirken).
* **Bewusst zurückgestellt:** Zwei weitere dokumentierte Bibliotheks-Fehler (A5-04-03-Encode-Offset, A5-10-03-Zieltemperatur-Offset) verändern an echte Hardware **gesendete** Telegramme bzw. die Climate-Anzeige und werden erst in der Hardware-Test-Session gepatcht — ohne Gerät wäre die Korrektheit nicht verifizierbar.
* Tests: 7 neue Tests (`test_eltakobus_patches.py`); Suite **242 grün**. Adversarial reviewt.

## Version 2.9.0 — Roadmap-Welle B (B4): Diagnose-Download + Reparatur-Hinweise
* **Diagnose-Download (`diagnostics`):** Über *Einstellungen → Geräte & Dienste → Eltako → ⋮ → Diagnose herunterladen* gibt es jetzt einen strukturierten Zustandsbericht (Gateway-Typ, Base-ID, Verbindungsstatus, Empfangszähler, Konfiguration). Erleichtert Fehlermeldungen erheblich. Der Gateway-Host (serieller Pfad / LAN-Adresse) wird **geschwärzt**, damit man den Bericht bedenkenlos teilen kann.
* **Reparatur-Hinweis bei fehlender Base-ID (AG1):** ESP2-Gateways außer dem FAM14 (FGW14-USB, FAM-USB, LAN-ESP2) fragen ihre Base-ID nicht selbst ab. Blieb sie auf `00-00-00-00`, wurden **alle** empfangenen Telegramme kommentarlos verworfen (der Empfangszähler stieg trotzdem) — ein extrem verwirrender „nichts geht"-Zustand. Home Assistant zeigt jetzt einen **Reparatur-Hinweis** im Reparatur-Center, der klar sagt, dass eine gültige `base_id` konfiguriert werden muss. Der Hinweis verschwindet automatisch, sobald das behoben ist.
* Tests: 8 neue Tests (`test_diagnostics.py`, `test_repairs.py`); Suite **232 grün**. Adversarial reviewt.

## Version 2.8.0 — Roadmap-Welle B (B3/AS2+AS3): Luftqualitäts-/VOC-Sensoren korrigiert
* **Sprachwechsel verwaist VOC-Sensoren nicht mehr (AS3):** Die interne ID der Luftqualitäts-Sensoren (A5-09-0C) enthielt den **lokalisierten** Substanznamen — beim Umstellen der Home-Assistant-Sprache bekamen die Entities neue IDs und verloren Historie/Anpassungen. Die ID ist jetzt **sprachunabhängig** (englischer Name als Basis); der **angezeigte Name bleibt lokalisiert** (deutsch/englisch). Englische Installationen sind unverändert; deutschsprachige Einträge werden beim Update automatisch und schonend migriert (Config-Entry-Version **1.3**, erste Entity-Registry-Migration über das B2-Fundament).
* **VOC-Statistiken werden nicht mehr abgelehnt (AS2):** Der VOC-Gesamtwert (ppb) bekam die device_class `VOLATILE_ORGANIC_COMPOUNDS` (erwartet µg/m³) → Home Assistant lehnte die Langzeitstatistik ab. Jetzt korrekt `VOLATILE_ORGANIC_COMPOUNDS_PARTS` (ppb). Einzelne Substanzen (einheitsloser Index) bekommen **keine** VOC-device_class mehr (die vorher zur Ablehnung führte).
* **Robuste Migration:** Ein Upstream-Datenfehler in der Bibliothek (zwei Substanzen teilen den deutschen Namen „Styren") würde einen eindeutigen Rückschluss verhindern — solche mehrdeutigen Namen werden bewusst **nicht** migriert (statt falsch zugeordnet). Existiert nach einem früheren Sprachwechsel bereits ein Ziel-Eintrag, wird die Migration übersprungen statt abzubrechen.
* Tests: 13 neue Tests (`test_sensor_voc.py`, `test_migration.py`); Suite **224 grün**. Adversarial reviewt.

## Version 2.7.0 — Roadmap-Welle B (B3/AS1): Mehr-Tarif-Zähler funktionieren vollständig
* **Mehr-Tarif-Zähler (`meter_tariffs: [1, 2, …]`) zeigen jetzt ALLE Tarife:** Bisher teilten sich mehrere Tarife desselben Zählers dieselbe interne ID (`unique_id`) — Home Assistant verwarf alle bis auf den ersten, ab Tarif 2 fehlte der Sensor **still**. Der Tarif ist jetzt Teil der `unique_id`, sodass jeder Tarif eine eigene Entity bekommt (Strom kumulativ, Gas/Wasser kumulativ + Durchfluss).
* **Bestehende Entities bleiben erhalten — keine Migration nötig:** Der **erste** konfigurierte Tarif behält seine bisherige `unique_id` unverändert (Historie/Anpassungen bleiben erhalten); nur die zusätzlichen, zuvor verworfenen Tarife kommen als neue Entities dazu. Wer nur einen Tarif nutzt, merkt keinen Unterschied.
* Der angezeigte Name trug den Tarif bereits („… (Tariff 2)"); jetzt ist auch die ID eindeutig.
* Versehentlich doppelt angegebene Tarifwerte (`meter_tariffs: [1, 2, 2]`) werden bei der Konfigurationsprüfung dedupliziert (nicht abgelehnt), damit kein Tippfehler die Zähler-Konfiguration bricht.
* Tests: 5 neue Tests (`test_sensor_meter_A5_12.py`); Suite **211 grün**. Adversarial reviewt (Backward-Kompatibilität bestätigt: erster Tarif behält seine ID unverändert).

## Version 2.6.0 — Roadmap-Welle B (B2): Migrations-Fundament (`async_migrate_entry`)
* **Sauberes Versions-/Migrations-Gerüst für Config-Einträge:** Die Integration besitzt jetzt ein `async_migrate_entry` (Config-Entry-Version **1.2**). Home Assistant ruft es automatisch auf, wenn ein gespeicherter Eintrag älter ist, und aktualisiert ihn schonend — die Grundlage für alle künftigen `unique_id`-/Struktur-Migrationen (kommt in B3).
* **Alt-Einträge erhalten die Doppel-Gateway-Absicherung nachträglich:** Vor v2.4.0 angelegte Gateways hatten noch keine eindeutige Config-Entry-ID (`unique_id`). Die Migration trägt sie jetzt nach (`eltako_gateway_<id>`), sodass auch bestehende Installationen vor versehentlichem doppelten Einrichten desselben Gateways geschützt sind (AF1).
* **Robuste Migration:** Ein bereits vorhandenes `unique_id` wird nie überschrieben; bei einem Alt-Zustand mit zwei Einträgen für dasselbe Gateway wird **keine** kollidierende ID erzeugt (der zweite bleibt ohne, mit Warnhinweis); eine unerwartet neuere Major-Version wird sauber abgelehnt statt still akzeptiert.
* Tests: 6 neue Tests (`test_migration.py`); Suite **204 grün**. Keine Nutzeraktion nötig — die Migration läuft transparent beim Update.

## Version 2.5.0 — Roadmap-Welle B (B1): Verfügbarkeit folgt der Gateway-Verbindung
* **Entities werden „nicht verfügbar", wenn das Gateway die Verbindung verliert:** Bricht die serielle/TCP-Verbindung zum Gateway ab (z. B. LAN-Gateway-Drop, USB abgezogen), zeigen die betroffenen Geräte-Entities jetzt **`unavailable`** statt stillschweigend eingefrorene Altwerte. Ein Verbindungsabriss wird dadurch im UI **sichtbar** und Automationen können darauf reagieren (`available`-Kopplung an den Verbindungszustand — behebt HA-Konformitätslücke §3d). Sobald das Gateway wieder verbindet, werden die Entities automatisch wieder verfügbar.
* **Gateway-eigene Diagnose/Steuerung bleibt bewusst sichtbar:** Der Verbindungs-Sensor („Connected"), der **Reconnect-Button**, „Read memory", Base-ID und die Gateway-Infofelder bleiben **gerade dann** bedienbar/sichtbar, wenn die Verbindung getrennt ist — sonst könnte man nicht mehr reconnecten oder den Zustand ablesen.
* **Robust gegen Verbindungs-Flattern:** Der Verfügbarkeits-Zustand wird immer gegen den **aktuellen** Bus-Zustand abgeglichen (nicht gegen eine evtl. veraltete Benachrichtigung), und die Handler-Fan-out-Liste wird per Snapshot iteriert — so kann ein Entity bei schnellem Auf/Zu nicht auf einem falschen Zustand „hängen bleiben" (adversarial reviewt: 3 Finder + Verifier).
* Tests: 12 neue Tests (`test_availability.py`); Suite **198 grün**.
* *Hinweis:* Bei einem instabilen Gateway, das sich alle paar Sekunden neu verbindet, wechseln die Entities entsprechend zwischen verfügbar/nicht verfügbar — das spiegelt die Realität wider und ist durch `reconnection_timeout` (Standard 15 s) gedeckelt.

## Version 2.4.0 — Roadmap-Welle A: Lifecycle-Politur, neue Sensoren, mehr Tests
* **Doppeltes Hinzufügen desselben Gateways verhindert:** Der Config-Entry bekommt jetzt eine eindeutige ID pro Gateway (`_abort_if_unique_id_configured`) — ein Gateway kann nicht mehr versehentlich zweimal eingerichtet werden (behebt AF1).
* **Diagnose-Entities richtig kategorisiert:** Gateway-Status/-Infos, Base-ID, Zähler, Batteriespannung laufen jetzt als **Diagnose**, die Aktions-Buttons (Reconnect, Speicher auslesen, Teach-In) als **Konfiguration** (`entity_category`). Sie werden dadurch im UI korrekt gruppiert und aus Auto-Dashboards/Sprachassistenten herausgehalten.
* **Helligkeits-/Dämmerungssensor (A5-06-01):** zusätzlich zum kombinierten Helligkeitswert gibt es jetzt eigene Sensoren für **Dämmerung (twilight)** und **Tageslicht (daylight, lux)**.
* **Deutlich mehr Testabdeckung:** 15 neue Tests (Priority-Select, Buttons, Stromzähler inkl. 0x8F-Seriennummer-Guard, Config-Flow) — bislang komplett ungetestete Module (Select/Button/Config-Flow: 0 %) sind jetzt abgedeckt. Gesamt-Zeilenabdeckung 53 % → 58 %; Testsuite 186 grün.

## Version 2.3.1 — Aufräumen & Doku-Korrekturen (Roadmap-Welle A)
* **Doku-Korrektur (wichtig):** In den Konfigurationsbeispielen war der Kühlmodus-Schlüssel als `switch-button` (Bindestrich) geschrieben — das Schema erwartet `switch_button` (Unterstrich). Wer das Beispiel kopiert hatte, dessen Kühlmodus-Taste wurde **stillschweigend ignoriert**. Korrigiert.
* Doku aktualisiert: veralteter „nur ESP2"-Hinweis entfernt (ESP3/USB300/MGW-LAN werden unterstützt), Tippfehler `gam14`→`fam14`, veralteter „NEEDS TO BE REVISED"-Vermerk entfernt, neues `area:`-Feld dokumentiert.
* Code-Hygiene: 29 ungenutzte Importe, 6 ungenutzte Variablen, eine Redefinition und ein leerer f-String entfernt (statische Analyse). Keine funktionale Änderung; Testsuite unverändert grün (171).

## Version 2.3.0 — Neue Features aus Branch-Konsolidierung (V1)
Aus dem Branch-Inventar (V1) nach `main` überführte, ohne Hardware testbare Features:
* **Geräte-Bereich (Area) aus YAML:** Jedes Gerät kann jetzt `area: Wohnzimmer` angeben — der Bereich wird bei Bedarf angelegt und dem Gerät zugewiesen. **Non-destruktiv:** eine manuell in Home Assistant gesetzte Zuordnung wird NICHT überschrieben (nur gesetzt, wenn das Gerät noch keinen Bereich hat). Gilt für alle Geräte-Plattformen.
* **Climate `temperature_unit` ist jetzt optional** (Default °C).
* Doku-Tippfehler korrigiert (Gateways).

*Hinweis:* Weitere Features aus dem `version2`-Branch (externer Raumfühler `room_sensor`, `off_temperature`, Prioritäts-Defaults, Repeater-Modus, Climate-Anzeige-Fixes, Frontend-Panel) sind analysiert und für die Hardware-Test-Session vorgemerkt (Details in `KI-Optimierungen.md`, Abschnitt 7).

## Version 2.2.0 — Gesamt-Audit: 20 Korrektheits-Fixes + übersetzter Config-Flow
Ergebnis eines vollständigen Codebase-Audits (6 parallele Reviewer, jeder Fix 3-fach verifiziert). Weitere gefundene Punkte, die Hardware-Tests erfordern, sind in `KI-Optimierungen.md` (Abschnitt 6) dokumentiert.

**Sensoren & Taster:**
* Bewegungs-/Fenstersensoren, die unter `sensor:` konfiguriert sind, hängen nicht mehr dauerhaft auf „an" (invert-Logik mit fehlendem Flag).
* A5-07-01: Teach-in-Telegramme schalten den Sensor nicht mehr fälschlich; das Event-Feld `pressed` funktioniert jetzt (nutzte das Roh-Byte); Spannungswert respektiert das Verfügbarkeits-Bit. Teach-in-Guards auch für A5-30-01/-03.
* Luftqualität (A5-09-0C): unbekannte VOC-Substanz-Codes erzeugen keinen Fehler-Traceback pro Telegramm mehr.
* Gas-/Wasserzähler: Seriennummern-Telegramme (0x8F) werden nicht mehr als Durchfluss fehlinterpretiert.
* Doppelte „Id"-Diagnose-Entities bei Geräten in mehreren Plattform-Sektionen behoben.

**Rollladen (Cover):**
* Position geht beim HA-Neustart nicht mehr verloren, wenn keine `time_tilts` konfiguriert sind (Restore-Fehler bei jedem Start).
* Fahrzeit 255 crasht die Fahrbefehle nicht mehr.
* Ein noch laufender Tilt-Vorgang kann eine anschließende Fahrt nicht mehr ungewollt stoppen.
* Zwischenstopp-Telegramme ohne konfigurierte Fahrzeiten lassen den Status nicht mehr auf „öffnet/schließt" hängen.

**Licht:** Helligkeit 1–2 dimmt nicht mehr auf 0 (Nachtlicht!); Helligkeits-Umrechnung rundet korrekt (kein Drift mehr).

**Climate-Prioritätswahl (Select):** Ein beim Herunterfahren „nicht verfügbarer" Zustand wird nicht mehr als Priorität übernommen und auf den Bus gefeuert.

**Konfiguration:**
* `climate → cooling_mode → sender → gateway_id` akzeptiert jetzt (wie überall) eine Gateway-Nummer. **Hinweis:** Adress-Strings wie `FF-AA-80-00` werden dort nicht mehr akzeptiert — sie waren funktionslos, und die alte Validierung war ohnehin defekt (auch das Weglassen schlug fehl).
* `sensor → device_class` akzeptiert jetzt auch echte Sensor-Klassen wie `temperature` (vorher scheiterte die gesamte Konfiguration).

**Config-Flow (Gateway hinzufügen):** endlich richtige Texte statt roher Schlüssel (Übersetzungen EN/DE ergänzt — Custom-Integrationen laden nur `translations/`); Validierungsfehler werden nicht mehr von generischen Meldungen überdeckt; ohne konfigurierte Gateways bricht der Dialog mit klarer Meldung ab statt ein unabsendbares Formular zu zeigen.

**Gateway-Verwaltung:**
* „Read memory of bus devices": Doppelklick auf den Button kann den Empfang nicht mehr dauerhaft lahmlegen.
* Der Nachrichten-Zähler beginnt bei 0 statt 1.
* Beim Registrieren von Entities werden keine redundanten Base-Id-Abfragen mehr ausgelöst (O(N²)-Verhalten beim Setup).
* Virtual Network Gateway: neu geladene Gateways ersetzen ihre alten Einträge (kein Leak, keine veralteten Base-Id-Infos an Clients).

**Bekannte Einschränkung:** Über den Send-Message-Service erzeugte A5-30-Telegramme mit `learn_button=1` werden wegen eines Encode-Fehlers in der Bibliothek `eltako14bus` als Teach-in dekodiert und daher (korrekt) ignoriert — Fix gehört in die Bibliothek, siehe KI-Optimierungen.md.

## Version 2.1.6 — hassfest-Validierung grün
* **Manifest-Validierung (`hassfest`) behoben:** Die von der Integration genutzte Kern-Komponente `zeroconf` (für die mDNS-Bekanntmachung des Virtual Network Gateway) ist jetzt als `dependencies` deklariert. Dieser hassfest-Fehler bestand schon länger und wurde durch die Aufräumarbeiten in 2.1.5 sichtbar isoliert.

## Version 2.1.5 — Aufräumen & Korrektheit (Phase 7)
* **Wetterstation:** Ein selbst vergebener Gerätename wird nicht mehr durch den Standardnamen überschrieben.
* **Taster mit mehreren Kanälen / gleicher Taster auf mehreren Gateways:** Die Zuordnung von Drücken/Loslassen (und `push_duration`) wird nicht mehr zwischen Entities vermischt.
* **Sensor-Anzeige korrekt:** Ungültige Geräteklassen behoben — Batteriespannung ist jetzt „Spannung", Fenstergriff und Regen nutzen keine ungültigen Klassen mehr (Home Assistant validiert diese zunehmend streng).
* **Weniger Grundlast:** Push-Entities werden nicht mehr unnötig zyklisch abgefragt (`should_poll = False`).
* **Konfigurations-Validierung robuster & zukunftssicher:** Interne, nicht-öffentliche Validatoren durch offizielle ersetzt (Gateway-IDs als Ganzzahl, Port 1–65535, Zeitwerte als Zahl). Behebt am Rande auch die Gateway-Id-Erkennung.
* **Aufgeräumt:** Ungültiger Manifest-Schlüssel entfernt (behebt `hassfest`), sowie diverser toter Code (unfertiges Frontend-Panel samt Platzhalter-Seite, ungenutzte Funktionen, doppelte Property, fehleranfällige Log-Ausgaben).
* Testsuite: 154 grün (2 neue Regressionstests). Adversarial reviewt, keine Regressionen.

## Version 2.1.4 — Robustheit mittlerer Priorität (Phase 6)
* **Dimmer:** Ein Telegramm mit unerwartetem Funktyp (weder 4BS noch RPS, z. B. 1BS) legt den Empfang nicht mehr lahm (vorher `UnboundLocalError`).
* **Sensor-Plattform zukunftssicher:** Die interne Sensor-Beschreibung ist jetzt ein „frozen" Datentyp — verhindert einen künftigen Import-Absturz der gesamten Sensor-Plattform mit neueren Home-Assistant-Versionen.
* **Taster-Info-Sensor:** Fremde/manuell ausgelöste Events mit gleicher Event-ID führen nicht mehr zu einem Fehler.
* **Gateway-Einrichtung robuster:**
  * Der Gateway-Typ wird bei der Pfad-Validierung jetzt eindeutig aus der Konfiguration bestimmt (per Gateway-Id) statt über eine fehleranfällige Namens-Teilstring-Suche (die z. B. „lan" in „Planung" traf).
  * Abweichend benannte Gateways und Konfigurationen ohne Gateway-Abschnitt führen zu einer klaren Fehlermeldung statt zu einem Absturz.
  * Ein nicht als serielles Gateway unterstützter Typ (z. B. `ftd14`) meldet dies sauber, statt die Einrichtung mit einem `KeyError` abzubrechen.
* **Gerätemodell:** Diagnose-Entities ohne EEP crashen die Geräteinfo nicht mehr.
* **Aufgeräumt:** Die tote, nie geladene `datetime`-Plattform wurde entfernt (ihre Funktion — Zeitstempel der letzten Nachricht — gibt es weiterhin als Sensor).
* 6 neue Regressionstests; Testsuite: 152 grün.

## Version 2.1.3 — Taster-Events zurück, Virtual Network Gateway gehärtet
* **Knopfspezifische Taster-Events sind zurück (F6-02-Wandtaster).** Zusätzlich zum Basis-Event (`eltako_btn_pressed_<adresse>`) wird wieder ein Event je Knopf gefeuert (z. B. `eltako_btn_pressed_<adresse>_rt`) — wie vor v2.0.0. Automationen können damit ohne Bedingung direkt auf einen Knopf triggern; Prä-v2.0.0-Automationen funktionieren wieder. Beim Loslassen wird das Event des zuvor gedrückten Knopfs gefeuert (inkl. `push_duration_in_sec`). **Hinweis:** Wer auf Basis- UND Knopf-Event hört, triggert doppelt. Zwei-Tasten-Kombinationen haben jetzt eine stabile, sortierte ID (`.._lt_rb`, nie `.._rb_lt`).
* **Virtual Network Gateway (TCP-Server für Zweit-Instanzen) umfassend gehärtet:**
  * Server überlebt Bind-/DNS-Fehler und lässt sich danach neu starten (vorher: Thread tot, Status „läuft", bis HA-Neustart).
  * Hängende Clients blockieren nichts mehr: Sende-Timeout 10 s, begrenzte Warteschlangen (kein Speicherleck/OOM mehr nach Tagen), Verbindungen werden beim Stop aktiv geschlossen.
  * Race-Fixes aus dem adversarialen Review: Ein „Zombie"-Serverthread (hängender Stop) kann einen frisch neu gestarteten Server nicht mehr abschießen (Generation-Token); gleichzeitiges Entladen + Reconnect-Button crasht nicht mehr und kann den Server nicht mehr auf einem entladenen Eintrag wiederbeleben (Lifecycle-Lock).
  * Eine einzelne defekte Nachricht trennt nicht mehr alle verbundenen Clients.
* Testsuite erstmals komplett grün: 146 Tests, davon 7 neue Funktionstests für den TCP-Server.

## Version 2.1.2 — LAN-Gateway: Verbindungsabbruch ohne Wiederverbindung behoben
* **LAN-Gateway hängt nicht mehr dauerhaft nach Verbindungsabbruch (nur HA-Neustart half).** Ursache: Die Bibliothek `esp2_gateway_adapter` (bis einschl. 0.2.21) erkennt es nicht, wenn die Gegenstelle die TCP-Verbindung *sauber* schließt (FIN, z. B. wenn sich ein zweiter Client mit dem Single-Client-Gateway verbindet) — der Empfangs-Thread drehte endlos auf der toten Verbindung, ohne Reconnect und ohne Statuswechsel in HA. Die Integration bringt jetzt eine gehärtete Kommunikator-Klasse mit (`tcp2serial_hardened.py`), die den Fall erkennt und nach `reconnection_timeout` (Default 15 s) neu verbindet. Mit Regressionstest abgesichert (`tests/test_tcp_connection_hardening.py`).
* **Kernel-TCP-Keepalive aktiviert:** Auch still abgerissene Verbindungen (NAT-Idle-Timeout, Stromausfall am Gateway) werden nun vom Betriebssystem erkannt.
* `esp2_gateway_adapter` von 0.2.15 auf **0.2.21** angehoben (u. a. Empfangs-Thread als Daemon-Thread → kein Hängen beim HA-Shutdown; ACK-Antworten fluten das Log nicht mehr als Warnung).

## Version 2.1.1 — Climate, LAN-TCP stability & device removal
* **LAN-Gateway (mgw-lan/EUL etc.): stabilere TCP-Verbindung.** Nach einem Verbindungsabbruch wird jetzt nach **15 s** statt 60 s neu verbunden und ein stillgelegter Kanal nach **30 s** statt 60 s erkannt. Beides pro Gateway per YAML einstellbar (`reconnection_timeout:`, `tcp_keep_alive_timeout:`).
* **Geräte lassen sich jetzt über die HA-Oberfläche löschen** (Gerät → „Gerät löschen"). Noch in der YAML konfigurierte Geräte kommen beim nächsten Reload zurück (erwartetes Verhalten bei YAML-Integrationen).
* **Heizen/Kühlen (Climate):**
  * Kühl-Modus-Konfiguration (`cooling_mode`) repariert — war durch mehrere Fehler faktisch funktionslos und verwarf teils die ganze Climate-Entity.
  * `climate.turn_on`/`climate.turn_off` funktionieren wieder (ab HA 2025.1); `turn_off` schaltet zuverlässig aus (schaltete zuvor ein bereits ausgeschaltetes Gerät wieder ein).
  * Zustands-Wiederherstellung nach Neustart bereinigt (Modi/Temperaturen).
* Hinweis: Der Kühl-Pfad konnte nicht mit echter Hardware getestet werden.

## Version 2.1.0 — Stability & robustness release
Umfangreiche Stabilitäts-Überarbeitung (behebt wiederkehrende Abstürze/Einfrieren). Alle Änderungen wurden gegen die gepinnte `eltako14bus==0.0.73` und HA 2026.7 verifiziert und adversarial reviewt. Details siehe `KI-Optimierungen.md`.

* **Empfangs-Thread stirbt nicht mehr still:** Der serielle Empfangs-Callback ist nun gegen unerwartete Exceptions abgesichert (vorher konnte ein einzelnes fehlerhaftes Telegramm den Empfang bis zum HA-Neustart lahmlegen).
* **Kein Einfrieren von Home Assistant mehr** beim Reload/Reconnect/Beenden: blockierende Thread-Joins laufen im Executor mit Timeout statt im Event-Loop.
* **Sauberer Reload:** `async_unload_entry` entlädt jetzt alle Plattformen und deregistriert Services/Listener → keine doppelten Entities/Events, keine unique_id-Kollisionen, keine Memory-Leaks.
* **Automatischer Retry** bei temporären Setup-Fehlern (`ConfigEntryNotReady`), statt endgültig zu scheitern.
* **Taster (F6-02):** Kein Absturz mehr bei jedem Loslassen; Release-Events und Tastendauer funktionieren wieder.
* **Climate:** `set_temperature` crasht nicht mehr auf frischen Installationen; OFF-Zustand wird korrekt erkannt; Kühl-Modus-Konfiguration repariert.
* **Restore nach Neustart:** Sensoren (inkl. Zählerstände) verschwinden nicht mehr; robustes Parsen von Zahlen/Timestamps.
* **Cover:** kein `TypeError` mehr bei unbekannter Position nach Neustart; Tilt blockiert keine Threads mehr (async).
* **Fensterkontakt D5-00-01:** korrigierte Offen/Zu-Erkennung (ggf. `invert_signal` anpassen, falls es vorher zur Kompensation gesetzt war).
* Diverse kleinere Robustheitsfixes (Listener-Leaks, seriellen Port schließen, Logging-Fehler).

## Version 2.0.0
* No Need for defining base id in config file except for FGW14-SUB
  * Entity Ids of gateways change so that base id is not contained anymore
* Reverse Network EnOcean Bridge to be able to connect eo_man.
  * TODO: sending of message does often not work in the beginning
  * TODO: send gateway information frequently
* FAM14 can detect bus devices and report it into eo_man 
* Support for EUL Gateway
* Gateways can be used as repeater inside HA
* Button event ids changed => INCOMPATIBILTIY to older versions
* Button events can be used for e.g. dimming therefore events contains time information when and for how long buttons were pushed
* Created blueprint for dmming and switch lights off and on which trigger by EnOcean switches and not controlled via eltako actuators. You can use EnOcean switches to e.g. controll Zigbee lights from Philips Hue or any other protocol and lights which can be controlled by Home Assistant Automations.
* Connection state fixed: Display information about gateway connection was sometimes displayed incorrectly

TODO: improve performance of controlling groups. (send only one group telegram instead of many individual commands)

## Version 1.5.9
* Replaced deprecated log function warn through warning
* Fixed deprecation warning for async_forward_entry_setup

## Version 1.5.8
* Fixed dependency incompatibility with HA 2024.9

## Version 1.5.7
* Tested new devices: FB55EB, FWZ12
* Added EEP F6-01-01 and tested FMH1W

## Version 1.5.6 Added EEP A5-10-03 for current and target temperature
* Only for sensors available

## Version 1.5.5 Added message-delay for GWs as config parameter
* Added argument `message_delay` to config distance of bulk messages being translated in the gateway so that buffer overflows can be prevented.

## Version 1.5.4 Cover motion fixed
* changed min movement time from 0 to 1 so that covers won't move completely up or down.

## Version 1.5.3 Added auto-reconnect for GWs as config parameter
* Added argument `auto_reconnect` to disable auto-reconnect for all Gateways

## Version 1.5.2 BugFix for LAN Gateway Connection 
* Added argument port for LAN Gateway. Default port = 5100

## Version 1.5.1 Added into HACS list
* Added Eltako Intgration into list of HACS

## Version 1.5 MGW LAN Support
* Added support for [MGW Gateway](https://www.piotek.de/PioTek-MGW-POE) (ESP3 over LAN)

## Version 1.4.4 
* Thread sync clean up
* Lazy loading for ESP3 libs to prevent dependency issues

## Version 1.4.3 Compatibility to HA 2024.5
* 🐞 Incompatibility with HA 2024.5 fixed. (Cleaned up event loop synchronization)

## Version 1.4.2 Added EEPs A5-30-01 and A5-30-03
* Added EEPs (A5-30-01 preferred) for digital input which is used in water sensor (FSM60B)

## Version 1.4.1 Support for sending arbitrary messages
* Added Service for sending arbitrary EnOcean (ESP2) messages. Intended to be used in conjunction with [Home Assistant Automations](https://www.home-assistant.io/getting-started/automation/).
* 🐞 Fix for TargetTemperatureSensor (EEP: A5-10-06 and A5-10-12)
* 🐞 Fix for unknown cover positions and intermediate state + unit-tests added.
* Unit-Tests added and improved for EEP A5-04-01, A5-04-02, A5-10-06, A5-10-12, A5-13-01, and F6-10-00.
* EEP A5-04-03 added for Eltako FFT60 (temperature and humidity)
* EEP A5-06-01 added for light sensor (currently twilight and daylight are combined in one illumination sensor/entity)
* Bug fixes in EEPs (in [eltako14bus library](https://github.com/grimmpp/eltako14bus))

## Version 1.4.0 ESP3 Support (USB300)
* Docs about gateway usage added.
* Added EEPs F6-02-01 and F6-02-02 as sender EEP for lights so that regular switch commands can be sent from Home Assistant.
* &#x26A0; Changed default behavior of switches and lights to 'direct pushbutton top on' and 'left rocker' for sender EEP F6-02-01/-02
* Logging prettified.
* Added library for ESP3 (USB300 Support) => [esp2_gateway_adapter](https://github.com/grimmpp/esp2_gateway_adapter)
* Better support for Teach-In Button

## Version 1.3.8 Fixes and Smaller Improvements
* Fixed window handle F6-10-00 in binary sensor
* Added better tests for binary sensors
* Fixed covers which behaved differently after introducing recovery state feature.
* Added additional values (battery voltage, illumination, temperature) for A5-08-01 as sensor
* Occupancy Sensor of A5-08-01 added as binary sensor
* Improved ESP3 adapter for USB300 support. Sending telegrams works now but actuators are not accepting commands for e.g. lights - EEP: A5-38-08 😥
* Teach-In buttons for lights, covers, and climate are available.
* Static 'Event Id' of switches (EEP: F6-02-01 and F6-02-02) is displayed on entity page.
* Docs about how to use logging added.
* Updated docs about how to trigger automations with wall-mounted switches.

## Version 1.3.7 Restore Device States after HA Restart
* Trial to remove import warnings 
  Reported Issue: https://github.com/grimmpp/home-assistant-eltako/issues/61
* &#x1F41E; Removed entity_id bug from GatewayConnectionState &#x1F41E; => Requires removing and adding gateway again ❗
* Added state cache of device entities. When restarting HA entities like temperature sensors will show previous state/value after restart. 
  Reported Feature: https://github.com/grimmpp/home-assistant-eltako/issues/63

## Version 1.3.6 Dependencies fixed for 1.3.5
* &#x1F41E; Wrong dependency in manifest &#x1F41E; 

## Version 1.3.5 Prevent Message overflow for FGW14-USB
* Added info field for which button of a wall-mounted switch was pushed down
* Added static info filed for device id 
* Fixes for ESP3 to ESP2 messages converter (Still not stable)
* Message delay added to eltako14bus so that buffer overflow in FGW14-USB gets prevented. (When sending many messages, messages get lot.)

## Version 1.3.4 Improved FTS14EM and Gateway Support
*  &#x1F41E; ESP3 Serial Communicator bug fix  &#x1F41E; 
*  Support for FTS14EM sending switches (EEP: F6-02-01, F6-02-02) and contacts (EEP: D5-00-01) telegram. (There are different FTS14EM versions sending different message types. Depending on that you need to choose the correct EEP)
*  Added sender_eep A5-38-08 support for swtiches
*  Filter for EltakoPoll messages inserted so that those messages won't span the whole Home Assistant bus.
*  Gateway reconnect button added.
*  Info fields added for Gateway (Id, Base Id, Serialo Port Path, Connected State, Last Received Message Timestamp, Received Message Count)

## Version 1.3.3 Added Temp and Humidity (EEP A5-04-01) and Occupancy Sensor (EEP A5-07-01)
* Added support for EEP A5-04-01 and A5-07-01
* Wrapper for ESP3 serial communication added. (It can automatically reconnect as well.) (Experimental Support)
* Converter for ESP3 to ESP2 messages added

## Version 1.3.2 Correction of Window Handle Positions (EEP F6-10-00)
*  &#x1F41E; Fixed Bug &#x1F41E;: Window handle status was not evaluated correctly

## Version 1.3.0 Reliable Serial Communication
* Switched to new **serial communication which automatically reconnect** in case of temporary connection/serial port loss.
  E.g. USB cable can be disconnected and plugged back in again and it will automatically reconnect without manual HA restart.

## Version 1.2.4 GUI for Automatic Generation of Configuration
* Device and sensor discovery and automatic generation of configuration improved and GUI added (See [docs](./eltakodevice_discovery/readme.md))
* &#x1F41E; Fixed Bug &#x1F41E;: Device names fixed.
* Improved config flow
* Support for serial over ethernet added

## Version 1.2.3 Improvements in Device Discovery
* &#x1F41E; Fixed Bug &#x1F41E;: Entity grouping for devices were broken.
* Eltako FMZ14 is working and tested ([Multifunction Time Relay](https://www.eltako.com/fileadmin/downloads/en/_bedienung/FMZ14_30014009-2_gb.pdf))
* Adjusted and extended [device discovery](./eltakodevice_discovery/readme.md) to multi-gateway support
* Windows support for device discovery added
* Device discovery detects base id of FAM14 automatically
* Device discovery detects a few registered sensors and puts them into the auto generated configuration.

## Version 1.2.2 Support for Multiple Gateways
* Full support for gateway [Eltako FAM-USB](https://www.eltako.com/en/product/professional-standard-en/three-phase-energy-meters-and-one-phase-energy-meters/fam-usb/)
* Target temperature synchronization between climate panel in Home Assistant and thermostat implemented.
* BaseId validation for gateways introduced. It will show warnings as output logs.
* Device Id can be displayed in device name optionally.
* Home Assistant eventing prepared to support more than one gateway
* Introduced ids for gateways.
* Manual installation of multiple gateways/hubs implemented. 
* **&#x26A0; Breaking Changes &#x26A0;**
  * All devices get a new identifier. Unfortunately, all devices need to be deleted and recreated. History of data gets lost!!!
  * Configuration: 'id' in 'gateway' is mandatory. See [docs](./docs/update_home_assistant_configuration.md)
  * Events in Home Assistant for switch telegrams have got different event_ids. This affects automations reacting on old event ids. See [docs](./docs/rocker_switch/readme.md)

## Version 1.1.3
* Added read-only support for gateway [Eltako FAM-USB](https://www.eltako.com/en/product/professional-standard-en/three-phase-energy-meters-and-one-phase-energy-meters/fam-usb/)

## Version 1.1.2
* Docs for configuration added
* USB port for serial communication can be configured in gateway section.
* Configuration keys were made consistent. Replaced '-' through '_'. This change may require adaptation to existing configurations.

## version 1.1.1 - BugFix
* Problems with general-settings in configuration file.

## version 1.1.0 - Heating and Cooling
* Change file introduced
* **Climate Panel introduced** incl. support for actors like FAE14, FHK14, F4HK14, F2L14, FHK61, FME14 and EEP A5-10-06 as well as control panels like FTAF55ED.
* Docs for Climate Panel/Heating and Cooling
* Refactoring
  * Introduced many explicit types.
  * Logging improved
* Prepared config for other gateway types. (Currently supported Eltako fam14 and fgw14-usb)
* Support of different gateways e.g. enOcean USB300 with different protocol version (ESP3)
* Added teach-in buttons for climate and temperature controller
* Added Air Quality Sensor with EEP A5-09-0C for e.g. FLGTF
* Integrate Eltako FUTH ([Wireless thermo clock/hygrostat](https://www.eltako.com/fileadmin/downloads/en/_bedienung/FUTH65D_12-24VUC_30065741-1_gb.pdf))
  * Temperature synchronization with FUTH and Home Assistant Climate (temperature controller) not yet properly working.
* Fast status change added. You can set per configuration is you want to wait for actuator response or if you directly want to see the status change in HA.

## Version 1.0.0 Baseline

