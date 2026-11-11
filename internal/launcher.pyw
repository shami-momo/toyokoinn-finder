"""Open the local dashboard, starting its server only when needed."""

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import Tk, messagebox


ROOT = Path(__file__).resolve().parent.parent
ADDRESS = "http://127.0.0.1:4180/"


def server_running():
    try:
        with urllib.request.urlopen(ADDRESS + "api/state", timeout=2) as response:
            data = json.load(response)
        return isinstance(data, dict) and "config" in data and ("groups" in data or "results" in data)
    except (OSError, ValueError):
        return False


def show_error(message):
    root = Tk()
    root.withdraw()
    messagebox.showerror("토요코인 빈방 알림", message, parent=root)
    root.destroy()


def main():
    try:
        if not server_running():
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "app.py")],
                cwd=ROOT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            deadline = time.monotonic() + 20
            while not server_running():
                if process.poll() is not None:
                    raise RuntimeError("프로그램이 시작되지 않았습니다. Python 환경과 필요한 라이브러리를 확인해 주세요.")
                if time.monotonic() >= deadline:
                    raise RuntimeError("관리 화면에 연결할 수 없습니다. 4180 포트를 사용하는 다른 프로그램이 있는지 확인해 주세요.")
                time.sleep(0.5)
        if not webbrowser.open(ADDRESS):
            raise RuntimeError("브라우저를 열지 못했습니다. 주소창에 http://127.0.0.1:4180/ 을 입력해 주세요.")
    except (OSError, RuntimeError) as error:
        show_error(str(error))


if __name__ == "__main__":
    main()
