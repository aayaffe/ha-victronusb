# Changelog

## 0.2.1

- Do not declare textual VE.Direct fields, including `PID`, `Alarm`, and
  `Relay`, as numeric measurements. This prevents Home Assistant validation
  errors from terminating the serial frame callback and reconnect loop.

## 0.2.0

- Parse and validate complete VE.Direct TEXT frames as bytes, preserving the
  raw one-byte checksum.
- Recover automatically from EOF, serial errors, transport failures, and valid
  frame silence with bounded reconnect backoff.
- Mark entities unavailable while valid frames are absent and restore
  availability when data resumes.
- Retain, cancel, and await serial/availability tasks and close the serial
  writer during unload and reload.
- Use current awaited config-entry platform setup/unload APIs and remove the
  duplicate options-flow reload.
- Declare the serial runtime dependency in the integration manifest and remove
  the unrelated `serial` package from development requirements.
- Suppress repeated equivalent connection/checksum warnings until recovery.
- Add focused standard-library automated tests for parser and lifecycle
  behavior.
