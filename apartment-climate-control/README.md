# Apartment Climate Control

Home Assistant automation for controlling one apartment climate entity from indoor temperature, humidity, presence, schedule, window, radiator-heating, and outdoor-temperature inputs.

## Files

- [`smart-apartment-climate-control.yaml`](smart-apartment-climate-control.yaml) is an importable Home Assistant automation blueprint.
- [`ecodan-radiatorji-template.yaml`](ecodan-radiatorji-template.yaml) is an optional Home Assistant template configuration example. It is not a blueprint.
## Smart Apartment Climate Control

The blueprint applies these priorities, from highest to lowest:

1. Turn the climate entity off while a window is open.
2. While the schedule is active, prevent climate heating or cooling while the radiators are heating; dehumidification remains available. During a schedule pause, radiator heating forces the climate entity off in every mode.
3. During the active schedule, absence of all selected people prevents heating/cooling; dehumidification remains available.
4. Dehumidify above the configured humidity threshold.
5. Heat or cool around the configured targets and tolerances.
6. Optionally shift targets by tariff: lower the heating target in the most expensive time block, raise it while preheating, lower it further (without starting new heating) when total power in block 1 exceeds a limit, and lower the cooling target while precooling.
7. Limit target temperatures using the outdoor temperature.

It also supports delayed window handling, optional presence checks, minimum stable sensor time, cooling start persistence, humidity aggregation selection, optional manual-control hold time, minimum compressor run and idle times, per-mode fan settings, and an optional notification action when operation is blocked.

The optional preventive FAN cycle is separate from minimum compressor run/idle protection. During prolonged automatic cooling, it briefly switches to fan-only to help reduce the risk of indoor-unit icing in extreme conditions. This is a preventive measure, not guaranteed freeze protection; follow the equipment manufacturer's guidance. The cycle is disabled by default and is skipped if the climate entity does not support `fan_only`.

An optional Home Assistant Schedule helper can define when the automation controls the climate entity. When the schedule becomes inactive, the blueprint turns the climate entity off. During the pause, manual climate settings are left alone only when manual control is enabled; otherwise, a manual turn-on is switched off again. The window-open and radiator-heating safeguards remain active during this pause and force the climate entity off in every mode. An additional option can allow humidity-based dehumidification during the pause; it is disabled by default and is still blocked while radiators are heating. When the schedule becomes active again, normal automation resumes.

### Requirements

- Home Assistant 2024.10.0 or newer.
- One `climate` entity.
- The climate entity must expose the HVAC modes used by the automation. Dehumidification requires the `dry` mode.
- All other entities are optional. When no external temperature or humidity sensors are selected, the blueprint uses values reported by the climate entity when available.

### Import

[![Open your Home Assistant instance and import this blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2F49jan%2Fhome-assistant-blueprints%2Fmain%2Fapartment-climate-control%2Fsmart-apartment-climate-control.yaml)

Raw blueprint URL:

```text
https://raw.githubusercontent.com/49jan/home-assistant-blueprints/main/apartment-climate-control/smart-apartment-climate-control.yaml
```

To import it manually:

1. Open **Settings > Automations & scenes > Blueprints** in Home Assistant.
2. Select **Import blueprint**.
3. Paste the raw blueprint URL shown above and complete the import.
4. Create an automation from **Smart Apartment Climate Control** and select the apartment entities.

### Main Inputs

- Climate entity and optional master switch.
- Indoor temperature sensors are averaged. Humidity sensors use the selected aggregation method (average by default, or maximum).
- Optional person entities and distance radius for gating heating/cooling. Dehumidification can remain active when nobody is present.
- Sensor stability delay (0-300 seconds) and optional sustained-temperature delay before cooling starts.
- Window sensors with configurable open and close delays.
- Radiator-heating binary sensors.
- Outdoor temperature sensor and heating/cooling limits.
- Heating, cooling, and dehumidification targets.
- Minimum compressor run and idle times.
- Optional preventive FAN cycle with configurable cooling interval and fan-only duration.
- Optional fan modes and blocked-operation notification action.
- Optional Schedule helper, an option to allow dehumidification while its schedule is inactive, and an optional minimum temperature kept during the pause.
- Optional tariff inputs: time-block sensor, preheat and precool binary sensors, total power sensor, and their target offsets.

The input names and detailed descriptions in the blueprint are in Slovenian. By default, no people are selected, sensor stabilization is 30 seconds, cooling persistence is disabled, and the preventive FAN cycle is disabled. If sensor entities are selected but their readings have not yet remained stable long enough, those readings are temporarily ignored; the climate entity's reported value is only used when no corresponding external sensors are selected.

When person entities are selected, at least one person must be home or within the configured radius for automatic heating or cooling. If everyone is away, heating/cooling is turned off even when manual control is enabled; dehumidification remains allowed. An empty person list disables presence gating. Presence does not override manual settings during a schedule pause.

Humidity sensors are averaged by default; select **Najvišja vrednost** to start dehumidification based on the most humid selected room. Temperature sensors remain averaged. Selected temperature and humidity readings must remain unchanged for the configured stability duration before they affect decisions. Cooling persistence requires the cooling-start threshold to remain exceeded continuously; setting it to `0` keeps immediate starts.

The preventive FAN cycle is configured under **Preventivni FAN cikel**. It is disabled by default; if enabled, the default is a FAN pause after 120 minutes of uninterrupted automatic cooling, lasting 10 minutes. The interval can be set from 30 to 360 minutes and the pause from 2 to 30 minutes. It only applies to automatic cooling, and all schedule, presence, window, radiator, and temperature conditions are checked again before cooling resumes.

An optional minimum temperature can be kept during a schedule pause. Enable **Vzdržuj minimalno temperaturo med premorom urnika** and set **Minimalna temperatura med premorom** (default 16 °C, 5-24 °C). While the schedule is inactive and the room is below the minimum, the blueprint switches the climate entity to `heat` with the minimum as target and turns it off again at the minimum plus the heating tolerance. It is disabled by default, so existing behavior is unchanged. An open window, active radiator heating, the master switch and the minimum compressor run and idle times still take precedence; presence does not block it, so an empty apartment is still protected. It is a protective floor, not a comfort setting: tariff offsets (block 1, preheat, peak-power limit) are not applied, so the minimum is neither lowered nor raised by the tariff. With manual control allowed, a climate entity that was started manually is left alone; only a heating run that holds the minimum setpoint set by the automation is managed and turned off. It takes precedence over dehumidification during the pause while the room is below the minimum.

When using a Schedule helper, select it in **Urnik delovanja**. The helper's active periods are the periods when the automation manages the climate. Outside those periods the climate is turned off at the schedule transition. Later manual changes are left untouched only if **Dovoli ročno upravljanje z daljincem** is enabled; otherwise, a manual turn-on is switched off again, except when the separate dehumidification option calls for `dry`. An open window or active radiator-heating sensor always forces the climate off during the pause. Normal automation resumes immediately when the schedule becomes active. If **Dovoli razvlaževanje med premorom urnika** is enabled, the blueprint may use `dry` while humidity is above the configured threshold, but not while radiator heating is active; it turns `dry` off again at the target humidity.

The tariff inputs are under **Tarife in časovni bloki** and are all optional; without them the heating target is unchanged. The time-block sensor's state must be the current block number (1 = most expensive). In block 1 the heating target is shifted by **Zamik ciljne temperature gretja v bloku 1** (default −1 K), so heating starts later and stops earlier. While the preheat sensor is on outside block 1, the target is raised by the preheat offset (default +0.5 K). If a total power sensor (W or kW) is selected and its value stays above the limit for one minute during block 1, the target is lowered by an additional offset and no new heating is started; units already heating continue at the lower target. While the precool sensor is on, the cooling target is lowered by the precool offset (default −1 K), but never below the heating target and never below the outdoor-based cooling limit. With **Ne hladi, ko je zunaj hladneje kot znotraj** enabled, precooling only runs on warm nights, when ventilation cannot cool the apartment. Setpoints are rounded to the climate entity's `target_temp_step`. With manual control allowed, the blueprint updates the setpoint of a running heater only while it still holds one of the values the automation sets; a different value set with the remote is left alone.

### Updating

The blueprint contains a stable `source_url`. Use the blueprint menu in Home Assistant to check for an update, or import the same raw URL again after a newer version is published.

Existing automations keep their selected inputs. Review newly added inputs after an update.

## Optional Ecodan Radiator Template

`ecodan-radiatorji-template.yaml` creates a binary sensor that remains on for 20 minutes after Ecodan stops reporting radiator heating. This covers short interruptions such as domestic hot-water preparation.

Before using it:

1. Replace `climate.toplotna_crpalka_zone_1` with the actual Ecodan climate entity ID.
2. Make sure `configuration.yaml` includes the templates directory:
   `template: !include_dir_merge_list integrations/templates/`

3. Place `ecodan-radiatorji-template.yaml` in `/config/integrations/templates/binary_sensor/`. The file is a list fragment for the `template:` include and must not contain another top-level `template:` key.
4. Check the Home Assistant configuration and reload template entities or restart Home Assistant.
5. Select the resulting binary sensor, normally `binary_sensor.ecodan_gretje_radiatorjev`, under the blueprint's radiator-heating sensors.

The alternative pump and three-way-valve template in the file is only a fallback example and must be adapted and verified before use.

## Tariff Sensors

The blueprint does not contain any country-specific tariff schedule. Create the sensors with Home Assistant templates:

- **Time-block sensor:** a `sensor` whose state is the number of the current network-tariff block, where `1` is the most expensive block. Any other number means no block-1 offset.
- **Preheat sensor:** a `binary_sensor` that is on when the apartment should store heat before an expensive period, for example 1.5 hours before block 1 on working days.
- **Precool sensor:** a `binary_sensor` that is on during cheap, cool hours before a hot day, for example the last hours of the night tariff.

A minimal time-block sensor for a schedule with an expensive block on working days at 7–14 and 16–20 h:

```yaml
- sensor:
    - name: "Tariff block"
      state: >
        {% set h = now().hour %}
        {{ 1 if is_state('binary_sensor.workday_sensor', 'on')
              and (7 <= h < 14 or 16 <= h < 20) else 2 }}
```

## Troubleshooting

- If the automation does not run, verify that the climate entity is available and that an optional master switch is on.
- If a requested mode is skipped, check the climate entity's `hvac_modes` and `fan_modes` attributes in **Developer tools > States**.
- If averaging does not work, confirm that selected sensors have numeric states and the correct temperature or humidity device class.
- If radiator blocking does not work, verify the Ecodan entity ID and the generated binary sensor state.

## License

This project is licensed under the GNU General Public License v3.0. See the repository's [`LICENSE`](../LICENSE) file.
