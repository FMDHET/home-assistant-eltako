## K7 — Geräte über die UI/API löschbar machen (`async_remove_config_entry_device`)

**Status:** ☑ UMGESETZT (2026-07-17, permissive Variante `return True` in `eltako_integration_init.py` nach `async_unload_entry`). Optionaler Config-Abgleich bleibt Folge-Task.
**Priorität:** mittel (kein Funktionsverlust, aber deutliche UX-Verbesserung + Aufräum-Fähigkeit)
**Aufwand:** ~10 Minuten
**Betroffene Datei:** `custom_components/eltako/eltako_integration_init.py`
**Entdeckt am:** 17.07.2026, beim Versuch, das Gerät „FUD61" (`42-AA-F0-90`) zu entfernen

---

### Problem

Eltako-Geräte lassen sich **nicht** aus Home Assistant löschen — weder über die
UI (Einstellungen → Geräte & Dienste → Gerät → „Gerät löschen") noch über die
WebSocket-API. Der Löschen-Button fehlt in der Oberfläche komplett.

Das ist besonders schmerzhaft, weil die Integration YAML-konfiguriert ist und
laut Upstream-README gilt:

> *Devices that are later removed from the configuration are not deleted in HA.*

Entfernt man also ein Gerät aus der `eltako.yaml`, bleiben Geräteeintrag und
Entities als Karteileichen in der Registry zurück — **und es gibt keinen Weg,
sie über die Oberfläche loszuwerden.** Der einzige Ausweg ist aktuell, die
Registry-Einträge über die API einzeln zu löschen (`ha_remove_entity` o. ä.)
oder `core.device_registry` von Hand zu editieren.

### Symptom

WebSocket-Aufruf `config/device_registry/remove_config_entry` schlägt fehl mit:

```
Config entry does not support device removal
```

Vollständige Fehlerantwort beim Löschversuch:

```json
{
  "success": false,
  "error": {
    "code": "SERVICE_CALL_FAILED",
    "message": "Failed to remove device from any config entries"
  },
  "removal_results": [
    {
      "config_entry_id": "01KXRP0DFTWRWXYNYZ1PZZZH9N",
      "success": false,
      "error": "Command failed: Config entry does not support device removal"
    }
  ]
}
```

In der UI äußert sich das schlicht dadurch, dass im Drei-Punkte-Menü des Geräts
kein „Gerät löschen"-Eintrag erscheint.

### Ursache

Home Assistant prüft, ob die Integration die optionale Funktion
`async_remove_config_entry_device` auf Modulebene bereitstellt. Fehlt sie, wird
Gerätelöschung serverseitig grundsätzlich verweigert und der Button im Frontend
ausgeblendet.

In `eltako_integration_init.py` existieren aktuell:

- `async_setup`
- `async_setup_entry`
- `async_unload_entry`
- (Hilfsfunktionen: `async_unload_gateway`, `get_gateway_from_hass`, …)

**`async_remove_config_entry_device` fehlt** — daher das Verhalten. Es handelt
sich also nicht um einen Bug, sondern um eine schlicht nicht implementierte
optionale API.

### Lösung

Folgende Funktion auf **Modulebene** (nicht verschachtelt!) in
`custom_components/eltako/eltako_integration_init.py` ergänzen. Sinnvolle
Platzierung: direkt nach `async_unload_entry` am Dateiende.

```python
async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Erlaubt das Entfernen eines Eltako-Geräts über die Home-Assistant-UI.

    Die Integration ist YAML-konfiguriert: Geräte, die noch in der
    `eltako:`-Sektion stehen, werden beim nächsten Reload ohnehin wieder
    angelegt. Diese Funktion dient daher primär dem Aufräumen von Altlasten
    nach einer Konfigurationsänderung.

    Rückgabe True => Home Assistant entfernt den Geräteeintrag samt seiner
    Entities aus der Registry.
    """
    LOGGER.info(
        f"[{LOG_PREFIX_INIT}] Gerät wird auf Nutzerwunsch aus der Registry entfernt: "
        f"{device_entry.name} (identifiers: {device_entry.identifiers})"
    )
    return True
```

**Keine neuen Imports nötig** — alle benötigten Namen sind in der Datei bereits
vorhanden (geprüft):

| Name           | Herkunft (bereits importiert)                                          |
|----------------|------------------------------------------------------------------------|
| `HomeAssistant`| `from homeassistant.core import HomeAssistant`                          |
| `ConfigEntry`  | `from homeassistant.config_entries import ConfigEntry`                  |
| `dr`           | `from homeassistant.helpers import ... device_registry as dr, ...`      |
| `LOGGER`       | via `from .const import *`                                              |
| `LOG_PREFIX_INIT` | in derselben Datei definiert                                        |

### Testschritte

1. Funktion einfügen, Datei speichern
2. Home Assistant **neu starten** (ein Integrations-Reload reicht nicht — die
   Funktion wird beim Laden des Moduls registriert)
3. Einstellungen → Geräte & Dienste → Eltako → beliebiges Gerät öffnen
4. Drei-Punkte-Menü → **„Gerät löschen"** muss jetzt vorhanden sein
5. Gerät löschen → Geräteeintrag und zugehörige Entities verschwinden aus der Registry
6. Gegenprobe: Steht das Gerät noch in der `eltako.yaml`, wird es beim nächsten
   Reload/Neustart wieder angelegt — das ist erwartetes Verhalten (siehe unten)

### Wichtige Einschränkung

`return True` gibt **jedes** Gerät zur Löschung frei, auch solche, die noch aktiv
in der `eltako.yaml` konfiguriert sind. Diese kommen beim nächsten Reload
zurück. Das ist bei YAML-basierten Integrationen üblich und akzeptabel, kann für
Nutzer aber verwirrend wirken („ich hab's doch gelöscht?!").

**Optionale Verschärfung (Folge-Task):** Vor `return True` prüfen, ob der
Device-Identifier (`device_entry.identifiers`, Format `{("eltako", "42-AA-F0-90")}`)
noch in der geladenen Konfiguration vorkommt, und nur dann `True` zurückgeben,
wenn er dort **nicht** mehr steht. Die Konfiguration liegt zur Laufzeit unter
`hass.data[DATA_ELTAKO][ELTAKO_CONFIG]`; die Auflösung der Geräte-IDs pro Gateway
läuft über `config_helpers.get_device_config(...)`.

⚠️ Vor Umsetzung dieser Variante muss geprüft werden, wie die Device-Identifier
beim Anlegen konkret gebildet werden (Groß-/Kleinschreibung, Trennzeichen,
Gateway-Präfix) — sonst schlägt der Vergleich fehl und die Löschung wird
fälschlich verweigert. Die permissive Variante oben ist der sichere erste Schritt.

**Gateway-Gerät beachten:** Auch der Gateway-Eintrag selbst
(z. B. `EUL_Falk - mgw-lan (Id: 1)`) wird mit `return True` löschbar. Das Löschen
entfernt nur den Registry-Eintrag, nicht den Config Entry — das Gateway wird beim
nächsten Reload neu angelegt. Falls das unerwünscht ist: Gateway-Geräte anhand
ihres Modells (`EnOcean Gateway - …`) bzw. daran, dass `via_device_id == device_id`
gilt, gezielt ausschließen und `False` zurückgeben.

### Referenzen

- HA Developer Docs: *Config Entries → Removing devices*
  https://developers.home-assistant.io/docs/config_entries_index/#removing-devices
- Upstream-Repo: https://github.com/grimmpp/home-assistant-eltako
- Konkreter Anlassfall: Gerät „FUD61", Model `M5-38-08`, EnOcean-ID `42-AA-F0-90`,
  Device-ID `0c84b60c72ff7c70f346159270eefdf4` — hatte nur `sensor.*_id` und
  `button.*_teach_in_button`, aber **keine** `light`-Entity (im Gegensatz zu
  AktuatorEG/AktuatorOG mit demselben EEP). Separat prüfenswert, ob der
  YAML-Eintrag unvollständig ist oder unter der falschen Plattform steht.
