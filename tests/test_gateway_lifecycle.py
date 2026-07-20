"""R3-05 + R3-06: gateway bus lifecycle safety.

R3-05: reconnect() and _unload_blocking() are serialized by a per-gateway
lifecycle lock. Overlapping calls (double reconnect button press, or a reconnect
racing the unload on reload) can otherwise start one bus, then have the other
reassign self._bus and start a second one - leaving the first bus running
orphaned, holding a single-client gateway's only connection slot while self._bus
points at a bus that never connects. After unload, reconnect() is a no-op.

R3-06: bus connection-state callbacks are bound to their bus generation. A
superseded (zombie) bus thread that survived join(10) and later fires its exit
connected=False AFTER the new generation reported connected=True is ignored,
so the gateway connection state is never stranded on a stale value.

The tests patch _init_bus to hand out observable fake bus generations, so they
do not depend on real serial/TCP resources (and stay independent of the global
class mocking some other test modules install)."""
import threading
import time
import types
import unittest

from tests.mocks import GatewayMock


class _FakeBus:
    """Minimal stand-in for the RS485/TCP bus with an observable lifecycle.

    Deliberately does NOT fire the status handler on registration (unlike the
    real bus) so each test controls exactly when a generation reports state."""

    def __init__(self):
        self.started = False
        self.stopped = False
        self._alive = False
        self.status_handler = None

    def set_status_changed_handler(self, handler):
        self.status_handler = handler

    def is_active(self):
        return self.started and not self.stopped

    def start(self):
        self.started = True
        self._alive = True

    def stop(self):
        self.stopped = True

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        # simulate a thread taking a moment to wind down -> widens the race window
        time.sleep(0.05)
        self._alive = False


def _install_fake_bus_factory(gw):
    """Make gw._init_bus create tracked _FakeBus generations, and seed a running one."""
    created = []

    def fake_init_bus(self):
        bus = _FakeBus()
        self._bus = bus
        created.append(bus)
        bus.set_status_changed_handler(lambda status, b=bus: self._on_bus_status(b, status))

    gw._init_bus = types.MethodType(fake_init_bus, gw)
    gw._init_bus()
    gw._bus.start()
    return created


class TestGatewayLifecycle(unittest.TestCase):

    def _gw(self):
        gw = GatewayMock(dev_id=123)
        gw._shutdown = False
        return gw

    # --- R3-05: lifecycle serialization --------------------------------------

    def test_concurrent_reconnects_leave_exactly_one_running_bus(self):
        gw = self._gw()
        created = _install_fake_bus_factory(gw)

        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()
            gw.reconnect()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        active = [b for b in created if b.is_active()]
        self.assertEqual(len(active), 1,
                         "exactly one bus generation must remain running (no orphan)")
        self.assertIs(active[0], gw._bus, "the running bus must be the current self._bus")
        for b in created:
            if b is not gw._bus:
                self.assertTrue(b.stopped, "every superseded bus must have been stopped")

    def test_reconnect_racing_unload_does_not_resurrect_bus(self):
        gw = self._gw()
        created = _install_fake_bus_factory(gw)

        barrier = threading.Barrier(2)

        def do_reconnect():
            barrier.wait()
            gw.reconnect()

        def do_unload():
            barrier.wait()
            gw._unload_blocking()

        threads = [threading.Thread(target=do_reconnect), threading.Thread(target=do_unload)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(gw._shutdown, "_unload_blocking must set the shutdown flag")
        self.assertFalse(any(b.is_active() for b in created),
                         "after unload no bus generation may still be running")

    def test_reconnect_after_unload_is_noop(self):
        gw = self._gw()
        created = _install_fake_bus_factory(gw)
        gw._unload_blocking()
        n = len(created)
        gw.reconnect()
        self.assertEqual(len(created), n, "reconnect() after unload must not build a new bus")
        self.assertFalse(any(b.is_active() for b in created))

    # --- R3-06: per-generation connection-state guard ------------------------

    def test_stale_generation_status_is_ignored(self):
        gw = self._gw()
        fired = []
        gw._fire_connection_state_changed_event = lambda status: fired.append(status)
        old_bus = object()
        new_bus = object()
        gw._bus = new_bus
        gw._on_bus_status(old_bus, False)   # zombie/superseded generation -> ignored
        gw._on_bus_status(new_bus, True)     # current generation -> forwarded
        self.assertEqual(fired, [True])

    def test_zombie_exit_after_generation_swap_is_dropped(self):
        gw = self._gw()
        fired = []
        gw._fire_connection_state_changed_event = lambda status: fired.append(status)
        _install_fake_bus_factory(gw)
        gen1 = gw._bus
        gen1.status_handler(True)      # current generation reports connected -> delivered
        gw._init_bus()                 # supersede: new generation becomes current
        gen1.status_handler(False)     # zombie exit fires connected=False -> must be dropped
        gw._bus.status_handler(True)   # new current generation -> delivered
        self.assertEqual(fired, [True, True])


if __name__ == "__main__":
    unittest.main()
