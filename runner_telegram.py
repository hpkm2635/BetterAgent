#!/usr/bin/env python3
"""
BetterAgent Lightweight Telegram-Only Microservice Supervisor
Designed for low-resource VPS / Cloud deployments:
- Only spawns 4 core components: NATS Server, Go Core (Gotd TG Adapter), Memory Service, Cognitive Service.
- Omits Web stage-web frontend (:5173), Admin panel (:8094/:8095), TTS service (:8091), STT service (:8092), Campus KB (:8093), and STS2 watcher.
- Supports cross-platform execution (Linux / Windows VPS).
"""

import os
import sys
import time
import socket
import signal
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

# Force UTF-8 encoding on console streams
if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    os.environ.setdefault("PYTHONUTF8", "1")
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent
LOGS_DIR = ROOT_DIR / "logs"
BIN_DIR = ROOT_DIR / "bin"

LOGS_DIR.mkdir(exist_ok=True)
BIN_DIR.mkdir(exist_ok=True)

# Load root .env
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT_DIR / ".env")
except ImportError:
    pass

IS_WINDOWS = os.name == "nt"
GO_BINARY_NAME = "betteragent_core.exe" if IS_WINDOWS else "betteragent-core"
GO_BINARY_PATH = BIN_DIR / GO_BINARY_NAME
NATS_BINARY_NAME = "nats-server.exe" if IS_WINDOWS else "nats-server"
NATS_BINARY_PATH = BIN_DIR / NATS_BINARY_NAME

GLOBAL_JOB_OBJECT = None


def enable_vt100_console():
    """Enable ANSI escape sequences on Windows console."""
    if IS_WINDOWS:
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32
            kernel32.GetStdHandle.restype = wintypes.HANDLE
            kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
            kernel32.GetConsoleMode.restype = wintypes.BOOL
            kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel32.SetConsoleMode.restype = wintypes.BOOL
            kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]

            h_out = kernel32.GetStdHandle(-11)
            if h_out and h_out != wintypes.HANDLE(-1).value:
                mode = wintypes.DWORD()
                if kernel32.GetConsoleMode(h_out, ctypes.byref(mode)):
                    kernel32.SetConsoleMode(h_out, mode.value | 0x0004)
        except Exception:
            pass


def init_windows_job_object():
    """Ensure child processes terminate when supervisor dies on Windows."""
    global GLOBAL_JOB_OBJECT
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32

        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]

        GLOBAL_JOB_OBJECT = kernel32.CreateJobObjectW(None, None)
        if not GLOBAL_JOB_OBJECT or GLOBAL_JOB_OBJECT == wintypes.HANDLE(-1).value:
            GLOBAL_JOB_OBJECT = None
            return

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(f, ctypes.c_ulonglong) for f in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryLimit", ctypes.c_size_t),
                ("PeakJobMemoryLimit", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject(GLOBAL_JOB_OBJECT, 9, ctypes.byref(info), ctypes.sizeof(info))
    except Exception:
        pass


def attach_process_to_job(proc):
    if IS_WINDOWS and GLOBAL_JOB_OBJECT:
        try:
            import ctypes
            from ctypes import wintypes
            ctypes.windll.kernel32.AssignProcessToJobObject(GLOBAL_JOB_OBJECT, wintypes.HANDLE(proc._handle))
        except Exception:
            pass


def get_linux_pdeathsig_preexec():
    if not IS_WINDOWS and sys.platform.startswith("linux"):
        def _preexec():
            try:
                import ctypes
                ctypes.CDLL("libc.so.6").prctl(1, 15)  # PR_SET_PDEATHSIG, SIGTERM
            except Exception:
                pass
        return _preexec
    return None


def get_python_interpreter() -> str:
    venv_py = ROOT_DIR / (".venv/Scripts/python.exe" if IS_WINDOWS else ".venv/bin/python")
    if venv_py.exists():
        return str(venv_py)
    alt_venv = ROOT_DIR / ".venv/bin/python.exe"
    if alt_venv.exists():
        return str(alt_venv)
    return sys.executable


def is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


def wait_for_readiness(probe_fn: Callable[[], bool], service_name: str, timeout: float = 15.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if probe_fn():
            print(f" [✓] {service_name} ready probe passed in {time.time() - start:.2f}s 🟢")
            return True
        time.sleep(0.1)
    print(f" [✗] {service_name} readiness probe timed out after {timeout}s!")
    return False


def build_go_core_if_needed() -> bool:
    core_dir = ROOT_DIR / "core"
    needs_build = not GO_BINARY_PATH.exists()
    if not needs_build:
        try:
            binary_mtime = GO_BINARY_PATH.stat().st_mtime
            needs_build = any(f.stat().st_mtime > binary_mtime for f in core_dir.rglob("*.go"))
        except OSError:
            needs_build = True

    if not needs_build:
        print(f" [✓] Go Core binary ready -> {GO_BINARY_PATH}")
        return True

    go_cmd = "go.exe" if IS_WINDOWS else "go"
    try:
        print(" [!] Building Go Core binary...")
        cmd = [go_cmd, "build", "-o", str(GO_BINARY_PATH), "./cmd/main.go"]
        res = subprocess.run(cmd, cwd=str(core_dir), capture_output=True, text=True)
        if res.returncode == 0 and GO_BINARY_PATH.exists():
            print(f" [✓] Go Core compiled -> {GO_BINARY_PATH}")
            return True
        else:
            print(f" [✗] Go Core build failed: {res.stderr.strip()}")
            return False
    except FileNotFoundError:
        print(" [!] 'go' compiler not found in PATH and binary is missing.")
        return False


class ServiceManager:
    WINDOW_SECONDS = 300.0
    MAX_RESTARTS_IN_WINDOW = 5

    def __init__(self):
        self.services = {}
        self.stop_requested = threading.Event()
        self.py_exe = get_python_interpreter()
        self._stop_lock = threading.Lock()
        self._is_stopping = False

    def start_nats_if_needed(self) -> bool:
        if is_port_open("127.0.0.1", 4222):
            print(" [✓] NATS Server is active on port 4222 🟢")
            return True

        nats_user = os.environ.get("NATS_USER")
        nats_password = os.environ.get("NATS_PASSWORD")
        if not nats_user or not nats_password:
            print(" [✗] NATS_USER / NATS_PASSWORD missing in .env")
            return False

        if not NATS_BINARY_PATH.exists():
            print(" [!] Downloading NATS binary via install_local_deps.py...")
            try:
                from scripts.install_local_deps import download_nats
                download_nats()
            except Exception as e:
                print(f" [✗] Failed to download NATS: {e}")

        if NATS_BINARY_PATH.exists():
            print(" [1/4] Starting NATS Server...")
            nats_log = open(LOGS_DIR / "nats_server.log", "a", encoding="utf-8")
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
            nats_cmd = [str(NATS_BINARY_PATH), "-js", "--user", nats_user, "--pass", nats_password]
            proc = subprocess.Popen(
                nats_cmd,
                stdout=nats_log,
                stderr=subprocess.STDOUT,
                cwd=str(ROOT_DIR),
                creationflags=creationflags,
                preexec_fn=get_linux_pdeathsig_preexec(),
            )
            attach_process_to_job(proc)

            self.services["nats-server"] = {
                "proc": proc,
                "cmd": nats_cmd,
                "out_fd": nats_log,
                "err_fd": None,
                "restart": True,
                "restart_history": [],
            }
            return wait_for_readiness(lambda: is_port_open("127.0.0.1", 4222), "NATS Server (4222)")
        return wait_for_readiness(lambda: is_port_open("127.0.0.1", 4222), "External NATS Server (4222)")

    def spawn_service(self, name: str, cmd: list, cwd: Path = ROOT_DIR):
        out_fd = open(LOGS_DIR / f"{name}_stdout.log", "a", encoding="utf-8")
        err_fd = open(LOGS_DIR / f"{name}_stderr.log", "a", encoding="utf-8")

        print(f" [+] Launching {name}...")
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
        proc = subprocess.Popen(
            cmd,
            stdout=out_fd,
            stderr=err_fd,
            cwd=str(cwd),
            creationflags=creationflags,
            preexec_fn=get_linux_pdeathsig_preexec(),
        )
        attach_process_to_job(proc)
        print(f"     -> {name} PID: {proc.pid} 🟢")

        self.services[name] = {
            "proc": proc,
            "cmd": cmd,
            "cwd": cwd,
            "out_fd": out_fd,
            "err_fd": err_fd,
            "restart": True,
            "restart_history": [],
        }

    def close_service_fds(self, item: dict):
        for k in ("out_fd", "err_fd"):
            fd = item.get(k)
            if fd and not fd.closed:
                try:
                    fd.flush()
                    fd.close()
                except Exception:
                    pass
            item[k] = None

    def monitor_loop(self):
        while not self.stop_requested.is_set():
            time.sleep(2.0)
            if self.stop_requested.is_set():
                break

            for name, item in list(self.services.items()):
                proc = item["proc"]
                if proc.poll() is not None and not self.stop_requested.is_set():
                    print(f" [!] Alert: {name} (PID {proc.pid}) exited unexpectedly!")
                    # Basic auto-restart
                    now = time.time()
                    history = [ts for ts in item.get("restart_history", []) if now - ts <= self.WINDOW_SECONDS]
                    history.append(now)
                    item["restart_history"] = history
                    if len(history) <= self.MAX_RESTARTS_IN_WINDOW:
                        print(f"     -> Restarting {name}...")
                        self.close_service_fds(item)
                        out_fd = open(LOGS_DIR / f"{name}_stdout.log", "a", encoding="utf-8")
                        err_fd = open(LOGS_DIR / f"{name}_stderr.log", "a", encoding="utf-8")
                        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
                        new_proc = subprocess.Popen(
                            item["cmd"],
                            stdout=out_fd,
                            stderr=err_fd,
                            cwd=str(item.get("cwd", ROOT_DIR)),
                            creationflags=creationflags,
                            preexec_fn=get_linux_pdeathsig_preexec(),
                        )
                        attach_process_to_job(new_proc)
                        item.update({"proc": new_proc, "out_fd": out_fd, "err_fd": err_fd})
                        print(f"     -> {name} new PID: {new_proc.pid} 🟢")

    def stop_all(self):
        with self._stop_lock:
            if self._is_stopping:
                return
            self._is_stopping = True

        print("\n [!] Stopping Telegram Agent services...")
        self.stop_requested.set()

        running_items = []
        for name, item in reversed(list(self.services.items())):
            proc = item["proc"]
            if proc.poll() is None:
                try:
                    if IS_WINDOWS:
                        os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
                    else:
                        proc.send_signal(signal.SIGINT)
                except Exception:
                    pass
                running_items.append((name, item))

        deadline = time.time() + 4.0
        while time.time() < deadline:
            if not [item for name, item in running_items if item["proc"].poll() is None]:
                break
            time.sleep(0.1)

        for name, item in running_items:
            proc = item["proc"]
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            self.close_service_fds(item)

        print(" [✓] All Telegram Agent services stopped cleanly.")


def main():
    enable_vt100_console()
    init_windows_job_object()

    print("""
=========================================================================
 🐱 BetterAgent Telegram Lite Mode Supervisor
 🚀 Minimal Footprint Stack (NATS + Go Core + Memory + Cognitive)
=========================================================================
""")

    mgr = ServiceManager()

    # 1. Start NATS Server
    if not mgr.start_nats_if_needed():
        sys.exit(" [✗] FATAL: NATS Server is required. Aborting.")

    # 2. Build & Launch Go Core (TG Gotd Adapter)
    if not build_go_core_if_needed():
        sys.exit(" [✗] FATAL: Go Core failed to build. Aborting.")

    mgr.spawn_service("betteragent_core", [str(GO_BINARY_PATH)])
    wait_for_readiness(lambda: mgr.services["betteragent_core"]["proc"].poll() is None, "Go Core (betteragent_core)")

    # 3. Python Memory Service
    mgr.spawn_service("memory_service", [mgr.py_exe, "-u", "-m", "services.memory.main"])
    wait_for_readiness(lambda: mgr.services["memory_service"]["proc"].poll() is None, "Python Memory Service")

    # 4. Python Cognitive Service
    mgr.spawn_service("cognitive_service", [mgr.py_exe, "-u", "-m", "services.cognitive.main"])
    wait_for_readiness(lambda: mgr.services["cognitive_service"]["proc"].poll() is None, "Python Cognitive Service")

    def signal_handler(signum, frame):
        mgr.stop_all()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    monitor_thread = threading.Thread(target=mgr.monitor_loop, daemon=True)
    monitor_thread.start()

    print("\n =========================================================================")
    print("  [✓] Telegram Catgirl Agent is ONLINE and ready! 🟢")
    print("  📱 Telegram Bot is actively listening for messages.")
    print(" =========================================================================\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        mgr.stop_all()


if __name__ == "__main__":
    main()
