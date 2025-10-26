# Testing Guide

This document describes how to test the Reticulum BLE Interface.

## Test Suite Overview

The test suite includes:

- **Unit tests**: Test individual components in isolation
- **Integration tests**: Test component interactions and simulated multi-device scenarios
- **Coverage**: 98+ tests covering core functionality

## Quick Start

```bash
# Create and activate virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # On Linux/macOS

# Install test dependencies
pip install -r requirements-dev.txt

# Run all tests
pytest

# Run with verbose output
pytest -v

# Run with coverage report
pytest --cov=src/RNS/Interfaces --cov-report=html
```

## Test Organization

### Test Files

- `conftest.py` - Pytest fixtures and shared test utilities
- `test_fragmentation.py` - Packet fragmentation and reassembly
- `test_gatt_server.py` - GATT server functionality
- `test_ble_peer_interface.py` - Per-peer connection management
- `test_error_recovery.py` - Error handling and recovery
- `test_prioritization.py` - Connection prioritization logic
- `test_multi_device_simulation.py` - Multi-node mesh simulation
- `test_integration.py` - Configuration and integration tests

### Running Specific Tests

```bash
# Run single test file
pytest tests/test_fragmentation.py

# Run single test function
pytest tests/test_fragmentation.py::test_fragment_single_packet

# Run tests matching pattern
pytest -k "fragment"

# Run with specific markers
pytest -m "not slow"
```

## Test Categories

### 1. Fragmentation Tests

Tests for packet fragmentation and reassembly:

```bash
pytest tests/test_fragmentation.py -v
```

Key tests:
- Single packet fragmentation
- Large packet handling (multiple fragments)
- Packet reassembly
- Fragment ordering
- Error cases (corrupted fragments, timeout)

### 2. GATT Server Tests

Tests for peripheral mode (GATT server):

```bash
pytest tests/test_gatt_server.py -v
```

Key tests:
- GATT server initialization
- Service registration
- Characteristic read/write
- Notification handling
- Multiple client connections

### 3. Connection Management Tests

Tests for peer discovery and connection:

```bash
pytest tests/test_ble_peer_interface.py -v
```

Key tests:
- Peer discovery
- Connection establishment
- Disconnection handling
- Connection state management
- Data transmission

### 4. Error Recovery Tests

Tests for error handling:

```bash
pytest tests/test_error_recovery.py -v
```

Key tests:
- Connection timeout handling
- Retry logic
- Exponential backoff
- Blacklist management
- Recovery from errors

### 5. Prioritization Tests

Tests for connection prioritization:

```bash
pytest tests/test_prioritization.py -v
```

Key tests:
- RSSI-based scoring
- Connection history tracking
- Peer selection algorithm
- Blacklist expiration

### 6. Multi-Device Simulation

Tests for multi-node mesh networking:

```bash
pytest tests/test_multi_device_simulation.py -v
```

Key tests:
- Multiple simultaneous connections
- Packet routing through mesh
- Network topology changes
- Connection rotation

## Coverage

### Generate Coverage Report

```bash
# HTML report (recommended)
pytest --cov=src/RNS/Interfaces --cov-report=html
# Open htmlcov/index.html in browser

# Terminal report
pytest --cov=src/RNS/Interfaces --cov-report=term-missing

# XML report (for CI)
pytest --cov=src/RNS/Interfaces --cov-report=xml
```

### Coverage Goals

- Overall coverage: >90%
- Core modules (BLEInterface, BLEFragmentation): >95%
- Error handling paths: >85%

## Integration Testing

### Prerequisites

For integration testing with real BLE hardware:

- 2+ BLE-enabled devices (e.g., Raspberry Pi Zero W)
- BlueZ 5.x installed
- Devices on same network (for coordination)

### Setup

1. Install on each device:
   ```bash
   pip install -r requirements.txt
   cp src/RNS/Interfaces/BLE*.py ~/.reticulum/interfaces/
   ```

2. Configure interface on each device (same `service_uuid`):
   ```toml
   [[BLE Interface]]
     type = BLEInterface
     enabled = yes
     device_name = Device-1  # Unique per device
     service_uuid = 00000001-5824-4f48-9e1a-3b3e8f0c1234
   ```

3. Start Reticulum on each device:
   ```bash
   rnsd --verbose
   ```

### Integration Test Scenarios

#### Test 1: Peer Discovery

**Objective**: Verify devices discover each other

1. Start `rnsd` on both devices
2. Monitor logs for discovery messages
3. Verify: Each device discovers the other within 10 seconds

Expected output:
```
[2025-10-26 10:00:15] [INFO] Discovered peer: Device-2 (RSSI: -65 dBm)
```

#### Test 2: Connection Establishment

**Objective**: Verify devices connect successfully

1. Wait for discovery
2. Monitor logs for connection
3. Check `rnstatus` for active connections

Expected output:
```
BLE Interface [Enabled]
  Peers: 1 connected, 0 discovered
  Active connections: Device-2 (RSSI: -65 dBm)
```

#### Test 3: Packet Exchange

**Objective**: Verify data transmission

1. Establish connection
2. Send announces from one device
3. Monitor reception on other device

```bash
# On Device 1
rnid -a

# On Device 2 - should receive announce
tail -f ~/.reticulum/logfile
```

#### Test 4: Multi-Hop Routing

**Objective**: Verify mesh routing (requires 3+ devices)

1. Place devices in line: A <-> B <-> C
2. Ensure A and C can only connect via B
3. Send packets from A to C
4. Verify routing through B

#### Test 5: Connection Recovery

**Objective**: Verify reconnection after disconnection

1. Establish connection
2. Move devices out of range or restart one device
3. Return to range
4. Verify: Automatic reconnection within 60 seconds

## Performance Testing

### Throughput Test

Measure packet transmission rate:

```python
# Run from examples/
python ble_minimal_test.py test
```

Expected results:
- BLE 4.2 (185 byte MTU): ~15-20 KB/s
- BLE 5.0 (512 byte MTU): ~30-40 KB/s

### Latency Test

Measure round-trip time:

1. Send echo request from Device A
2. Device B responds immediately
3. Measure time from send to receive

Expected latency:
- Local (same room): 50-200ms
- Medium range (10-15m): 100-500ms

### Connection Scaling

Test maximum connections:

1. Configure `max_connections = 7`
2. Connect 7 devices simultaneously
3. Verify all connections stable

Expected: All 7 connections maintained for >5 minutes

## Troubleshooting Tests

### Test Not Running

**Problem**: Pytest can't find tests

**Solution**:
```bash
# Ensure you're in project root
cd /path/to/ble-reticulum

# Run from root directory
pytest

# Or specify path explicitly
pytest tests/
```

### Import Errors

**Problem**: `ModuleNotFoundError: No module named 'RNS'`

**Solution**:
```bash
# Install in development mode
pip install -e .

# Or set PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"
pytest
```

### Async Warnings

**Problem**: Warnings about unclosed asyncio resources

**Solution**: These are usually harmless in tests, but can be suppressed:
```bash
pytest -W ignore::DeprecationWarning
```

### BLE Hardware Tests Skipped

**Problem**: Integration tests marked as skipped

**Reason**: Unit tests don't require real BLE hardware (they use mocks)

**Info**: This is expected behavior. Integration tests with real hardware should be run manually.

## Continuous Integration

### GitHub Actions

The repository includes CI configuration in `.github/workflows/test.yml`:

- Runs on: Python 3.8, 3.9, 3.10, 3.11
- Tests: All unit tests
- Coverage: Generates coverage report
- Linting: Code style checks (if configured)

### Running Locally

Simulate CI environment:

```bash
# Test on specific Python version
python3.9 -m pytest

# Test with clean environment
python -m venv test-env
source test-env/bin/activate
pip install -r requirements-dev.txt
pytest
deactivate
```

## Test Development

### Writing New Tests

1. Create test file in `tests/` directory
2. Import required fixtures from `conftest.py`
3. Write test functions (prefix with `test_`)
4. Use descriptive names and docstrings

Example:

```python
import pytest
from RNS.Interfaces.BLEFragmentation import BLEFragmenter

def test_fragmenter_handles_empty_packet():
    """Test that fragmenter raises error for empty packets"""
    fragmenter = BLEFragmenter(mtu=185)

    with pytest.raises(ValueError, match="empty"):
        fragmenter.fragment_packet(b"")
```

### Using Fixtures

Common fixtures available in `conftest.py`:

```python
def test_with_fragmenter(ble_fragmenter):
    """Use fragmenter fixture from conftest.py"""
    fragments = ble_fragmenter.fragment_packet(b"test data")
    assert len(fragments) >= 1
```

### Async Tests

For async code:

```python
import pytest

@pytest.mark.asyncio
async def test_async_operation():
    """Test asynchronous BLE operations"""
    result = await some_async_function()
    assert result is not None
```

## Best Practices

1. **Run tests before committing**
   ```bash
   pytest
   ```

2. **Check coverage for new code**
   ```bash
   pytest --cov=src/RNS/Interfaces --cov-report=term-missing
   ```

3. **Test both success and failure cases**
   - Happy path
   - Error conditions
   - Edge cases

4. **Use meaningful assertions**
   ```python
   # Good
   assert len(fragments) == 3, "Expected 3 fragments for 500-byte packet"

   # Less helpful
   assert len(fragments) == 3
   ```

5. **Keep tests independent**
   - Each test should work in isolation
   - Don't rely on test execution order
   - Clean up resources in teardown

## Additional Resources

- [pytest documentation](https://docs.pytest.org/)
- [pytest-asyncio documentation](https://pytest-asyncio.readthedocs.io/)
- [Coverage.py documentation](https://coverage.readthedocs.io/)

## Questions?

If you have questions about testing, please open an issue with the `testing` label.
