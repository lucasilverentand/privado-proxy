#!/usr/bin/env python3

import html
import json
import os
import shlex
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs


def run(command, timeout=5):
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except Exception as error:
        return "", str(error), 1

    return result.stdout.strip(), result.stderr.strip(), result.returncode


def truthy(value):
    return str(value).lower() in {"1", "true", "yes", "on"}


def get_handshake():
    output, _, code = run(["wg", "show", "wg0", "latest-handshakes"])
    if code != 0 or not output:
        return None

    try:
        timestamp = int(output.split()[1])
    except (IndexError, ValueError):
        return None

    if timestamp <= 0:
        return None

    return {
        "timestamp": timestamp,
        "ageSeconds": max(int(time.time()) - timestamp, 0),
    }


def get_transfer():
    output, _, code = run(["wg", "show", "wg0", "transfer"])
    if code != 0 or not output:
        return None

    try:
        _, received, sent = output.split()[:3]
        return {"receivedBytes": int(received), "sentBytes": int(sent)}
    except (ValueError, IndexError):
        return None


def get_public_ip():
    output, _, code = run(["curl", "-sS", "--max-time", "5", "https://api.ipify.org"])
    if code == 0 and output:
        return output
    return "unknown"


def process_running(name):
    _, _, code = run(["pgrep", "-x", name], timeout=2)
    return code == 0


def get_status():
    handshake = get_handshake()
    transfer = get_transfer()
    wg_up = run(["ip", "link", "show", "wg0"], timeout=2)[2] == 0
    proxy_up = process_running("microsocks")
    handshake_fresh = bool(handshake and handshake["ageSeconds"] < 180)

    if wg_up and proxy_up and handshake_fresh:
        state = "healthy"
    elif wg_up or proxy_up:
        state = "degraded"
    else:
        state = "down"

    return {
        "state": state,
        "wireguardUp": wg_up,
        "proxyUp": proxy_up,
        "handshake": handshake,
        "transfer": transfer,
        "publicIp": get_public_ip(),
        "server": os.environ.get("PRIVADO_SERVER", ""),
        "credentialsConfigured": bool(
            os.environ.get("PRIVADO_USERNAME")
            and os.environ.get("PRIVADO_PASSWORD")
            and os.environ.get("PRIVADO_SERVER")
        ),
        "configFile": os.environ.get("CONFIG_FILE", "/config/privado.env"),
        "socksPort": os.environ.get("SOCK_PORT", "1080"),
        "dashboardEnabled": truthy(os.environ.get("DASHBOARD_ENABLED", "false")),
        "generatedAt": int(time.time()),
    }


def save_config(username, password, server):
    config_file = os.environ.get("CONFIG_FILE", "/config/privado.env")
    os.makedirs(os.path.dirname(config_file), exist_ok=True)
    content = "\n".join(
        [
            f"PRIVADO_USERNAME={shlex.quote(username)}",
            f"PRIVADO_PASSWORD={shlex.quote(password)}",
            f"PRIVADO_SERVER={shlex.quote(server)}",
            "",
        ]
    )
    with open(config_file, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(config_file, 0o600)


def restart_main():
    run(["supervisorctl", "restart", "main"], timeout=10)


def human_bytes(value):
    if value is None:
        return "Unknown"
    units = ["B", "KB", "MB", "GB", "TB"]
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return "Unknown"


def status_label(state):
    return {
        "healthy": "Connected",
        "degraded": "Needs attention",
        "down": "Offline",
    }.get(state, "Unknown")


def render_dashboard(status):
    handshake = status.get("handshake")
    transfer = status.get("transfer") or {}
    age = f"{handshake['ageSeconds']}s ago" if handshake else "No handshake"
    generated = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(status["generatedAt"]))
    state = html.escape(status_label(status["state"]))
    state_class = html.escape(status["state"])

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="30">
    <title>Privado Proxy Dashboard</title>
    <style>
      :root {{
        color-scheme: dark;
        --bg: #111418;
        --panel: #1b2027;
        --panel-2: #222a34;
        --text: #f5f7fb;
        --muted: #9aa8b8;
        --line: #313a46;
        --ok: #45c486;
        --warn: #f0b85d;
        --bad: #ef6b6b;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        min-height: 100vh;
        background: var(--bg);
        color: var(--text);
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      main {{
        width: min(980px, calc(100vw - 32px));
        margin: 0 auto;
        padding: 28px 0 40px;
      }}
      header {{
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 20px;
        align-items: end;
        border-bottom: 1px solid var(--line);
        padding-bottom: 22px;
      }}
      h1 {{
        margin: 0;
        font-size: clamp(30px, 5vw, 52px);
        line-height: 1.05;
        letter-spacing: 0;
      }}
      .subtitle {{
        max-width: 640px;
        margin: 10px 0 0;
        color: var(--muted);
        font-size: 15px;
        line-height: 1.55;
      }}
      .state {{
        min-width: 220px;
        padding: 16px;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: var(--panel);
      }}
      .label {{
        color: var(--muted);
        font-size: 12px;
        font-weight: 700;
        letter-spacing: .08em;
        text-transform: uppercase;
      }}
      .status {{
        display: flex;
        gap: 10px;
        align-items: center;
        margin-top: 10px;
        font-size: 20px;
        font-weight: 750;
      }}
      .dot {{
        width: 10px;
        height: 10px;
        border-radius: 999px;
        background: var(--warn);
      }}
      .healthy .dot {{ background: var(--ok); }}
      .down .dot {{ background: var(--bad); }}
      .grid {{
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 12px;
        margin-top: 24px;
      }}
      .metric {{
        min-height: 116px;
        padding: 16px;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: var(--panel);
      }}
      .metric strong {{
        display: block;
        margin-top: 10px;
        font-size: 22px;
        line-height: 1.2;
        overflow-wrap: anywhere;
      }}
      .metric span {{
        display: block;
        margin-top: 7px;
        color: var(--muted);
        font-size: 12px;
        line-height: 1.45;
      }}
      .section {{
        margin-top: 18px;
        padding: 16px;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: var(--panel);
      }}
      form {{
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr)) auto;
        gap: 12px;
        align-items: end;
        margin-top: 14px;
      }}
      input {{
        width: 100%;
        min-height: 42px;
        border: 1px solid var(--line);
        border-radius: 6px;
        background: var(--panel-2);
        color: var(--text);
        padding: 10px 12px;
        font: inherit;
      }}
      button {{
        min-height: 42px;
        border: 0;
        border-radius: 6px;
        background: #2f80ed;
        color: #fff;
        padding: 10px 14px;
        font: inherit;
        font-weight: 700;
        cursor: pointer;
      }}
      .rows {{
        display: grid;
        gap: 0;
      }}
      .row {{
        display: grid;
        grid-template-columns: minmax(120px, 220px) minmax(0, 1fr);
        gap: 16px;
        padding: 12px 0;
        border-top: 1px solid var(--line);
      }}
      .row:first-child {{ border-top: 0; }}
      code {{
        color: #d8e3f2;
        background: var(--panel-2);
        border-radius: 6px;
        padding: 2px 6px;
      }}
      footer {{
        margin-top: 18px;
        color: var(--muted);
        font-size: 12px;
      }}
      @media (max-width: 760px) {{
        header,
        .grid,
        form,
        .row {{
          grid-template-columns: 1fr;
        }}
        .state {{
          min-width: 0;
        }}
      }}
    </style>
  </head>
  <body>
    <main>
      <header>
        <div>
          <div class="label">Privado proxy</div>
          <h1>VPN Dashboard</h1>
          <p class="subtitle">Read-only tunnel and SOCKS5 proxy status. Data refreshes every 30 seconds and never includes credentials.</p>
        </div>
        <section class="state {state_class}" aria-label="Connection state">
          <div class="label">Current state</div>
          <div class="status"><span class="dot"></span><span>{state}</span></div>
        </section>
      </header>

      <section class="section">
        <div class="label">Setup</div>
        <p class="subtitle">{html.escape("Credentials are configured." if status["credentialsConfigured"] else "Enter Privado credentials to start the VPN tunnel. Values are stored inside the container config volume.")}</p>
        <form method="post" action="/setup">
          <label>
            <span class="label">Username</span>
            <input name="username" autocomplete="username" value="" required>
          </label>
          <label>
            <span class="label">Password</span>
            <input name="password" type="password" autocomplete="current-password" required>
          </label>
          <label>
            <span class="label">Server</span>
            <input name="server" value="{html.escape(status["server"])}" placeholder="de-frankfurt" required>
          </label>
          <button type="submit">Save</button>
        </form>
      </section>

      <section class="grid">
        <div class="metric"><div class="label">Exit IP</div><strong>{html.escape(status["publicIp"])}</strong><span>Fetched through the container route.</span></div>
        <div class="metric"><div class="label">Server</div><strong>{html.escape(status["server"] or "Unset")}</strong><span>Configured Privado location.</span></div>
        <div class="metric"><div class="label">Handshake</div><strong>{html.escape(age)}</strong><span>Fresh under 180 seconds.</span></div>
        <div class="metric"><div class="label">SOCKS5</div><strong>{html.escape("Running" if status["proxyUp"] else "Stopped")}</strong><span>Listening on port {html.escape(status["socksPort"])}.</span></div>
      </section>

      <section class="section">
        <div class="label">Tunnel detail</div>
        <div class="rows">
          <div class="row"><span>WireGuard interface</span><strong>{html.escape("Up" if status["wireguardUp"] else "Down")}</strong></div>
          <div class="row"><span>Received</span><strong>{html.escape(human_bytes(transfer.get("receivedBytes")))}</strong></div>
          <div class="row"><span>Sent</span><strong>{html.escape(human_bytes(transfer.get("sentBytes")))}</strong></div>
          <div class="row"><span>Status API</span><strong><code>/api/status</code></strong></div>
          <div class="row"><span>Config file</span><strong><code>{html.escape(status["configFile"])}</code></strong></div>
        </div>
      </section>

      <footer>Generated at {html.escape(generated)}. Enable with <code>DASHBOARD_ENABLED=true</code>; set <code>DASHBOARD_PORT</code> to change the listen port.</footer>
    </main>
  </body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def send_body(self, content_type, body, status=200):
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path == "/api/status":
            self.send_body("application/json", json.dumps(get_status(), indent=2))
            return

        if self.path in {"/", "/index.html"}:
            self.send_body("text/html; charset=utf-8", render_dashboard(get_status()))
            return

        self.send_body("text/plain; charset=utf-8", "not found\n", status=404)

    def do_POST(self):
        if self.path != "/setup":
            self.send_body("text/plain; charset=utf-8", "not found\n", status=404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        values = parse_qs(self.rfile.read(length).decode("utf-8"))
        username = values.get("username", [""])[0].strip()
        password = values.get("password", [""])[0]
        server = values.get("server", [""])[0].strip()

        if not username or not password or not server:
            self.send_body("text/plain; charset=utf-8", "username, password, and server are required\n", status=400)
            return

        save_config(username, password, server)
        restart_main()
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()


def main():
    port = int(os.environ.get("DASHBOARD_PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
