# Everlights v3 Cloud for Home Assistant

Control EverLights v3 permanent lights from Home Assistant through the EverLights cloud.

Bridge firmware B5010 turned off the local API. Local requests now come back as HTTP 410, so integrations that talk to the bridge directly stopped working. This one signs in with your EverLights app account and reaches the bridge the same way the app does.

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=borski&repository=everlightsv3_cloud&category=integration)

## What you get

- One light per zone with on, off, colour, and brightness.
- Every sequence saved on your bridge as a light effect.
- Changes made in the EverLights app show up in Home Assistant within a minute.
- Signal strength, signal to noise, last request, and last response sensors per zone.
- A reauth prompt when your password changes.
- Downloadable diagnostics with serials, addresses, and names redacted.

Brightness works by scaling the colours in the pattern, since the bridge has no brightness setting of its own.

## Install

1. Click the badge above, or in HACS open the menu, choose **Custom repositories**, and add `https://github.com/borski/everlightsv3_cloud` as an **Integration**.
2. Download **Everlights v3 Cloud**.
3. Restart Home Assistant.
4. Add the integration and sign in with your EverLights app email and password.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=everlightsv3_cloud)

Manual install: copy `custom_components/everlightsv3_cloud` into your `config/custom_components` folder and restart.

## Moving from the local integration

If you used [theOrakle/everlightsv3](https://github.com/theOrakle/everlightsv3), you can keep your entity IDs, history, and automations.

1. Download this integration in HACS.
2. In **Settings > Devices & services**, disable the old Everlights entry. Don't delete it.
3. Restart Home Assistant.
4. Add Everlights v3 Cloud.

On setup, lights and sensors from the old integration are moved to this one in place. This only happens while the old entry is disabled. If it's still running, you get new entities under new IDs and a warning in the log.

Once everything works, you can delete the old entry and remove the old integration.

## How it behaves

- It polls the bridge every 60 seconds.
- Every command is read back from the bridge before Home Assistant shows the new state. If the bridge doesn't confirm, the service call fails.
- A command is sent once. If it times out, the integration checks what the bridge is doing and never resends on its own.
- A zone that hasn't answered its bridge for 15 minutes shows as unavailable.
- If the cloud is down, entities go unavailable and recover on the next good poll.

## Limits

- It needs the internet and the EverLights cloud. There is no local fallback on B5010.
- Zones paired to your account but attached to someone else's bridge are skipped.
- It doesn't manage schedules, create or edit saved sequences, or change bridge and zone settings. Use the app for those, or Home Assistant automations for schedules.
- If two saved sequences share a name, the effect list shows the name once.
- Solid colours and off are tested against real hardware. Effects and multi colour patterns use the same call and are covered by the test suite, but have had less live testing. Please open an issue if one misbehaves.

Tested on bridge hardware BH300 with firmware B5010 and zone firmware N3063.

## Development

```
pip install -r requirements_test.txt
pytest tests -q
```

The fixtures in `tests/fixtures` are captures from a real bridge with serials, names, addresses, tokens, and dates replaced.

## Credits

Thanks to [theOrakle](https://github.com/theOrakle/everlightsv3) for the original local integration. This project keeps its entity ID format so the move is painless.

This project isn't affiliated with or endorsed by EverLights. It uses the same cloud service as the official app and could break if that service changes.

## License

MIT
