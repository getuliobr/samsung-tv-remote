"""Minimal local-network controller for Samsung Tizen TVs (2016+).

Talks to the TV's built-in remote-control API:
  - REST   http://<ip>:8001/api/v2/                       device info, app status/launch
  - WSS    wss://<ip>:8002/api/v2/channels/samsung.remote.control   keys, text, app list
  - WoL    magic packet to the TV's MAC                    power on from standby

The first WSS connection makes the TV show an "Allow" prompt; the token it
returns is saved to tv_config.json so later connections are silent.
"""
from __future__ import annotations

import base64
import json
import socket
import ssl
import time
import urllib.request
from pathlib import Path
from typing import Optional

import websocket  # websocket-client

CONFIG_PATH = Path(__file__).with_name("tv_config.json")

# Common Tizen app IDs (the TV can't list apps on 2022+ firmware, so we probe these).
KNOWN_APPS = {
    "Netflix": "3201907018807",
    "YouTube": "111299001912",
    "Prime Video": "3201910019365",
    "Disney+": "3201901017640",
    "Max": "3202301029760",
    "HBO Max (old)": "3201601007230",
    "Globoplay": "3201908019041",
    "Spotify": "3201606009684",
    "Apple TV": "3201807016597",
    "Twitch": "3202203026841",
    "Plex": "3201512006963",
    "Samsung TV Plus": "3201710015037",
    "Internet (Browser)": "org.tizen.browser",
    "SmartThings": "com.samsung.tv.ARTIK-SmartThings",
    "Gallery": "3201710015067",
}
APP_NAME = "MyRemote"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")


class SamsungTV:
    def __init__(self, host: str, token: Optional[str] = None, mac: Optional[str] = None,
                 name: str = APP_NAME, timeout: float = 30):
        self.host = host
        self.token = token
        self.mac = mac
        self.name = name
        self.timeout = timeout
        self.ws: Optional[websocket.WebSocket] = None

    # ---------- REST (port 8001, no auth) ----------
    def _rest(self, path: str, method: str = "GET") -> dict:
        req = urllib.request.Request(f"http://{self.host}:8001/api/v2/{path}", method=method)
        with urllib.request.urlopen(req, timeout=5) as r:
            body = r.read().decode() or "{}"
        try:
            return json.loads(body)
        except ValueError:
            return {"raw": body}

    def info(self) -> dict:
        return self._rest("")

    def is_on(self) -> bool:
        try:
            return self.info()["device"].get("PowerState", "on") == "on"
        except Exception:
            return False

    def app_status(self, app_id: str) -> dict:
        return self._rest(f"applications/{app_id}")

    def launch_app_rest(self, app_id: str) -> dict:
        return self._rest(f"applications/{app_id}", method="POST")

    def close_app(self, app_id: str) -> dict:
        return self._rest(f"applications/{app_id}", method="DELETE")

    # ---------- UPnP RenderingControl (port 9197, no auth) ----------
    def _upnp(self, action: str, args: str = "") -> str:
        body = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                f'<u:{action} xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">'
                f'<InstanceID>0</InstanceID><Channel>Master</Channel>{args}</u:{action}></s:Body></s:Envelope>')
        req = urllib.request.Request(
            f"http://{self.host}:9197/upnp/control/RenderingControl1", data=body.encode(), method="POST",
            headers={"Content-Type": 'text/xml; charset="utf-8"',
                     "SOAPACTION": f'"urn:schemas-upnp-org:service:RenderingControl:1#{action}"'})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.read().decode()

    @staticmethod
    def _tag(xml: str, tag: str) -> str:
        return xml.split(f"<{tag}>", 1)[1].split(f"</{tag}>", 1)[0].strip()

    def get_volume(self) -> int:
        return int(self._tag(self._upnp("GetVolume"), "CurrentVolume"))

    def set_volume(self, level: int) -> None:
        self._upnp("SetVolume", f"<DesiredVolume>{max(0, min(100, int(level)))}</DesiredVolume>")

    def get_mute(self) -> bool:
        return self._tag(self._upnp("GetMute"), "CurrentMute") == "1"

    def set_mute(self, muted: bool) -> None:
        self._upnp("SetMute", f"<DesiredMute>{1 if muted else 0}</DesiredMute>")

    # ---------- WebSocket (port 8002, token auth) ----------
    def connect(self) -> None:
        name_b64 = base64.b64encode(self.name.encode()).decode()
        url = f"wss://{self.host}:8002/api/v2/channels/samsung.remote.control?name={name_b64}"
        if self.token:
            url += f"&token={self.token}"
        self.ws = websocket.create_connection(
            url, timeout=self.timeout, sslopt={"cert_reqs": ssl.CERT_NONE, "check_hostname": False})
        # Wait for the handshake; this blocks while the "Allow" prompt is on screen.
        while True:
            msg = json.loads(self.ws.recv())
            event = msg.get("event")
            if event == "ms.channel.connect":
                new_token = msg.get("data", {}).get("token")
                if new_token:
                    self.token = new_token
                return
            if event == "ms.channel.unauthorized":
                raise PermissionError("TV denied the connection (pressed Deny, or prompt timed out).")
            if event == "ms.channel.timeOut":
                raise TimeoutError("TV prompt timed out.")

    def close(self) -> None:
        if self.ws:
            self.ws.close()
            self.ws = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    def _send(self, payload: dict) -> None:
        if not self.ws:
            self.connect()
        self.ws.send(json.dumps(payload))

    def key(self, key: str, cmd: str = "Click") -> None:
        """Send a remote key, e.g. KEY_VOLUP. cmd: Click | Press | Release."""
        self._send({"method": "ms.remote.control", "params": {
            "Cmd": cmd, "DataOfCmd": key, "Option": "false", "TypeOfRemote": "SendRemoteKey"}})

    def hold(self, key: str, seconds: float) -> None:
        self.key(key, "Press")
        time.sleep(seconds)
        self.key(key, "Release")

    def keys(self, *keys: str, delay: float = 0.3) -> None:
        for k in keys:
            self.key(k)
            time.sleep(delay)

    def type_text(self, text: str) -> None:
        """Type into an on-screen text field (search boxes, etc.)."""
        self._send({"method": "ms.remote.control", "params": {
            "Cmd": base64.b64encode(text.encode()).decode(), "DataOfCmd": "base64",
            "TypeOfRemote": "SendInputString"}})
        self._send({"method": "ms.remote.control", "params": {"TypeOfRemote": "SendInputEnd"}})

    def move_cursor(self, dx: int, dy: int) -> None:
        self._send({"method": "ms.remote.control", "params": {
            "Cmd": "Move", "Position": {"x": dx, "y": dy, "Time": str(int(time.time()))},
            "TypeOfRemote": "ProcessMouseDevice"}})

    def apps(self) -> list:
        """List installed apps (appId, name, ...).

        Note: 2022+ firmware (incl. this QN700B) usually ignores this request;
        returns [] in that case. Use KNOWN_APPS + app_status() instead.
        """
        self._send({"method": "ms.channel.emit", "params": {"event": "ed.installedApp.get", "to": "host"}})
        self.ws.settimeout(5)
        try:
            while True:
                msg = json.loads(self.ws.recv())
                if msg.get("event") == "ed.installedApp.get":
                    return msg["data"]["data"]
        except (websocket.WebSocketTimeoutException, socket.timeout):
            return []
        finally:
            self.ws.settimeout(self.timeout)

    def installed_known_apps(self) -> dict:
        """Probe KNOWN_APPS over REST; returns {name: status} for those present."""
        found = {}
        for name, app_id in KNOWN_APPS.items():
            try:
                st = self.app_status(app_id)
                if "id" in st or "running" in st:
                    found[name] = {"appId": app_id, "running": st.get("running"), "visible": st.get("visible")}
            except Exception:
                pass
        return found

    def launch_app(self, app_id: str, deep_link: Optional[str] = None) -> None:
        self._send({"method": "ms.channel.emit", "params": {
            "event": "ed.apps.launch", "to": "host",
            "data": {"appId": app_id,
                     "action_type": "DEEP_LINK" if deep_link else "NATIVE_LAUNCH",
                     "metaTag": deep_link or ""}}})

    def open_browser(self, url: str) -> None:
        self._send({"method": "ms.channel.emit", "params": {
            "event": "ed.apps.launch", "to": "host",
            "data": {"appId": "org.tizen.browser", "action_type": "NATIVE_LAUNCH", "metaTag": url}}})

    # ---------- Power ----------
    def power_off(self) -> None:
        self.key("KEY_POWER")

    def power_on(self) -> None:
        """Wake-on-LAN. Needs 'Power On with Mobile' enabled on the TV."""
        if not self.mac:
            raise ValueError("MAC address unknown")
        mac = bytes.fromhex(self.mac.replace(":", "").replace("-", ""))
        packet = b"\xff" * 6 + mac * 16
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for _ in range(3):
                s.sendto(packet, ("255.255.255.255", 9))
                s.sendto(packet, (self.host, 9))


def from_config() -> SamsungTV:
    cfg = load_config()
    if "host" not in cfg:
        raise SystemExit("No tv_config.json yet; run: python remote.py pair <ip>")
    return SamsungTV(cfg["host"], token=cfg.get("token"), mac=cfg.get("mac"))
