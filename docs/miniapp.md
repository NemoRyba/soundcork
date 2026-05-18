# SoundCork mobile app

The mobile app is the phone-friendly control surface at `/miniapp`. It is meant
for daily use after a SoundTouch device has already been switched to a SoundCork
server.

The original `/admin` UI is still the setup and repair surface. Use `/admin` for
initial speaker configuration and use `/miniapp` for living with the radio after
that.

## Goals

- Keep SoundTouch radios useful after the cloud service shutdown.
- Make the common radio workflows possible from a phone browser.
- Avoid requiring the old Bose app for presets, volume, source selection, or
  basic network maintenance.
- Keep risky device changes visible and reversible.

## Dashboard

The dashboard is designed for one-handed phone use. It contains movable panels,
so every user can put the controls they care about near the top.

After choosing an account, SoundCork selects the only radio automatically. If an
account has multiple radios, the miniapp shows a radio-only picker before the
dashboard so no control panels are shown until a specific device is selected.

Current dashboard panels include:

- Live status in the top bar, including play state, current station, and mute
  state.
- Live station and current track/program text in the top bar when the speaker
  reports it.
- Volume slider and mute, with separate polling so physical button changes can
  appear in the browser.
- Sources, including TuneIn, AUX, and Bluetooth when the speaker reports them.
- Presets, with play buttons and drag reorder.
- Sleep timer and simple alarm scheduler.
- Radio search for TuneIn-backed stations.
- Custom direct stream URL saving.
- Backup and restore for presets and panel layout.
- Radios, for selecting a known SoundTouch device.

## Presets

Preset handling is intentionally visual because the physical buttons on many
SoundTouch devices are too limited for editing.

You can:

- Play an existing preset by tapping it.
- Drag a preset between two preset slots to reorder it.
- Search radio stations inline after typing, with a scrollable result list.
- Drop a searched radio station directly onto a preset slot.
- Save a custom stream URL into a preset slot.
- Resync presets from the speaker when the server data and radio data might be
  out of step.

When preset reorder is finished, the app automatically posts the new order back
to SoundCork and then to the selected speaker. During the drag, the preset grid
does not live-swap tiles; it shows a source placeholder and a blue insertion bar
so the user can see exactly where the preset will land before release.

Radio search starts automatically after typing stops. The delay and preset
thumbnail size can be adjusted in the Presets panel settings.

## Radio Search

The current radio search uses the configured TuneIn-compatible search endpoint.
The default endpoint is stored in the app settings and can be changed if the
provider endpoint ever needs to move.

Search results are provider records, not raw MP3 stream URLs. SoundCork stores
the station identifier and metadata in the preset, and the speaker later asks the
SoundCork BMX/TuneIn emulation for playback information.

For stations that do not work through the provider search, use Custom Stream and
store a direct `http` or `https` audio stream URL.

## Settings

The Settings page contains the more permanent configuration:

- Account display name.
- Radio display name and IP address.
- Wi-Fi profile submission for reachable radios, plus a setup-mode helper for
  configuring Wi-Fi without the discontinued Bose app.
- Radio search endpoint.
- Top bar ordering for status, Home, Dashboard, Settings, and Exit controls.
- Usability settings, including separate live-indicator and volume polling
  intervals.
- Language selection for English and German.
- Movable settings panels.
- Panel settings opened from the panel handle, including editable labels,
  dragged preset tile opacity, panel-specific sizing, and per-panel background,
  text, border, inner background, and button colors.
- A Visual panel for applying light, dark, or custom colors across all panels and
  the page shell.

Panel order, panel placement, panel labels, top bar ordering, presets, radio
metadata, backup files, polling intervals, language, and provider settings are
stored on the server so the same layout follows you across phones.

## Backup And Restore

Backup files are saved on the SoundCork server under the configured data
directory in a `backups` folder. The website lists existing backups when files
are present, so a phone user can restore without hunting through the filesystem.

Backups currently cover:

- Preset layout.
- Preset data.
- Miniapp settings, including panel order, panel placement, panel labels, top bar
  ordering, panel colors, dragged preset tile opacity, polling intervals,
  language, and radio search endpoint.

Before experimenting with preset changes on a real speaker, save a backup.

## Bluetooth

The app can show Bluetooth as a source if the speaker reports it in `/sources`.
For compatible devices it can request Bluetooth source selection, pairing mode,
or paired-device clearing.

This does not replace the Bluetooth stack on the speaker. It only calls the local
SoundTouch API actions that the speaker already exposes.

## Sleep Timer And Alarm

The sleep timer schedules standby actions for the selected device. The alarm
scheduler can start a preset later. Both support one-shot entries and weekly
repeat entries.

Important limitations:

- These jobs live in the running SoundCork server process.
- If the server restarts, the current scheduled jobs are lost.
- One-shot entries are removed after they execute. Repeating entries remain until
  paused or deleted.
- This is useful for testing the radio API behavior, but persistent alarms should
  eventually be stored on disk and restored on startup.

## Raspberry Pi Notes

For a permanent installation, run SoundCork on a Raspberry Pi or another
always-on machine.

Recommended setup:

- Run `./install.sh` from the repository root to install dependencies, create
  `.env.private`, and register a `systemd` service.
- Give the Pi a stable IP address or reliable hostname.
- Set `base_url` to the address the radio can reach, for example
  `http://soundcork.local:8000`.
- Set `data_dir` to a persistent directory, for example `/home/soundcork/data`.
- Copy the existing `data` directory from the test machine to the Pi.
- Run SoundCork with systemd or Docker Compose.
- Keep the Pi and the SoundTouch radios on the same trusted home network.

After moving servers, update each speaker's SoundCork override configuration so
`margeServerUrl`, `bmxRegistryUrl`, and related URLs point at the Pi.

## Safety Notes

- Do not expose SoundCork directly to the public internet.
- Keep it on a trusted home LAN or behind a VPN.
- Prefer `OverrideSdkPrivateCfg.xml` over editing factory files on the speaker.
- Back up presets before experimenting.
- Avoid changing firmware URLs unless you are intentionally testing update
  behavior.

## Public Fork Checklist

Before publishing a fork for other users:

- Remove personal `data`, backups, logs, IP addresses, and account identifiers.
- Keep `.env.private`, `.venv`, `data`, `logs`, backups, and generated miniapp
  settings out of git.
- Run the test suite.
- Test `/miniapp` on a phone viewport.
- Add screenshots of the mobile dashboard and settings page.
- Document which SoundTouch model and firmware were tested.
- Mark experimental features clearly, especially Wi-Fi changes, Bluetooth
  actions, and scheduled alarms.
