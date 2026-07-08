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
        ),
        "configFile": os.environ.get("CONFIG_FILE", "/config/privado.env"),
        "socksPort": os.environ.get("SOCK_PORT", "1080"),
        "dashboardEnabled": truthy(os.environ.get("DASHBOARD_ENABLED", "false")),
        "generatedAt": int(time.time()),
    }


def save_config(username, password):
    config_file = os.environ.get("CONFIG_FILE", "/config/privado.env")
    os.makedirs(os.path.dirname(config_file), exist_ok=True)
    content = "\n".join(
        [
            f"PRIVADO_USERNAME={shlex.quote(username)}",
            f"PRIVADO_PASSWORD={shlex.quote(password)}",
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
    credentials_message = (
        "Privado login is saved. Update it here when you rotate credentials."
        if status["credentialsConfigured"]
        else "Enter your Privado login to start the VPN tunnel. Server selection is automatic."
    )
    server_display = status["server"] or "Automatic"
    server_help = (
        "Explicit location from environment or config."
        if status["server"]
        else "The first available non-maintenance Privado server is selected at connection time."
    )
    proxy_state = "Running" if status["proxyUp"] else "Stopped"
    tunnel_state = "Up" if status["wireguardUp"] else "Down"

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
        --bg: #0e1116;
        --panel: #171c23;
        --panel-2: #202732;
        --text: #f7f9fc;
        --muted: #a7b1bf;
        --line: #303946;
        --ok: #4fd18b;
        --warn: #f2bd63;
        --bad: #f17373;
        --accent: #6aa4ff;
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
        width: min(1080px, calc(100vw - 32px));
        margin: 0 auto;
        padding: 24px 0 36px;
      }}
      header {{
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(260px, 340px);
        gap: 16px;
        align-items: stretch;
        border-bottom: 1px solid var(--line);
        padding-bottom: 18px;
      }}
      h1 {{
        margin: 0;
        font-size: clamp(28px, 4vw, 44px);
        line-height: 1.08;
        letter-spacing: 0;
      }}
      .subtitle {{
        max-width: 640px;
        margin: 9px 0 0;
        color: var(--muted);
        font-size: 15px;
        line-height: 1.55;
      }}
      .state {{
        padding: 18px;
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
      .scope {{
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 12px;
        margin-top: 14px;
      }}
      .scope-item {{
        border-top: 1px solid var(--line);
        padding-top: 12px;
      }}
      .scope-item strong {{
        display: block;
        margin-top: 5px;
        font-size: 15px;
        overflow-wrap: anywhere;
      }}
      .content {{
        display: grid;
        grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
        gap: 16px;
        margin-top: 18px;
        align-items: start;
      }}
      .grid {{
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 12px;
      }}
      .metric {{
        min-height: 112px;
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
        padding: 16px;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: var(--panel);
      }}
      .details {{
        margin-top: 16px;
      }}
      form {{
        display: grid;
        gap: 12px;
        margin-top: 16px;
      }}
      label span {{
        display: block;
        margin-bottom: 7px;
      }}
      input {{
        width: 100%;
        min-height: 44px;
        border: 1px solid var(--line);
        border-radius: 6px;
        background: var(--panel-2);
        color: var(--text);
        padding: 10px 12px;
        font: inherit;
      }}
      button {{
        min-height: 44px;
        border: 0;
        border-radius: 6px;
        background: var(--accent);
        color: #061223;
        padding: 10px 14px;
        font: inherit;
        font-weight: 750;
        cursor: pointer;
      }}
      .note {{
        margin-top: 12px;
        padding: 12px;
        border: 1px solid var(--line);
        border-radius: 6px;
        background: rgba(106, 164, 255, .08);
        color: var(--muted);
        font-size: 13px;
        line-height: 1.45;
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
      @media (max-width: 860px) {{
        header,
        .content,
        .grid,
        .row {{
          grid-template-columns: 1fr;
        }}
        .scope {{ grid-template-columns: 1fr; }}
      }}
    </style>
  </head>
  <body>
    <main>
      <header>
        <div>
          <div class="label">Privado proxy</div>
          <h1>VPN Dashboard</h1>
          <p class="subtitle">Sign in once, then use this page to confirm the WireGuard tunnel and shared SOCKS5 proxy are ready for dependent apps.</p>
        </div>
        <section class="state {state_class}" aria-label="Connection state">
          <div class="label">Current state</div>
          <div class="status"><span class="dot"></span><span>{state}</span></div>
          <div class="scope">
            <div class="scope-item"><div class="label">Login</div><strong>{html.escape("Saved" if status["credentialsConfigured"] else "Required")}</strong></div>
            <div class="scope-item"><div class="label">Server</div><strong>{html.escape(server_display)}</strong></div>
            <div class="scope-item"><div class="label">Refresh</div><strong>30s</strong></div>
          </div>
        </section>
      </header>

      <div class="content">
        <section class="section">
          <div class="label">Privado login</div>
          <p class="subtitle">{html.escape(credentials_message)}</p>
          <form method="post" action="/setup">
            <label>
              <span class="label">Username</span>
              <input name="username" autocomplete="username" value="" required>
            </label>
            <label>
              <span class="label">Password</span>
              <input name="password" type="password" autocomplete="current-password" required>
            </label>
            <button type="submit">Save login</button>
          </form>
          <div class="note">No server is required. {html.escape(server_help)}</div>
        </section>

        <div class="grid">
          <div class="metric"><div class="label">Exit IP</div><strong>{html.escape(status["publicIp"])}</strong><span>Fetched through the container route.</span></div>
          <div class="metric"><div class="label">Handshake</div><strong>{html.escape(age)}</strong><span>Fresh under 180 seconds.</span></div>
          <div class="metric"><div class="label">SOCKS5</div><strong>{html.escape(proxy_state)}</strong><span>Listening on port {html.escape(status["socksPort"])}.</span></div>
          <div class="metric"><div class="label">WireGuard</div><strong>{html.escape(tunnel_state)}</strong><span>Interface state inside the container.</span></div>
        </div>
      </div>

      <section class="section details">
        <div class="label">Tunnel detail</div>
        <div class="rows">
          <div class="row"><span>Server selection</span><strong>{html.escape(server_display)}</strong></div>
          <div class="row"><span>Received</span><strong>{html.escape(human_bytes(transfer.get("receivedBytes")))}</strong></div>
          <div class="row"><span>Sent</span><strong>{html.escape(human_bytes(transfer.get("sentBytes")))}</strong></div>
          <div class="row"><span>Status API</span><strong><code>/api/status</code></strong></div>
          <div class="row"><span>Config file</span><strong><code>{html.escape(status["configFile"])}</code></strong></div>
        </div>
      </section>

      <footer>Generated at {html.escape(generated)}. Credentials are stored in the app config volume and are never rendered back into the page.</footer>
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

        if not username or not password:
            self.send_body("text/plain; charset=utf-8", "username and password are required\n", status=400)
            return

        save_config(username, password)
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
