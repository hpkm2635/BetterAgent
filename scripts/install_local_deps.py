import os
import zipfile
import urllib.request

NATS_ZIP_URL = "https://github.com/nats-io/nats-server/releases/download/v2.10.22/nats-server-v2.10.22-windows-amd64.zip"
QDRANT_ZIP_URL = "https://github.com/qdrant/qdrant/releases/download/v1.19.0/qdrant-x86_64-pc-windows-msvc.zip"


def _bin_dir():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")


def _download_and_extract_exe(zip_url: str, exe_name: str) -> str:
    bin_dir = _bin_dir()
    os.makedirs(bin_dir, exist_ok=True)
    exe_path = os.path.join(bin_dir, exe_name)

    if os.path.exists(exe_path):
        print(f"{exe_name} already exists at: {exe_path}")
        return exe_path

    zip_path = os.path.join(bin_dir, f"{exe_name}.download.zip")
    print(f"Downloading {exe_name} from {zip_url}...")

    headers = {'User-Agent': 'Mozilla/5.0'}
    req = urllib.request.Request(zip_url, headers=headers)
    with urllib.request.urlopen(req) as resp, open(zip_path, 'wb') as f:
        f.write(resp.read())

    print(f"Extracting {exe_name}...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        for file in zip_ref.namelist():
            if file.endswith(exe_name):
                with zip_ref.open(file) as source, open(exe_path, "wb") as target:
                    target.write(source.read())
                break

    if os.path.exists(zip_path):
        os.remove(zip_path)

    print(f"Successfully installed {exe_name} to {exe_path}!")
    return exe_path


def download_nats():
    return _download_and_extract_exe(NATS_ZIP_URL, "nats-server.exe")


def download_qdrant():
    return _download_and_extract_exe(QDRANT_ZIP_URL, "qdrant.exe")


if __name__ == "__main__":
    download_nats()
    download_qdrant()
