import os
import zipfile
import urllib.request

NATS_ZIP_URL = "https://github.com/nats-io/nats-server/releases/download/v2.10.22/nats-server-v2.10.22-windows-amd64.zip"

def download_nats():
    bin_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
    os.makedirs(bin_dir, exist_ok=True)
    nats_exe = os.path.join(bin_dir, "nats-server.exe")

    if os.path.exists(nats_exe):
        print(f"nats-server.exe already exists at: {nats_exe}")
        return nats_exe

    zip_path = os.path.join(bin_dir, "nats.zip")
    print(f"Downloading nats-server from {NATS_ZIP_URL}...")
    
    headers = {'User-Agent': 'Mozilla/5.0'}
    req = urllib.request.Request(NATS_ZIP_URL, headers=headers)
    with urllib.request.urlopen(req) as resp, open(zip_path, 'wb') as f:
        f.write(resp.read())

    print("Extracting nats-server.exe...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        for file in zip_ref.namelist():
            if file.endswith("nats-server.exe"):
                with zip_ref.open(file) as source, open(nats_exe, "wb") as target:
                    target.write(source.read())
                break

    if os.path.exists(zip_path):
        os.remove(zip_path)

    print(f"Successfully installed nats-server.exe to {nats_exe}!")
    return nats_exe

if __name__ == "__main__":
    download_nats()
