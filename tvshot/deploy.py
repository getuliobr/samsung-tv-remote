"""Build TVShot, install it on the TV over sdb and (re)start it. TV must be in Developer Mode.

  .venv/bin/python tvshot/deploy.py
"""
import glob
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from samsung_tv import load_config  # noqa: E402
from sdb import SDB  # noqa: E402

PKG = "io.github.getulio.tvshot"
REMOTE = "/home/owner/share/tmp/sdk_tools/tvshot.tpk"


def main():
    host = load_config()["host"]
    proj = HERE / "TVShot"
    subprocess.run(["dotnet", "build", "-c", "Release", "-v", "q", "-nologo"], cwd=proj, check=True)
    tpk = glob.glob(str(proj / "bin/Release/tizen90/*.tpk"))[0]
    s = SDB(host)
    s.push(REMOTE, Path(tpk).read_bytes())
    out = s.shell(f"0 vd_appinstall {PKG} {REMOTE}", timeout=180)
    print("install:", "completed" if "install completed" in out else out[-400:])
    print("kill:", s.shell(f"0 kill {PKG}", timeout=20).strip()[:200])
    time.sleep(1)
    print("execute:", s.shell(f"0 execute {PKG}", timeout=30).strip()[:200])
    for _ in range(15):
        time.sleep(2)
        try:
            print("ping:", urllib.request.urlopen(f"http://{host}:18765/ping", timeout=3).read().decode().strip())
            return
        except Exception:
            pass
    print("TVShot did not answer /ping")


if __name__ == "__main__":
    main()
