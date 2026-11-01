# TV reference

## App IDs (for `remote.py launch <appId>`)

| App | appId |
|---|---|
| Netflix | 3201907018807 |
| YouTube | 111299001912 |
| Prime Video | 3201910019365 |
| Disney+ | 3201901017640 |
| Max | 3202301029760 |
| HBO Max (old) | 3201601007230 |
| Globoplay | 3201908019041 |
| Spotify | 3201606009684 |
| Apple TV | 3201807016597 |
| Twitch | 3202203026841 |
| Browser (Internet) | org.tizen.browser (prefer `remote.py browser <url>`) |

The full probe list is `KNOWN_APPS` in `samsung_tv.py`. Run `remote.py apps` to see which are actually
installed now; an app that does not appear there is probably not installed.

## Keys (for `remote.py key ...`)

- Power / navigation: `KEY_POWER KEY_HOME KEY_RETURN` (back) `KEY_EXIT KEY_ENTER` (OK)
  `KEY_UP KEY_DOWN KEY_LEFT KEY_RIGHT KEY_MENU KEY_INFO KEY_GUIDE KEY_TOOLS`
- Volume / channels: `KEY_VOLUP KEY_VOLDOWN KEY_MUTE KEY_CHUP KEY_CHDOWN KEY_0` .. `KEY_9`
- Inputs: `KEY_SOURCE KEY_HDMI1 KEY_HDMI2 KEY_HDMI3 KEY_HDMI4`
- Playback: `KEY_PLAY KEY_PAUSE KEY_STOP KEY_FF KEY_REWIND`

Portuguese phrasing -> key: "volta" = KEY_RETURN, "sai"/"fecha" = KEY_EXIT, "OK"/"confirma" = KEY_ENTER,
"início"/"tela inicial" = KEY_HOME, "pausa" = KEY_PAUSE, "continua"/"dá play" = KEY_PLAY,
"avança" = KEY_FF, "volta um pouco"/"retrocede" = KEY_REWIND, "próximo canal" = KEY_CHUP,
"fonte"/"entrada" = KEY_SOURCE.

## Network endpoints (for debugging only)

| Channel | Endpoint | Used for |
|---|---|---|
| REST | `http://<tv-ip>:8001/api/v2/` | info, app status/launch/close |
| WebSocket | `wss://<tv-ip>:8002/api/v2/channels/samsung.remote.control` (token) | keys, text, app launch |
| UPnP | `http://<tv-ip>:9197/upnp/control/RenderingControl1` | exact volume, mute |
| DIAL | `http://<tv-ip>:8080/ws/app/YouTube` | YouTube screenId (GET only) |
| WoL | magic packet to the TV's Wi-Fi MAC (`mac` in `tv_config.json`) | power on |
| TVShot | `http://<tv-ip>:18765/ping`, `/screenshot` | screenshots (our app on the TV, Developer Mode) |
