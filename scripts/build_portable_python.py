"""Builds a portable, install-free Python 3.12 runtime for the "绿化包"
distribution: downloads the official python.org embeddable Windows zip,
enables site-packages/pip support (disabled by default in embeddable
builds), bootstraps pip, then installs the union of every requirements.txt
this repo's services need.

Run once on a Windows build machine. The result is bin/python-portable/,
consumed by runner.py's get_python_interpreter() and by
scripts/build_portable_package.py -- target machines receive that folder
already fully populated and never need internet access or a Python install
of their own.
"""
import re
import shutil
import subprocess
import sys
import zipfile
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
BIN_DIR = ROOT_DIR / "bin"
PORTABLE_PYTHON_DIR = BIN_DIR / "python-portable"

PYTHON_VERSION = "3.12.7"
PYTHON_EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

# Every requirements.txt this repo's services (spawned by runner.py) need at
# runtime. Kept as an explicit list rather than a glob over services/*/ so a
# new service's requirements file is a deliberate addition here, not
# silently picked up (or silently missed).
REQUIREMENTS_FILES = [
    ROOT_DIR / "requirements.txt",
    ROOT_DIR / "services" / "campus_kb" / "requirements.txt",
    ROOT_DIR / "services" / "cognitive" / "requirements.txt",
    ROOT_DIR / "services" / "companion" / "requirements.txt",
    ROOT_DIR / "services" / "mcp_ppt" / "requirements.txt",
    ROOT_DIR / "services" / "mcp_vscode" / "requirements.txt",
    ROOT_DIR / "services" / "memory" / "requirements.txt",
    ROOT_DIR / "services" / "stt" / "requirements.txt",
    ROOT_DIR / "admin" / "backend" / "requirements.txt",
]


def _download(url: str, dest: Path):
    headers = {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def download_and_extract_python():
    if (PORTABLE_PYTHON_DIR / "python.exe").exists():
        print(f"Portable Python already present at {PORTABLE_PYTHON_DIR}")
        return

    PORTABLE_PYTHON_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = BIN_DIR / "python-embed.download.zip"
    print(f"Downloading embeddable Python {PYTHON_VERSION} from {PYTHON_EMBED_URL}...")
    _download(PYTHON_EMBED_URL, zip_path)

    print(f"Extracting into {PORTABLE_PYTHON_DIR}...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(PORTABLE_PYTHON_DIR)
    zip_path.unlink()


def enable_site_packages():
    """Embeddable Python ships with a ._pth file that disables the `site`
    module (and with it, pip) by default -- re-enable it, per python.org's
    documented embeddable-distribution convention, so pip/site-packages work."""
    pth_candidates = list(PORTABLE_PYTHON_DIR.glob("python3*._pth"))
    if not pth_candidates:
        raise RuntimeError(f"No python3*._pth file found in {PORTABLE_PYTHON_DIR} -- unexpected embeddable zip layout")
    pth_file = pth_candidates[0]

    content = pth_file.read_text(encoding="utf-8")
    if re.search(r"^import site", content, flags=re.MULTILINE):
        print(f"{pth_file.name} already enables site-packages.")
        return

    patched, count = re.subn(r"^#\s*import site", "import site", content, flags=re.MULTILINE)
    if count == 0:
        patched = content.rstrip("\n") + "\nimport site\n"
    pth_file.write_text(patched, encoding="utf-8")
    print(f"Enabled site-packages support in {pth_file.name}")


def bootstrap_pip():
    python_exe = PORTABLE_PYTHON_DIR / "python.exe"
    check = subprocess.run([str(python_exe), "-m", "pip", "--version"], capture_output=True)
    if check.returncode == 0:
        print("pip already bootstrapped.")
        return

    get_pip_path = PORTABLE_PYTHON_DIR / "get-pip.py"
    print(f"Downloading get-pip.py from {GET_PIP_URL}...")
    _download(GET_PIP_URL, get_pip_path)

    print("Bootstrapping pip...")
    subprocess.run([str(python_exe), str(get_pip_path), "--no-warn-script-location"], check=True)
    get_pip_path.unlink()


def install_requirements():
    python_exe = PORTABLE_PYTHON_DIR / "python.exe"
    missing = [str(p) for p in REQUIREMENTS_FILES if not p.exists()]
    if missing:
        raise FileNotFoundError(f"Missing requirements files: {missing}")

    print(f"Installing {len(REQUIREMENTS_FILES)} requirements files into the portable interpreter...")
    cmd = [str(python_exe), "-m", "pip", "install"]
    for req_file in REQUIREMENTS_FILES:
        cmd.extend(["-r", str(req_file)])
    subprocess.run(cmd, check=True)


def build():
    if sys.platform != "win32":
        print(" [!] NOTICE: the embeddable distribution this script downloads is Windows-only.")
        print("     Run it on a Windows build machine to produce a usable bin/python-portable/.")
    download_and_extract_python()
    enable_site_packages()
    bootstrap_pip()
    install_requirements()
    print(f"Portable Python runtime ready at {PORTABLE_PYTHON_DIR}")


if __name__ == "__main__":
    build()
