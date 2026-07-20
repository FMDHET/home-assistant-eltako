"""In-integration patches for known eltako14bus (0.0.73) bugs. (B5)

Strategy (decided in ANALYSE-UND-ROADMAP.md): patch the isolated buggy methods of the
PINNED library from within the integration rather than forking it. apply_eltakobus_patches()
is called once from async_setup and is idempotent. See KI-Optimierungen.md section 3b for
the bug catalogue.

Three bugs are patched here, all verifiable WITHOUT a device:
  * DefaultEnum.__repr__  - purely cosmetic UnboundLocalError when an enum value is
    formatted (no telegram/control-flow impact).
  * A5-30-01/-03 encode   - the learn (LRN) flag is written into the wrong bit. This
    does change bytes sent to real A5-30 devices, but the correction is
    spec-unambiguous: the EnOcean 4BS LRN bit is DB0.3 and the library's own
    decode_message already reads it there, so the patch only RELOCATES the same flag
    to the position both the spec and the decoder expect - no device needed to verify.
  * A5-04-03 decode (R3-08) - decode_message computes the raw temperature as
    ``data[1] * 265 + data[2]`` (a typo for ``* 256``). It offsets the DECODED
    temperature by +0.70 C per MSB count with a discontinuity at every 256 boundary.
    Decode-only - no telegram is sent - so the correction is verifiable without a
    device (this is distinct from the A5-04-03 ENCODE offset, still deferred).
One further documented bug changes VALUE/OFFSET semantics of telegrams sent to real
hardware / the climate display (A5-04-03 encode offset, A5-10-03 target-temp +8 offset)
and is deferred to the hardware session - the correct scaling must be verified against
a device, so patching it blind would be guesswork.

Every applied patch has a drift-guard test (tests/test_eltakobus_patches.py) that asserts
the pinned library STILL exhibits the original bug (via the saved original in _ORIGINALS),
so a future library bump that changes or fixes the method fails the test and forces the
patch to be re-evaluated instead of silently double-applying or conflicting."""
from __future__ import annotations

from .const import LOGGER

# Pre-patch method references, kept so the drift-guard tests can assert the pinned
# library still exhibits each original bug.
_ORIGINALS: dict = {}
_applied = False


def apply_eltakobus_patches() -> None:
    """Apply all in-integration eltakobus patches once (idempotent)."""
    global _applied
    if _applied:
        return
    try:
        _patch_default_enum_repr()
        _patch_a5_30_learn_bit_encode()
        _patch_a5_04_03_decode_temperature()
        _applied = True
        LOGGER.debug("Applied eltakobus in-integration patches (DefaultEnum repr, A5-30 learn-bit encode, A5-04-03 decode temp).")
    except Exception:
        # A library change could move or rename the patched symbols. Never let a patch
        # failure block integration setup - the drift-guard tests catch the change.
        LOGGER.exception("Could not apply eltakobus patches; continuing with the unpatched library.")


def _patch_default_enum_repr() -> None:
    """Bug: DefaultEnum.__repr__ binds a LOCAL variable named `repr`, which shadows the
    builtin for the whole function, so the `... or repr` on the preceding line raises
    UnboundLocalError as soon as an enum value is formatted (e.g. a ControllerPriority in
    an f-string). It also used %S instead of %s. Rebuild without shadowing."""
    from eltakobus.util import DefaultEnum
    if "DefaultEnum.__repr__" in _ORIGINALS:   # already patched (idempotent)
        return
    _ORIGINALS["DefaultEnum.__repr__"] = DefaultEnum.__repr__

    def __repr__(self) -> str:
        # getattr: _value_repr_ is a CPython Enum internal; fall back to the builtin repr
        value_repr = getattr(self.__class__, "_value_repr_", None) or repr
        text = "<%s.%s: %s" % (self.__class__.__name__, self._name_, value_repr(self._value_))
        if self.code:
            text += ' "%s"' % (self.code,)
        if self.description:
            text += ' "%s"' % (self.description,)
        return text + ">"

    DefaultEnum.__repr__ = __repr__


def _patch_a5_30_learn_bit_encode() -> None:
    """Bug: A5-30-01/-03 encode_message writes `data[3] = learn_button` (bit 0), but
    decode_message reads the LRN flag from bit 3 (`(data[3] & 0x08) >> 3`). A
    learn_button=1 telegram from the send-message service therefore decodes as a teach-in
    and is dropped by the (correct) learn guards. Wrap encode to place the flag in bit 3
    so encode/decode are symmetric and spec-correct (the LRN bit IS bit 3).

    Implemented as a wrapper that only rewrites data[3] (data[0..2] come from the original
    encode unchanged), so it survives unrelated changes to the rest of the method body."""
    from eltakobus.eep import _DigitalInputAndBattery, _DigitalInputsAndTemperature

    for cls in (_DigitalInputAndBattery, _DigitalInputsAndTemperature):
        key = f"{cls.__name__}.encode_message"
        if key in _ORIGINALS:   # already patched (idempotent - never wrap the wrapper)
            continue
        orig = cls.encode_message
        _ORIGINALS[key] = orig

        def _make(orig_encode):
            def encode_message(self, address):
                msg = orig_encode(self, address)
                # data[3] holds only the LRN flag for these EEPs; move it to bit 3.
                msg.data[3] = (int(self.learn_button) & 0x01) << 3
                return msg
            return encode_message

        cls.encode_message = _make(orig)


def _patch_a5_04_03_decode_temperature() -> None:
    """Bug (R3-08): A5-04-03 decode_message computes ``raw_temp = data[1] * 265 + data[2]``
    - a typo for ``* 256`` - so the decoded temperature is offset (+0.70 C per MSB count,
    with a jump at every 256 boundary). Decode-only, so verifiable without a device.

    Wrap decode_message to recompute ONLY the temperature with the correct * 256, rebuilding
    the result from the original's public fields so it survives unrelated changes elsewhere
    in the method body."""
    from eltakobus.eep import A5_04_03
    key = "A5_04_03.decode_message"
    if key in _ORIGINALS:   # already patched (idempotent - never wrap the wrapper)
        return
    orig = A5_04_03.decode_message   # bound classmethod of the pinned library
    _ORIGINALS[key] = orig

    def decode_message(cls, msg):
        obj = orig(msg)   # original (buggy) decode - carries humidity/learn_button/telegram_type
        raw_temp = msg.data[1] * 256 + msg.data[2]   # R3-08: * 256, not * 265
        temperature = ((raw_temp / 1024) * (cls.temp_max - cls.temp_min)) + cls.temp_min
        return cls(temperature, obj.humidity, obj.learn_button, obj.telegram_type)

    A5_04_03.decode_message = classmethod(decode_message)
