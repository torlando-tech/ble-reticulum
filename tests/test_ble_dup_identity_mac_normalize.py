"""
TDD test for the false-positive "Android MAC rotation" duplicate-identity rejection.

BUG DESCRIPTION
---------------
On two peers with FIXED MACs that both run central + peripheral mode, the same
physical peer is represented by two different address strings:

  * peripheral (GATT) path  -> "dev:B8:27:EB:43:04:BC"   (BlueZ D-Bus 'dev:' prefix)
  * central (scan) path     -> "B8:27:EB:43:04:BC"       (bare MAC)

BLEInterface.identity_to_address stores the peripheral (dev:-prefixed) form,
while the incoming central-mode handshake arrives with the bare form. The
_check_duplicate_identity() comparison:

    if existing_address and existing_address != address:   # "different MAC"?

therefore sees "dev:AA:BB" != "AA:BB" and concludes a false Android MAC rotation.
Because the existing (peripheral) connection is still alive, it REJECTS the
incoming connection and the peer interface is detached after the grace period.
The data path is then dropped: discovery announces are routed to 0 peers and the
peer never appears in the destination table.

OBSERVED (from logs, two touching Pi Zero 2 W, fixed MACs):
    GATTServer: Central connected: dev:B8:27:EB:43:04:BC (MTU: 517)
    duplicate identity detected: 211155f3 already connected via dev:B8:27:EB:43:04:BC,
        rejecting connection from B8:27:EB:43:04:BC (Android MAC rotation)
    scheduled detach ... / detached interface ... (grace period)
    TX: 183 bytes to 0 peer(s)

EXPECTED BEHAVIOR
-----------------
_check_duplicate_identity must treat "dev:AA:BB:.." and "AA:BB:.." as the SAME
address (same physical peer), so a fixed-MAC peer reconnecting via a different
mode is NOT rejected as a duplicate. True Android MAC rotation (genuinely
different MACs) must still be handled.

This test calls the REAL _check_duplicate_identity (not a replicated copy) and
should FAIL before the fix and PASS after.
"""

import pytest
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

from unittest.mock import MagicMock

import RNS
if not hasattr(RNS, 'LOG_INFO'):
    RNS.LOG_CRITICAL = 0
    RNS.LOG_ERROR = 1
    RNS.LOG_WARNING = 2
    RNS.LOG_NOTICE = 3
    RNS.LOG_INFO = 4
    RNS.LOG_VERBOSE = 5
    RNS.LOG_DEBUG = 6
    RNS.LOG_EXTREME = 7
    RNS.log = lambda msg, level=4: None
    RNS.prettyhexrep = lambda data: data.hex() if isinstance(data, bytes) else str(data)
    RNS.hexrep = lambda data, delimit=True: data.hex() if isinstance(data, bytes) else str(data)

if not hasattr(RNS, 'Transport'):
    RNS.Transport = MagicMock()
    RNS.Transport.interfaces = []

if not hasattr(RNS, 'Identity'):
    RNS.Identity = MagicMock()
    RNS.Identity.full_hash = lambda x: (x * 2)[:16]

import sys as _sys
if 'ble_reticulum.Interface' not in _sys.modules:
    class MockInterface:
        MODE_FULL = 1
        def __init__(self):
            self.IN = True
            self.OUT = True
            self.online = False
        @staticmethod
        def get_config_obj(configuration):
            class ConfigObj:
                def __init__(self, config):
                    self._config = config if config else {}
                def __getitem__(self, key):
                    return self._config.get(key)
                def get(self, key, default=None):
                    return self._config.get(key, default)
                def as_string(self, key, default=None):
                    val = self._config.get(key)
                    return str(val) if val is not None else default
                def as_int(self, key, default=None):
                    val = self._config.get(key)
                    return int(val) if val is not None else default
                def as_bool(self, key, default=False):
                    val = self._config.get(key)
                    if isinstance(val, bool):
                        return val
                    if isinstance(val, str):
                        return val.lower() in ('true', 'yes', '1', 'on')
                    return bool(val) if val is not None else default
            return ConfigObj(configuration)

    interface_module = MagicMock()
    interface_module.Interface = MockInterface
    _sys.modules['ble_reticulum.Interface'] = interface_module

from tests.mock_ble_driver import MockBLEDriver
from ble_reticulum.BLEInterface import BLEInterface

def _make_interface(local_mac):
    driver = MockBLEDriver(local_address=local_mac)
    owner = MagicMock()
    owner.inbound = MagicMock()
    config = {"name": "Test", "enable_central": True, "enable_peripheral": True}
    interface = BLEInterface(owner, config)
    interface.driver = driver
    interface.local_address = driver.local_address
    return interface


class TestDuplicateIdentityMacNormalization:
    """Fixed-MAC peers must not be rejected as a false Android MAC rotation."""

    def test_dev_prefix_same_mac_is_not_duplicate(self):
        """
        The exact observed case: identity_to_address holds the dev:-prefixed
        peripheral address and is still connected; the incoming central handshake
        carries the bare form of the SAME MAC. This is the same physical peer,
        NOT a MAC rotation, so it must NOT be rejected.
        """
        interface = _make_interface("11:22:33:44:55:66")
        peer_identity = b"\x01" * 16
        h = interface._compute_identity_hash(peer_identity)

        # Peripheral (GATT) connection is alive and stored with the dev: prefix.
        dev_addr = "dev:B8:27:EB:43:04:BC"
        bare_addr = "B8:27:EB:43:04:BC"
        interface.identity_to_address[h] = dev_addr
        interface.peers[dev_addr] = (MagicMock(), 0, 517)
        interface.driver.connect(dev_addr)  # keep it in driver.connected_peers

        # Central-mode handshake arrives with the bare MAC (same physical peer).
        rejected = interface._check_duplicate_identity(bare_addr, peer_identity)

        # Same MAC (differing only by the dev: prefix) is NOT a duplicate.
        assert rejected is False, (
            "A fixed-MAC peer reconnecting via a different mode was falsely "
            "rejected as an Android MAC rotation (dev: prefix not normalized)."
        )

    def test_case_only_difference_is_not_duplicate(self):
        """
        Uppercase vs lowercase of the same MAC must also compare equal.
        """
        interface = _make_interface("11:22:33:44:55:66")
        peer_identity = b"\x02" * 16
        h = interface._compute_identity_hash(peer_identity)

        stored = "B8:27:EB:43:04:BC"
        incoming = "b8:27:eb:43:04:bc"
        interface.identity_to_address[h] = stored
        interface.peers[stored] = (MagicMock(), 0, 517)
        interface.driver.connect(stored)

        rejected = interface._check_duplicate_identity(incoming, peer_identity)
        assert rejected is False, (
            "Case-only difference of the same MAC was falsely rejected as a "
            "duplicate identity."
        )

    def test_genuine_different_mac_still_evaluated(self):
        """
        A genuinely different MAC for the same identity (true Android MAC
        rotation) must still be rejected while the old connection is alive,
        so the fix does not disable MAC-rotation protection.
        """
        interface = _make_interface("11:22:33:44:55:66")
        peer_identity = b"\x03" * 16
        h = interface._compute_identity_hash(peer_identity)

        old_mac = "B8:27:EB:43:04:BC"
        new_mac = "CA:FE:DE:AD:00:01"
        interface.identity_to_address[h] = old_mac
        interface.peers[old_mac] = (MagicMock(), 0, 517)
        interface.driver.connect(old_mac)  # old connection alive

        rejected = interface._check_duplicate_identity(new_mac, peer_identity)
        assert rejected is True, (
            "A genuinely different MAC for the same identity (true MAC rotation) "
            "with the old connection still alive must still be rejected."
        )
