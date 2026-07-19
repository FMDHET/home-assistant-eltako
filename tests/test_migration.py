"""Tests for B2 (roadmap wave B): async_migrate_entry foundation.

Covers the 1.1 -> 1.2 config-entry unique_id backfill (AF1) for pre-v2.4.0 entries,
the idempotency/no-op behavior, the duplicate-gateway collision guard, and the
future-major-version rejection. This is the foundation the B3 unique_id migrations
will extend."""
import asyncio
import inspect
import os
import unittest
from unittest import mock

from tests.mocks import *
from custom_components.eltako.eltako_integration_init import (
    async_migrate_entry, _async_migrate_voc_unique_ids, _voc_localized_to_stable_key,
)
from custom_components.eltako.const import DOMAIN, CONF_GATEWAY_DESCRIPTION

ER = "homeassistant.helpers.entity_registry"
_PREFIX = "eltako_gw_123_00_00_00_01_"


class _FakeRegEntry:
    def __init__(self, unique_id, entity_id, domain="sensor", platform="eltako"):
        self.unique_id = unique_id
        self.entity_id = entity_id
        self.id = entity_id
        self.domain = domain
        self.platform = platform


class _FakeEntReg:
    """Minimal entity-registry stand-in modelling HA's uniqueness on update."""
    def __init__(self, entries):
        self._entries = list(entries)
        self.updated = []
    def async_get_entity_id(self, domain, platform, unique_id):
        for e in self._entries:
            if e.domain == domain and e.platform == platform and e.unique_id == unique_id:
                return e.entity_id
        return None
    def async_update_entity(self, entity_id, **kw):
        for e in self._entries:
            if e.entity_id == entity_id and "new_unique_id" in kw:
                e.unique_id = kw["new_unique_id"]
        self.updated.append((entity_id, kw))


def _run_voc_migration(entries):
    """Drive _async_migrate_voc_unique_ids against a fake registry, replicating HA's
    real async_migrate_entries loop (call migrator per entry, apply returned updates)."""
    reg = _FakeEntReg(entries)

    async def _fake_migrate(hass, entry_id, cb):
        for e in list(reg._entries):
            updates = cb(e)
            if updates is not None:
                reg.async_update_entity(e.entity_id, **updates)

    with mock.patch(f"{ER}.async_get", return_value=reg), \
         mock.patch(f"{ER}.async_migrate_entries", side_effect=_fake_migrate):
        asyncio.run(_async_migrate_voc_unique_ids(HassMock(), ConfigEntryMock(entry_id="e1")))
    return reg


def _entry(hass, unique_id=None, minor_version=1, version=1, desc="GW - fam-usb (Id: 2)"):
    eid = f"entry_{len(hass.config_entries.entries)}"
    e = ConfigEntryMock(data={CONF_GATEWAY_DESCRIPTION: desc}, entry_id=eid, domain=DOMAIN, unique_id=unique_id)
    e.minor_version = minor_version
    e.version = version
    hass.config_entries.entries.append(e)
    return e


class TestMigration(unittest.TestCase):
    # CURRENT_MINOR = the config flow's MINOR_VERSION; a full migration terminates here.
    CURRENT_MINOR = 3

    def setUp(self):
        # async_migrate_entry now also runs the 1.2->1.3 VOC entity-registry step. These
        # config-entry tests have no VOC entities, so stub the registry to a no-op
        # (er.async_get needs a real hass, which HassMock is not).
        self._patches = [
            mock.patch(f"{ER}.async_get", return_value=_FakeEntReg([])),
            mock.patch(f"{ER}.async_migrate_entries", side_effect=self._noop_migrate),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    @staticmethod
    async def _noop_migrate(hass, entry_id, cb):
        return None

    def test_legacy_entry_gets_unique_id_backfilled(self):
        hass = HassMock()
        e = _entry(hass, unique_id=None, minor_version=1)
        ok = asyncio.run(async_migrate_entry(hass, e))
        self.assertTrue(ok)
        self.assertEqual(e.unique_id, "eltako_gateway_2")
        self.assertEqual(e.minor_version, self.CURRENT_MINOR)

    def test_entry_with_unique_id_only_bumps_minor(self):
        hass = HassMock()
        e = _entry(hass, unique_id="eltako_gateway_2", minor_version=1)
        asyncio.run(async_migrate_entry(hass, e))
        self.assertEqual(e.unique_id, "eltako_gateway_2", "an existing unique_id must be preserved")
        self.assertEqual(e.minor_version, self.CURRENT_MINOR)

    def test_already_current_is_safe_noop(self):
        # at the current minor version async_migrate_entry must not rewrite anything
        hass = HassMock()
        e = _entry(hass, unique_id="eltako_gateway_2", minor_version=self.CURRENT_MINOR)
        ok = asyncio.run(async_migrate_entry(hass, e))
        self.assertTrue(ok)
        self.assertEqual(e.unique_id, "eltako_gateway_2")
        self.assertEqual(e.minor_version, self.CURRENT_MINOR)
        self.assertEqual(hass.config_entries.updated, [], "must not re-write an already-current entry")

    def test_collision_leaves_unique_id_unset(self):
        hass = HassMock()
        # a sibling entry already owns the derived unique_id (legacy AF1 duplicate)
        _entry(hass, unique_id="eltako_gateway_2", minor_version=self.CURRENT_MINOR)
        legacy = _entry(hass, unique_id=None, minor_version=1)   # same gateway (Id: 2)
        asyncio.run(async_migrate_entry(hass, legacy))
        self.assertIsNone(legacy.unique_id, "must not create a duplicate unique_id")
        self.assertEqual(legacy.minor_version, self.CURRENT_MINOR, "migration still completes (won't retry)")

    def test_future_major_version_rejected(self):
        """Defensive guard, called directly. In production HA itself rejects
        version>VERSION BEFORE invoking async_migrate_entry, so this path is not
        reachable via HA; the guard is belt-and-suspenders for a future major bump
        and is exercised here directly."""
        hass = HassMock()
        e = _entry(hass, unique_id=None, minor_version=1, version=2)
        ok = asyncio.run(async_migrate_entry(hass, e))
        self.assertFalse(ok, "a newer major version must not be silently accepted")
        self.assertEqual(e.minor_version, 1, "must not touch an entry it cannot migrate")

    def test_malformed_description_leaves_unset_but_bumps(self):
        hass = HassMock()
        e = _entry(hass, unique_id=None, minor_version=1, desc="gateway without an id marker")
        asyncio.run(async_migrate_entry(hass, e))
        self.assertIsNone(e.unique_id)
        self.assertEqual(e.minor_version, self.CURRENT_MINOR, "cannot derive an id, but the steps still complete")


class TestMigrationWiring(unittest.TestCase):
    """Guards for how HA discovers and calls the migration - the feature is wired
    only via `from .eltako_integration_init import *` (no __all__) and depends on the
    HA async_update_entry signature; both could break with a still-green suite."""

    @unittest.skipIf(os.environ.get("SKIPP_IMPORT_HOME_ASSISTANT"), "package init skipped in lib-only mode")
    def test_async_migrate_entry_is_discoverable_on_package(self):
        """HA calls hasattr(component, 'async_migrate_entry') where component is the
        `custom_components.eltako` package module. A future refactor that adds an
        __all__ omitting it (or drops the star-import) would silently disable ALL
        config-entry migrations while every direct-import test stays green."""
        import custom_components.eltako as pkg
        self.assertTrue(hasattr(pkg, "async_migrate_entry"),
                        "async_migrate_entry must be re-exported on the package for HA to call it")

    def test_async_update_entry_signature_has_expected_kwargs(self):
        """Drift guard: async_migrate_entry relies on hass.config_entries.async_update_entry
        accepting unique_id= and minor_version=. Pin those against the installed HA so a
        breaking API change fails here, not at runtime on entry load."""
        from homeassistant.config_entries import ConfigEntries
        params = inspect.signature(ConfigEntries.async_update_entry).parameters
        for kw in ("unique_id", "minor_version"):
            self.assertIn(kw, params, f"HA async_update_entry lost the '{kw}' kwarg the migration relies on")


class TestVocMigration(unittest.TestCase):
    """B3/AS3: VOC air-quality unique_id remap (localized -> language-independent name_en),
    the first entity-registry migration built on the B2 foundation."""

    def _entry(self, uid_suffix, eid="s1"):
        return _FakeRegEntry(_PREFIX + uid_suffix, eid)

    def test_german_substance_id_migrated_to_english(self):
        reg = _run_voc_migration([self._entry("air_quality_sensor_benzol")])
        self.assertEqual(reg._entries[0].unique_id, _PREFIX + "air_quality_sensor_benzene")

    def test_english_substance_id_unchanged(self):
        reg = _run_voc_migration([self._entry("air_quality_sensor_benzene")])
        self.assertEqual(reg._entries[0].unique_id, _PREFIX + "air_quality_sensor_benzene")
        self.assertEqual(reg.updated, [], "an already-stable id must not be rewritten")

    def test_ambiguous_localized_name_not_migrated(self):
        # 'styren' maps to two substances (upstream data bug) -> left as-is, never remapped
        reg = _run_voc_migration([self._entry("air_quality_sensor_styren")])
        self.assertEqual(reg._entries[0].unique_id, _PREFIX + "air_quality_sensor_styren")
        self.assertEqual(reg.updated, [])

    def test_collision_with_existing_stable_entity_is_skipped(self):
        # user switched language: both the de and en entities exist for one substance;
        # the de remap must be skipped (not crash / not overwrite the en entity).
        reg = _run_voc_migration([
            self._entry("air_quality_sensor_benzol", "de1"),
            self._entry("air_quality_sensor_benzene", "en1"),
        ])
        ids = sorted(e.unique_id for e in reg._entries)
        self.assertEqual(ids, [_PREFIX + "air_quality_sensor_benzene", _PREFIX + "air_quality_sensor_benzol"],
                         "collision must be skipped, leaving both entities intact")

    def test_non_voc_entity_untouched(self):
        reg = _run_voc_migration([self._entry("electricity_cumulative", "m1")])
        self.assertEqual(reg._entries[0].unique_id, _PREFIX + "electricity_cumulative")
        self.assertEqual(reg.updated, [])

    def test_stable_key_map_excludes_ambiguous_and_maps_de_to_en(self):
        m = _voc_localized_to_stable_key()
        self.assertNotIn("styren", m, "ambiguous localized name must be excluded")
        self.assertEqual(m.get("benzol"), "benzene")
        self.assertEqual(m.get("benzene"), "benzene")

    def test_full_migrate_entry_advances_to_1_3(self):
        entry = ConfigEntryMock(data={CONF_GATEWAY_DESCRIPTION: "GW (Id: 2)"},
                                entry_id="e9", domain=DOMAIN, unique_id="eltako_gateway_2")
        entry.minor_version = 2
        hass = HassMock()
        hass.config_entries.entries.append(entry)
        reg = _FakeEntReg([_FakeRegEntry(_PREFIX + "air_quality_sensor_benzol", "s1")])

        async def _fake_migrate(h, eid, cb):
            for e in list(reg._entries):
                u = cb(e)
                if u is not None:
                    reg.async_update_entity(e.entity_id, **u)

        with mock.patch(f"{ER}.async_get", return_value=reg), \
             mock.patch(f"{ER}.async_migrate_entries", side_effect=_fake_migrate):
            ok = asyncio.run(async_migrate_entry(hass, entry))
        self.assertTrue(ok)
        self.assertEqual(entry.minor_version, 3)
        self.assertEqual(reg._entries[0].unique_id, _PREFIX + "air_quality_sensor_benzene")


if __name__ == "__main__":
    unittest.main()
