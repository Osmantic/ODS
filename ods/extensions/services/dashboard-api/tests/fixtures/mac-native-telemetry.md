Captured through read-only native commands on the qualification Mac Mini on
2026-09-24, before the telemetry fix. The fixture contains only allowlisted CPU,
memory, chip name, vm_stat counters, and AGX PerformanceStatistics. It contains
no machine identifiers, process lists, credentials, or session data.

The last top sample is the one-second CPU interval. RAM usage is active + wired
+ compressed physical pages. AGX "In use system memory" measures GPU allocations
within shared system memory; it must not be added to RAM usage. The fixture has
no thermal sensor, and the API must retain null rather than report zero Celsius.
