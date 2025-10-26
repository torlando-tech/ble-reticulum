#!/usr/bin/env python3
"""
Test if BleakScanner hangs when the code is loaded via exec() like Reticulum does.

This mimics how Reticulum loads external interfaces.
"""

import asyncio
import threading
import time


def test_direct_vs_exec():
    """Compare direct import vs exec() loading"""

    print("\n" + "=" * 70)
    print("Testing BleakScanner with exec() loading (Reticulum-style)")
    print("=" * 70)

    # Test code that will be exec'd
    test_code = '''
import asyncio
import threading
import time
from bleak import BleakScanner

result_holder = {"completed": False, "devices": None}
loop_holder = {"loop": None}

def run_loop():
    """Background thread with custom event loop"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop_holder["loop"] = loop
    loop.run_forever()

async def scan():
    """Scan from custom loop"""
    print("  [exec] Calling BleakScanner.discover()...")
    devices = await BleakScanner.discover(timeout=1.0)
    result_holder["devices"] = devices
    result_holder["completed"] = True
    print(f"  [exec] Found {len(devices)} devices")

# Start loop thread
thread = threading.Thread(target=run_loop, daemon=True)
thread.start()
time.sleep(0.5)

# Schedule scan
future = asyncio.run_coroutine_threadsafe(scan(), loop_holder["loop"])
'''

    # Create namespace for exec
    namespace = {}

    print("\n[TEST] Executing code via exec() (like Reticulum loads interfaces)...")
    start = time.time()

    # Execute the code
    exec(test_code, namespace)

    # Wait for completion
    timeout = 10.0
    print(f"[TEST] Waiting up to {timeout}s for completion...")

    while not namespace["result_holder"]["completed"] and (time.time() - start) < timeout:
        time.sleep(0.1)

    elapsed = time.time() - start

    if namespace["result_holder"]["completed"]:
        devices = namespace["result_holder"]["devices"]
        print(f"\n[TEST] ✓ SUCCESS: Scan completed in {elapsed:.2f}s, found {len(devices)} devices")
        print("[TEST] exec() loading does NOT cause the hang!")
        return True
    else:
        print(f"\n[TEST] ✗ FAILED: Scan HUNG after {elapsed:.2f}s")
        print("[TEST] exec() loading DOES cause the hang!")
        return False


if __name__ == "__main__":
    success = test_direct_vs_exec()
    exit(0 if success else 1)
