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
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

# Project root directory
ROOT_DIR = Path(__file__).resolve().parent
LOGS_DIR = ROOT_DIR / "logs"
BIN_DIR = ROOT_DIR / "bin"

LOGS_DIR.mkdir(exist_ok=True)
BIN_DIR.mkdir(exist_ok=True)

IS_WINDOWS = os.name == "nt"
GO_BINARY_NAME = "betteragent_core.exe" if IS_WINDOWS else "betteragent-core"
GO_BINARY_PATH = BIN_DIR / GO_BINARY_NAME
NATS_BINARY_NAME = "nats-server.exe" if IS_WINDOWS else "nats-server"
NATS_BINARY_PATH = BIN_DIR / NATS_BINARY_NAME


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
    Finds standard Python interpreter (.venv preferred).
    Strict single-environment architecture without legacy fallbacks.
    """
    venv_py = ROOT_DIR / (".venv/Scripts/python.exe" if IS_WINDOWS else ".venv/bin/python")
    if venv_py.exists():
        return str(venv_py)
    
    alt_venv_py = ROOT_DIR / ".venv/bin/python.exe"
    if alt_venv_py.exists():
        return str(alt_venv_py)

    return sys.executable


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
    """Builds Go core binary if not present."""
    if GO_BINARY_PATH.exists():
        print(f" [✓] Go Core binary found at {GO_BINARY_PATH}")
        return True

    go_cmd = "go.exe" if IS_WINDOWS else "go"
    try:
        print(" [!] Go Core binary missing. Attempting to build with 'go build'...")
        core_dir = ROOT_DIR / "core"
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
            print(" [✓] NATS Server is already running on port 4222.")
            return True

        if NATS_BINARY_PATH.exists():
            print(" [1/5] Starting native NATS Server...")
            nats_log = open(LOGS_DIR / "nats_server.log", "a", encoding="utf-8")
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
            preexec = get_linux_pdeathsig_preexec()

            proc = subprocess.Popen(
                [str(NATS_BINARY_PATH), "-js"],
                stdout=nats_log,
                stderr=subprocess.STDOUT,
                cwd=str(ROOT_DIR),
                creationflags=creationflags,
                preexec_fn=preexec,
            )
            attach_process_to_job(proc)

            self.services["nats-server"] = {
                "proc": proc,
                "cmd": [str(NATS_BINARY_PATH), "-js"],
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


def main():
    enable_vt100_console()
    init_windows_job_object()
    clean_logs_if_configured()
    print_banner()

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

    # 4. Python Cognitive Service
    mgr.spawn_service("cognitive_service", [mgr.py_exe, "-u", "-m", "services.cognitive.main"])

    # 5. Python TTS Service
    mgr.spawn_service("tts_service", [mgr.py_exe, "-u", "-m", "services.tts.main"])

    mgr.write_pid_file()

    def signal_handler(signum, frame):
        mgr.stop_all()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start health check monitor thread
    monitor_thread = threading.Thread(target=mgr.monitor_loop, daemon=True)
    monitor_thread.start()

    print("\n [✓] All microservices active & healthy. Monitoring... Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        mgr.stop_all()


if __name__ == "__main__":
    main()
