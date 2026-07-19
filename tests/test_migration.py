"""Tests for B2 (roadmap wave B): async_migrate_entry foundation.

Covers the 1.1 -> 1.2 config-entry unique_id backfill (AF1) for pre-v2.4.0 entries,
the idempotency/no-op behavior, the duplicate-gateway collision guard, and the
future-major-version rejection. This is the foundation the B3 unique_id migrations
will extend."""
import asyncio
import inspect
import os
import unittest

from tests.mocks import *
from custom_components.eltako.eltako_integration_init import async_migrate_entry
from custom_components.eltako.const import DOMAIN, CONF_GATEWAY_DESCRIPTION


def _entry(hass, unique_id=None, minor_version=1, version=1, desc="GW - fam-usb (Id: 2)"):
    eid = f"entry_{len(hass.config_entries.entries)}"
    e = ConfigEntryMock(data={CONF_GATEWAY_DESCRIPTION: desc}, entry_id=eid, domain=DOMAIN, unique_id=unique_id)
    e.minor_version = minor_version
    e.version = version
    hass.config_entries.entries.append(e)
    return e


class TestMigration(unittest.TestCase):

    def test_legacy_entry_gets_unique_id_backfilled(self):
        hass = HassMock()
        e = _entry(hass, unique_id=None, minor_version=1)
        ok = asyncio.run(async_migrate_entry(hass, e))
        self.assertTrue(ok)
        self.assertEqual(e.unique_id, "eltako_gateway_2")
        self.assertEqual(e.minor_version, 2)

    def test_entry_with_unique_id_only_bumps_minor(self):
        hass = HassMock()
        e = _entry(hass, unique_id="eltako_gateway_2", minor_version=1)
        asyncio.run(async_migrate_entry(hass, e))
        self.assertEqual(e.unique_id, "eltako_gateway_2", "an existing unique_id must be preserved")
        self.assertEqual(e.minor_version, 2)

    def test_already_current_is_safe_noop(self):
        # HA only calls this when behind, but calling it at 1.2 must not corrupt anything.
        hass = HassMock()
        e = _entry(hass, unique_id="eltako_gateway_2", minor_version=2)
        ok = asyncio.run(async_migrate_entry(hass, e))
        self.assertTrue(ok)
        self.assertEqual(e.unique_id, "eltako_gateway_2")
        self.assertEqual(e.minor_version, 2)
        self.assertEqual(hass.config_entries.updated, [], "must not re-write an already-current entry")

    def test_collision_leaves_unique_id_unset(self):
        hass = HassMock()
        # a sibling entry already owns the derived unique_id (legacy AF1 duplicate)
        _entry(hass, unique_id="eltako_gateway_2", minor_version=2)
        legacy = _entry(hass, unique_id=None, minor_version=1)   # same gateway (Id: 2)
        asyncio.run(async_migrate_entry(hass, legacy))
        self.assertIsNone(legacy.unique_id, "must not create a duplicate unique_id")
        self.assertEqual(legacy.minor_version, 2, "migration is still marked done (won't retry)")

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
        self.assertEqual(e.minor_version, 2, "cannot derive an id, but the step still completes")


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


if __name__ == "__main__":
    unittest.main()
