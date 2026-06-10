# BLE-Reticulum Protocol Specification v0.4.0

**Version**: 0.4.0
**Date**: June 2026
**Status**: Draft
**Backwards Compatible With**: v0.3.0, v2.2

## 1. Overview

This document specifies the v0.4.0 extension to the BLE-Reticulum protocol. This
version adds a **data-path liveness probe** to detect and recover from "connected
but data-dead" BLE links.

### 1.1 Problem Statement

A BLE connection can remain established at the link layer while application data
silently stops flowing:

- The Bluetooth **link layer keeps an idle connection alive** indefinitely (empty
  PDUs at each connection event). It only drops on supervision timeout — i.e. radio
  loss — not on application silence.
- Under RF degradation a link can pass small writes (such as the 1-byte keepalive
  used to defeat Android's app-inactivity timeout) while **larger data fragments
  fail**: keepalives succeed, real data does not.

In this state the link is genuinely "up", so every existing liveness mechanism
misses it:

- the reactive zombie check (`_last_real_data`) is only consulted when a *new*
  connection arrives — it is never swept;
- `_validate_spawned_interfaces` reconciles against the driver's connected-peer
  set, which still lists the peer;
- the keepalive-write-failure reaper never fires, because keepalive writes still
  succeed.

The peer therefore stays "connected" forever while no data flows, with no detection
and no recovery — a permanent deadlock. (Empirically reproduced between two
Linux/BlueZ nodes.)

### 1.2 Solution

v0.4.0 introduces an **active round-trip probe over the real data path**. Each node
periodically sends a small `PING` that the peer echoes as a `PONG`. Because the
probe traverses the same data path as real fragments, it fails exactly when real
data fails. A link that round-trips the probe is proven alive; a link that stops
round-tripping it while still connected at the link layer is data-dead and is torn
down so it re-establishes.

Crucially the probe **is** the keep-fresh traffic: a genuinely idle-but-healthy
link is kept alive by the probe's own round-trips, so idle links are never falsely
reaped.

## 2. Frame Format

v0.4.0 defines two new 2-byte control frames, sent on the same RX characteristic /
notification path as data fragments:

| Frame | Byte 0 (type) | Byte 1  | Meaning                                |
|-------|---------------|---------|----------------------------------------|
| PING  | `0x04`        | nonce   | Liveness request                       |
| PONG  | `0x05`        | nonce   | Liveness reply (echoes the PING nonce) |

The `nonce` is an opaque 1-byte value chosen by the sender; the responder copies it
verbatim into the PONG. It exists for future round-trip correlation and is not
currently interpreted.

These type bytes do not collide with the fragment header (`0x01`=START,
`0x02`=CONTINUE, `0x03`=END) or the 1-byte `0x00` keepalive.

## 3. Probe State Machine

State is tracked **per peer, keyed by stable identity** (not by BLE address, which
rotates).

### 3.1 Capability Negotiation

A peer is considered **probe-capable** once a PING or PONG has been received from
it. No handshake change is required — capability is inferred from observed probe
traffic. Peers that never emit probe frames (pre-v0.4.0) are never marked capable.

### 3.2 Liveness Tracking

Receiving any inbound traffic that proves the data path — a real data fragment, a
PING, or a PONG — updates the peer's `last_real_data` timestamp. The 1-byte
keepalive does **not**, by design: it proves only the link, not the data path.

### 3.3 Periodic Sweep

Every `data_path_probe_poll_interval`, for each established peer:

1. If the link has had no real data for longer than `data_path_probe_interval`,
   send a PING. A healthy peer echoes a PONG, refreshing `last_real_data`.
2. If the peer is probe-capable **and** `last_real_data` is older than
   `data_path_timeout`, the data path is dead: disconnect the peer
   (`driver.disconnect`) so the connection re-establishes and re-handshakes.

A non-probe-capable peer is never reaped by this mechanism; it falls through to the
existing reactive checks.

### 3.4 PING Handling

On receiving a PING, a node immediately replies with a PONG echoing the nonce, then
treats the inbound PING itself as proof of data-path liveness.

### 3.5 Asymmetric Failures

Because both peers probe independently, each detects the death of its own **inbound**
direction (it stops receiving the other's PINGs/PONGs). If only A→B fails, B sees no
inbound from A, declares the path dead, and reconnects — re-establishing both
directions. One side detecting is sufficient.

## 4. Configuration

| Key                              | Default | Meaning                                          |
|----------------------------------|---------|--------------------------------------------------|
| `data_path_probe_interval`       | 15 s    | PING a link that has had no real data this long  |
| `data_path_timeout`              | 45 s    | Reconnect a probe-capable peer silent this long  |
| `data_path_probe_poll_interval`  | 10 s    | How often the sweep runs                         |

The defaults give roughly three probe attempts before a reconnect and keep an idle
link refreshed well inside the timeout.

## 5. Backwards Compatibility

### 5.1 Compatibility Matrix

| Peers              | Behavior                                                                       |
|--------------------|--------------------------------------------------------------------------------|
| v0.4.0 ↔ v0.4.0    | Full probe + data-dead recovery, both directions.                              |
| v0.4.0 ↔ older     | v0.4.0 still PINGs; the 2-byte frame is shorter than the 5-byte fragment header, so the older peer's reassembler rejects it as "too short" and ignores it. The older peer never replies, never becomes probe-capable, and is never reaped by the probe — it retains pre-v0.4.0 behavior. |

The probe is therefore safe to deploy incrementally.

### 5.2 Address Normalization

On a dual-role (connection-collision) link a peer may deliver a frame under its
`dev:`-prefixed peripheral address while its identity was learned under the plain
MAC via the central-path handshake. Implementations **MUST** normalize (strip the
`dev:` prefix, and try both forms) when resolving a probe frame's identity, or the
frame will fail to attribute and capability will never be established.

## 6. GATT Service (Unchanged from v2.2)

The probe reuses the existing RX characteristic (central → peripheral write) and the
notification path (peripheral → central). No new characteristics are added.

## 7. Implementation Notes

### 7.1 Python (BlueZ/Bleak) — reference

`BLEInterface.py`: `_send_probe`, `_handle_probe_frame`, and `_run_data_path_probes`
on a `threading.Timer`. Probe frames are intercepted immediately after the keepalive
filter in both the central (`_handle_ble_data`) and peripheral receive paths, before
reassembly.

### 7.2 Android (Kotlin driver)

Android Columba bundles this `BLEInterface.py` via Chaquopy, so it inherits the probe
unchanged. The Kotlin driver must deliver 2-byte writes/notifications unfragmented
(it already does for the 1-byte keepalive).

### 7.3 swift (CoreBluetooth) — TODO

reticulum-swift's `BLEInterface` must mirror the probe, plus handle the
CoreBluetooth-specific case of a probe-driven disconnect of a **peripheral-role**
peer: CoreBluetooth cannot force-disconnect a subscribed central, so the app layer
must drop the central and let it reconnect.

## 8. Version History

| Version | Date     | Change                                                              |
|---------|----------|--------------------------------------------------------------------|
| v2.2    | Nov 2025 | Base protocol (MAC sorting, identity handshake, fragmentation, keepalive) |
| v0.3.0  | Dec 2025 | Capability advertisement (peripheral-only devices)                 |
| v0.4.0  | Jun 2026 | Data-path liveness probe (this document)                           |

## 9. References

- `BLE_PROTOCOL_v2.2.md` — base protocol
- `BLE_PROTOCOL_v0.3.0.md` — capability advertisement
- `docs/ble-architecture.md` — architecture explainer
- `CHANGELOG.md` — 0.3.0 release entry
