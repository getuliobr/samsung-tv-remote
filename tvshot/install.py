"""End-to-end installer for TVShot (no Tizen Studio).

  .venv/bin/python tvshot/install.py duid               # check sdb + print the TV's DUID
  .venv/bin/python tvshot/install.py cert access.json   # create Samsung certificate for this TV
  .venv/bin/python tvshot/install.py install            # build, sign, push, install, start

Requires TV Developer Mode ON with Host PC IP = this Mac.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from samsung_tv import load_config  # noqa: E402
from sdb import SDB  # noqa: E402

PKG = "io.github.getulio.tvshot"
CERT_DIR = HERE / "cert"
PROJECT = HERE / "TVShot"
TMP = "/home/owner/share/tmp/sdk_tools"


def tv_host() -> str:
    return load_config()["host"]


def get_duid(s: SDB) -> str:
    return s.shell("0 getduid").strip().splitlines()[-1].strip()


def cmd_duid():
    s = SDB(tv_host())
    print("connected:", s.banner)
    print("DUID:", get_duid(s))
    print("capability:", s.shell("capability")[:800])


def cmd_cert(access_path: str):
    from samsung_cert import create
    access = json.loads(Path(access_path).read_text())
    s = SDB(tv_host())
    duid = get_duid(s)
    email = access.get("inputEmailID") or access.get("email") or "tvshot@example.com"
    res = create(access, duid, email, "TVShot", CERT_DIR)
    print("certificate created in", res["dir"], "for DUID", duid)


def build_signed() -> Path:
    pw = (CERT_DIR / "password.txt").read_text().strip()
    subprocess.run(["dotnet", "build", "-c", "Release",
                    f"-p:AuthorPath={CERT_DIR / 'author.p12'}", f"-p:AuthorPass={pw}",
                    f"-p:DistributorPath={CERT_DIR / 'distributor.p12'}", f"-p:DistributorPass={pw}"],
                   cwd=PROJECT, check=True)
    return next((PROJECT / "bin/Release/tizen90").glob("*.tpk"))


def cmd_install():
    tpk = build_signed()
    s = SDB(tv_host())
    print("connected:", s.banner)
    s.push(f"{TMP}/device-profile.xml", (CERT_DIR / "device-profile.xml").read_bytes())
    print("pushed device-profile.xml")
    remote = f"{TMP}/tvshot.tpk"
    s.push(remote, tpk.read_bytes())
    print(f"pushed {tpk.name} ({tpk.stat().st_size} bytes)")
    print(s.shell(f"0 vd_appinstall {PKG} {remote}", timeout=180))
    print(s.shell(f"0 execute {PKG}"))
    time.sleep(3)
    from shot import ping
    try:
        print("ping:", ping(tv_host()))
    except Exception as e:
        print("TVShot not answering yet:", e)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
    elif sys.argv[1] == "duid":
        cmd_duid()
    elif sys.argv[1] == "cert":
        cmd_cert(sys.argv[2])
    elif sys.argv[1] == "install":
        cmd_install()
