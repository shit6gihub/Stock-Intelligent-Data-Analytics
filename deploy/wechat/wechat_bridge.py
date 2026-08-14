#!/usr/bin/env python3
"""WeChat bridge HTTP service.

Exposes endpoints on 127.0.0.1:8001 for the SIDA container (via 172.17.0.1):
  POST /start   - spawn `openclaw channels login` for a bind_id, return QR URL
  GET  /status  - report whether an account bind has completed (userId present)
  POST /send    - send a WeChat message via the openclaw gateway
  GET  /health  - liveness probe
"""

import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 8001
HOME = "/home/ubuntu"
# 内置容器化: 凭证目录走 OPENCLAW_HOME 环境变量(默认 ~/.openclaw), 支持挂载数据卷持久化
_OPENCLAW_HOME = os.environ.get("OPENCLAW_HOME") or os.path.join(HOME, ".openclaw")
ACCOUNTS_DIR = os.path.join(_OPENCLAW_HOME, "openclaw-weixin", "accounts")
LOG_DIR = "/tmp"

QR_RE = re.compile(r"https?://liteapp\.weixin\.qq\.com/q/[^\s\"']+")

OPENCLAW = shutil.which("openclaw") or "openclaw"


def read_json_body(handler):
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        length = 0
    if length <= 0:
        return None
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # ---- helpers -------------------------------------------------------
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    # ---- GET -----------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self.send_json({"status": "ok"})
            return
        if path == "/status":
            bind_id = (urllib.parse.parse_qs(parsed.query).get("bind") or [""])[0]
            if not bind_id:
                self.send_json({"error": "missing bind param"}, 400)
                return
            acct_file = os.path.join(ACCOUNTS_DIR, bind_id + ".json")
            if os.path.isfile(acct_file):
                user_id = ""
                try:
                    with open(acct_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    user_id = data.get("userId", "")
                except Exception:
                    pass
                self.send_json({
                    "status": "success",
                    "account_id": bind_id,
                    "user_id": user_id,
                })
            else:
                self.send_json({"status": "waiting", "account_id": bind_id})
            return
        self.send_json({"error": "not found"}, 404)

    # ---- POST ----------------------------------------------------------
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = read_json_body(self)
        if body is None:
            self.send_json({"error": "invalid or missing JSON body"}, 400)
            return

        if path == "/start":
            bind_id = str(body.get("bind_id") or "").strip()
            if not bind_id:
                self.send_json({"error": "missing bind_id"}, 400)
                return
            qr_url, err = start_login(bind_id)
            if qr_url:
                self.send_json({"qrcode_url": qr_url, "bind_id": bind_id})
            else:
                self.send_json({"error": err, "bind_id": bind_id})
            return

        if path == "/send":
            account_id = str(body.get("account_id") or "").strip()
            to = str(body.get("to") or "").strip()
            message = body.get("message")
            idem = str(body.get("idempotency_key") or "").strip()
            if not account_id or not to or message is None:
                self.send_json({"error": "missing account_id/to/message"}, 400)
                return
            ok, message_id, err = send_message(account_id, to, message, idem)
            if ok:
                self.send_json({"ok": True, "message_id": message_id})
            else:
                self.send_json({"ok": False, "error": err})
            return

        self.send_json({"error": "not found"}, 404)


def start_login(bind_id):
    """Launch the login process (kept alive, never waited) and poll its log
    for the WeChat QR code URL, up to 20 seconds."""
    log_path = os.path.join(LOG_DIR, "wechat_login_%s.log" % bind_id)
    try:
        os.remove(log_path)
    except OSError:
        pass
    try:
        logf = open(log_path, "w")
        proc = subprocess.Popen(
            [OPENCLAW, "channels", "login", "--channel", "openclaw-weixin",
             "--account", bind_id],
            cwd=HOME,
            stdout=logf,
            stderr=subprocess.STDOUT,
        )
    except Exception as e:
        return None, "failed to launch login: %s" % e
    # Popen is intentionally never waited: the login process stays alive in
    # the background to keep refreshing the QR code.
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    print("login process exited early; log tail:\n%s" % f.read()[-500:], flush=True)
            except OSError:
                pass
            return None, "timeout"
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            m = QR_RE.search(content)
            if m:
                return m.group(0), None
        except OSError:
            pass
        time.sleep(0.5)
    return None, "timeout"


def send_message(account_id, to, message, idem):
    params = json.dumps({
        "to": to,
        "channel": "openclaw-weixin",
        "accountId": account_id,
        "message": message,
        "idempotencyKey": idem,
    }, ensure_ascii=False)
    try:
        result = subprocess.run(
            [OPENCLAW, "gateway", "call", "send", "--params", params],
            cwd=HOME,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return False, None, "gateway call timed out after 30s"
    except Exception as e:
        return False, None, "failed to run gateway call: %s" % e

    out = (result.stdout or "").strip()
    if result.returncode != 0:
        err = (result.stderr or out or "").strip()
        return False, None, (err or "gateway call failed (rc=%d)" % result.returncode)[:500]

    # Try to extract a message id from the gateway JSON reply.
    message_id = out
    try:
        data = json.loads(out)
        if isinstance(data, dict):
            for key in ("messageId", "message_id", "id"):
                if data.get(key):
                    message_id = data[key]
                    break
    except ValueError:
        pass
    return True, message_id, None


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    print("wechat_bridge listening on %s:%d" % (HOST, PORT), flush=True)
    server.serve_forever()
