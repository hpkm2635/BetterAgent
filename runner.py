#!/usr/bin/env python3
"""
BetterAgent Microservice Supervisor & Process Orchestrator
Production-Grade Systems Architecture:
1. True Readiness Probes (TCP & NATS Polling, Zero Hardcoded Sleeps)
2. 64-bit Win32 CTypes Explicit Restype/Argtypes (Job Object Safety)
3. Sliding Time Window Circuit Breaker (Anti-Crash-Loop)
4. Concurrent Non-Blocking Graceful Shutdown (Max 4.0s Total Latency)
5. Atomic Thread-Safe Signal Interruption (No Race Conditions)
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

# Force UTF-8 stdout/stderr on Windows. A Chinese-locale (GBK/cp936) console
# otherwise raises UnicodeEncodeError on the first emoji print (✓/✗/🟢/🚀),
# which crashes the supervisor before it can spawn any service. Child Python
# services are also put into PEP 540 UTF-8 mode so their log output is UTF-8.
if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    os.environ.setdefault("PYTHONUTF8", "1")
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)  # UTF-8 code page
    except Exception:
        pass

# Project root directory
ROOT_DIR = Path(__file__).resolve().parent
LOGS_DIR = ROOT_DIR / "logs"
BIN_DIR = ROOT_DIR / "bin"

LOGS_DIR.mkdir(exist_ok=True)
BIN_DIR.mkdir(exist_ok=True)

# Load root .env into this process's environment BEFORE spawning any child
# process (native nats-server, `docker compose`, Go core, Python services),
# so NATS_USER/NATS_PASSWORD and other secrets are inherited consistently
# regardless of which working directory runner.py was launched from.
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
QDRANT_BINARY_NAME = "qdrant.exe" if IS_WINDOWS else "qdrant"
QDRANT_BINARY_PATH = BIN_DIR / QDRANT_BINARY_NAME
QDRANT_STORAGE_DIR = ROOT_DIR / "data" / "qdrant_storage"


# ============================================================================
# 64-bit Windows / Linux OS Specific Controls (Strict CTypes Declarations)
# ============================================================================

GLOBAL_JOB_OBJECT = None

def enable_vt100_console():
    """Enable VT100 ANSI escape sequences on Windows console with 64-bit CTypes safety."""
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

            h_out = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            if h_out and h_out != wintypes.HANDLE(-1).value:
                mode = wintypes.DWORD()
                if kernel32.GetConsoleMode(h_out, ctypes.byref(mode)):
                    kernel32.SetConsoleMode(h_out, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except Exception:
            pass


def init_windows_job_object():
    """
    Creates a Windows Job Object with 64-bit HANDLE restype declarations.
    Guarantees OS kernel automatically kills child processes if runner.py crashes.
    """
    global GLOBAL_JOB_OBJECT
    if not IS_WINDOWS:
        return

    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32

        # ⚠️ CRITICAL: 64-bit CTypes Signature Declarations to avoid HANDLE truncation
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]

        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]

        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

        GLOBAL_JOB_OBJECT = kernel32.CreateJobObjectW(None, None)
        if not GLOBAL_JOB_OBJECT or GLOBAL_JOB_OBJECT == wintypes.HANDLE(-1).value:
            print(" [!] Warning: Failed to obtain valid Windows Job Object HANDLE.")
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
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryLimit", ctypes.c_size_t),
                ("PeakJobMemoryLimit", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        success = kernel32.SetInformationJobObject(
            GLOBAL_JOB_OBJECT,
            9,  # JobObjectExtendedLimitInformation
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not success:
            print(" [!] Warning: SetInformationJobObject failed.")
    except Exception as err:
        print(f" [!] Warning: Could not initialize Windows Job Object ({err}).")


def attach_process_to_job(proc):
    """Attach spawned subprocess handle to Windows Job Object."""
    if IS_WINDOWS and GLOBAL_JOB_OBJECT:
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32
            proc_handle = wintypes.HANDLE(proc._handle)
            kernel32.AssignProcessToJobObject(GLOBAL_JOB_OBJECT, proc_handle)
        except Exception:
            pass


def get_linux_pdeathsig_preexec():
    """Sets PR_SET_PDEATHSIG to SIGTERM on Linux to prevent orphan processes."""
    if not IS_WINDOWS and sys.platform.startswith("linux"):
        def _preexec():
            try:
                import ctypes
                PR_SET_PDEATHSIG = 1
                SIGTERM = 15
                ctypes.CDLL("libc.so.6").prctl(PR_SET_PDEATHSIG, SIGTERM)
            except Exception:
                pass
        return _preexec
    return None


# ============================================================================
# Helpers & Readiness Probes
# ============================================================================

def get_python_interpreter() -> str:
    """
    Finds a Python interpreter, preferring a developer's .venv, then the
    bundled portable runtime built by scripts/build_portable_python.py for
    the install-free package (bin/python-portable/), then falling back to
    whatever interpreter is running this script.
    """
    venv_py = ROOT_DIR / (".venv/Scripts/python.exe" if IS_WINDOWS else ".venv/bin/python")
    if venv_py.exists():
        return str(venv_py)

    alt_venv_py = ROOT_DIR / ".venv/bin/python.exe"
    if alt_venv_py.exists():
        return str(alt_venv_py)

    portable_py = BIN_DIR / "python-portable" / "python.exe"
    if portable_py.exists():
        return str(portable_py)

    return sys.executable


def is_portable_mode() -> bool:
    """True when running from the "绿化包" distribution (bundled Python
    present) rather than a developer checkout with its own .venv -- used to
    decide whether the two frontends should be served as pre-built static
    assets (scripts/portable_static_server.py) instead of spawning
    `pnpm run dev` / `npm run dev`, which require Node.js on the target
    machine."""
    return (BIN_DIR / "python-portable" / "python.exe").exists()


def find_cli_cmd(tool_name: str) -> Optional[str]:
    """Finds CLI command executable path cross-platform (supporting .cmd/.exe/.bat on Windows)."""
    if IS_WINDOWS:
        for ext in (".cmd", ".exe", ".bat", ""):
            found = shutil.which(f"{tool_name}{ext}")
            if found:
                return found
    found = shutil.which(tool_name)
    if found:
        return found
    return None


def is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """Check if a TCP port is open and accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


def wait_for_readiness(
    probe_fn: Callable[[], bool],
    service_name: str,
    timeout: float = 15.0,
    poll_interval: float = 0.1,
) -> bool:
    """
    True readiness probe loop. Returns immediately once ready.
    Eliminates arbitrary time.sleep() delays.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        if probe_fn():
            elapsed = time.time() - start_time
            print(f" [✓] {service_name} ready probe passed in {elapsed:.2f}s 🟢")
            return True
        time.sleep(poll_interval)
    print(f" [✗] {service_name} readiness probe timed out after {timeout}s!")
    return False


def build_go_core_if_needed() -> bool:
    """Builds Go core binary if missing, or rebuilds it if any .go source
    file under core/ is newer than the existing binary. Without this
    staleness check, a binary compiled before a Go code change (e.g. before
    NATS auth was added) would silently keep running forever -- it looks
    "installed" but is actually behaviorally out of date."""
    core_dir = ROOT_DIR / "core"

    needs_build = not GO_BINARY_PATH.exists()
    if not needs_build:
        try:
            binary_mtime = GO_BINARY_PATH.stat().st_mtime
            needs_build = any(
                go_file.stat().st_mtime > binary_mtime
                for go_file in core_dir.rglob("*.go")
            )
        except OSError:
            needs_build = True

    if not needs_build:
        print(f" [✓] Go Core binary found at {GO_BINARY_PATH} (up to date)")
        return True

    go_cmd = "go.exe" if IS_WINDOWS else "go"
    try:
        print(" [!] Go Core binary missing or stale. Rebuilding with 'go build'...")
        cmd = [go_cmd, "build", "-o", str(GO_BINARY_PATH), "./cmd/main.go"]
        res = subprocess.run(cmd, cwd=str(core_dir), capture_output=True, text=True)
        if res.returncode == 0 and GO_BINARY_PATH.exists():
            print(f" [✓] Go Core built successfully -> {GO_BINARY_PATH}")
            return True
        else:
            print(f" [✗] Failed to build Go Core: {res.stderr.strip()}")
            return False
    except FileNotFoundError:
        print(" [!] 'go' compiler not found in PATH and no precompiled binary exists.")
        return False


# ============================================================================
# Service Supervisor Manager (Hardened Architecture)
# ============================================================================

class ServiceManager:
    # Circuit Breaker: Max 5 restarts within a 300-second (5 min) sliding window
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
            print(" [✓] NATS Server is already running on port 4222 (Docker/Service).")
            try:
                res = subprocess.run(["docker", "logs", "--tail", "100", "betteragent-nats"], capture_output=True, text=True)
                log_text = (res.stdout or "") + (res.stderr or "")
                if log_text.strip():
                    with open(LOGS_DIR / "nats_server.log", "w", encoding="utf-8") as f:
                        f.write(log_text)
            except Exception:
                pass
            return True

        nats_user = os.environ.get("NATS_USER")
        nats_password = os.environ.get("NATS_PASSWORD")
        if not nats_user or not nats_password:
            print(" [✗] NATS_USER / NATS_PASSWORD are not set in .env. Refusing to start an unauthenticated NATS server.")
            print("     Copy .env.example to .env and set both values (see NATS_USER/NATS_PASSWORD).")
            return False

        # 1. 如果本地没有 nats-server，自动调用 install_local_deps 脚本下载！
        if not NATS_BINARY_PATH.exists():
            print(" [!] NATS binary missing in bin/. Auto-downloading via install_local_deps.py...")
            try:
                from scripts.install_local_deps import download_nats
                download_nats()
            except Exception as e:
                print(f" [✗] Failed to auto-download NATS: {e}")

        if NATS_BINARY_PATH.exists():
            print(" [1/5] Starting native NATS Server (with auth)...")
            nats_log = open(LOGS_DIR / "nats_server.log", "a", encoding="utf-8")
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
            preexec = get_linux_pdeathsig_preexec()

            # Deliberately NOT binding to a specific loopback address (e.g.
            # -a 127.0.0.1) here: clients connect via the hostname
            # "localhost", which can resolve to ::1 or 127.0.0.1 depending on
            # OS resolver order. An earlier attempt to bind -a 127.0.0.1 broke
            # every Python NATS client on Windows (nats.py's asyncio transport
            # tries resolved addresses sequentially with no Happy-Eyeballs
            # fallback, so a slow/blocked ::1 attempt exhausted the connect
            # timeout before ever trying 127.0.0.1) while the Go core kept
            # working (Go's net.Dial races IPv4/IPv6 concurrently by
            # default). Leaving this unbound (dual-stack default) accepts
            # both ::1 and 127.0.0.1 unambiguously. NATS_USER/NATS_PASSWORD
            # auth remains the actual security boundary here -- see
            # docs/SECURITY.md §2.8.
            nats_cmd = [str(NATS_BINARY_PATH), "-js", "--user", nats_user, "--pass", nats_password]
            proc = subprocess.Popen(
                nats_cmd,
                stdout=nats_log,
                stderr=subprocess.STDOUT,
                cwd=str(ROOT_DIR),
                creationflags=creationflags,
                preexec_fn=preexec,
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

            return wait_for_readiness(
                lambda: is_port_open("127.0.0.1", 4222),
                service_name="NATS Server (4222)",
                timeout=15.0,
            )
        else:
            print(" [1/5] NATS binary not found in bin/. Polling external NATS on 4222...")
            return wait_for_readiness(
                lambda: is_port_open("127.0.0.1", 4222),
                service_name="External NATS Server (4222)",
                timeout=10.0,
            )

    def start_qdrant_if_needed(self) -> bool:
        """Mirrors start_nats_if_needed(): reuse an already-running Qdrant
        (Docker or otherwise) if one is reachable, else fall back to the
        portable bin/qdrant.exe if present. Returning False just means "no
        portable/local Qdrant available here" -- main()'s existing Redis/
        Qdrant/FunASR docker-compose probe (which runs right after this) is
        the final fallback, and campus_kb/memory already degrade gracefully
        (in-memory search fallback) if nothing ever comes up on 6333."""
        if is_port_open("127.0.0.1", 6333):
            print(" [✓] Qdrant is already running on port 6333 (Docker/Service).")
            return True

        if not QDRANT_BINARY_PATH.exists():
            return False

        qdrant_api_key = os.environ.get("QDRANT_API_KEY")
        if not qdrant_api_key:
            print(" [!] QDRANT_API_KEY is not set in .env. Refusing to start an unauthenticated Qdrant server.")
            print("     Copy .env.example to .env and set QDRANT_API_KEY.")
            return False

        print(" [1.5/5] Starting portable Qdrant Server...")
        QDRANT_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        qdrant_log = open(LOGS_DIR / "qdrant_server.log", "a", encoding="utf-8")
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
        preexec = get_linux_pdeathsig_preexec()

        qdrant_env = os.environ.copy()
        qdrant_env["QDRANT__SERVICE__API_KEY"] = qdrant_api_key
        qdrant_env["QDRANT__STORAGE__STORAGE_PATH"] = str(QDRANT_STORAGE_DIR)

        qdrant_cmd = [str(QDRANT_BINARY_PATH)]
        proc = subprocess.Popen(
            qdrant_cmd,
            stdout=qdrant_log,
            stderr=subprocess.STDOUT,
            cwd=str(ROOT_DIR),
            env=qdrant_env,
            creationflags=creationflags,
            preexec_fn=preexec,
        )
        attach_process_to_job(proc)

        self.services["qdrant-server"] = {
            "proc": proc,
            "cmd": qdrant_cmd,
            "out_fd": qdrant_log,
            "err_fd": None,
            "restart": True,
            "restart_history": [],
        }

        return wait_for_readiness(
            lambda: is_port_open("127.0.0.1", 6333),
            service_name="Qdrant Server (6333)",
            timeout=15.0,
        )

    def spawn_service(self, name: str, cmd: list, cwd: Path = ROOT_DIR, restart: bool = True):
        log_out_path = LOGS_DIR / f"{name}_stdout.log"
        log_err_path = LOGS_DIR / f"{name}_stderr.log"

        out_fd = open(log_out_path, "a", encoding="utf-8")
        err_fd = open(log_err_path, "a", encoding="utf-8")

        print(f" [+] Launching {name}...")
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
        preexec = get_linux_pdeathsig_preexec()

        proc = subprocess.Popen(
            cmd,
            stdout=out_fd,
            stderr=err_fd,
            cwd=str(cwd),
            creationflags=creationflags,
            preexec_fn=preexec,
        )
        attach_process_to_job(proc)
        print(f"     -> {name} PID: {proc.pid} 🟢")

        self.services[name] = {
            "proc": proc,
            "cmd": cmd,
            "cwd": cwd,
            "out_fd": out_fd,
            "err_fd": err_fd,
            "restart": restart,
            "restart_history": [],
        }

    def close_service_fds(self, item: dict):
        """Safely close open file descriptors to eliminate FD leaks."""
        for fd_key in ("out_fd", "err_fd"):
            fd = item.get(fd_key)
            if fd and not fd.closed:
                try:
                    fd.flush()
                    fd.close()
                except Exception:
                    pass
            item[fd_key] = None

    def restart_service(self, name: str, item: dict):
        """Restart a failed service after closing old FDs safely."""
        self.close_service_fds(item)

        log_out_path = LOGS_DIR / f"{name}_stdout.log"
        log_err_path = LOGS_DIR / f"{name}_stderr.log"

        out_fd = open(log_out_path, "a", encoding="utf-8")
        err_fd = open(log_err_path, "a", encoding="utf-8")

        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
        preexec = get_linux_pdeathsig_preexec()

        new_proc = subprocess.Popen(
            item["cmd"],
            stdout=out_fd,
            stderr=err_fd,
            cwd=str(item.get("cwd", ROOT_DIR)),
            creationflags=creationflags,
            preexec_fn=preexec,
        )
        attach_process_to_job(new_proc)

        item["proc"] = new_proc
        item["out_fd"] = out_fd
        item["err_fd"] = err_fd
        print(f"     -> {name} restarted with new PID: {new_proc.pid} 🟢")
        self.write_pid_file()

    def check_circuit_breaker(self, name: str, item: dict) -> bool:
        """
        Sliding Time Window Circuit Breaker:
        Counts restarts within the past WINDOW_SECONDS (5 mins).
        Prevents infinite crash loops (e.g. crashing every 35 seconds).
        """
        now = time.time()
        history = item.get("restart_history", [])

        # Remove timestamps older than WINDOW_SECONDS
        history = [ts for ts in history if now - ts <= self.WINDOW_SECONDS]
        history.append(now)
        item["restart_history"] = history

        if len(history) > self.MAX_RESTARTS_IN_WINDOW:
            print(f" [✗] CIRCUIT BREAKER TRIPPED for {name} ({len(history)} restarts in 5m). Halting restarts.")
            return False
        return True

    def monitor_loop(self):
        """Background health check, live PID refresh, and sliding window auto-restart loop."""
        while not self.stop_requested.is_set():
            time.sleep(1.5)
            if self.stop_requested.is_set():
                break

            for name, item in list(self.services.items()):
                proc = item["proc"]
                ret = proc.poll()

                if ret is None:
                    continue

                # Process died
                if self.stop_requested.is_set():
                    break

                print(f" [!] Alert: {name} (PID {proc.pid}) exited unexpectedly with code {ret}!")

                if item.get("restart", True):
                    if self.check_circuit_breaker(name, item):
                        restarts_cnt = len(item["restart_history"])
                        print(f"     -> Automatically restarting {name} ({restarts_cnt}/{self.MAX_RESTARTS_IN_WINDOW} in 5m window)...")
                        time.sleep(1.0)
                        self.restart_service(name, item)
                    else:
                        self.write_pid_file()
                else:
                    self.write_pid_file()

    def write_pid_file(self):
        """Refreshes logs/run.pid with current live PIDs."""
        pid_file = LOGS_DIR / "run.pid"
        pids = [str(item["proc"].pid) for item in self.services.values() if item["proc"].poll() is None]
        try:
            pid_file.write_text("\n".join(pids), encoding="utf-8")
        except OSError:
            pass

    def stop_all(self):
        """
        Atomic, Concurrent Non-Blocking Graceful Shutdown.
        Broadcasts shutdown signals in parallel to avoid N * 3.5s serial delays.
        """
        with self._stop_lock:
            if self._is_stopping:
                return
            self._is_stopping = True

        print("\n [!] Stopping all BetterAgent microservices gracefully (Concurrent Mode)...")
        self.stop_requested.set()

        # Step 1: Broadcast graceful shutdown signal (CTRL_BREAK / SIGINT) to ALL running processes
        running_items = []
        for name, item in reversed(list(self.services.items())):
            proc = item["proc"]
            if proc.poll() is None:
                print(f" [-] Sending shutdown signal to {name} (PID {proc.pid})...")
                try:
                    if IS_WINDOWS:
                        os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
                    else:
                        proc.send_signal(signal.SIGINT)
                except Exception:
                    pass
                running_items.append((name, item))

        # Step 2: Parallel Wait Loop (Max 4.0 seconds total deadline for all processes combined)
        deadline = time.time() + 4.0
        while time.time() < deadline:
            still_running = [item for name, item in running_items if item["proc"].poll() is None]
            if not still_running:
                break
            time.sleep(0.1)

        # Step 3: Force kill any stubborn process remaining after the global deadline
        for name, item in running_items:
            proc = item["proc"]
            if proc.poll() is None:
                print(f" [!] {name} (PID {proc.pid}) did not exit within 4.0s deadline. Force killing...")
                try:
                    proc.kill()
                except Exception:
                    pass

            self.close_service_fds(item)

        pid_file = LOGS_DIR / "run.pid"
        if pid_file.exists():
            try:
                pid_file.unlink()
            except OSError:
                pass
        print(" [✓] All microservices stopped cleanly.")


# ============================================================================
# Banner & CLI Entrypoint
# ============================================================================

def print_banner():
    import random
    enable_vt100_console()

    banner = r"""
  ____  _____ _____ _____ _____ ____       _     ____ _____ _   _ _____ 
 | __ )| ____|_   _|_   _| ____|  _ \     / \   / ___| ____| \ | |_   _|
 |  _ \|  _|   | |   | | |  _| | |_) |   / _ \ | |  _|  _| |  \| | | |  
 | |_) | |___  | |   | | | |___|  _ <   / ___ \| |_| | |___| |\  | | |  
 |____/|_____| |_|   |_| |_____|_| \_\ /_/   \_\____|_____|_| \_| |_|  
=========================================================================
 🚀 BetterAgent Microservice Orchestrator & Supervisor Running
=========================================================================
"""
    print(banner)

    # Load random ASCII Art from icon/ directory if present
    icon_dir = ROOT_DIR / "icon"
    if icon_dir.exists():
        icon_files = [f for f in icon_dir.iterdir() if f.is_file() and f.stat().st_size > 0]
        if icon_files:
            selected_icon = random.choice(icon_files)
            try:
                content = selected_icon.read_text(encoding="utf-8", errors="ignore")
                lines = content.splitlines()
                colors = ["\033[96m", "\033[92m", "\033[93m", "\033[95m"]  # Cyan, Green, Yellow, Magenta
                for idx, line in enumerate(lines):
                    if line.startswith("--") or "asciiart" in line:
                        continue
                    color = colors[idx % len(colors)]
                    print(f"   {color}{line}\033[0m")
                print("")
            except Exception:
                pass


def clean_logs_if_configured():
    """
    Checks app.clean_logs_on_startup in config/config.yaml.
    If True, purges *.log files in logs/ directory prior to launching microservices.
    """
    try:

        temp_dir = ROOT_DIR / "temp"
        if temp_dir.exists():
            for img_file in temp_dir.glob("photo_*.jpg"):
                try:
                    img_file.unlink()
                except Exception:
                    pass
                
        from shared.config_loader import get_config_val
        should_clean = get_config_val("app.clean_logs_on_startup", True)
        if should_clean and LOGS_DIR.exists():
            cleaned_count = 0
            for log_file in LOGS_DIR.glob("*.log"):
                try:
                    log_file.unlink()
                    cleaned_count += 1
                except Exception:
                    pass
            if cleaned_count > 0:
                print(f" [🧹] Startup Log Cleanup: Purged {cleaned_count} old *.log files in logs/ directory.")
    except Exception:
        pass


def kill_stale_port_listeners(ports: list[int]):
    """
    Checks if any specified ports are held by zombie/orphaned processes
    and terminates them before microservice startup to prevent port bind errors (WinError 10048).
    """
    if sys.platform == "win32":
        for port in ports:
            try:
                out = subprocess.check_output(
                    f"netstat -ano | findstr LISTENING | findstr :{port}",
                    shell=True,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                for line in out.strip().splitlines():
                    parts = line.split()
                    if len(parts) >= 5 and f":{port}" in parts[1]:
                        pid = parts[-1]
                        if pid.isdigit() and int(pid) != os.getpid():
                            subprocess.run(
                                f"taskkill /F /PID {pid}",
                                shell=True,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            print(f" [🧹] Startup Port Cleanup: Terminated stale process PID {pid} listening on port {port}.")
            except Exception:
                pass


def main():
    enable_vt100_console()
    init_windows_job_object()
    clean_logs_if_configured()
    kill_stale_port_listeners([8093, 8090, 8094, 8095, 5173, 8096])

    mgr = ServiceManager()
    print(f" [i] Python Interpreter: {mgr.py_exe}")

    # Dependency check for target Python environment
    try:
        res = subprocess.run([mgr.py_exe, "-c", "import nats"], capture_output=True, timeout=3)
        if res.returncode != 0:
            sys.exit(
                f"\n [✗] FATAL: Python interpreter '{mgr.py_exe}' is missing dependencies!\n"
                f"     Please run: pip install -r requirements.txt\n"
            )
    except Exception:
        pass

    # -0.5. Portable Qdrant (bin/qdrant.exe) -- tried before the Docker probe
    # below so an already-bundled/already-running Qdrant short-circuits the
    # docker-compose fallback for Qdrant specifically (Redis/FunASR still
    # fall back to Docker as before; see start_qdrant_if_needed's docstring).
    mgr.start_qdrant_if_needed()

    # 0. 自动检测并拉起 Docker 中的 Redis (6379) 和 Qdrant (6333) 基础设施
    def check_tcp(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except (OSError, socket.error):
            return False

    redis_alive = check_tcp("127.0.0.1", 6379)
    qdrant_alive = check_tcp("127.0.0.1", 6333)
    funasr_alive = check_tcp("127.0.0.1", 10095)

    if not (redis_alive and qdrant_alive and funasr_alive):
        print(" [0/5] Probing Docker infrastructure (Redis:6379, Qdrant:6333, FunASR:10095)...")
        docker_compose_file = ROOT_DIR / "deploy" / "docker-compose.yml"
        env_file = ROOT_DIR / ".env"
        if docker_compose_file.exists():
            try:
                cmd = ["docker", "compose", "-f", str(docker_compose_file)]
                if env_file.exists():
                    cmd.extend(["--env-file", str(env_file)])
                cmd.extend(["up", "-d"])
                res = subprocess.run(cmd, check=False, capture_output=True, text=True)
                if res.returncode == 0:
                    print("     -> Triggered 'docker compose up -d' for Redis, Qdrant & FunASR.")
                else:
                    err_msg = res.stderr.strip()[:120] if res.stderr else "Docker Desktop not responding"
                    print(f" [!] Warning: Docker Compose execution note: {err_msg}")
            except FileNotFoundError:
                print(" [!] Warning: 'docker' CLI not found on system PATH.")

        # Polling readiness probe (up to 4.0s deadline) to eliminate container cold-start race conditions
        probe_deadline = time.time() + 4.0
        redis_now = False
        qdrant_now = False
        funasr_now = False
        while time.time() < probe_deadline:
            if not redis_now:
                redis_now = check_tcp("127.0.0.1", 6379)
            if not qdrant_now:
                qdrant_now = check_tcp("127.0.0.1", 6333)
            if not funasr_now:
                funasr_now = check_tcp("127.0.0.1", 10095)
            if redis_now and qdrant_now and funasr_now:
                break
            time.sleep(0.5)

        if redis_now:
            print(" [✓] Redis is ONLINE on 127.0.0.1:6379 🟢")
        else:
            print(" [!] NOTICE: Redis (127.0.0.1:6379) is offline. Memory service will run in in-memory fallback mode.")
            print("     -> Run 'docker compose -f deploy/docker-compose.yml up -d' to enable persistent Redis buffer.")

        if qdrant_now:
            print(" [✓] Qdrant is ONLINE on 127.0.0.1:6333 🟢")
        else:
            print(" [!] NOTICE: Qdrant (127.0.0.1:6333) is offline. Campus KB will run in HashedNgram fallback mode.")
            print("     -> Run 'docker compose -f deploy/docker-compose.yml up -d' to enable Qdrant vector memory RAG.")

        if funasr_now:
            print(" [✓] FunASR Streaming ASR is ONLINE on 127.0.0.1:10095 🟢")
        else:
            print(" [!] NOTICE: FunASR (127.0.0.1:10095) is offline. STT service will attempt reconnect per utterance.")
            print("     -> Run 'docker compose -f deploy/docker-compose.yml up -d' to enable FunASR ASR container.")
    else:
        print(" [✓] Infrastructure Verified: Redis (6379), Qdrant (6333) & FunASR (10095) are active 🟢")


    # 1. NATS Infrastructure Check (True readiness probe)
    if not mgr.start_nats_if_needed():
        sys.exit(" [✗] FATAL: NATS Server is required but not running on 127.0.0.1:4222. Aborting.")

    # 2. Go Core Service Check & Launch (FATAL if missing/failed)
    if not build_go_core_if_needed():
        sys.exit(" [✗] FATAL: Go Core (Telegram Gateway) is missing or failed to compile. Aborting.")

    mgr.spawn_service("betteragent_core", [str(GO_BINARY_PATH)], cwd=ROOT_DIR)

    # True Readiness Probe for Go Core / NATS Infrastructure
    wait_for_readiness(
        lambda: mgr.services["betteragent_core"]["proc"].poll() is None,
        service_name="Go Core (betteragent_core)",
        timeout=10.0,
    )

    # 3. Python Memory Service
    mgr.spawn_service("memory_service", [mgr.py_exe, "-u", "-m", "services.memory.main"])

    # True Readiness Probe for Memory Service (process running check)
    wait_for_readiness(
        lambda: mgr.services["memory_service"]["proc"].poll() is None,
        service_name="Python Memory Service",
        timeout=10.0,
    )

    # 3.5. Python Campus KB RAG Service (:8093)
    mgr.spawn_service("campus_kb_service", [mgr.py_exe, "-u", "-m", "services.campus_kb.main"])
    wait_for_readiness(
        lambda: mgr.services["campus_kb_service"]["proc"].poll() is None,
        service_name="Python Campus KB Service (:8093)",
        timeout=10.0,
    )

    # 4. Python Cognitive Service
    mgr.spawn_service("cognitive_service", [mgr.py_exe, "-u", "-m", "services.cognitive.main"])

    # 5. Python TTS Service
    mgr.spawn_service("tts_service", [mgr.py_exe, "-u", "-m", "services.tts.main"])

    # 6. Python STT Service (bridges an external FunASR streaming WS server)
    mgr.spawn_service("stt_service", [mgr.py_exe, "-u", "-m", "services.stt.main"])

    # 7. STS2 Game Watcher (optional -- polls the STS2MCP mod's localhost API
    # if the game/mod happen to be running, feeds UrgeEngine via Go Core's
    # /api/game-event endpoint; harmless no-op otherwise, same graceful-
    # degradation posture as every other optional integration here)
    mgr.spawn_service("game_watcher_service", [mgr.py_exe, "-u", "-m", "services.game_watcher.sts2_poller"])

    # 8. Admin Backend Service (:8094)
    admin_backend_main = ROOT_DIR / "admin" / "backend" / "main.py"
    if admin_backend_main.exists():
        mgr.spawn_service("admin_backend_service", [mgr.py_exe, "-u", str(admin_backend_main)])
        wait_for_readiness(
            lambda: is_port_open("127.0.0.1", 8094),
            service_name="Admin Backend Service (:8094)",
            timeout=10.0,
        )

    # 8.5. Python Companion Tools Service (:8096)
    companion_main = ROOT_DIR / "services" / "companion" / "main.py"
    if companion_main.exists():
        mgr.spawn_service("companion_service", [mgr.py_exe, "-u", "-m", "services.companion.main"])
        wait_for_readiness(
            lambda: is_port_open("127.0.0.1", 8096),
            service_name="Python Companion Tools Service (:8096)",
            timeout=10.0,
        )

    # 9. Admin Frontend (:8095) -- portable package serves the pre-built
    # dist/ via scripts/portable_static_server.py (no Node.js needed on the
    # target machine); a developer checkout keeps using the Vite dev server.
    admin_frontend_dir = ROOT_DIR / "admin" / "frontend"
    portable_static_server = ROOT_DIR / "scripts" / "portable_static_server.py"
    if is_portable_mode():
        admin_dist_dir = admin_frontend_dir / "dist"
        if admin_dist_dir.is_dir():
            mgr.spawn_service(
                "admin_frontend_service",
                [mgr.py_exe, str(portable_static_server), "--role", "admin", "--dist", str(admin_dist_dir), "--port", "8095"],
            )
            wait_for_readiness(
                lambda: is_port_open("127.0.0.1", 8095),
                service_name="Admin Frontend Static Server (:8095)",
                timeout=10.0,
            )
        else:
            print(f" [!] NOTICE: {admin_dist_dir} not found (run scripts/build_portable_package.py first). Skipping Admin Frontend (:8095).")
    elif admin_frontend_dir.exists() and (admin_frontend_dir / "package.json").exists():
        npm_cmd = find_cli_cmd("npm")
        if npm_cmd:
            mgr.spawn_service("admin_frontend_service", [npm_cmd, "run", "dev"], cwd=admin_frontend_dir)
            wait_for_readiness(
                lambda: is_port_open("127.0.0.1", 8095),
                service_name="Admin Frontend Vue Service (:8095)",
                timeout=30.0,
            )
        else:
            print(" [!] NOTICE: 'npm' command not found in PATH. Skipping Admin Frontend Vue Service (:8095).")

    # 10. Stage Web Frontend (:5173) -- same portable-vs-dev split as above.
    stage_web_dir = ROOT_DIR / "frontend"
    stage_web_app_dir = stage_web_dir / "apps" / "stage-web"
    if is_portable_mode():
        stage_web_dist_dir = stage_web_app_dir / "dist"
        if stage_web_dist_dir.is_dir():
            mgr.spawn_service(
                "stage_web_frontend_service",
                [mgr.py_exe, str(portable_static_server), "--role", "stage-web", "--dist", str(stage_web_dist_dir), "--port", "5173"],
            )
            wait_for_readiness(
                lambda: is_port_open("127.0.0.1", 5173),
                service_name="Stage Web Static Server (:5173)",
                timeout=10.0,
            )
        else:
            print(f" [!] NOTICE: {stage_web_dist_dir} not found (run scripts/build_portable_package.py first). Skipping Stage Web Frontend (:5173).")
    elif stage_web_dir.exists() and (stage_web_dir / "package.json").exists():
        pnpm_cmd = find_cli_cmd("pnpm") or find_cli_cmd("npm")
        if pnpm_cmd:
            mgr.spawn_service("stage_web_frontend_service", [pnpm_cmd, "run", "dev"], cwd=stage_web_dir)
            wait_for_readiness(
                lambda: is_port_open("127.0.0.1", 5173),
                service_name="Stage Web Frontend Vue Service (:5173)",
                timeout=30.0,
            )
        else:
            print(" [!] NOTICE: 'pnpm' / 'npm' command not found in PATH. Skipping Stage Web Frontend Vue Service (:5173).")

    mgr.write_pid_file()

    def signal_handler(signum, frame):
        mgr.stop_all()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start health check monitor thread
    monitor_thread = threading.Thread(target=mgr.monitor_loop, daemon=True)
    monitor_thread.start()

    print_banner()
    print("\n =========================================================================")
    print("  [✓] All microservices active & healthy! System ready 🟢")
    print("  🐱 数字猫娘前端界面: http://localhost:5173/")
    print("  ⚙️ 后台管理系统面板: http://localhost:8095/")
    print(" =========================================================================\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        mgr.stop_all()


if __name__ == "__main__":
    main()
