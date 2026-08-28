"""End-to-end build for the "绿化包" (portable, install-free Windows
distribution): builds the Go core, fetches the NATS/Qdrant portable
binaries, builds the portable Python runtime, builds both frontends, then
assembles everything into portable_package/ -- a folder a teammate can copy
to a machine with nothing installed and run via START.bat.

Run this on a Windows build machine (the Python/NATS/Qdrant binaries it
fetches/builds are all Windows-targeted). Requires the Go toolchain, Node.js
+ pnpm/npm, and internet access -- none of which the OUTPUT package needs.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import runner  # noqa: E402 -- reuses build_go_core_if_needed/find_cli_cmd/BIN_DIR
from scripts.install_local_deps import download_nats, download_qdrant  # noqa: E402
from scripts import build_portable_python  # noqa: E402

PACKAGE_DIR = ROOT_DIR / "portable_package"

ADMIN_FRONTEND_DIR = ROOT_DIR / "admin" / "frontend"
STAGE_WEB_ROOT_DIR = ROOT_DIR / "frontend"
STAGE_WEB_APP_DIR = STAGE_WEB_ROOT_DIR / "apps" / "stage-web"

# Vite bakes VITE_BETTERAGENT_WS_TOKEN into the built static JS at compile
# time -- unlike the backend's WEBGATEWAY_TOKEN (read at runtime via
# os.getenv, so a recipient's own .env can set it freely), there is no way
# to change this after packaging without Node/pnpm, which the portable
# package deliberately doesn't require. So both sides ship pinned to this
# same fixed value (see .env.example / frontend/apps/stage-web/.env.example)
# instead of "fill in your own" -- this token's job is just to stop an
# unrelated local process from connecting to the WebGateway's WS port, not
# to guard a network-facing secret, so a shared published default is fine
# for the portable package's threat model (everything bound to 127.0.0.1).
# Passed via the build subprocess's environment, not written into the
# developer's own frontend/apps/stage-web/.env -- Vite's env vars take that
# file as a default and let process env override it, so this never touches
# (or risks corrupting) whatever the build machine's own dev .env has.
PORTABLE_WEBGATEWAY_TOKEN = "portable_package_default_token_rebuild_frontend_to_change"

# Top-level entries copied into portable_package/ verbatim (directories
# copied recursively). Frontend dist/ output is assembled separately below,
# after building it -- only the built static assets ship, never the
# monorepo source / node_modules. .env itself is deliberately NOT copied
# (would leak the build machine's real secrets) -- only .env.example ships,
# each machine fills in its own NATS_PASSWORD/QDRANT_API_KEY/ADMIN_SECRET_KEY.
COPY_ENTRIES = [
    "bin",
    "services",
    "shared",
    "admin/backend",
    "config",
    "scripts",
    "runner.py",
    ".env.example",
]

START_BAT_CONTENT = (
    "@echo off\r\n"
    "cd /d %~dp0\r\n"
    "if not exist .env (\r\n"
    "    echo [!] .env not found -- copy .env.example to .env and fill in the required secrets first.\r\n"
    "    pause\r\n"
    "    exit /b 1\r\n"
    ")\r\n"
    "bin\\python-portable\\python.exe runner.py\r\n"
    "pause\r\n"
)


def run(cmd, cwd, extra_env=None):
    print(f"$ {' '.join(cmd)}  (cwd={cwd})")
    env = {**os.environ, **extra_env} if extra_env else None
    subprocess.run(cmd, cwd=str(cwd), check=True, env=env)


def build_go_and_binaries():
    if not runner.build_go_core_if_needed():
        sys.exit("Go core build failed -- aborting package build.")
    download_nats()
    download_qdrant()


def build_python_runtime():
    build_portable_python.build()


def build_frontends():
    pnpm = runner.find_cli_cmd("pnpm") or runner.find_cli_cmd("npm")
    if not pnpm:
        sys.exit("pnpm (or npm) not found on PATH -- required to build frontend/ (stage-web).")
    run([pnpm, "install", "--frozen-lockfile"], cwd=STAGE_WEB_ROOT_DIR)
    run(
        [pnpm, "run", "build"],
        cwd=STAGE_WEB_ROOT_DIR,
        extra_env={"VITE_BETTERAGENT_WS_TOKEN": PORTABLE_WEBGATEWAY_TOKEN},
    )

    npm = runner.find_cli_cmd("npm")
    if not npm:
        sys.exit("npm not found on PATH -- required to build admin/frontend.")
    run([npm, "ci"], cwd=ADMIN_FRONTEND_DIR)
    run([npm, "run", "build"], cwd=ADMIN_FRONTEND_DIR)


def _copy_tree(src: Path, dst: Path):
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        dirs_exist_ok=True,
    )


def assemble_package():
    if PACKAGE_DIR.exists():
        shutil.rmtree(PACKAGE_DIR)
    PACKAGE_DIR.mkdir(parents=True)

    for name in COPY_ENTRIES:
        src = ROOT_DIR / name
        if not src.exists():
            print(f" [!] Skipping missing entry: {name}")
            continue
        dst = PACKAGE_DIR / name
        if src.is_dir():
            _copy_tree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    admin_dist_src = ADMIN_FRONTEND_DIR / "dist"
    stage_web_dist_src = STAGE_WEB_APP_DIR / "dist"
    if not admin_dist_src.is_dir() or not stage_web_dist_src.is_dir():
        sys.exit("Frontend dist/ output missing -- build_frontends() must run before assemble_package().")

    _copy_tree(admin_dist_src, PACKAGE_DIR / "admin" / "frontend" / "dist")
    _copy_tree(stage_web_dist_src, PACKAGE_DIR / "frontend" / "apps" / "stage-web" / "dist")

    _pin_webgateway_token_in_shipped_env_example()

    (PACKAGE_DIR / "START.bat").write_text(START_BAT_CONTENT, encoding="utf-8")


def _pin_webgateway_token_in_shipped_env_example():
    """Rewrite WEBGATEWAY_TOKEN in the *shipped copy* of .env.example to
    PORTABLE_WEBGATEWAY_TOKEN -- must match what build_frontends() baked into
    the stage-web dist/ bundle already assembled above. Only touches the
    copy under PACKAGE_DIR; the source repo's own .env.example (which still
    correctly tells a from-source dev setup to generate its own random
    secret, since that setup builds its own frontend and has no baked-in
    value to mismatch) is left untouched.
    """
    env_example_path = PACKAGE_DIR / ".env.example"
    if not env_example_path.is_file():
        return
    text = env_example_path.read_text(encoding="utf-8")
    patched = text.replace(
        "WEBGATEWAY_TOKEN=change_me_to_a_strong_random_secret",
        f"WEBGATEWAY_TOKEN={PORTABLE_WEBGATEWAY_TOKEN}",
    )
    if patched == text:
        print(" [!] Could not find the expected WEBGATEWAY_TOKEN line in .env.example to pin -- "
              "check it hasn't been reworded, or the portable package's frontend/backend tokens will mismatch.")
    env_example_path.write_text(patched, encoding="utf-8")

    print(f" [OK] Portable package assembled at {PACKAGE_DIR}")


def main():
    if sys.platform != "win32":
        print(" [!] NOTICE: this produces a Windows-targeted package (portable Python/Qdrant/NATS binaries are all Windows builds).")
        print("     Run this on a Windows build machine for a usable result.")
    build_go_and_binaries()
    build_python_runtime()
    build_frontends()
    assemble_package()


if __name__ == "__main__":
    main()
