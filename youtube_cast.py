"""Play a YouTube video on the TV via the YouTube Lounge API (same path as phone casting)."""
from __future__ import annotations
import json, random, re, string, sys, urllib.parse, urllib.request

LOUNGE = "https://www.youtube.com/api/lounge"


def _post(url: str, data: dict) -> str:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode()


def get_screen_id(tv_host: str) -> str:
    with urllib.request.urlopen(f"http://{tv_host}:8080/ws/app/YouTube", timeout=5) as r:
        m = re.search(r"<screenId>([^<]+)</screenId>", r.read().decode())
    if not m:
        raise RuntimeError("No screenId; open the YouTube app on the TV once and retry.")
    return m.group(1)


class Lounge:
    def __init__(self, screen_id: str, name: str = "MyRemote"):
        tok = json.loads(_post(f"{LOUNGE}/pairing/get_lounge_token_batch", {"screen_ids": screen_id}))
        self.token = tok["screens"][0]["loungeToken"]
        self.name, self.rid, self.ofs = name, 1, 0
        self.params = {"device": "REMOTE_CONTROL", "name": name, "app": "youtube-desktop",
                       "id": "".join(random.choices(string.ascii_lowercase + string.digits, k=26)),
                       "loungeIdToken": self.token, "VER": "8", "v": "2", "CVER": "1", "t": "1"}
        body = self._bind({"count": "0"})
        self.sid = re.search(r'\["c","([^"]+)"', body).group(1)
        self.gsession = re.search(r'\["S","([^"]+)"', body).group(1)

    def _bind(self, data: dict, extra: dict | None = None) -> str:
        q = dict(self.params, RID=str(self.rid), zx="".join(random.choices(string.ascii_lowercase, k=12)))
        q.update(extra or {})
        self.rid += 1
        return _post(f"{LOUNGE}/bc/bind?" + urllib.parse.urlencode(q), data)

    def send(self, command: str, **args) -> None:
        data = {"count": "1", "ofs": str(self.ofs), "req0__sc": command}
        data.update({f"req0_{k}": str(v) for k, v in args.items()})
        self.ofs += 1
        self._bind(data, {"SID": self.sid, "gsessionid": self.gsession})

    def play_video(self, video_id: str, start: int = 0) -> None:
        self.send("setPlaylist", videoId=video_id, currentTime=start, currentIndex=0,
                  videoIds=video_id, listId="")

    def pause(self): self.send("pause")
    def play(self): self.send("play")
    def seek(self, seconds: int): self.send("seekTo", newTime=seconds)


def search(query: str, n: int = 5) -> list[tuple[str, str]]:
    req = urllib.request.Request("https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": query}),
                                 headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "pt-BR"})
    html = urllib.request.urlopen(req, timeout=15).read().decode()
    data = json.loads(re.search(r"var ytInitialData = (\{.*?\});</script>", html).group(1))
    out = []
    def walk(o):
        if isinstance(o, dict):
            if "videoRenderer" in o:
                v = o["videoRenderer"]
                out.append((v["videoId"], "".join(r.get("text", "") for r in v["title"]["runs"])))
            for x in o.values(): walk(x)
        elif isinstance(o, list):
            for x in o: walk(x)
    walk(data)
    return out[:n]


if __name__ == "__main__":
    sys.path.insert(0, __import__("os").path.dirname(__file__))
    from samsung_tv import load_config
    lounge = Lounge(get_screen_id(load_config()["host"]))
    vid = sys.argv[1]
    lounge.play_video(vid)
    print(f"setPlaylist sent: {vid}")
