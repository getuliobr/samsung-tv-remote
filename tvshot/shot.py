"""Grab a screenshot from the TVShot service on the TV and save it as PNG.

  .venv/bin/python tvshot/shot.py [out.png] [width] [height]
"""
from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from samsung_tv import load_config  # noqa: E402

PORT = 18765


def ping(host: str) -> str:
    with urllib.request.urlopen(f"http://{host}:{PORT}/ping", timeout=3) as r:
        return r.read().decode().strip()


def nv12_to_rgb(y: np.ndarray, uv: np.ndarray, w: int, h: int, fmt: int) -> np.ndarray:
    Y = y.reshape(h, w).astype(np.float32)
    if fmt == 0:      # 4:2:0, interleaved UV (NV12)
        uvp = uv.reshape(h // 2, w // 2, 2).astype(np.float32)
        U = uvp[:, :, 0].repeat(2, 0).repeat(2, 1)
        V = uvp[:, :, 1].repeat(2, 0).repeat(2, 1)
    elif fmt == 1:    # 4:2:2, interleaved UV per row
        uvp = uv.reshape(h, w // 2, 2).astype(np.float32)
        U = uvp[:, :, 0].repeat(2, 1)
        V = uvp[:, :, 1].repeat(2, 1)
    else:             # 4:4:4, interleaved
        uvp = uv.reshape(h, w, 2).astype(np.float32)
        U, V = uvp[:, :, 0], uvp[:, :, 1]
    U -= 128
    V -= 128
    Yc = (Y - 16) * 1.164
    r = Yc + 1.793 * V            # BT.709 limited range
    g = Yc - 0.213 * U - 0.533 * V
    b = Yc + 2.112 * U
    return np.clip(np.dstack([r, g, b]), 0, 255).astype(np.uint8)


def screenshot(host: str, w: int = 960, h: int = 540) -> Image.Image:
    try:
        with urllib.request.urlopen(f"http://{host}:{PORT}/screenshot?w={w}&h={h}", timeout=15) as r:
            hdr, body = r.headers, r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read().decode(errors="ignore").strip())
    rw, rh, fmt = int(hdr["X-Width"]), int(hdr["X-Height"]), int(hdr["X-Format"])
    ys = int(hdr["X-YSize"])
    y = np.frombuffer(body[:ys], np.uint8)
    uv = np.frombuffer(body[ys:], np.uint8)
    img = Image.fromarray(nv12_to_rgb(y, uv, rw, rh, fmt))
    # The TV hands back an anamorphic frame (e.g. 720x540); the screen is 16:9.
    if abs(img.width / img.height - 16 / 9) > 0.05:
        img = img.resize((round(img.height * 16 / 9), img.height), Image.LANCZOS)
    return img


TILE_W, TILE_H = 960, 540  # largest 1:1 tile the scaler returns for the video plane


def _grab(host: str, query: str) -> Image.Image:
    with urllib.request.urlopen(f"http://{host}:{PORT}/screenshot?{query}", timeout=20) as r:
        hdr, body = r.headers, r.read()
    w, h, ys = int(hdr["X-Width"]), int(hdr["X-Height"]), int(hdr["X-YSize"])
    return Image.fromarray(nv12_to_rgb(np.frombuffer(body[:ys], np.uint8), np.frombuffer(body[ys:], np.uint8), w, h, 0))


def _tile(host: str, cx: int, cy: int, cw: int = TILE_W, ch: int = TILE_H) -> Image.Image:
    return _grab(host, f"fn=vmain_crop&w={cw}&h={ch}&cx={cx}&cy={cy}&cw={cw}&ch={ch}")


CANDIDATES = [(1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)]


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a - a.mean(), b - b.mean()
    return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-6))


def detect_source(host: str, debug: bool = False) -> tuple[int, int]:
    """Measure the video plane's native resolution (crop coordinates are in source pixels).

    For each candidate WxH, crop its top-left quarter and compare with the top-left quarter of the
    whole (TV-downscaled) frame. Only the true source size makes them match.
    """
    full = _grab(host, "fn=vmain&w=960&h=540").convert("L")
    ref = np.asarray(full.crop((0, 0, 480, 270)), np.float32)
    scores = {}
    for w, h in CANDIDATES:
        t = _grab(host, f"fn=vmain_crop&w=480&h=270&cx=0&cy=0&cw={w // 2}&ch={h // 2}").convert("L")
        scores[(w, h)] = _corr(np.asarray(t.resize((480, 270)), np.float32), ref)
    if debug:
        print("source scores:", {f"{w}x{h}": round(s, 3) for (w, h), s in scores.items()})
    best = max(scores, key=scores.get)
    if scores[best] < 0.8:
        raise RuntimeError(f"could not measure the video resolution (best match {scores[best]:.2f}); "
                           "is a video playing full-screen?")
    return best


def _is_moving(host: str) -> bool:
    a = np.asarray(_grab(host, "fn=vmain&w=480&h=270"), np.float32)
    time.sleep(0.4)
    b = np.asarray(_grab(host, "fn=vmain&w=480&h=270"), np.float32)
    return float(np.abs(a - b).mean()) > 1.5


def screenshot_hd(host: str, freeze: bool = True) -> Image.Image:
    """Native-resolution frame of the main video plane, stitched from 1:1 tiles (no menus/overlays).

    The scaler only returns <=960x540 per call, so we crop the source frame into tiles. If the picture
    is moving, we pause playback while stitching (KEY_PAUSE / KEY_PLAY) so every tile is the same frame.
    """
    from samsung_tv import from_config
    paused = False
    tv = None
    if freeze and _is_moving(host):
        tv = from_config()
        tv.connect()
        tv.key("KEY_PAUSE")
        paused = True
        time.sleep(0.8)
    try:
        sw, sh = detect_source(host)
        out = Image.new("RGB", (sw, sh))
        for cy in range(0, sh, TILE_H):
            for cx in range(0, sw, TILE_W):
                cw, ch = min(TILE_W, sw - cx), min(TILE_H, sh - cy)
                tile = _tile(host, cx, cy, cw, ch)
                if tile.size != (cw, ch):
                    tile = tile.resize((cw, ch))
                out.paste(tile, (cx, cy))
        # sanity check: the stitched frame must look like the TV's own downscaled full frame
        full = np.asarray(_grab(host, "fn=vmain&w=960&h=540").convert("L"), np.float32)
        mine = np.asarray(out.convert("L").resize((960, 540)), np.float32)
        score = _corr(full, mine)
        if score < 0.85:
            raise RuntimeError(f"stitched image does not match the screen (similarity {score:.2f})")
        return out
    finally:
        if paused:
            tv.key("KEY_PLAY")
            tv.close()


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "tv_screenshot.png"
    w = int(sys.argv[2]) if len(sys.argv) > 2 else 960
    h = int(sys.argv[3]) if len(sys.argv) > 3 else 540
    host = load_config()["host"]
    img = screenshot(host, w, h)
    img.save(out)
    print(f"saved {out} ({img.width}x{img.height})")
