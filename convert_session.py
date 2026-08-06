import sqlite3
import os
import subprocess

session_path = "meowclient.session"

if not os.path.exists(session_path):
    print(f"Error: {session_path} not found.")
    exit(1)

conn = sqlite3.connect(session_path)
cursor = conn.cursor()
cursor.execute("SELECT dc_id, server_address, port, auth_key FROM sessions")
row = cursor.fetchone()

if not row or not row[3]:
    print("Error: No valid auth_key found in Telethon session.")
    exit(1)

dc_id, server_address, port, auth_key = row
auth_key_hex = auth_key.hex()

print(f"Extracted Telethon AuthKey from {session_path} (DC {dc_id}, {server_address}:{port})")

env = os.environ.copy()
env["AUTH_KEY_HEX"] = auth_key_hex

res = subprocess.run(["go", "run", "./cmd/convert/main.go"], cwd="core", env=env, capture_output=True, text=True)
print(res.stdout)
if res.stderr:
    print(res.stderr)
