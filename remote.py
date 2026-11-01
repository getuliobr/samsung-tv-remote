#!/usr/bin/env python3
"""Command-line remote for the Samsung TV.

  python remote.py pair <tv-ip>           # first time: accept the prompt on the TV
  python remote.py info
  python remote.py key KEY_VOLUP KEY_VOLUP
  python remote.py hold KEY_VOLDOWN 2
  python remote.py volume [N]              # read, or set exact level 0-100
  python remote.py text "hello"
  python remote.py apps
  python remote.py launch 3201907018807    # Netflix app id
  python remote.py youtube gatinho fofo    # search + play on the TV (or pass a video id)
  python remote.py browser https://example.com
  python remote.py shot [file.png]         # screenshot via the TVShot app (whole screen incl. menus, ~720p)
  python remote.py shot --hd [file.png]    # native-res video frame (up to 4K), stitched from tiles
  python remote.py off | on
"""
import json
import sys

from samsung_tv import SamsungTV, from_config, load_config, save_config


def main(argv):
    if not argv:
        print(__doc__)
        return
    cmd, args = argv[0], argv[1:]

    if cmd == "pair":
        host = args[0] if args else load_config().get("host")
        tv = SamsungTV(host)
        info = tv.info()["device"]
        print(f"Pairing with {info['name']} ({info['modelName']}). Press ALLOW on the TV...")
        tv.connect()
        tv.close()
        save_config({"host": host, "token": tv.token, "mac": info.get("wifiMac"),
                     "model": info.get("modelName"), "name": info.get("name")})
        print(f"Paired. Token saved to tv_config.json")
        return

    tv = from_config()
    if cmd == "info":
        print(json.dumps(tv.info(), indent=2))
    elif cmd == "apps":
        for n, s in tv.installed_known_apps().items():
            flag = "(on screen)" if s["visible"] else ("running" if s["running"] else "")
            print(f"{s['appId']:<34} {n:<20} {flag}")
    elif cmd == "volume":
        if args:
            tv.set_volume(int(args[0]))
        print(f"volume {tv.get_volume()}{' (muted)' if tv.get_mute() else ''}")
    elif cmd == "youtube":
        import re
        from youtube_cast import Lounge, get_screen_id, search
        q = " ".join(args)
        if re.fullmatch(r"[\w-]{11}", q):
            vid, title = q, q
        else:
            vid, title = search(q, 1)[0]
        Lounge(get_screen_id(tv.host)).play_video(vid)
        print(f"Tocando na TV: {title} ({vid})")
    elif cmd == "shot":
        import time
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).with_name("tvshot")))
        from shot import screenshot, screenshot_hd
        hd = "--hd" in args
        args = [a for a in args if a != "--hd"]
        name = f"tv_{'hd_' if hd else ''}{time.strftime('%Y%m%d_%H%M%S')}.png"
        out = Path(args[0]) if args else Path(__file__).with_name("screenshots") / name
        out.parent.mkdir(exist_ok=True)
        img = screenshot_hd(tv.host) if hd else screenshot(tv.host)
        img.save(out)
        print(f"{out} ({img.width}x{img.height})")
    elif cmd == "on":
        tv.power_on()
        print("Wake-on-LAN sent.")
    else:
        with tv:
            if cmd == "key":
                tv.keys(*args)
            elif cmd == "hold":
                tv.hold(args[0], float(args[1]))
            elif cmd == "text":
                tv.type_text(" ".join(args))
            elif cmd == "launch":
                tv.launch_app(args[0], args[1] if len(args) > 1 else None)
            elif cmd == "browser":
                tv.open_browser(args[0])
            elif cmd == "off":
                tv.power_off()
            else:
                raise SystemExit(f"Unknown command: {cmd}")
        cfg = load_config()
        if tv.token and tv.token != cfg.get("token"):
            cfg["token"] = tv.token
            save_config(cfg)


if __name__ == "__main__":
    main(sys.argv[1:])
