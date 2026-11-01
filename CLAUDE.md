# CLAUDE.md

Tooling to control a Samsung Tizen TV (tested: QN55QN700B, Tizen 6.5) from this computer. See README.md for the
full picture; this file covers how to work in the repo.

## Running things

- Python 3.9+, venv at `.venv` (`.venv/bin/pip install -r requirements.txt`). Run tools as `.venv/bin/python …`.
- **Any command that talks to the TV or the LAN must run with the Bash sandbox disabled**
  (`dangerouslyDisableSandbox: true`). In the sandbox it fails with "No route to host"; that is not the TV.
- TV address and pairing token are in `tv_config.json` (git-ignored). Never print, log or commit it.
- The user is often watching the TV. Don't send keys, change volume, launch apps or power it off as a "test"
  unless asked; prefer read-only checks (`info`, `apps`, `volume`, `shot`).
- Replies to the user: Brazilian Portuguese when they write in Portuguese.

## Day-to-day control

Use the project skills in `.claude/skills/` (`tv-control`, `tv-youtube`); they list the commands, what works
on this firmware and how to verify actions. To look at the screen, run `remote.py shot` and open the PNG with
the Read tool.

## TVShot (on-TV screenshot service)

- Code: `tvshot/TVShot/App.cs` (single file, Tizen.NET service app, HTTP on port 18765).
- Build + install + restart: `.venv/bin/python tvshot/deploy.py` (needs the .NET SDK and the TV in Developer
  Mode with this Mac's IP). Check with `curl http://<tv-ip>:18765/ping`.
- Capture calls are private Samsung APIs found by reverse engineering. Native structs are larger than they look:
  always pass oversized, zeroed unmanaged buffers (see `Capture.Grab`), never `ref` small C# structs.
- `secvideo_api_capture_screen` returns at most 720x576 (kernel limit). Native resolution only comes from
  `vmain_crop` tiles of the video plane (`shot.py: screenshot_hd`). Coordinates are in the video's source
  resolution, which must be measured (`detect_source`), not guessed.
- Experimental endpoints `/cap2` and `/dbg` are research leftovers; keep anything that reads the TV filesystem
  out of the shipped app.

## Conventions

- Keep `remote.py` a thin CLI over `samsung_tv.py`, `youtube_cast.py` and `tvshot/shot.py`.
- When something doesn't work on this firmware, record it in README "Quirks" or "What was tried" so nobody
  retries it.
- Python 3.9 compatible (`from __future__ import annotations`, no `match`).
