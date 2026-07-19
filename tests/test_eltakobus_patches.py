"""Tests for the in-integration eltakobus patches (B5).

Two kinds of test per patch:
  * DRIFT GUARD - asserts the PINNED library still exhibits the original bug (via the
    saved original in _ORIGINALS). If a library bump changes/fixes the method, this fails
    and forces the patch to be re-evaluated.
  * BEHAVIOR - asserts the applied patch produces the corrected result."""
import unittest

from custom_components.eltako.eltakobus_patches import apply_eltakobus_patches, _ORIGINALS
from eltakobus.eep import A5_10_06, A5_30_01, A5_30_03
from eltakobus.message import Regular4BSMessage

ADDR = b'\xff\xaa\x80\x01'


class TestEltakobusPatches(unittest.TestCase):

    def setUp(self):
        apply_eltakobus_patches()   # idempotent; captures _ORIGINALS on first call

    # --- drift guards: the pinned library must still have each original bug ----

    def test_drift_default_enum_repr_bug_present(self):
        self.assertIn("DefaultEnum.__repr__", _ORIGINALS,
                      "DefaultEnum.__repr__ not captured - the library may have renamed it")
        orig_repr = _ORIGINALS["DefaultEnum.__repr__"]
        # original binds a local `repr`, shadowing the builtin -> UnboundLocalError
        with self.assertRaises(UnboundLocalError):
            orig_repr(A5_10_06.ControllerPriority.AUTO)

    def test_drift_a5_30_learn_bit_bug_present(self):
        key = "_DigitalInputAndBattery.encode_message"
        self.assertIn(key, _ORIGINALS, f"{key} not captured - the library may have renamed it")
        orig = _ORIGINALS[key]
        inst = A5_30_01(battery_status=200, contact_status=200, learn_button=1)
        msg = orig(inst, ADDR)
        self.assertEqual(msg.data[3], 0x01,
                         "pinned lib should still write the LRN flag into bit 0 (the encode bug)")
        # The patch's correctness ALSO depends on decode reading the LRN flag from bit 3;
        # pin that so a lib bump that moves the decoded bit is caught by the drift guard
        # itself (not only by the behavior round-trip test).
        dec_bit3 = A5_30_01.decode_message(Regular4BSMessage(ADDR, 0x00, bytes([0, 200, 200, 0x08])))
        dec_bit0 = A5_30_01.decode_message(Regular4BSMessage(ADDR, 0x00, bytes([0, 200, 200, 0x01])))
        self.assertEqual(dec_bit3.learn_button, 1, "decode must read the LRN flag from bit 3")
        self.assertEqual(dec_bit0.learn_button, 0, "decode must NOT read the LRN flag from bit 0")

    # --- behavior: DefaultEnum repr ------------------------------------------

    def test_default_enum_repr_no_longer_raises(self):
        r = repr(A5_10_06.ControllerPriority.AUTO)
        self.assertIn("ControllerPriority", r)
        self.assertIn("AUTO", r)

    # --- behavior: A5-30 learn bit -------------------------------------------

    def test_a5_30_01_learn_bit_in_bit3_and_roundtrips(self):
        inst = A5_30_01(battery_status=200, contact_status=200, learn_button=1)
        msg = inst.encode_message(ADDR)
        self.assertEqual((msg.data[3] & 0x08) >> 3, 1, "LRN flag must be in bit 3")
        self.assertEqual(A5_30_01.decode_message(msg).learn_button, 1,
                         "encode/decode must round-trip learn_button=1")

    def test_a5_30_03_learn_bit_in_bit3_and_roundtrips(self):
        inst = A5_30_03(20, 0, 0, 0, 0, 0, 1)
        msg = inst.encode_message(ADDR)
        self.assertEqual((msg.data[3] & 0x08) >> 3, 1)
        self.assertEqual(A5_30_03.decode_message(msg).learn_button, 1)

    def test_a5_30_learn_bit_zero_stays_zero(self):
        inst = A5_30_01(battery_status=200, contact_status=200, learn_button=0)
        msg = inst.encode_message(ADDR)
        self.assertEqual(msg.data[3], 0x00)
        self.assertEqual(A5_30_01.decode_message(msg).learn_button, 0)

    def test_a5_30_03_other_data_bytes_preserved(self):
        # the wrapper must only touch data[3] - digital inputs (data[2]) stay intact
        inst = A5_30_03(20, 1, 0, 1, 0, 0, 1)
        msg = inst.encode_message(ADDR)
        self.assertEqual(msg.data[2] & 0x01, 1)   # digital_input_0
        self.assertEqual((msg.data[2] & 0x04) >> 2, 1)   # digital_input_2


class TestTcp2SerialDriftGuard(unittest.TestCase):
    """Drift guard for tcp2serial_hardened.py, which carries a COPY of the pinned
    esp2_gateway_adapter (0.2.21) TCP2SerialCommunicator.run() with the EOF + kernel
    keep-alive fix (K8). If a future adapter bump changes upstream run(), the copy must
    be re-diffed - this test fails loudly with that instruction."""

    # sha256 of inspect.getsource(TCP2SerialCommunicator.run) for esp2_gateway_adapter==0.2.21
    _RUN_BASELINE_SHA256 = "3f564cd0095c5642625bec0c005c3f2766a2ff11edbeb2862d4017231c683cba"

    def test_upstream_run_source_unchanged(self):
        import hashlib
        import inspect
        from esp2_gateway_adapter.esp3_tcp_com import TCP2SerialCommunicator
        src = inspect.getsource(TCP2SerialCommunicator.run)
        digest = hashlib.sha256(src.encode("utf-8")).hexdigest()
        self.assertEqual(
            digest, self._RUN_BASELINE_SHA256,
            "esp2_gateway_adapter TCP2SerialCommunicator.run() changed upstream. Re-diff "
            "custom_components/eltako/tcp2serial_hardened.py against the new implementation, "
            "port any changes into HardenedTCP2SerialCommunicator.run(), then update this "
            "baseline hash.")


if __name__ == "__main__":
    unittest.main()
