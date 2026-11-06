# samsung-tv-remote

Control a Samsung Tizen TV from a computer on the same network: remote keys, exact volume, apps, YouTube
casting, and screenshots of the TV screen, including a small app that runs on the TV to capture it.

Built and tested against a **Samsung QN55QN700B** (2022 Neo QLED 8K, Tizen 6.5). Other 2016+ Tizen TVs
share the same remote-control API; the screenshot app depends on Samsung's private capture libraries and has only
been tested on this model.

Claude Code skills that drive this tooling (`tv-control`, `tv-youtube`) are in [`.claude/skills/`](.claude/skills)
and are also published in [getuliobr/claude](https://github.com/getuliobr/claude).

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python remote.py pair 192.168.0.50   # your TV's IP; press "Allow" on the TV
```

Pairing saves the TV's address, Wi-Fi MAC and access token to `tv_config.json` (git-ignored, keep it private).
To find the TV's IP, check your router or run any SSDP/UPnP scan: the TV answers on port 8001.

## Usage

```sh
.venv/bin/python remote.py info                    # device info and power state
.venv/bin/python remote.py apps                    # known apps, and which one is on screen
.venv/bin/python remote.py key KEY_HOME KEY_RIGHT  # any remote key(s)
.venv/bin/python remote.py hold KEY_VOLDOWN 2      # hold a key for N seconds
.venv/bin/python remote.py volume                  # read volume (and mute)
.venv/bin/python remote.py volume 20               # set exact volume
.venv/bin/python remote.py text "stranger things"  # type into a focused text field
.venv/bin/python remote.py launch 3201907018807    # open an app (Netflix)
.venv/bin/python remote.py browser https://example.com
.venv/bin/python remote.py youtube gatinho fofo    # search YouTube and play the first hit (or pass a video id)
.venv/bin/python remote.py shot                    # screenshot of the whole screen (needs TVShot, below)
.venv/bin/python remote.py shot --hd               # native-resolution screenshot of the video (up to 4K)
.venv/bin/python remote.py off | on                # power (on = Wake-on-LAN)
```

From Python:

```python
from samsung_tv import from_config
tv = from_config()
tv.set_volume(15)
with tv:
    tv.key("KEY_HOME")
```

## How it talks to the TV

| Channel | Endpoint | Auth | Used for |
| --- | --- | --- | --- |
| REST | `http://TV:8001/api/v2/` | none | info, power state, app status, launch (`POST`), close (`DELETE`) |
| WebSocket | `wss://TV:8002/api/v2/channels/samsung.remote.control` | token | keys, text input, pointer |
| UPnP RenderingControl | `http://TV:9197/upnp/control/RenderingControl1` | none | exact volume and mute |
| DIAL + YouTube Lounge API | `http://TV:8080/ws/app/YouTube` → `youtube.com/api/lounge` | screenId | play a specific YouTube video (same path as phone casting) |
| Wake-on-LAN | magic packet to the TV's MAC | none | power on from standby |
| TVShot (this repo) | `http://TV:18765/screenshot` | none | screenshots |

### Quirks on this firmware

- Listing installed apps (`ed.installedApp.get`) gets no reply; `apps` probes the IDs in `KNOWN_APPS` instead.
- Launching apps over the WebSocket (`ed.apps.launch`) is ignored; `launch` uses the REST call, which works for
  store apps only (system screens such as the Apps panel can't be opened by ID). `NATIVE_LAUNCH` and `DEEP_LINK`
  both fail, with the socket held open or closed at once, and the TV sends no reply. Keys on the same socket still
  work, so only this message is dropped.
- `remote.py browser` sends the same message, so it does nothing. Its REST status returns 404 for
  `org.tizen.browser` and `3201907018784`, so there is no known working way to open a URL yet.
- A cold start of YouTube can stop on the profile picker. A cast then still prints `Tocando na TV` and `apps` shows
  YouTube `(on screen)`, but nothing plays until a profile is chosen. Check with `shot`.
- YouTube: DIAL `POST` returns 403 and deep links are ignored. Use `remote.py youtube`, which casts through the
  Lounge API.
- Wake-on-LAN over Wi-Fi needs **Settings → General → Network → Expert Settings → Power On with Mobile**.

## Screenshots: TVShot

The TV has no network screenshot API, so [`tvshot/`](tvshot) contains **TVShot**, a .NET background service
installed on the TV. It calls Samsung's private capture library (`libsec-video-capture.so`, the same one
[HyperTizen](https://github.com/reisxd/HyperTizen) uses for ambient lighting) and serves frames over HTTP. The
Mac side converts them to PNG.

| Mode | What you get | Limit |
| --- | --- | --- |
| `shot` | the whole screen, menus and overlays included | 720x576 max (scaler hardware limit), upscaled to 16:9 |
| `shot --hd` | the main video plane at its native resolution (1080p, 4K…) | video only, no menus; pauses playback for ~4–9 s |

`--hd` works around the 720x576 cap: the scaler can crop the source frame 1:1, so it measures the video's real
resolution, captures 960x540 tiles and stitches them, pausing playback so all tiles show the same frame. It
checks the result against the TV's own downscaled frame and raises an error rather than return a broken image.
DRM-protected video (e.g. Netflix) may refuse capture.

### Install TVShot

1. **Developer Mode on the TV**: open **Apps → Settings** (on newer UIs: Apps tab, scroll to *Config* →
   *Settings*), type `12345` (no number keys? `remote.py key KEY_1 KEY_2 KEY_3 KEY_4 KEY_5`), switch
   Developer mode **On**, enter this computer's IP, press OK. Then **unplug the TV for 30 s**: turning it off
   with the remote only puts it in standby, and Developer Mode needs a real reboot.
2. **.NET SDK** (`brew install --cask dotnet-sdk`).
3. `.venv/bin/python tvshot/deploy.py` builds the package, installs it over `sdb` and starts it. Run it again
   after code changes or if `curl http://TV:18765/ping` stops answering.

No Tizen Studio and no Samsung account are needed: [`tvshot/sdb.py`](tvshot/sdb.py) is a small sdb (ADB-protocol)
client, and in Developer Mode this TV accepted a package signed with the SDK's default certificate. If your TV
rejects it with a certificate error, [`tvshot/install.py`](tvshot/install.py) and
[`tvshot/samsung_cert.py`](tvshot/samsung_cert.py) implement Samsung's certificate flow (Samsung account login +
DUID). That path is **written but untested**.

> TVShot answers anyone on your network, without authentication. If that matters to you, turn Developer Mode
> off when you are not using it (the service then can't be reinstalled or restarted until you turn it back on).

### What was tried and doesn't work

- The full `IVideoCapture` implementation (`libvideo-capture-impl-sec.so`) needs `libtzcapturec.so`, which
  is blocked for user apps (TrustZone): `Operation not permitted`.
- `libdisplay-capture-api` caps at 960x540 unless the `pensee_support` feature flag is set.
- `secvideo_api_capture_screen_lock` is a mutex, not a frame freeze; captures fail while it is held.

## Layout

```
remote.py            CLI
samsung_tv.py        REST / WebSocket / UPnP / Wake-on-LAN client
youtube_cast.py      YouTube search + Lounge API casting
tvshot/
  TVShot/            the on-TV service (C#, Tizen.NET)
  deploy.py          build + install + start TVShot over sdb
  sdb.py             minimal sdb client
  shot.py            fetch frames, NV12 -> RGB, HD tile stitching
  install.py         DUID / Samsung-certificate install path (untested)
  samsung_cert.py    Samsung certificate creation (untested)
.claude/skills/      Claude Code skills (tv-control, tv-youtube)
```

## Credits

- [reisxd/HyperTizen](https://github.com/reisxd/HyperTizen) and forks, for the capture entry points.
- [reisxd/tizen.js](https://github.com/reisxd/tizen.js) and TizenBrewInstaller, for the sdb and Samsung certificate flows.

## License

[MIT](LICENSE)
