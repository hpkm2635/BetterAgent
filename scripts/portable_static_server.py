"""Serves a built frontend's dist/ folder as static files, using only the
Python standard library (no extra pip dependency, so it runs from the same
portable interpreter runner.py already spawns the other services with).

Two roles:
  - stage-web: pure static file server. betteragent-ws.ts talks directly to
    the Go core over ws://localhost:8080, so no proxy is needed.
  - admin: static file server *plus* a reverse proxy for /api and /health to
    the admin backend (127.0.0.1:8094) -- replicates
    admin/frontend/vite.config.js's dev-server proxy exactly (same-origin
    from the browser's perspective, no header injection/stripping), since
    App.vue's fetch() calls use relative /api/... paths that only resolve
    through a proxy, not a bare static server.

Both roles fall back to index.html for any GET path that doesn't match an
existing file, so client-side routing (stage-web uses vue-router) works.
"""
import argparse
import sys
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

PROXY_PREFIXES = ("/api", "/health")
HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}


def make_handler(dist_dir: Path, proxy_target: Optional[str]):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(dist_dir), **kwargs)

        def log_message(self, fmt, *args):
            sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

        def _should_proxy(self) -> bool:
            if not proxy_target:
                return False
            return any(
                self.path == prefix or self.path.startswith((prefix + "/", prefix + "?"))
                for prefix in PROXY_PREFIXES
            )

        def _proxy(self):
            target_url = f"http://{proxy_target}{self.path}"
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else None

            fwd_headers = {
                k: v for k, v in self.headers.items()
                if k.lower() not in HOP_BY_HOP_HEADERS and k.lower() != "host"
            }
            req = urllib.request.Request(target_url, data=body, headers=fwd_headers, method=self.command)
            try:
                with urllib.request.urlopen(req) as resp:
                    self._relay_response(resp.status, resp.headers, resp.read())
            except urllib.error.HTTPError as err:
                self._relay_response(err.code, err.headers, err.read())
            except urllib.error.URLError as err:
                self.send_response(502)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(f"Bad Gateway: admin backend unreachable ({err.reason})".encode())

        def _relay_response(self, status, headers, body):
            self.send_response(status)
            for k, v in headers.items():
                if k.lower() not in HOP_BY_HOP_HEADERS:
                    self.send_header(k, v)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self):
            if self._should_proxy():
                self._proxy()
                return
            fs_path = self.translate_path(self.path)
            if not Path(fs_path).is_file():
                self.path = "/index.html"
            super().do_GET()

        def do_HEAD(self):
            if self._should_proxy():
                self._proxy()
                return
            super().do_HEAD()

        def do_POST(self):
            self._proxy() if self._should_proxy() else self.send_error(501, "Unsupported method")

        def do_PUT(self):
            self._proxy() if self._should_proxy() else self.send_error(501, "Unsupported method")

        def do_DELETE(self):
            self._proxy() if self._should_proxy() else self.send_error(501, "Unsupported method")

        def do_PATCH(self):
            self._proxy() if self._should_proxy() else self.send_error(501, "Unsupported method")

        def do_OPTIONS(self):
            self._proxy() if self._should_proxy() else self.send_error(501, "Unsupported method")

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=["admin", "stage-web"])
    parser.add_argument("--dist", required=True, help="Path to the built dist/ directory to serve")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument(
        "--proxy-target", default="127.0.0.1:8094",
        help="admin role only: host:port of the admin backend to proxy /api and /health to",
    )
    args = parser.parse_args()

    dist_dir = Path(args.dist).resolve()
    if not dist_dir.is_dir():
        sys.exit(f"[portable_static_server] dist directory not found: {dist_dir}")

    proxy_target = args.proxy_target if args.role == "admin" else None
    handler_cls = make_handler(dist_dir, proxy_target)

    server = ThreadingHTTPServer(("0.0.0.0", args.port), handler_cls)
    proxy_note = f" (proxying /api,/health -> {proxy_target})" if proxy_target else ""
    print(f"[portable_static_server] role={args.role} serving {dist_dir} on :{args.port}{proxy_note}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
