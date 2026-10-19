# FoxESS Cloud for Home Assistant

This fork exposes supported FoxESS Cloud readings without presenting the entire cloud variable catalogue as installed hardware.
It requires **Home Assistant Core 2026.7 or newer**.

## Installation

In HACS, add [`teh-hippo/foxess-ha`](https://github.com/teh-hippo/foxess-ha) as a custom repository with the **Integration** category,
download FoxESS and restart Home Assistant.  The default HACS catalogue entry installs upstream, not this fork.

For manual installation, copy `custom_components/foxess` into your Home Assistant `custom_components` directory and restart.

Open **Settings > Devices & services > Add integration > FoxESS Cloud**.  Enter the OpenAPI key from your
[FoxESS Cloud profile](https://www.foxesscloud.com), the inverter serial number and an optional name.  Select Evo if applicable.
Configure each inverter separately.  A replacement key updates the existing entry through reauthentication.

## Upgrading to 3.0

This is a breaking entity-selection update.  Back up Home Assistant and review dashboards, automations and Energy configuration
that reference FoxESS entities before upgrading.

Existing config entries retain their `deviceID`, retained sensor unique IDs, entity IDs, customisations and recorder statistics.
Successful detection removes integration-owned unsupported or superseded entity-registry entries, including old battery orphans.
It does not delete recorder statistics, user-created Integral helpers or entities belonging to another integration or inverter.
References to removed entities need manual review.

The integration adds a FoxESS device with the reported model, serial and firmware.  Missing hardware versions are omitted.
The old Restrict, Use_V1_Api, xtZone and manual extended-PV switches no longer control polling or entity selection.
Evo and daylight settings remain available.

### Legacy YAML migration

YAML no longer creates operational sensors.  On the first restart, leave the existing FoxESS sensor platform block intact:
it is imported into a config entry using its **original `deviceID`**.  Renamed and disabled entities are adopted without changing
their identities.  If the cloud is unavailable, normal config-entry setup retry or reauthentication applies.

Confirm the imported entry works in **Settings > Devices & services**, then remove only the old FoxESS YAML sensor block and restart.
The migration Repairs notice clears after YAML is removed.  Do not change `deviceID` or create a second entry.
If legacy entities remain after YAML was removed too early, restore the original block and restart to complete the import.

## Supported entities

Initial setup and upgrade examine static metadata plus the preceding **48 hours** of cloud history.
Normal polling does not change the entity set.  To check changed wiring or hardware, open **Configure** and select
**Re-detect supported entities**.

| Group | Evidence used |
|---|---|
| PV inputs 1-18 | An input voltage reaching 80 V selects its voltage, current and power together |
| AC phases | Observed voltage selects the associated phase readings |
| Grid exchange | Import or export activity selects both power directions, including genuine zero readings |
| Additional meter | Informative corresponding power |
| Battery | Static battery metadata; conflicting evidence does not authorise night offload |
| Temperatures | Informative readings assessed individually |
| Power quality | Informative reactive-power or Power Factor readings, not zero-padding or a binary 0/1 profile |
| Energy counters | Non-zero counter evidence, checked against activity in the corresponding power flow |
| Diagnostics | Inverter metadata, Status, Running State, response time and real generation totals |

Detection is a short-window heuristic, not proof of physical hardware.  A quiet or failed window retains unresolved capabilities
rather than asserting that hardware is absent.  New entries without useful history retain a compatible selection and show a Repairs
notice.  Retry detection after the inverter has reported while operating.

Only aligned, informative readings with the same units and meaning can be deduplicated, such as confirmed single-phase generation
power or duplicate inverter temperatures.  DC PV power is not AC generation, and unrelated PV inputs are never merged.
The old calculated Solar and Solar Power conservation formulae are retired, not assumed equivalent to generation on every inverter.
Their identities remain unknown during inconclusive detection and are removed after successful detection.

The Inverter entity exposes the last accepted cloud sample, last successful request and detection outcome.
Detailed capability reasons and the detection timestamp are persisted in the config entry.

## Missing data, sleep and faults

Missing live measurements are **unknown**, not zero.  A reported zero remains zero, and a genuinely reported running state 164 remains
off-grid.  Neither is invented to represent sleep.

Cloud samples expire after 361 seconds from their own timestamp.  Cached coordinator refreshes do not refresh that deadline or count
as new samples.  Raw polling is scheduled before expiry with allowance for request and dispatch latency; slower reports have separate
deadlines.

An isolated failed request is retried on the next refresh opportunity, about one minute after completion.  The 75-second request timeout
can extend the wall-clock wait.  Last readings remain numeric only within their original freshness allowance, then become unknown.
Continuous cloud failures become **unavailable** after 15 minutes from the first failed request.  Recovery polling continues.

At least three consecutive API-key rejections spanning 15 minutes start one native reauthentication flow.  Recovery attempts continue
with a 15-minute backoff, and a successful authenticated request clears obsolete reauthentication.  Rate limiting honours Retry-After,
or uses a 15-minute backoff if the server provides no usable delay.

Status remains **online / asleep / offline**.  Cloud or key errors are not evidence that the inverter itself is offline; Status is unknown
when it cannot be assessed and unavailable during sustained integration failure.

### Solar-only night behaviour

Polling can be suspended only after a successful offline/stale-device observation, confirmed absence of a battery, and the sun remaining
below the configured wake elevation for the offload grace window.  Darkness or a failed HTTP request alone is not enough.

Defaults are **5 degrees** wake elevation and **60 minutes** grace.  The configurable bounds are -6 to 15 degrees and 0 to 180 minutes.
At sunrise, Status remains asleep during the allowance for fresh data, rather than briefly flickering offline.
Overnight time does not accrue towards a daylight outage; unfinished timers reset at night.
Existing Repairs warnings remain until fresh readings return.  Confirmed sustained device outages still surface as offline.

Battery installations are monitored throughout the night, including genuine battery-device outages.
New runtime battery evidence prevents unsafe solar-only offload without silently changing the selected entities.

Actual cumulative totals can remain cached during sleep.  Yesterday's daily value, or last month's monthly value, becomes unknown at the
period boundary until current-period data is established.  Midnight zeroes and period resets are not synthesised.

## Energy and estimates

**Energy Generated**, **Energy Generated Month** and **Energy Generated Cumulative** use actual FoxESS generation counters.
For a solar-only inverter, use the cumulative counter for solar in the
[Home Assistant Energy dashboard](https://www.home-assistant.io/docs/energy/solar-panels/).
Battery owners should confirm their device's reported generation semantics before assigning it to an Energy flow.
Working cloud consumption/import/export counters are preferred where supported.

Where a working counter is absent but reported power is supported, the integration provides:

- **Estimated Load Energy**
- **Estimated Grid Import Energy**
- **Estimated Grid Export Energy**

These are FoxESS-owned entities with new identities, not replacements that silently reuse a cloud counter's history.
They use Home Assistant's native [Integral implementation](https://www.home-assistant.io/integrations/integration/), trapezoidal integration
and kWh units.  There is no timer-based extrapolation.  Source renames reload the subscription; disabling or removing a source stops its estimate.
Restarts restore accumulated energy, including a genuine zero, but never restore stale power.

**Estimates cover observed valid intervals only.**  Unknown/unavailable intervals and invalid negative consumption readings are skipped,
including the transitions into and out of those intervals.  Raw negative readings are not clamped or changed.
No overnight household use, missing export or unobserved daytime power is reconstructed.
On an inverter-only installation that stops reporting overnight, household and import estimates are not full-day whole-house totals.
Export estimates also miss unobserved daytime intervals.  Battery installations can include genuine observed overnight telemetry.

Estimates use Home Assistant event times, not a historical reconstruction of exact cloud timestamps.  They are not billing-grade and have
no accuracy guarantee.  Actual cumulative solar can catch up after a gap; it cannot recover missing household/import/export information.
Use native statistics or [Utility Meter](https://www.home-assistant.io/integrations/utility_meter/) for reporting periods.

Energy dashboard references are **not migrated automatically**.  Do not double-count a real counter and an estimate of the same flow, or
substitute incomplete household estimates without accepting their coverage limits.

## API usage and troubleshooting

Requests share a one-second pacing gate.  Raw polling targets a three-minute interval, shortened when needed to protect freshness.
Device details and supported energy reports refresh every 15 minutes; generation and battery settings refresh hourly.
Confirmed solar-only sleep reduces usage.  Retries and initial/re-detection history calls add requests.

Accounts may have limited quotas.  A 24-hour simulated battery installation with three-minute samples, publication delay and request latency
stays below a 1,440-call daily allocation for one inverter.  This is not a quota guarantee: other clients, multiple inverters, slow responses
and recovery attempts also consume calls.  Check your FoxESS account's actual allowance.

For persistent failures, check the integration's setup error, reauthentication or Repairs notice before changing entities.
For debug logging:

```yaml
logger:
  default: warning
  logs:
    custom_components.foxess: debug
```

Do not publish OpenAPI keys or unredacted config-entry files.

## Development

Run `scripts/check.sh` with the project's locked `uv` environment.  It runs Ruff and the regression suite.
CI additionally runs Hassfest and HACS validation.  Version fields are managed by semantic-release.
