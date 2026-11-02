# Heating Source Advisor

Home Assistant automation blueprint that recommends the seasonal switch between heating with the air-to-air climate units and heating the radiators with an air-to-water heat pump. It only sends notifications; it never switches anything itself. The switch is expected roughly twice a year, at the start and end of the cold period, and occasionally more often when the weather changes for a longer time.

## How It Works

Once a day at **Čas dnevnega preverjanja** (default 17:00), the blueprint reads the daily weather forecast and calculates the average daily temperature ((high + low) / 2) for the next **Število dni napovedi** days (default 5).

1. **Switch to the heat pump** when the current source is the climate units and the forecast average is at or below **Toplotna črpalka pod** (default −2 °C, nights around −6 to −8 °C). The forecast alone is enough, so the notification arrives before the cold period.
2. **Switch back to the climate units** when the current source is the heat pump and the forecast average is at or above **Klime nad** (default +4 °C). If a trend sensor is selected, the average of the last days must also be above this threshold, so a short thaw does not trigger the switch. If this threshold is not higher than the heat-pump threshold, the heat-pump threshold + 1 K is used.

No new recommendation is made for **Najmanj dni po preklopu** days (default 14) after the current source changes. While a recommendation applies and the source has not been switched, the notification is repeated once per day and replaces the previous one on the phone.

## Requirements

- Home Assistant 2024.10.0 or newer.
- An `input_boolean` helper for the current heating source, for example `input_boolean.ogrevanje_s_tc`: **on** = heat pump (radiators), **off** = climate units. Create it under **Settings > Devices & services > Helpers > Toggle** and set it to the source you use now.
- A weather entity that provides a daily forecast.
- A notify action, for example `notify.mobile_app_phone`.

### Optional Trend Sensor

Create a **Statistics** helper under **Settings > Devices & services > Helpers**:

- Source: the outdoor temperature sensor.
- Characteristic: mean (arithmetic mean).
- Max age: 3 days. Set the sampling size high enough to keep all readings from that period.

Select it as **Povprečje zunanje temperature zadnjih dni**.

### Notification Button

With **Gumb "Preklopljeno" v obvestilu** enabled (default), the notification contains a **Preklopljeno** button. Pressing it sets the helper to the recommended source, which also starts the waiting period. The button only works with the Home Assistant companion app (`notify.mobile_app_*`); disable the option for other notify services. Without the button, change the helper manually after switching.

## Import

[![Open your Home Assistant instance and import this blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2F49jan%2Fhome-assistant-blueprints%2Fmain%2Fheating-source-advisor%2Fheating-source-advisor.yaml)

```text
https://raw.githubusercontent.com/49jan/home-assistant-blueprints/main/heating-source-advisor/heating-source-advisor.yaml
```

## Notes

- Restarting Home Assistant may reset the helper's last-changed time. This only extends the waiting period.
- When using Smart Apartment Climate Control, consider setting its `heat_min_outdoor` near the night temperatures that correspond to **Toplotna črpalka pod**, so the climate units do not heat in deep cold.
- The input names and descriptions in the blueprint are in Slovenian.

## Tests

```text
python -m unittest discover -s heating-source-advisor/tests
```

## License

This project is licensed under the GNU General Public License v3.0. See the repository's [`LICENSE`](../LICENSE) file.
