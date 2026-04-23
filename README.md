# hass-snopud-energy

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A Home Assistant custom integration for [Snohomish County PUD](https://www.snopud.com/) (SnoPUD) energy usage data.

This integration scrapes your usage data from the [MySnoPUD](https://my.snopud.com) customer portal and creates sensor entities for use with Home Assistant's Energy dashboard.

## Features

- Automatic login and session management with the MySnoPUD portal
- Daily energy usage (kWh) and cost ($) sensors
- Current month aggregated energy and cost
- Compatible with the Home Assistant Energy dashboard
- UI-based configuration (no YAML editing required)
- Re-authentication flow if credentials change

## Sensors

| Sensor | Description | Unit |
|--------|-------------|------|
| Latest Daily Energy | kWh from the most recent daily reading | kWh |
| Latest Daily Cost | Cost from the most recent daily reading | USD |
| Current Month Energy | Aggregated kWh for the current calendar month | kWh |
| Current Month Cost | Aggregated cost for the current calendar month | USD |
| Last Read Date | Date of the most recent meter reading | — |

## Installation

### HACS (Recommended)

1. Open HACS in your Home Assistant instance
2. Click the three dots in the top right corner and select **Custom repositories**
3. Add `https://github.com/frozenfoxx/hass-snopud-energy` with category **Integration**
4. Click **Install**
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/snopud_energy` directory to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings** > **Devices & Services** > **Add Integration**
2. Search for **Snohomish County PUD**
3. Enter your MySnoPUD email and password
4. The integration will validate your credentials and create the sensor entities

## Usage with the Energy Dashboard

After setup, go to **Settings** > **Dashboards** > **Energy** and add the "Current Month Energy" sensor as a grid consumption source.

## How It Works

The integration logs into the MySnoPUD portal using your credentials, then downloads your usage data as a CSV file via the portal's built-in download feature. Data is refreshed every 12 hours by default. No API key is required — the integration uses the same endpoints as the MySnoPUD web interface.

## Requirements

- A Snohomish County PUD account with an active MySnoPUD profile at [my.snopud.com](https://my.snopud.com)
- An AMI (smart) meter installed at your property

## Limitations

- Data availability depends on SnoPUD's meter reading schedule (typically 1-2 day delay)
- Two-factor authentication and secret questions are not yet supported
- Water meter data is not yet supported (electric only)
- The integration relies on web scraping; changes to the MySnoPUD portal may require updates

## Contributing

Contributions are welcome! Please open an issue or pull request on GitHub.

## License

Apache License — see [LICENSE](LICENSE) for details.

## Disclaimer

This integration is not affiliated with, endorsed by, or sponsored by Snohomish County PUD (SnoPUD). Use at your own risk.

## Trademark Notice

"Snohomish County PUD," "SnoPUD," and any associated names and logos are trademarks or registered trademarks of the Snohomish County Public Utility District. This project uses these names solely for identification and compatibility purposes and does not claim any ownership or affiliation.