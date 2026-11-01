---
name: tv-control
description: Control a Samsung Tizen TV over the local network with the samsung-tv-remote tooling - power on/off, volume and mute, remote keys (navigation, play/pause, channels, HDMI inputs), opening apps (Netflix, Prime Video, Disney+, Max, Globoplay, Spotify, Apple TV, Twitch, browser), opening a URL, typing text, taking screenshots and checking what is on screen. Use for requests like "turn off the TV", "volume to 20", "open Netflix", "press back", "what's on the TV?", "take a screenshot of the TV", or in Portuguese "desliga a TV", "liga a TV", "sobe/abaixa o volume", "coloca o volume em 20", "tira o som", "muta", "abre a Netflix", "volta", "pausa", "muda pro HDMI 2", "abre o navegador", "digita ...", "o que está passando na TV?", "tira um print da TV". For playing a YouTube video or song, use the tv-youtube skill instead.
---

# Samsung TV remote

The tooling is the [samsung-tv-remote](https://github.com/getuliobr/samsung-tv-remote) repository. Its clone is
at `${TV_REMOTE_DIR:-$HOME/Documents/my-remote}`; below, `$DIR` means that path. The TV's IP and pairing token
live in `$DIR/tv_config.json` (created by `remote.py pair <ip>`). Tested on a QN55QN700B (2022, Tizen 6.5).

## How to run commands

- Always run from the clone: `cd "$DIR" && .venv/bin/python remote.py <cmd> ...`
- **Every Bash call that touches the TV or the local network must use `dangerouslyDisableSandbox: true`.**
  Inside the sandbox they fail with "No route to host"; that is the sandbox, not the TV being off.
- Never print, cat or commit `tv_config.json` (it holds the pairing token).
- If the user wrote in Portuguese, reply in Portuguese (short, e.g. "Pronto, volume em 20.").

## Commands (`remote.py`)

| Want | Command |
|---|---|
| Device info / power state | `info` (JSON; `device.PowerState` is `on` or standby) |
| Which app is open | `apps` (probes known IDs; `(on screen)` = foreground, `running` = background) |
| Read volume | `volume` -> prints `volume 20` or `volume 20 (muted)` |
| Set exact volume | `volume 20` (0-100, via UPnP, reliable; prefer over repeated KEY_VOLUP) |
| Send keys | `key KEY_RETURN` or several: `key KEY_DOWN KEY_DOWN KEY_ENTER` (0.3 s apart) |
| Hold a key | `hold KEY_VOLDOWN 2` (seconds) |
| Type into an open text field | `text "stranger things"` |
| Open an app | `launch <appId>` (IDs in [reference.md](reference.md)) |
| Open a URL in the TV browser | `browser https://example.com` |
| Screenshot (whole screen, menus included, ~720p) | `shot [file.png]` |
| Screenshot of the video at native resolution (up to 4K, no menus) | `shot --hd [file.png]` |
| Turn off | `off` (sends KEY_POWER) |
| Turn on | `on` (Wake-on-LAN) |
| YouTube | `youtube ...` -> see the **tv-youtube** skill |

Mute: `key KEY_MUTE` toggles. For an explicit state, use the library:
`.venv/bin/python -c "from samsung_tv import from_config; tv=from_config(); tv.set_mute(True); print(tv.get_mute())"`
(`set_mute(False)` to unmute). Check `volume` first if you are not sure of the current state.

Relative volume ("sobe um pouco"): read `volume`, then set e.g. current +5 with `volume N`.

## Seeing the screen

`shot` needs the TVShot app installed on the TV (TV in Developer Mode; see the repo README). Check with
`curl -s -m 3 http://<tv-ip>:18765/ping`; if it does not answer, try `.venv/bin/python tvshot/deploy.py`.

- `shot` saves to `$DIR/screenshots/` and prints the path; open it with the Read tool to look at it. Use it
  to check menus and app screens, and to navigate: take a shot, decide the next key, repeat.
- `shot --hd` pauses the video for ~4-9 s while it stitches tiles, then resumes. Only use it when the user
  wants a high-resolution picture of the video; it shows the video plane only (menus/overlays are missing,
  menus-only screens come out black). It errors out instead of returning a broken image.
- DRM-protected playback (e.g. Netflix) may make the capture fail.

## Rules and known behaviour

- **Power off**: only send `off` when the user clearly asked to turn the TV off. If it is ambiguous, confirm first.
  `off` toggles KEY_POWER, so check `info` first; don't send it if the TV is already in standby.
- **Power on**: `on` needs "Power On with Mobile" enabled on the TV (Settings > General > Network > Expert
  Settings). After sending, wait a few seconds and check `info`.
- **Apps**: launch with `launch <appId>` (REST). WebSocket launches and deep links are ignored on this
  firmware; to open specific content, launch the app and then navigate with keys/`text`, checking with `shot`.
- **App list**: `ed.installedApp.get` gets no reply on this firmware; `apps` probes the `KNOWN_APPS` dict in
  `samsung_tv.py` instead, so an app missing from that dict won't show.
- **YouTube**: never use `launch 111299001912 <deeplink>` or DIAL for playback (DIAL returns 403, deep link
  is ignored). Use the tv-youtube skill.
- **Text input** only works when a text box is focused on the TV (e.g. an app's search field).
- Errors: "No route to host" -> you forgot `dangerouslyDisableSandbox`. Timeouts on `info` -> TV off or
  off the network. `PermissionError` on keys -> token revoked; the user must run `remote.py pair` and press
  Allow on the TV.

## Verifying

After an action, confirm with what you can read, and say what you checked:
- anything visual (menus, an episode, an input switch) -> `shot` and look at it
- app opened -> `apps` shows it `(on screen)`
- volume/mute -> `volume`
- power -> `info` (`PowerState`), or no answer when fully off

Key list and app IDs: [reference.md](reference.md).
