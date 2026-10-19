# FoxESS Cloud

Requires **Home Assistant Core 2026.7 or newer**.  Install this fork by adding
[`teh-hippo/foxess-ha`](https://github.com/teh-hippo/foxess-ha) as a HACS custom repository of type **Integration**.
The default catalogue entry installs upstream, not this fork.

## 3.0 migration

Entity selection now uses static metadata and the preceding 48 hours of operating history.
Unsupported and superseded FoxESS entities are removed only after successful detection.  Retained identities, customisations and recorder
statistics remain unchanged.  Review references in dashboards, automations and Energy configuration before upgrading.

Legacy YAML is imported into a config entry with the original `deviceID`.  Leave its sensor block intact for the first restart, confirm the
imported entry works, then remove that block and restart again.  YAML no longer creates sensors.  Do not add a duplicate UI entry.

Configure keeps Evo and daylight settings, and adds **Re-detect supported entities** for changed hardware.
Restrict, Use_V1_Api, xtZone and manual extended-PV settings no longer control operation.

## Honest readings and estimates

Missing live readings become **unknown**, never invented zeroes.  Solar-only sleep is not a cloud failure.
Single request failures are retried; sustained cloud trouble becomes unavailable after 15 minutes, without stopping recovery polling.
Battery installations retain night monitoring.

Real FoxESS generation totals remain available.  When supported load/import/export power has no working cloud energy counter,
clearly named **Estimated** energy entities use Home Assistant's native Integral machinery.
They accumulate only observed valid intervals, skip gaps and invalid negative consumption, and restore totals without restoring stale power.

Inverter-only household/import estimates exclude unobserved overnight usage.  Export estimates also miss unobserved daytime intervals.
These are not complete household totals or billing-grade measurements.  Energy dashboard references are not changed automatically.

See the [migration, detection and energy guidance](https://github.com/teh-hippo/foxess-ha#upgrading-to-30) before updating.
