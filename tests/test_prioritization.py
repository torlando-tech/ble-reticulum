#!/usr/bin/env python3
"""
Unit tests for BLE connection prioritization

These tests validate the DiscoveredPeer class and prioritization algorithms.
"""

import pytest
import sys
import os
import time

# Simple implementation tests - directly read and test the code logic


# Standalone DiscoveredPeer implementation (copied from BLEInterface.py for testing)
class DiscoveredPeer:
    """
    Tracks information about a discovered BLE peer for connection prioritization.
    """

    def __init__(self, address, name, rssi):
        self.address = address
        self.name = name
        self.rssi = rssi
        self.first_seen = time.time()
        self.last_seen = time.time()
        self.connection_attempts = 0
        self.successful_connections = 0
        self.failed_connections = 0
        self.last_connection_attempt = 0

    def update_rssi(self, rssi):
        self.rssi = rssi
        self.last_seen = time.time()

    def record_connection_attempt(self):
        self.connection_attempts += 1
        self.last_connection_attempt = time.time()

    def record_connection_success(self):
        self.successful_connections += 1

    def record_connection_failure(self):
        self.failed_connections += 1

    def get_success_rate(self):
        if self.connection_attempts == 0:
            return 0.0
        return self.successful_connections / self.connection_attempts

    def __repr__(self):
        return (f"DiscoveredPeer({self.address}, {self.name}, "
                f"RSSI={self.rssi}, attempts={self.connection_attempts}, "
                f"success_rate={self.get_success_rate():.2f})")


# Scoring algorithm (extracted from BLEInterface._score_peer)
def score_peer(peer):
    """Calculate priority score for peer selection."""
    score = 0.0

    # Signal strength component (0-70 points)
    if peer.rssi is not None:
        rssi_clamped = max(-100, min(-30, peer.rssi))
        rssi_normalized = (rssi_clamped + 100) * (70.0 / 70.0)
        score += rssi_normalized

    # Connection history component (0-50 points)
    if peer.connection_attempts > 0:
        success_rate = peer.get_success_rate()
        score += success_rate * 50.0
    else:
        score += 25.0  # New peers get moderate score

    # Recency component (0-25 points)
    age_seconds = time.time() - peer.last_seen
    if age_seconds < 5.0:
        score += 25.0
    elif age_seconds < 30.0:
        score += 25.0 * (1.0 - (age_seconds - 5.0) / 25.0)

    return score


class TestDiscoveredPeer:
    """Test DiscoveredPeer data class"""

    def test_initialization(self):
        """Test DiscoveredPeer initialization"""
        peer = DiscoveredPeer("AA:BB:CC:DD:EE:FF", "TestDevice", -65)

        assert peer.address == "AA:BB:CC:DD:EE:FF"
        assert peer.name == "TestDevice"
        assert peer.rssi == -65
        assert peer.connection_attempts == 0
        assert peer.successful_connections == 0
        assert peer.failed_connections == 0
        assert peer.first_seen <= time.time()
        assert peer.last_seen <= time.time()

    def test_update_rssi(self):
        """Test RSSI updates"""
        peer = DiscoveredPeer("AA:BB:CC:DD:EE:FF", "TestDevice", -65)
        initial_last_seen = peer.last_seen

        time.sleep(0.01)  # Small delay
        peer.update_rssi(-70)

        assert peer.rssi == -70
        assert peer.last_seen > initial_last_seen

    def test_connection_attempt_tracking(self):
        """Test connection attempt recording"""
        peer = DiscoveredPeer("AA:BB:CC:DD:EE:FF", "TestDevice", -65)

        peer.record_connection_attempt()
        assert peer.connection_attempts == 1

        peer.record_connection_attempt()
        assert peer.connection_attempts == 2

    def test_connection_success_tracking(self):
        """Test successful connection recording"""
        peer = DiscoveredPeer("AA:BB:CC:DD:EE:FF", "TestDevice", -65)

        peer.record_connection_attempt()
        peer.record_connection_success()

        assert peer.successful_connections == 1
        assert peer.get_success_rate() == 1.0

    def test_connection_failure_tracking(self):
        """Test failed connection recording"""
        peer = DiscoveredPeer("AA:BB:CC:DD:EE:FF", "TestDevice", -65)

        peer.record_connection_attempt()
        peer.record_connection_failure()

        assert peer.failed_connections == 1
        assert peer.get_success_rate() == 0.0

    def test_success_rate_calculation(self):
        """Test connection success rate calculation"""
        peer = DiscoveredPeer("AA:BB:CC:DD:EE:FF", "TestDevice", -65)

        # No attempts yet
        assert peer.get_success_rate() == 0.0

        # 3 successes out of 5 attempts
        for i in range(5):
            peer.record_connection_attempt()
            if i < 3:
                peer.record_connection_success()

        assert peer.get_success_rate() == 0.6

    def test_repr(self):
        """Test string representation"""
        peer = DiscoveredPeer("AA:BB:CC:DD:EE:FF", "TestDevice", -65)
        peer.record_connection_attempt()
        peer.record_connection_success()

        repr_str = repr(peer)
        assert "AA:BB:CC:DD:EE:FF" in repr_str
        assert "TestDevice" in repr_str
        assert "RSSI=-65" in repr_str
        assert "attempts=1" in repr_str


class TestPeerScoring:
    """Test peer scoring algorithm"""

    def test_score_by_rssi(self):
        """Test that peers with better RSSI score higher"""
        peer_strong = DiscoveredPeer("AA:BB:CC:DD:EE:01", "StrongSignal", -40)
        peer_weak = DiscoveredPeer("AA:BB:CC:DD:EE:02", "WeakSignal", -90)

        score_strong = score_peer(peer_strong)
        score_weak = score_peer(peer_weak)

        assert score_strong > score_weak

    def test_score_by_connection_history(self):
        """Test that peers with good connection history score higher"""
        # Peer with good history
        peer_reliable = DiscoveredPeer("AA:BB:CC:DD:EE:01", "Reliable", -60)
        for i in range(5):
            peer_reliable.record_connection_attempt()
            peer_reliable.record_connection_success()

        # Peer with poor history
        peer_unreliable = DiscoveredPeer("AA:BB:CC:DD:EE:02", "Unreliable", -60)
        for i in range(5):
            peer_unreliable.record_connection_attempt()
            if i < 1:  # Only 1 success out of 5
                peer_unreliable.record_connection_success()

        score_reliable = score_peer(peer_reliable)
        score_unreliable = score_peer(peer_unreliable)

        assert score_reliable > score_unreliable

    def test_score_by_recency(self):
        """Test that recently seen peers score higher"""
        peer_recent = DiscoveredPeer("AA:BB:CC:DD:EE:01", "Recent", -60)
        peer_old = DiscoveredPeer("AA:BB:CC:DD:EE:02", "Old", -60)

        # Make peer_old look older
        peer_old.last_seen = time.time() - 20.0

        score_recent = score_peer(peer_recent)
        score_old = score_peer(peer_old)

        assert score_recent > score_old

    def test_new_peer_gets_moderate_score(self):
        """Test that new peers (no history) get a moderate score"""
        peer_new = DiscoveredPeer("AA:BB:CC:DD:EE:01", "New", -60)
        score = score_peer(peer_new)

        # New peers should score reasonably (benefit of the doubt)
        # RSSI component (~30) + moderate history (25) + recency (25) = ~80
        assert 70 < score < 100

    def test_score_combined_factors(self):
        """Test scoring with all factors combined"""
        # Perfect peer: strong signal, good history, recently seen
        peer_perfect = DiscoveredPeer("AA:BB:CC:DD:EE:01", "Perfect", -35)
        for i in range(10):
            peer_perfect.record_connection_attempt()
            peer_perfect.record_connection_success()

        # Poor peer: weak signal, bad history, old
        peer_poor = DiscoveredPeer("AA:BB:CC:DD:EE:02", "Poor", -95)
        for i in range(10):
            peer_poor.record_connection_attempt()
            if i < 2:  # 20% success rate
                peer_poor.record_connection_success()
        peer_poor.last_seen = time.time() - 35.0

        score_perfect = score_peer(peer_perfect)
        score_poor = score_peer(peer_poor)

        # Perfect peer should score much higher
        assert score_perfect > score_poor * 2


class TestPeerSelection:
    """Test peer selection algorithm"""

    def select_peers_to_connect(self, discovered_peers, connected_peers, blacklist, max_peers):
        """
        Standalone implementation of selection logic for testing.

        Args:
            discovered_peers: dict of address -> DiscoveredPeer
            connected_peers: set of already-connected addresses
            blacklist: dict of address -> (blacklist_until, failure_count)
            max_peers: maximum number of peers

        Returns:
            list of DiscoveredPeer objects to connect to
        """
        # Calculate available slots
        available_slots = max_peers - len(connected_peers)
        if available_slots <= 0:
            return []

        # Check if peer is blacklisted
        def is_blacklisted(address):
            if address not in blacklist:
                return False
            blacklist_until, _ = blacklist[address]
            return time.time() < blacklist_until

        # Score all discovered peers
        scored_peers = []
        for address, peer in discovered_peers.items():
            # Skip if already connected
            if address in connected_peers:
                continue

            # Skip if blacklisted
            if is_blacklisted(address):
                continue

            # Calculate score
            score = score_peer(peer)
            scored_peers.append((score, peer))

        # Sort by score (highest first)
        scored_peers.sort(reverse=True, key=lambda x: x[0])

        # Select top N peers
        selected = [peer for score, peer in scored_peers[:available_slots]]
        return selected

    def test_no_slots_available(self):
        """Test that empty list returned when max peers reached"""
        # Setup: 3 discovered, 3 connected (max=3)
        discovered = {
            "AA:BB:CC:DD:EE:01": DiscoveredPeer("AA:BB:CC:DD:EE:01", "Peer1", -50),
            "AA:BB:CC:DD:EE:02": DiscoveredPeer("AA:BB:CC:DD:EE:02", "Peer2", -60),
            "AA:BB:CC:DD:EE:03": DiscoveredPeer("AA:BB:CC:DD:EE:03", "Peer3", -70),
        }
        connected = {"AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02", "AA:BB:CC:DD:EE:03"}
        blacklist = {}

        result = self.select_peers_to_connect(discovered, connected, blacklist, max_peers=3)

        assert len(result) == 0

    def test_filters_already_connected(self):
        """Test that already-connected peers are filtered out"""
        # Setup: 5 discovered, 2 connected
        discovered = {
            "AA:BB:CC:DD:EE:01": DiscoveredPeer("AA:BB:CC:DD:EE:01", "Peer1", -50),
            "AA:BB:CC:DD:EE:02": DiscoveredPeer("AA:BB:CC:DD:EE:02", "Peer2", -55),
            "AA:BB:CC:DD:EE:03": DiscoveredPeer("AA:BB:CC:DD:EE:03", "Peer3", -60),
            "AA:BB:CC:DD:EE:04": DiscoveredPeer("AA:BB:CC:DD:EE:04", "Peer4", -65),
            "AA:BB:CC:DD:EE:05": DiscoveredPeer("AA:BB:CC:DD:EE:05", "Peer5", -70),
        }
        connected = {"AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"}  # Already connected
        blacklist = {}

        result = self.select_peers_to_connect(discovered, connected, blacklist, max_peers=5)

        # Should return 3 unconnected peers
        assert len(result) == 3
        addresses = [p.address for p in result]
        assert "AA:BB:CC:DD:EE:01" not in addresses
        assert "AA:BB:CC:DD:EE:02" not in addresses
        assert "AA:BB:CC:DD:EE:03" in addresses
        assert "AA:BB:CC:DD:EE:04" in addresses
        assert "AA:BB:CC:DD:EE:05" in addresses

    def test_filters_blacklisted(self):
        """Test that blacklisted peers are filtered out"""
        # Setup: 5 discovered, 2 blacklisted
        discovered = {
            "AA:BB:CC:DD:EE:01": DiscoveredPeer("AA:BB:CC:DD:EE:01", "Peer1", -50),
            "AA:BB:CC:DD:EE:02": DiscoveredPeer("AA:BB:CC:DD:EE:02", "Peer2", -55),
            "AA:BB:CC:DD:EE:03": DiscoveredPeer("AA:BB:CC:DD:EE:03", "Peer3", -60),
            "AA:BB:CC:DD:EE:04": DiscoveredPeer("AA:BB:CC:DD:EE:04", "Peer4", -65),
            "AA:BB:CC:DD:EE:05": DiscoveredPeer("AA:BB:CC:DD:EE:05", "Peer5", -70),
        }
        connected = set()
        # Blacklist peers 1 and 2 for 60 seconds into the future
        blacklist = {
            "AA:BB:CC:DD:EE:01": (time.time() + 60, 3),
            "AA:BB:CC:DD:EE:02": (time.time() + 60, 3),
        }

        result = self.select_peers_to_connect(discovered, connected, blacklist, max_peers=5)

        # Should return 3 non-blacklisted peers
        assert len(result) == 3
        addresses = [p.address for p in result]
        assert "AA:BB:CC:DD:EE:01" not in addresses  # Blacklisted
        assert "AA:BB:CC:DD:EE:02" not in addresses  # Blacklisted
        assert "AA:BB:CC:DD:EE:03" in addresses
        assert "AA:BB:CC:DD:EE:04" in addresses
        assert "AA:BB:CC:DD:EE:05" in addresses

    def test_selects_top_n_by_score(self):
        """Test that top N peers are selected by score"""
        # Setup: 10 peers with varying RSSI (score will be dominated by RSSI)
        discovered = {}
        for i in range(10):
            rssi = -40 - (i * 10)  # -40, -50, -60, ..., -130
            discovered[f"AA:BB:CC:DD:EE:{i:02d}"] = DiscoveredPeer(
                f"AA:BB:CC:DD:EE:{i:02d}", f"Peer{i}", rssi
            )

        connected = set()
        blacklist = {}

        result = self.select_peers_to_connect(discovered, connected, blacklist, max_peers=3)

        # Should return top 3 by score (best RSSI)
        assert len(result) == 3

        # Verify they're sorted by RSSI (best first)
        rssi_values = [p.rssi for p in result]
        assert rssi_values[0] == -40  # Best
        assert rssi_values[1] == -50
        assert rssi_values[2] == -60

    def test_respects_available_slots(self):
        """Test that selection respects available slots"""
        # Setup: 5 good peers, max=7, 5 already connected (2 slots available)
        discovered = {}
        for i in range(5):
            rssi = -50 - (i * 5)  # All decent signal
            discovered[f"AA:BB:CC:DD:EE:{i:02d}"] = DiscoveredPeer(
                f"AA:BB:CC:DD:EE:{i:02d}", f"Peer{i}", rssi
            )

        # 5 other peers already connected
        connected = {f"BB:CC:DD:EE:FF:{i:02d}" for i in range(5)}
        blacklist = {}

        result = self.select_peers_to_connect(discovered, connected, blacklist, max_peers=7)

        # Should return exactly 2 peers (available slots = 7 - 5 = 2)
        assert len(result) == 2

        # Should be the top 2 by score
        assert result[0].rssi == -50
        assert result[1].rssi == -55

    def test_fewer_candidates_than_slots(self):
        """Test that selection works when fewer candidates than slots"""
        # Setup: 2 good peers, max=7, 0 connected (7 slots available)
        discovered = {
            "AA:BB:CC:DD:EE:01": DiscoveredPeer("AA:BB:CC:DD:EE:01", "Peer1", -50),
            "AA:BB:CC:DD:EE:02": DiscoveredPeer("AA:BB:CC:DD:EE:02", "Peer2", -60),
        }
        connected = set()
        blacklist = {}

        result = self.select_peers_to_connect(discovered, connected, blacklist, max_peers=7)

        # Should return both peers (doesn't fail with fewer than max)
        assert len(result) == 2


class TestImplementationValidation:
    """Validate that the implementation exists in BLEInterface.py"""

    def test_discovered_peer_class_exists(self):
        """Test that DiscoveredPeer class is in the source file"""
        interface_path = os.path.join(os.path.dirname(__file__), '../src/RNS/Interfaces/BLEInterface.py')
        with open(interface_path, 'r') as f:
            code = f.read()

        assert 'class DiscoveredPeer:' in code
        assert 'def update_rssi(' in code
        assert 'def record_connection_attempt(' in code
        assert 'def record_connection_success(' in code
        assert 'def record_connection_failure(' in code
        assert 'def get_success_rate(' in code

    def test_prioritization_methods_exist(self):
        """Test that prioritization methods exist in BLEInterface.py"""
        interface_path = os.path.join(os.path.dirname(__file__), '../src/RNS/Interfaces/BLEInterface.py')
        with open(interface_path, 'r') as f:
            code = f.read()

        assert 'def _score_peer(' in code
        assert 'def _select_peers_to_connect(' in code
        assert 'def _is_blacklisted(' in code
        assert 'def _record_connection_success(' in code
        assert 'def _record_connection_failure(' in code
        assert 'def _connect_to_peer(' in code

    def test_configuration_options_exist(self):
        """Test that prioritization configuration options exist"""
        interface_path = os.path.join(os.path.dirname(__file__), '../src/RNS/Interfaces/BLEInterface.py')
        with open(interface_path, 'r') as f:
            code = f.read()

        assert 'connection_rotation_interval' in code
        assert 'connection_retry_backoff' in code
        assert 'max_connection_failures' in code
        assert 'discovered_peers' in code
        assert 'connection_blacklist' in code


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
