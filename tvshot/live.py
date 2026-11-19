"""Live view of the TV screen in the browser (MJPEG), with a clickable remote.

  .venv/bin/python tvshot/live.py            # http://127.0.0.1:8765
  .venv/bin/python tvshot/live.py --lan      # also reachable from other devices on your network

Frames come from the TVShot app on the TV (see README). Modes:
  screen  whole screen incl. menus (720x540 from the TV, stretched to 16:9)
  video   main video plane only (960x540, sharper, no menus)
"""
from __future__ import annotations

import io
import json
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from samsung_tv import from_config, load_config  # noqa: E402
from shot import detect_transfer, fetch, is_blank, to_image  # noqa: E402

TV = load_config()["host"]
QUERY = {"screen": "fn=screen&w=960&h=540", "video": "fn=vmain&w=960&h=540"}
ALLOWED_KEYS = {
    "KEY_UP", "KEY_DOWN", "KEY_LEFT", "KEY_RIGHT", "KEY_ENTER", "KEY_RETURN", "KEY_HOME", "KEY_EXIT",
    "KEY_VOLUP", "KEY_VOLDOWN", "KEY_MUTE", "KEY_PLAY", "KEY_PAUSE", "KEY_FF", "KEY_REWIND",
    "KEY_CHUP", "KEY_CHDOWN", "KEY_SOURCE", "KEY_MENU", "KEY_INFO",
}


class Grabber(threading.Thread):
    """Pulls frames from TVShot as fast as it answers and keeps the latest JPEG."""

    def __init__(self):
        super().__init__(daemon=True)
        self.mode = "screen"
        self.jpeg = b""
        self.seq = 0
        self.fps = 0.0
        self.error = ""
        self.cond = threading.Condition()
        self.session = urllib.request.build_opener()
        self.transfer, self.transfer_at = "sdr", 0.0

    def grab(self) -> Image.Image:
        if self.mode == "video" and time.time() - self.transfer_at > 5:   # HDR sources (e.g. a PS5) send PQ
            self.transfer, self.transfer_at = detect_transfer(TV, self.session), time.time()
        transfer = self.transfer if self.mode == "video" else "sdr"
        img = to_image(fetch(TV, QUERY[self.mode], timeout=10, opener=self.session), transfer)
        if img.size != (960, 540):
            img = img.resize((960, 540), Image.BILINEAR)
        return img

    def run(self):
        times = []
        while True:
            try:
                img = self.grab()
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=80)
                now = time.time()
                times = [t for t in times if now - t < 2] + [now]
                with self.cond:
                    self.jpeg = buf.getvalue()
                    self.seq += 1
                    self.fps = (len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 else 0.0
                    self.error = "quadro todo preto: a TV respondeu OK, mas a captura pode estar bloqueada" \
                        if is_blank(img) else ""
                    self.cond.notify_all()
            except Exception as e:  # TV off, DRM, TVShot not running...
                self.error = str(e)[:200]
                time.sleep(1)


GRABBER = Grabber()

PAGE = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TV ao vivo</title>
<style>
  :root { color-scheme: dark; --bg:#0f1115; --panel:#181b22; --line:#2a2f3a; --fg:#e8eaf0; --dim:#8b93a7; --acc:#4f8cff; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.4 -apple-system, system-ui, sans-serif; }
  main { max-width:1280px; margin:0 auto; padding:16px; display:grid; gap:16px; grid-template-columns: 1fr 230px; }
  @media (max-width: 860px) { main { grid-template-columns: 1fr; } }
  .screen { position:relative; background:#000; border:1px solid var(--line); border-radius:12px; overflow:hidden; aspect-ratio:16/9; }
  .screen img { width:100%; height:100%; object-fit:contain; display:block; }
  .badge { position:absolute; top:10px; left:10px; background:rgba(0,0,0,.6); padding:4px 10px; border-radius:999px; font-size:13px; display:flex; gap:8px; align-items:center; }
  .dot { width:8px; height:8px; border-radius:50%; background:#ff4d4f; animation: blink 1s infinite; }
  @keyframes blink { 50% { opacity:.3 } }
  .err { position:absolute; bottom:10px; left:10px; right:10px; background:rgba(120,20,20,.85); padding:6px 10px; border-radius:8px; font-size:13px; display:none; }
  aside { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px; display:flex; flex-direction:column; gap:14px; }
  h1 { font-size:16px; margin:0; }
  .seg { display:flex; border:1px solid var(--line); border-radius:8px; overflow:hidden; }
  .seg button { flex:1; border:0; border-radius:0; }
  .seg button.on { background:var(--acc); color:#fff; }
  button { background:#232834; color:var(--fg); border:1px solid var(--line); border-radius:8px; padding:9px 0; font-size:15px; cursor:pointer; }
  button:hover { background:#2c3342; } button:active { transform:scale(.97); }
  .pad { display:grid; grid-template-columns:repeat(3,1fr); gap:6px; }
  .row { display:grid; grid-template-columns:repeat(3,1fr); gap:6px; }
  .ok { background:var(--acc); color:#fff; border-color:var(--acc); }
  .hint { color:var(--dim); font-size:12px; }
</style></head><body><main>
  <div class="screen">
    <img id="v" src="/stream" alt="Tela da TV ao vivo">
    <div class="badge"><span class="dot"></span><span>AO VIVO</span><span id="fps">– fps</span></div>
    <div class="err" id="err"></div>
  </div>
  <aside>
    <h1>📺 TV ao vivo</h1>
    <div class="seg"><button data-mode="screen" class="on">Tela inteira</button><button data-mode="video">Só vídeo</button></div>
    <div class="pad">
      <span></span><button data-k="KEY_UP">▲</button><span></span>
      <button data-k="KEY_LEFT">◀</button><button class="ok" data-k="KEY_ENTER">OK</button><button data-k="KEY_RIGHT">▶</button>
      <span></span><button data-k="KEY_DOWN">▼</button><span></span>
    </div>
    <div class="row"><button data-k="KEY_RETURN">↩︎</button><button data-k="KEY_HOME">⌂</button><button data-k="KEY_EXIT">✕</button></div>
    <div class="row"><button data-k="KEY_REWIND">⏪</button><button data-k="KEY_PLAY">⏯</button><button data-k="KEY_FF">⏩</button></div>
    <div class="row"><button data-k="KEY_VOLDOWN">🔉</button><button data-k="KEY_MUTE">🔇</button><button data-k="KEY_VOLUP">🔊</button></div>
    <p class="hint">Teclado: setas, Enter (OK), Backspace (voltar), H (home), espaço (play/pause), +/− (volume).</p>
  </aside>
</main>
<script>
  const send = k => fetch('/key?k=' + k, {method:'POST'});
  document.querySelectorAll('[data-k]').forEach(b => b.onclick = () => send(b.dataset.k));
  document.querySelectorAll('[data-mode]').forEach(b => b.onclick = () => {
    fetch('/mode?m=' + b.dataset.mode, {method:'POST'});
    document.querySelectorAll('[data-mode]').forEach(x => x.classList.toggle('on', x === b));
  });
  const keys = {ArrowUp:'KEY_UP',ArrowDown:'KEY_DOWN',ArrowLeft:'KEY_LEFT',ArrowRight:'KEY_RIGHT',Enter:'KEY_ENTER',
                Backspace:'KEY_RETURN',h:'KEY_HOME',' ':'KEY_PLAY','+':'KEY_VOLUP','=':'KEY_VOLUP','-':'KEY_VOLDOWN'};
  addEventListener('keydown', e => { const k = keys[e.key]; if (k) { e.preventDefault(); send(k); } });
  setInterval(async () => {
    try {
      const s = await (await fetch('/status')).json();
      document.getElementById('fps').textContent = s.fps.toFixed(1) + ' fps';
      const err = document.getElementById('err');
      err.style.display = s.error ? 'block' : 'none'; err.textContent = s.error;
    } catch (e) {}
  }, 1000);
  // reconnect the stream if it drops
  const v = document.getElementById('v');
  v.onerror = () => setTimeout(() => v.src = '/stream?' + Date.now(), 1000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, ctype: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/":
            self._send(200, "text/html; charset=utf-8", PAGE.encode())
        elif path == "/status":
            self._send(200, "application/json", json.dumps(
                {"fps": GRABBER.fps, "mode": GRABBER.mode, "error": GRABBER.error}).encode())
        elif path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            last = -1
            try:
                while True:
                    with GRABBER.cond:
                        GRABBER.cond.wait_for(lambda: GRABBER.seq != last, timeout=5)
                        jpeg, last = GRABBER.jpeg, GRABBER.seq
                    if not jpeg:
                        continue
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                     + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self):
        path, _, query = self.path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if path == "/key" and params.get("k") in ALLOWED_KEYS:
            tv = from_config()
            with tv:
                tv.key(params["k"])
            self._send(200, "text/plain", b"ok")
        elif path == "/mode" and params.get("m") in QUERY:
            GRABBER.mode = params["m"]
            self._send(200, "text/plain", b"ok")
        else:
            self._send(400, "text/plain", b"bad request")


def main():
    bind = "0.0.0.0" if "--lan" in sys.argv else "127.0.0.1"
    GRABBER.start()
    server = ThreadingHTTPServer((bind, 8765), Handler)
    print(f"TV ao vivo: http://{'<este-mac>' if bind == '0.0.0.0' else '127.0.0.1'}:8765  (Ctrl+C para parar)")
    server.serve_forever()


if __name__ == "__main__":
    main()
