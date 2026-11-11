# Everlights v3 Cloud for Home Assistant

Control EverLights v3 permanent lights from Home Assistant through the EverLights cloud.

Bridge firmware B5010 turned off the local API. Local requests now come back as HTTP 410, so integrations that talk to the bridge directly stopped working. This one signs in with your EverLights app account and reaches the bridge the same way the app does.

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=borski&repository=everlightsv3_cloud&category=integration)

## What you get

- One light per zone with on, off, colour, and brightness.
- Every sequence saved on your bridge as a light effect.
- Changes made in the EverLights app show up in Home Assistant within a minute.
- A calendar per bridge with the shows scheduled on it, one entry per night.
- Actions to save, change, and delete your own sequences, and to schedule and delete shows.
- Signal strength, signal to noise, last request, and last response sensors per zone.
- A cloud connection sensor, last check-in, and scheduled show count per bridge.
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

## Actions

Light actions. Target any Everlights light.

| Action | What it does |
|---|---|
| `everlightsv3_cloud.clear` | Drops what Home Assistant sent and hands the zone back to the bridge's schedule. With nothing scheduled, the lights go off. |
| `everlightsv3_cloud.save_sequence` | Saves colours and effects as a new sequence. With no colours given, it saves what the light is showing at full brightness. |
| `everlightsv3_cloud.update_sequence` | Changes the colours, effects, or name of one of your own sequences. |
| `everlightsv3_cloud.delete_sequence` | Deletes one of your own sequences. |

Calendar actions. Target the bridge's schedule calendar.

| Action | What it does |
|---|---|
| `everlightsv3_cloud.schedule_show` | Schedules a show for one or more nights in a row. |
| `everlightsv3_cloud.delete_show` | Deletes a show and all of its nights. |

Built-in sequences can't be changed or deleted. Only the ones under Personal in the app can.

A show lives on the bridge, so it runs even when Home Assistant is offline. While it runs, the bridge cycles through its sequences.

```yaml
action: everlightsv3_cloud.schedule_show
target:
  entity_id: calendar.everlights_bridge_schedule
data:
  name: Holiday lights
  sequences:
    Candy Cane: 30
    Warm White: 60
  start: "2026-12-01 17:00:00"
  end: "2026-12-01 23:00:00"
  nights: 31
  start_at_dusk: true
```

To drop a single night, delete that entry from the calendar. Deleting the last night removes the show.

### Turn off or clear

`light.turn_off` sends an empty pattern. The zone stays dark even if a show is scheduled, until the show's next change.

`clear` removes the override. The zone goes back to whatever the bridge has scheduled.

## Extra entities

These start disabled. Enable the ones you want.

- **Sync clock** button. Sets the bridge clock to Home Assistant's.
- **Restart** button. Restarts the bridge.
- **Clock offset** sensor. How far the bridge clock is from Home Assistant's, in seconds.
- **Reboot count** sensors for the bridge and each zone.
- **Voltage raw**, **Temperature raw**, and **Configured length** per zone. The bridge reports the first two as bare numbers with no documented unit, so they're shown as is.

## How it behaves

- It polls the bridge every 60 seconds. The schedule is read every 5 minutes.
- Every write is read back from the bridge before Home Assistant shows the result. If the bridge doesn't confirm, the action fails.
- A write is sent once. If it times out, the integration checks what the bridge did and never resends on its own.
- A zone that hasn't answered its bridge for 15 minutes shows as unavailable.
- If the cloud is down, entities go unavailable and recover on the next good poll.

## Limits

- It needs the internet and the EverLights cloud. There is no local fallback on B5010.
- Zones paired to your account but attached to someone else's bridge are skipped.
- It doesn't pair or unpair bridges and zones, rename them, or change strip length and colour order. Use the app for those.
- A show's sequences, times, and zones can't be edited in place. Delete it and schedule it again.
- If two saved sequences share a name, the effect list shows the name once.

### What has been tested on real hardware

Tested on bridge hardware BH300 with firmware B5010 and zone firmware N3063.

| Feature | Status |
|---|---|
| Reading zones, sequences, schedule, and clock | Tested |
| Solid colour, off, and clear | Tested |
| Saving and deleting a sequence | Tested |
| Effects and multi colour patterns | Same call as a solid colour. Less live testing |
| Updating a sequence | Not yet tested live |
| Scheduling, changing, and deleting shows | Not yet tested live |
| Sync clock and restart | Not yet tested live |

The untested ones follow the bridge's old local API, which the cloud tunnel mirrors for everything tested so far. All of them are covered by the test suite against a fake bridge. Please open an issue if one misbehaves.

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
