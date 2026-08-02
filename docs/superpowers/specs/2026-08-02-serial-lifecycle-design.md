# Victron USB Serial Lifecycle Design

## Objective

Keep the Victron BMV-712 integration updating indefinitely, recover from serial
disconnects without restarting Home Assistant, and make config-entry reloads
release the USB port before starting a replacement reader.

## Confirmed Failure Paths

- `StreamReader.readline()` returns `b""` at EOF. The current loop treats it as
  a line and spins forever instead of reconnecting.
- Only `SerialException` is handled. EOF, `OSError`, read silence, and other
  transport failures either strand or terminate the reader.
- The writer returned by `open_serial_connection()` is discarded, so unload
  cannot close the serial transport.
- The reader task is cancelled but never awaited. The availability task is not
  retained or cancelled at all.
- The options flow updates the config entry, which invokes its reload listener,
  and then explicitly reloads the entry a second time.
- The parser decodes and strips every complete line. VE.Direct's checksum is a
  raw byte, so non-UTF-8, tab, CR, LF, space, and other control values are
  corrupted or rejected.
- Checksum records are sent to the ordinary field parser and produce the
  repeated `Malformed line: Checksum` log.
- Availability is checked only every five minutes and does not influence the
  connection lifecycle.

## Architecture

### Byte-oriented frame parser

`custom_components/victronusb/vedirect.py` will implement a small incremental
TEXT-protocol parser. It consumes arbitrary byte chunks, treats the checksum
value as exactly one raw byte, validates that the modulo-256 sum of the complete
frame is zero, and returns decoded label/value records only for valid frames.
Malformed input increments a diagnostic counter and resynchronizes without
raising into the serial reader.

The parser will ignore interleaved VE.Direct HEX lines while it searches for
the next TEXT frame. Record and frame size limits prevent unbounded buffering.

### Connection manager

`custom_components/victronusb/serial_connection.py` will own:

- one serial reader/reconnect task;
- one availability watchdog task;
- the current writer/transport;
- parser state and valid-frame timestamp.

`start()` is idempotent. `stop()` cancels and awaits both tasks and closes the
writer. The reader reconnects after EOF, exceptions, checksum-only traffic, or
silence, using bounded exponential backoff. Cancellation is always re-raised
immediately. Repeated equivalent failures are logged once at warning level and
then at debug level until a valid frame resets suppression.

### Home Assistant entity adapter

`SerialSensor` remains the hub entity and preserves the existing configured
name. It starts the manager in `async_added_to_hass()` and awaits manager
shutdown in `async_will_remove_from_hass()`. Dynamic `SmartSensor` entities keep
their existing unique IDs and explicit entity IDs. Valid records continue to
use the existing five-second per-label update throttle.

Connection availability is applied to the hub and all dynamic sensors.
Validated data makes entities available again. Disconnect or watchdog expiry
makes them unavailable and triggers reconnection.

### Config-entry lifecycle

Entry state is scoped under `hass.data[DOMAIN][entry_id]`. Setup uses the
awaited plural platform-forwarding API. Unload first asks Home Assistant to
unload the sensor platform; only a successful unload removes entry runtime
state. Entity removal performs task cancellation and port closure.

The options flow stores serial settings in entry options and relies on the
single registered update listener for reload. Existing entries that store the
same settings in entry data remain compatible.

## Logging

- First connection, disconnect, retry, reconnection, checksum failure, silence,
  and reader termination are explicit.
- Repeated connection and checksum failures are demoted to debug.
- A valid frame after recovery resets error suppression.
- Malformed input never logs once per incoming record.

## Testing

The repository will use Python's built-in `unittest`, including
`IsolatedAsyncioTestCase`, so focused tests require no full Home Assistant
installation.

Tests cover:

- checksum bytes including space, tab, CR, LF, `0x01`, and a non-UTF-8 byte;
- corrupt and malformed frames followed by a valid frame;
- EOF and serial exceptions followed by reconnection;
- cancellation awaiting both tasks and closing the current writer;
- stop/start reload sequencing with exactly one active reader;
- plural config-entry setup/unload and runtime cleanup ordering.

## Compatibility and Documentation

- Existing entity IDs, entry data, options, and field throttling remain valid.
- New configuration defaults to 19200 baud and suggests
  `/dev/serial/by-id/`, without embedding a particular adapter identifier.
- The integration version will be bumped from `0.1.0` to `0.2.0`.
- The README will document persistent serial paths, reconnect behavior, and
  test commands. A changelog will record the lifecycle fix.
