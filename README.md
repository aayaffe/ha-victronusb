# ha-victronusb

Home Assistant custom integration for Victron devices that publish VE.Direct
TEXT data through a USB serial adapter, including the BMV-712 Smart.

## Configuration

Install the `custom_components/victronusb` directory through HACS or copy it
into the matching directory in your Home Assistant configuration, then add
**Victron USB Integration** from the integrations UI.

VE.Direct normally uses 19200 baud. On Linux/Home Assistant OS, select the
persistent adapter path under:

```text
/dev/serial/by-id/...
```

Use the full path for the adapter shown on the host. Do not configure only the
directory name. A persistent by-ID path is preferred to `/dev/ttyUSB0` because
the latter can change after a reboot or USB reconnect.

Existing entries using `/dev/ttyUSB0` or settings stored by version `0.1.0`
remain supported. The options dialog stores new serial settings as entry
options and reloads the integration once.

## Connection behavior

Version `0.2.0` reads VE.Direct as bytes and validates each complete TEXT frame
before updating sensors. The checksum value remains a raw byte, including when
it is whitespace, a control byte, or not valid UTF-8.

The integration automatically closes and reopens the serial transport after:

- EOF or USB disconnect;
- serial/operating-system transport errors;
- 30 seconds without a valid VE.Direct frame.

Entities become unavailable while valid frames are absent and become available
again when validated data resumes. Reload and unload cancel and await both
background tasks and close the transport before setup can start another reader.

## Tests

The focused test suite uses Python's standard library and has no additional
test dependencies:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q custom_components tests
```

The tests cover binary checksum values, malformed-frame recovery, EOF and read
exceptions followed by reconnection, valid-frame silence, unload cancellation
and transport closure, and reload sequencing with one active reader.

## Hardware limitations

Software reconnection cannot repair a physically failing USB cable, inadequate
USB power, a defective VE.Direct-to-USB adapter, kernel/host USB lockups, or a
device that stops transmitting until it is power-cycled. Check Home Assistant
host logs and test another cable/port if reconnect attempts continue without
valid frames. USB passthrough in a VM or container must also keep the persistent
device available to Home Assistant.

