# Victron USB Serial Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Victron VE.Direct USB reader recover automatically and unload cleanly while preserving existing sensors.

**Architecture:** Parse complete VE.Direct TEXT frames as bytes in a pure parser, then feed validated records through a connection manager that exclusively owns serial tasks and transport lifecycle. `SerialSensor` adapts manager callbacks to Home Assistant entities, while config-entry setup/unload uses awaited current platform APIs.

**Tech Stack:** Python 3.12, asyncio, pyserial-asyncio, Home Assistant config entries/entities, unittest

## Global Constraints

- Preserve existing entity IDs, configuration, and five-second sensor update throttling.
- Do not hardcode a particular `/dev/serial/by-id/` adapter identifier.
- A malformed frame must not terminate the serial reader.
- Cancellation must not wait for reconnect backoff or watchdog intervals.
- No production dependency is added for the test suite.

---

### Task 1: Incremental VE.Direct Parser

**Files:**
- Create: `custom_components/victronusb/vedirect.py`
- Create: `tests/test_vedirect.py`

**Interfaces:**
- Produces: `VEDirectFrame(records: tuple[tuple[str, str], ...])`
- Produces: `VEDirectParser.feed(data: bytes) -> list[VEDirectFrame]`
- Produces: parser counters `checksum_failures` and `malformed_frames`

- [ ] **Step 1: Write failing binary-checksum and recovery tests**

Create hand-derived frames whose checksum byte covers space, tab, CR, LF,
`0x01`, and `0xB7`. Corrupt one checksum and place a valid frame after it.
Feed malformed records before a valid frame and assert the valid frame is
still emitted.

- [ ] **Step 2: Run parser tests and confirm import/API failures**

Run:
`py -3.12 -m unittest tests.test_vedirect -v`

Expected: failure because `custom_components.victronusb.vedirect` does not
exist.

- [ ] **Step 3: Implement the byte state machine**

Implement sync, label, value, checksum, and HEX-ignore states. Preserve the
checksum byte without decoding, validate the complete byte sum, decode only
ordinary labels/values, enforce bounded record sizes, and resynchronize after
malformed input.

- [ ] **Step 4: Run parser tests**

Run:
`py -3.12 -m unittest tests.test_vedirect -v`

Expected: all parser tests pass.

### Task 2: Serial Connection Lifecycle

**Files:**
- Create: `custom_components/victronusb/serial_connection.py`
- Create: `tests/test_serial_connection.py`

**Interfaces:**
- Consumes: `VEDirectParser`
- Produces: `SerialConnectionManager.start() -> None`
- Produces: `SerialConnectionManager.stop() -> None`
- Exposes read-only `reader_task`, `availability_task`, and `writer`
- Calls async `on_frame(records)` and sync `on_availability(available)`

- [ ] **Step 1: Write failing reconnect tests**

Use deterministic fake readers/writers/connectors. Cover EOF followed by a
valid connection, an arbitrary serial exception followed by reconnect, and a
valid frame restoring availability.

- [ ] **Step 2: Write failing shutdown/reload tests**

Assert `stop()` cancels and awaits both tasks, closes the writer, and leaves no
manager task active. Stop manager A before starting manager B and assert the
connector observes at most one active serial reader.

- [ ] **Step 3: Run lifecycle tests and confirm import/API failures**

Run:
`py -3.12 -m unittest tests.test_serial_connection -v`

Expected: failure because `serial_connection.py` does not exist.

- [ ] **Step 4: Implement the manager**

Add idempotent start/stop, retained task/writer references, `finally` transport
closure, EOF/error/silence reconnect, bounded backoff, valid-frame watchdog,
availability transitions, and duplicate-log suppression.

- [ ] **Step 5: Run lifecycle tests**

Run:
`py -3.12 -m unittest tests.test_serial_connection -v`

Expected: all lifecycle tests pass.

### Task 3: Home Assistant Integration Lifecycle

**Files:**
- Modify: `custom_components/victronusb/sensor.py`
- Modify: `custom_components/victronusb/__init__.py`
- Modify: `custom_components/victronusb/config_flow.py`
- Create: `tests/test_config_entry_lifecycle.py`

**Interfaces:**
- Consumes: `SerialConnectionManager`
- Produces: `SerialSensor.async_will_remove_from_hass()`
- Stores: `hass.data[DOMAIN][entry_id]["sensor"]`

- [ ] **Step 1: Write failing config-entry lifecycle tests**

Stub the narrow Home Assistant interfaces and assert setup uses
`async_forward_entry_setups`, unload uses `async_unload_platforms`, successful
unload removes runtime data, and failed unload retains it.

- [ ] **Step 2: Run lifecycle test and confirm deprecated behavior fails**

Run:
`py -3.12 -m unittest tests.test_config_entry_lifecycle -v`

Expected: assertions fail against singular platform forwarding and unconditional
runtime removal.

- [ ] **Step 3: Integrate the manager with entities**

Load metadata from the integration directory, construct the manager in
`SerialSensor`, retain dynamic sensors on the hub entity, process validated
records with the existing throttle, propagate availability, and await manager
shutdown from `async_will_remove_from_hass()`.

- [ ] **Step 4: Correct entry and options lifecycle**

Use plural platform setup/unload, entry-ID-scoped runtime state, options-over-
data lookup, and a single listener-driven reload.

- [ ] **Step 5: Run all focused tests**

Run:
`py -3.12 -m unittest discover -s tests -v`

Expected: all tests pass.

### Task 4: Version and Documentation

**Files:**
- Modify: `custom_components/victronusb/manifest.json`
- Modify: `README.md`
- Create: `CHANGELOG.md`

**Interfaces:**
- No code interfaces.

- [ ] **Step 1: Bump version and document operation**

Set version `0.2.0`. Document 19200 baud, persistent `/dev/serial/by-id/...`
selection, reconnect/watchdog behavior, unload cleanup, test command, and
hardware limitations.

- [ ] **Step 2: Run syntax, tests, and repository checks**

Run:

`py -3.12 -m compileall -q custom_components tests`

`py -3.12 -m unittest discover -s tests -v`

`git diff --check`

Expected: all commands exit 0.

- [ ] **Step 3: Review the complete diff against requirements**

Confirm every requested failure mode has a test or explicit limitation, entity
identifiers are unchanged, `log.txt` is untouched, and no task/transport can
survive successful unload.
