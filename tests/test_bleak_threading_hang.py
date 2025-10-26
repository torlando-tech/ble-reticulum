#!/usr/bin/env python3
"""
Minimal reproducer for BleakScanner hanging in custom asyncio event loop thread.

This test demonstrates the issue where BleakScanner.discover() works fine in
the main thread but hangs indefinitely when called from a custom event loop
running in a separate thread.

Run this test with a timeout to see the hang:
    timeout 30 python test_bleak_threading_hang.py

Expected behavior: Both tests should complete successfully
Actual behavior: test_scan_from_thread_loop hangs indefinitely
"""

import asyncio
import threading
import time
from bleak import BleakScanner


def test_scan_from_main_thread():
    """Test 1: BleakScanner in main thread - WORKS"""
    print("\n[TEST 1] Running BleakScanner.discover() from main thread...")
    start = time.time()

    async def scan():
        devices = await BleakScanner.discover(timeout=1.0)
        return devices

    devices = asyncio.run(scan())
    elapsed = time.time() - start

    print(f"[TEST 1] ✓ SUCCESS: Found {len(devices)} devices in {elapsed:.2f}s")
    return True


def test_scan_from_thread_loop():
    """Test 2: BleakScanner from custom event loop in thread - HANGS"""
    print("\n[TEST 2] Running BleakScanner.discover() from custom thread loop...")
    print("[TEST 2] (This mimics BLEInterface's architecture)")

    result_holder = {"devices": None, "error": None, "completed": False}
    loop_holder = {"loop": None}

    def run_loop():
        """Background thread running custom event loop (like BLEInterface)"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop_holder["loop"] = loop
        loop.run_forever()

    async def scan():
        """Async scan function scheduled in custom loop"""
        print("[TEST 2]   Calling BleakScanner.discover()...")
        try:
            devices = await BleakScanner.discover(timeout=1.0)
            result_holder["devices"] = devices
            result_holder["completed"] = True
            print(f"[TEST 2]   Scan completed, found {len(devices)} devices")
        except Exception as e:
            result_holder["error"] = e
            result_holder["completed"] = True
            print(f"[TEST 2]   Scan failed: {e}")

    # Start background loop
    thread = threading.Thread(target=run_loop, daemon=True)
    thread.start()

    # Wait for loop to initialize
    time.sleep(0.5)

    if loop_holder["loop"] is None:
        print("[TEST 2] ✗ FAILED: Loop didn't start")
        return False

    # Schedule scan in custom loop
    start = time.time()
    future = asyncio.run_coroutine_threadsafe(scan(), loop_holder["loop"])

    # Wait with timeout
    timeout = 10.0
    print(f"[TEST 2]   Waiting up to {timeout}s for scan to complete...")

    while not result_holder["completed"] and (time.time() - start) < timeout:
        time.sleep(0.1)

    elapsed = time.time() - start

    if result_holder["completed"]:
        if result_holder["error"]:
            print(f"[TEST 2] ✗ FAILED: Scan errored: {result_holder['error']}")
            return False
        else:
            print(f"[TEST 2] ✓ SUCCESS: Found {len(result_holder['devices'])} devices in {elapsed:.2f}s")
            return True
    else:
        print(f"[TEST 2] ✗ FAILED: Scan HUNG after {elapsed:.2f}s timeout")
        print("[TEST 2]   This is the bug! BleakScanner.discover() hangs in custom thread loop")
        return False


def test_scan_from_thread_loop_subprocess():
    """Test 3: BleakScanner via subprocess from custom thread loop"""
    print("\n[TEST 3] Running BleakScanner via subprocess from custom thread loop...")

    result_holder = {"devices": None, "error": None, "completed": False}
    loop_holder = {"loop": None}

    def run_loop():
        """Background thread running custom event loop"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop_holder["loop"] = loop
        loop.run_forever()

    async def scan_via_subprocess():
        """Try scanning via subprocess"""
        import sys
        import json

        print("[TEST 3]   Calling BleakScanner via subprocess...")

        scan_script = '''
import asyncio
import json
from bleak import BleakScanner

async def scan():
    devices = await BleakScanner.discover(timeout=1.0)
    return [{"address": d.address, "name": d.name} for d in devices]

print(json.dumps(asyncio.run(scan())))
'''

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, '-c', scan_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=5.0
            )

            if proc.returncode == 0:
                device_data = json.loads(stdout.decode())
                result_holder["devices"] = device_data
                result_holder["completed"] = True
                print(f"[TEST 3]   Subprocess scan completed, found {len(device_data)} devices")
            else:
                result_holder["error"] = f"Subprocess failed: {stderr.decode()}"
                result_holder["completed"] = True
        except asyncio.TimeoutError:
            result_holder["error"] = "Subprocess timed out"
            result_holder["completed"] = True
        except Exception as e:
            result_holder["error"] = str(e)
            result_holder["completed"] = True

    # Start background loop
    thread = threading.Thread(target=run_loop, daemon=True)
    thread.start()

    # Wait for loop to initialize
    time.sleep(0.5)

    if loop_holder["loop"] is None:
        print("[TEST 3] ✗ FAILED: Loop didn't start")
        return False

    # Schedule scan in custom loop
    start = time.time()
    future = asyncio.run_coroutine_threadsafe(scan_via_subprocess(), loop_holder["loop"])

    # Wait with timeout
    timeout = 10.0
    print(f"[TEST 3]   Waiting up to {timeout}s for subprocess scan to complete...")

    while not result_holder["completed"] and (time.time() - start) < timeout:
        time.sleep(0.1)

    elapsed = time.time() - start

    if result_holder["completed"]:
        if result_holder["error"]:
            print(f"[TEST 3] ✗ FAILED: {result_holder['error']}")
            return False
        else:
            print(f"[TEST 3] ✓ SUCCESS: Found {len(result_holder['devices'])} devices in {elapsed:.2f}s")
            return True
    else:
        print(f"[TEST 3] ✗ FAILED: Subprocess scan HUNG after {elapsed:.2f}s timeout")
        return False


if __name__ == "__main__":
    print("=" * 70)
    print("BleakScanner Threading Hang Reproducer")
    print("=" * 70)
    print("\nThis test reproduces the issue where BleakScanner.discover() hangs")
    print("when called from a custom asyncio event loop in a separate thread.")
    print("\nEnvironment:")
    print(f"  - Python: {asyncio.sys.version}")

    try:
        import bleak
        print(f"  - Bleak: {bleak.__version__}")
    except:
        print("  - Bleak: unknown version")

    results = {}

    # Run tests
    results["test1"] = test_scan_from_main_thread()
    results["test2"] = test_scan_from_thread_loop()
    results["test3"] = test_scan_from_thread_loop_subprocess()

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY:")
    print("=" * 70)
    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {test_name}: {status}")

    print("\n" + "=" * 70)
    if all(results.values()):
        print("All tests passed!")
        exit(0)
    else:
        print("Some tests failed. See output above for details.")
        exit(1)
