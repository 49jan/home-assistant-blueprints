# Apartment Climate Control

Home Assistant automation for controlling one apartment climate entity from indoor temperature, humidity, window, radiator-heating, and outdoor-temperature sensors.

## Files

- [`smart-apartment-climate-control.yaml`](smart-apartment-climate-control.yaml) is an importable Home Assistant automation blueprint.
- [`ecodan-radiatorji-template.yaml`](ecodan-radiatorji-template.yaml) is an optional Home Assistant template configuration example. It is not a blueprint.

## Smart Apartment Climate Control

The blueprint applies these priorities, from highest to lowest:

1. Turn the climate entity off while a window is open.
2. Prevent climate heating or cooling while the radiators are heating; dehumidification remains available.
3. Dehumidify above the configured humidity threshold.
4. Heat or cool around the configured targets and tolerances.
5. Limit target temperatures using the outdoor temperature.

It also supports delayed window handling, optional manual-control hold time, minimum compressor run and idle times, per-mode fan settings, and an optional notification action when operation is blocked.

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
- Indoor temperature and humidity sensors. Multiple selected sensors are averaged.
- Window sensors with configurable open and close delays.
- Radiator-heating binary sensors.
- Outdoor temperature sensor and heating/cooling limits.
- Heating, cooling, and dehumidification targets.
- Minimum compressor run and idle times.
- Optional fan modes and blocked-operation notification action.

The input names and detailed descriptions in the blueprint are in Slovenian.

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

## Troubleshooting

- If the automation does not run, verify that the climate entity is available and that an optional master switch is on.
- If a requested mode is skipped, check the climate entity's `hvac_modes` and `fan_modes` attributes in **Developer tools > States**.
- If averaging does not work, confirm that selected sensors have numeric states and the correct temperature or humidity device class.
- If radiator blocking does not work, verify the Ecodan entity ID and the generated binary sensor state.

## License

This project is licensed under the GNU General Public License v3.0. See the repository's [`LICENSE`](../LICENSE) file.
