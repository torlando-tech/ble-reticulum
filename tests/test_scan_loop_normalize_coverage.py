"""
Coverage + behavior tests for the scan-loop address-normalization block in
BLEInterface._select_peers_to_connect (the "Protocol v2.2: skip if interface
exists" branch), plus the falsy-input branch of _normalize_address.

WHY THIS FILE EXISTS
--------------------
The normalization added by this PR to the scan-loop peer-selection path
(_select_peers_to_connect, the "same identity at different MAC" handling) is
behaviorally pinned elsewhere by tests/test_v2_2_mac_sorting.py, but that file
is EXCLUDED from the integration coverage run (the workflow passes
--ignore=tests/test_v2_2_mac_sorting.py). As a result codecov/patch sees the
changed scan-loop lines as uncovered and the check fails even though the code
is tested.

This file drives the REAL _select_peers_to_connect from an INCLUDED test module
so those changed lines register as covered, and pins each branch:

  * MAC rotation, old connection still alive  -> skip (not re-selected)
  * MAC rotation, old connection dead         -> cleanup + re-select
  * same normalized address (dev: vs bare)    -> skip (interface exists)

It also covers the falsy-input branch of _normalize_address (empty/None -> "").

These are behavior tests: each asserts the actual selection outcome, not just
that a line was touched, so a regression that alters which peers get selected
will fail the test, not merely the coverage threshold.
"""

import sys
import os

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
from ble_reticulum.BLEInterface import BLEInterface, DiscoveredPeer

DEV_MAC = "B8:27:EB:43:04:BC"
DEV_FORM = "dev:" + DEV_MAC
OTHER_MAC = "CA:FE:DE:AD:00:99"


def _make_interface(local_mac):
    driver = MockBLEDriver(local_address=local_mac)
    owner = MagicMock()
    owner.inbound = MagicMock()
    config = {"name": "Test", "enable_central": True, "enable_peripheral": True,
              "max_connections": 4}
    interface = BLEInterface(owner, config)
    interface.driver = driver
    interface.local_address = driver.local_address
    return interface


def _seed_scan_loop(interface, identity, incoming_address, existing_address,
                    existing_alive):
    """
    Seed the interface so that _select_peers_to_connect reaches the "Protocol
    v2.2: skip if interface exists" branch for `incoming_address`.

    identity           : the 16-byte peer identity
    incoming_address   : the address the scan loop is considering
    existing_address   : what identity_to_address currently maps the identity to
    existing_alive     : whether existing_address is still in self.peers
    """
    h = interface._compute_identity_hash(identity)

    # The scan loop must see this address as having a known identity and an
    # already-spawned interface for that identity.
    interface.address_to_identity[incoming_address] = identity
    interface.spawned_interfaces[h] = MagicMock()
    interface.identity_to_address[h] = existing_address

    # The peer must not already be in self.peers (else the top-level skip at
    # "address in self.peers" fires before we reach the v2.2 branch).
    if existing_alive:
        interface.peers[existing_address] = (MagicMock(), 0, 517)

    # A discovered peer with no prior connection attempt (avoids the 5s
    # rate-limit skip) so the loop reaches the v2.2 block.
    peer = DiscoveredPeer(incoming_address, "test-peer", -40)
    peer.last_connection_attempt = 0
    interface.discovered_peers[incoming_address] = peer
    return peer


class TestScanLoopNormalizationBranches:
    """Behavior + coverage for the scan-loop v2.2 same-identity branch."""

    def test_rotation_old_connection_alive_is_skipped(self):
        """
        Genuine MAC rotation with the OLD connection still alive: the incoming
        (new) MAC must be SKIPPED (we do not tear down the live old connection).
        """
        interface = _make_interface("11:22:33:44:55:66")
        identity = b"\x11" * 16

        _seed_scan_loop(
            interface, identity,
            incoming_address=OTHER_MAC,          # new MAC
            existing_address=DEV_MAC,            # old MAC, still connected
            existing_alive=True,
        )

        selected = interface._select_peers_to_connect()
        selected_addrs = {p.address for p in selected}
        assert OTHER_MAC not in selected_addrs, (
            "A genuinely-rotated MAC with the old connection still alive must "
            "be skipped, not re-selected."
        )

    def test_rotation_old_connection_dead_is_reselected(self):
        """
        Genuine MAC rotation where the OLD connection is dead: the incoming new
        MAC must be cleaned up (stale address removed) and RE-SELECTED so we
        reconnect under the new MAC.
        """
        interface = _make_interface("11:22:33:44:55:66")
        identity = b"\x22" * 16

        _seed_scan_loop(
            interface, identity,
            incoming_address=OTHER_MAC,          # new MAC
            existing_address=DEV_MAC,            # old MAC, NOT in self.peers
            existing_alive=False,
        )
        # _cleanup_stale_address must be called for the old address.
        interface._cleanup_stale_address = MagicMock()

        selected = interface._select_peers_to_connect()
        selected_addrs = {p.address for p in selected}

        assert OTHER_MAC in selected_addrs, (
            "A rotated MAC whose old connection is dead must be re-selected so "
            "we reconnect under the new MAC."
        )
        interface._cleanup_stale_address.assert_called_once()
        # The cleanup was for the stale (old) address.
        call_args = interface._cleanup_stale_address.call_args[0]
        assert call_args[1] == DEV_MAC

    def test_same_normalized_address_interface_exists_is_skipped(self):
        """
        The fixed-MAC peer reappears under the other address FORM (bare where
        the stored form is dev:-prefixed, or vice versa). Once normalized they
        are the SAME physical peer and an interface already exists, so the scan
        loop must SKIP it (the standard same-MAC reconnect path handles it
        elsewhere). This is the branch the headline fix routes here.
        """
        interface = _make_interface("11:22:33:44:55:66")
        identity = b"\x33" * 16

        # Stored in dev: (peripheral) form; scan sees the bare (central) form
        # of the SAME MAC. They must normalize equal.
        _seed_scan_loop(
            interface, identity,
            incoming_address=DEV_MAC,             # bare form
            existing_address=DEV_FORM,            # dev: form, same MAC
            existing_alive=False,
        )

        selected = interface._select_peers_to_connect()
        selected_addrs = {p.address for p in selected}
        assert DEV_MAC not in selected_addrs, (
            "A stored dev:-prefixed address and an incoming bare form of the "
            "same MAC normalize equal; an interface exists, so the scan loop "
            "must skip it (not treat it as a rotation)."
        )


class TestNormalizeAddressFalsyInput:
    """The falsy-input branch of _normalize_address (empty / None -> '')."""

    def test_empty_string_returns_empty(self):
        interface = _make_interface("11:22:33:44:55:66")
        assert interface._normalize_address("") == ""

    def test_none_returns_empty(self):
        interface = _make_interface("11:22:33:44:55:66")
        assert interface._normalize_address(None) == ""
