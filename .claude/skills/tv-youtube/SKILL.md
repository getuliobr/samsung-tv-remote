---
name: tv-youtube
description: Search and play YouTube videos or music on a Samsung Tizen TV with the samsung-tv-remote tooling. Use when the user asks to put a song, music video, clip, show or any video on the TV, e.g. "play X on the TV", "put on Bohemian Rhapsody", or in Portuguese "coloca X na TV", "toca X", "bota aquela música do Y", "põe o clipe de X", "coloca a versão ao vivo de X", "passa um vídeo de gatinho na TV", "toca no YouTube". Also for pausing, resuming or seeking the video playing on the TV.
---

# Play YouTube on the TV

Tooling: the [samsung-tv-remote](https://github.com/getuliobr/samsung-tv-remote) repository, cloned at
`${TV_REMOTE_DIR:-$HOME/Documents/my-remote}` (`$DIR` below): `youtube_cast.py` + `remote.py youtube`. It casts
through the YouTube Lounge API, the same path a phone uses, using the TV's screenId from
`http://<tv-ip>:8080/ws/app/YouTube`.

- **Run every command below with `dangerouslyDisableSandbox: true`** (the search and the TV calls fail inside
  the sandbox).
- Reply in Portuguese when the user wrote in Portuguese.
- This is the ONLY playback path that works. Do not try DIAL launch (403), `launch 111299001912 <deeplink>`
  (ignored) or typing into the YouTube app's search.

## Steps

1. **Search and look at the titles** (don't play the first hit blindly):
   ```sh
   cd "$DIR" && .venv/bin/python -c "from youtube_cast import search; [print(v, t) for v, t in search('artista música', 8)]"
   ```
   `search(query, n)` returns `(videoId, title)` pairs (titles only, no channel or duration). Add useful words
   to the query: `oficial` / `official video` for the clip, `ao vivo` / `live` + venue/year if the user named
   a version, `letra` / `lyrics` if asked.
2. **Pick the best match**:
   - Default: the official clip or official audio of the artist ("Artista - Música (Official Video)",
     "Clipe Oficial", the plain "Artista - Música").
   - If the user named a version (ao vivo, acústico, MTV, a show/year), pick the title that matches it.
   - Avoid covers, karaoke, "reação"/reaction, tutorials, slowed/sped-up, shorts and compilations unless asked.
   - For generic requests ("vídeo de gatinho"), any reasonable top result is fine.
3. **Play by video id** (so the title you report is the one you chose):
   ```sh
   cd "$DIR" && .venv/bin/python remote.py youtube <11-char videoId>
   ```
   It prints `Tocando na TV: <id> (<id>)` when given an id; the real title is the one from step 1.
   (`remote.py youtube <free text>` also works but plays the first hit unseen; use it only for trivial requests.)
4. **Tell the user** the exact title you played, e.g. "Tocando na TV: *Legião Urbana - Tempo Perdido (Ao Vivo)*".
   If the choice was ambiguous, list 2-3 alternatives with short labels and offer to switch.

## Verify and troubleshoot

- Check with `remote.py apps`: YouTube (`111299001912`) should be `(on screen)`. To confirm the video itself,
  take a `remote.py shot` and look at it (see tv-control).
- "No screenId; open the YouTube app on the TV once": run `remote.py launch 111299001912`, wait ~5 s, retry.
- If `info` does not answer, the TV is off: offer `remote.py on` (see tv-control).
- Volume while playing: use tv-control (`remote.py volume N`).

## Pause / resume / seek

Simplest: `remote.py key KEY_PAUSE` / `key KEY_PLAY` / `key KEY_FF` / `key KEY_REWIND`.
Fallback via a new Lounge session (implemented in `youtube_cast.py` but not yet tested on this TV; if the
user says nothing changed, use the keys):
```sh
.venv/bin/python -c "from samsung_tv import load_config; from youtube_cast import Lounge, get_screen_id; Lounge(get_screen_id(load_config()['host'])).pause()"
```
Replace `.pause()` with `.play()` or `.seek(90)` (seconds).
