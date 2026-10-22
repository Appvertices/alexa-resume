"""Run Node client-logic checks against an ephemeral loopback server (no browser)."""
from http.server import HTTPServer
from pathlib import Path
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import make_handler

server = HTTPServer(('127.0.0.1', 0), make_handler())
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    result = subprocess.run(['node', str(Path(__file__).with_suffix('.cjs')),
                             f'http://127.0.0.1:{server.server_port}'], timeout=20)
    sys.exit(result.returncode)
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
