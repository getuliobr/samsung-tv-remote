"""Grab a screenshot from the TVShot service on the TV and save it as PNG.

  .venv/bin/python tvshot/shot.py [out.png] [width] [height]
"""
from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from samsung_tv import load_config  # noqa: E402

PORT = 18765


def ping(host: str) -> str:
    with urllib.request.urlopen(f"http://{host}:{PORT}/ping", timeout=3) as r:
        return r.read().decode().strip()


class Frame(NamedTuple):
    w: int
    h: int
    fmt: int          # 0=4:2:0 (NV12), 1=4:2:2, 2=4:4:4
    y: np.ndarray
    uv: np.ndarray


def fetch(host: str, query: str, timeout: float = 20, opener=None) -> Frame:
    """GET /screenshot?<query>. Raises RuntimeError with the TV's own message (e.g. "error -4 ...")."""
    url = f"http://{host}:{PORT}/screenshot?{query}"
    try:
        with (opener.open(url, timeout=timeout) if opener else urllib.request.urlopen(url, timeout=timeout)) as r:
            hdr, body = r.headers, r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read().decode(errors="ignore").strip() or str(e)) from None
    ys = int(hdr["X-YSize"])
    return Frame(int(hdr["X-Width"]), int(hdr["X-Height"]), int(hdr["X-Format"]),
                 np.frombuffer(body[:ys], np.uint8), np.frombuffer(body[ys:], np.uint8))


def _planes(y: np.ndarray, uv: np.ndarray, w: int, h: int, fmt: int):
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
    return Y, U - 128, V - 128


# HDR10 (PQ, BT.2020) -> SDR. Tuned against the TV's own tone-mapped `screen` capture of a PS5 in HDR.
_PQ_M1, _PQ_M2, _PQ_C1, _PQ_C2, _PQ_C3 = 0.1593017578125, 78.84375, 0.8359375, 18.8515625, 18.6875
_BT2020_TO_709 = np.array([[1.6605, -0.5876, -0.0728], [-0.1246, 1.1329, -0.0083], [-0.0182, -0.1006, 1.1187]],
                          np.float32)
SDR_WHITE_NITS = 250


def nv12_to_rgb(y: np.ndarray, uv: np.ndarray, w: int, h: int, fmt: int, transfer: str = "sdr") -> np.ndarray:
    """YUV from TVShot -> RGB. transfer="sdr": BT.709 limited range; "pq": HDR10 tone-mapped to SDR."""
    Y, U, V = _planes(y, uv, w, h, fmt)
    if transfer == "pq":
        Y = (Y - 16) / 219
        U /= 224
        V /= 224
        rgb = np.clip(np.dstack([Y + 1.4746 * V, Y - 0.16455 * U - 0.57135 * V, Y + 1.8814 * U]), 0, 1)
        p = rgb ** (1 / _PQ_M2)
        nits = 10000 * (np.maximum(p - _PQ_C1, 0) / (_PQ_C2 - _PQ_C3 * p)) ** (1 / _PQ_M1)
        lin = np.clip((nits / SDR_WHITE_NITS) @ _BT2020_TO_709.T, 0, 1)
        return (lin ** (1 / 2.2) * 255 + 0.5).astype(np.uint8)
    Yc = (Y - 16) * 1.164
    r = Yc + 1.793 * V            # BT.709 limited range
    g = Yc - 0.213 * U - 0.533 * V
    b = Yc + 2.112 * U
    return np.clip(np.dstack([r, g, b]), 0, 255).astype(np.uint8)


def to_image(f: Frame, transfer: str = "sdr") -> Image.Image:
    return Image.fromarray(nv12_to_rgb(f.y, f.uv, f.w, f.h, f.fmt, transfer))


def is_blank(img: Image.Image) -> bool:
    """True for an all-black frame. The TV can return one with res=0 (success), so callers should say so."""
    a = np.asarray(img.convert("L"), np.float32)
    return float(a.max()) <= 24 and float(a.std()) < 2


def screenshot(host: str, w: int = 960, h: int = 540) -> Image.Image:
    img = to_image(fetch(host, f"w={w}&h={h}", timeout=15))
    # The TV hands back an anamorphic frame (e.g. 720x540); the screen is 16:9.
    if abs(img.width / img.height - 16 / 9) > 0.05:
        img = img.resize((round(img.height * 16 / 9), img.height), Image.LANCZOS)
    return img


def detect_transfer(host: str, opener=None) -> str:
    """Return "pq" if the video plane carries HDR10, else "sdr".

    The video plane (fn=vmain) is the raw input signal, while fn=screen is after the TV's own tone mapping. Decode
    the video plane both ways and keep whichever looks like the screen.
    """
    ref = np.asarray(to_image(fetch(host, "fn=screen&w=960&h=540", opener=opener)).resize((240, 135), Image.BOX),
                     np.float32)
    v = fetch(host, "fn=vmain&w=480&h=270", opener=opener)
    err = {t: float(np.abs(np.asarray(to_image(v, t).resize((240, 135), Image.BOX), np.float32) - ref).mean())
           for t in ("sdr", "pq")}
    return "pq" if err["pq"] < 0.7 * err["sdr"] else "sdr"


TILE_W, TILE_H = 960, 540  # largest 1:1 tile the scaler returns for the video plane


def _grab(host: str, query: str, transfer: str = "sdr") -> Image.Image:
    return to_image(fetch(host, query), transfer)


def _tile(host: str, cx: int, cy: int, cw: int = TILE_W, ch: int = TILE_H, transfer: str = "sdr") -> Image.Image:
    return _grab(host, f"fn=vmain_crop&w={cw}&h={ch}&cx={cx}&cy={cy}&cw={cw}&ch={ch}", transfer)


CANDIDATES = [(1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)]


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a - a.mean(), b - b.mean()
    return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-6))


def detect_source(host: str, debug: bool = False) -> tuple[int, int]:
    """Measure the video plane's native resolution (crop coordinates are in source pixels).

    For each candidate WxH, crop its top-left quarter and compare with the top-left quarter of the
    whole (TV-downscaled) frame. Only the true source size makes them match.
    """
    full = _grab(host, "fn=vmain&w=960&h=540")
    if is_blank(full):
        raise RuntimeError("the video plane is all black (capture may be blocked, or nothing is playing)")
    full = full.convert("L")
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
        transfer = detect_transfer(host)
        out = Image.new("RGB", (sw, sh))
        for cy in range(0, sh, TILE_H):
            for cx in range(0, sw, TILE_W):
                cw, ch = min(TILE_W, sw - cx), min(TILE_H, sh - cy)
                tile = _tile(host, cx, cy, cw, ch, transfer)
                if tile.size != (cw, ch):
                    tile = tile.resize((cw, ch))
                out.paste(tile, (cx, cy))
        # sanity check: the stitched frame must look like the TV's own downscaled full frame
        full = np.asarray(_grab(host, "fn=vmain&w=960&h=540", transfer).convert("L"), np.float32)
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
