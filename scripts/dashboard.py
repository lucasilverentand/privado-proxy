#!/usr/bin/env python3

import html
import json
import os
import shlex
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


REFRESH_SECONDS = 30


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
    return "Unavailable"


def process_running(name):
    _, _, code = run(["pgrep", "-x", name], timeout=2)
    return code == 0


def config_has_credentials(config_file):
    try:
        with open(config_file, "r", encoding="utf-8") as handle:
            values = {}
            for line in handle:
                key, separator, value = line.strip().partition("=")
                if separator and key in {"PRIVADO_USERNAME", "PRIVADO_PASSWORD"}:
                    values[key] = value.strip()
    except OSError:
        return False

    empty_values = {"", "''", '""'}
    required_keys = {"PRIVADO_USERNAME", "PRIVADO_PASSWORD"}
    return required_keys.issubset(values) and all(
        values[key] not in empty_values for key in required_keys
    )


def credentials_configured(config_file):
    configured_in_environment = bool(
        os.environ.get("PRIVADO_USERNAME") and os.environ.get("PRIVADO_PASSWORD")
    )
    return configured_in_environment or config_has_credentials(config_file)


def get_main_process():
    output, _, _ = run(["supervisorctl", "status", "main"], timeout=2)
    if not output:
        return {"state": "unknown", "label": "Unknown", "detail": "Status unavailable"}

    parts = output.split(None, 2)
    raw_state = parts[1].lower() if len(parts) > 1 else "unknown"
    labels = {
        "running": "Running",
        "starting": "Starting",
        "backoff": "Retrying",
        "stopped": "Stopped",
        "exited": "Exited",
        "fatal": "Failed",
    }
    return {
        "state": raw_state,
        "label": labels.get(raw_state, raw_state.replace("_", " ").title()),
        "detail": parts[2] if len(parts) > 2 else "",
    }


def derive_state(configured, wireguard_up, proxy_up, handshake_fresh, main_state):
    if not configured:
        return "setup"
    if wireguard_up and proxy_up and handshake_fresh:
        return "healthy"
    if wireguard_up or proxy_up:
        return "degraded"
    if main_state in {"running", "starting"}:
        return "connecting"
    return "down"


def get_status():
    config_file = os.environ.get("CONFIG_FILE", "/config/privado.env")
    configured = credentials_configured(config_file)
    handshake = get_handshake()
    transfer = get_transfer()
    wireguard_up = run(["ip", "link", "show", "wg0"], timeout=2)[2] == 0
    proxy_up = process_running("microsocks")
    handshake_fresh = bool(handshake and handshake["ageSeconds"] < 180)
    main_process = get_main_process()
    state = derive_state(
        configured,
        wireguard_up,
        proxy_up,
        handshake_fresh,
        main_process["state"],
    )

    return {
        "state": state,
        "wireguardUp": wireguard_up,
        "proxyUp": proxy_up,
        "handshake": handshake,
        "transfer": transfer,
        "publicIp": get_public_ip() if wireguard_up else "Unavailable",
        "server": os.environ.get("PRIVADO_SERVER", ""),
        "credentialsConfigured": configured,
        "configFile": config_file,
        "socksPort": os.environ.get("SOCK_PORT", "1080"),
        "dashboardEnabled": truthy(os.environ.get("DASHBOARD_ENABLED", "false")),
        "mainProcess": main_process,
        "generatedAt": int(time.time()),
    }


def save_config(username, password):
    config_file = os.environ.get("CONFIG_FILE", "/config/privado.env")
    config_directory = os.path.dirname(config_file)
    if config_directory:
        os.makedirs(config_directory, exist_ok=True)
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
    return run(["supervisorctl", "restart", "main"], timeout=10)


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
        "connecting": "Connecting",
        "setup": "Setup required",
        "down": "Offline",
    }.get(state, "Unknown")


def state_message(state):
    return {
        "healthy": "The WireGuard tunnel is active and the shared SOCKS5 proxy is ready for dependent apps.",
        "degraded": "Part of the route is available, but one or more health checks still need attention.",
        "connecting": "Your login is saved and the VPN process is establishing the private route.",
        "setup": "Add your Privado login to establish the VPN tunnel. Server selection is automatic.",
        "down": "The VPN process is not providing a private route. Check the saved login or restart the connection.",
    }.get(state, "Current connection state could not be determined.")


def health_state(is_healthy, is_pending=False):
    if is_healthy:
        return "healthy"
    return "pending" if is_pending else "down"


def render_credential_form(button_label):
    return f"""
      <form class="credential-form" method="post" action="/setup" data-preserve-input>
        <label for="username">Privado username</label>
        <input id="username" name="username" autocomplete="username" required aria-describedby="login-help">
        <label for="password">Privado password</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required aria-describedby="login-help">
        <p id="login-help" class="field-help">Saved in the app's private config volume and never displayed back on this page.</p>
        <button class="button button-primary" type="submit">{html.escape(button_label)}</button>
      </form>
    """


def render_setup_panel(status):
    if not status["credentialsConfigured"]:
        return f"""
          <section class="surface setup-surface" aria-labelledby="setup-title">
            <div class="section-heading">
              <p class="eyebrow">One-time setup</p>
              <h2 id="setup-title">Connect Privado</h2>
              <p>Enter your account login. The proxy selects an available server automatically.</p>
            </div>
            {render_credential_form("Save login and connect")}
          </section>
        """

    return f"""
      <section class="surface setup-surface" aria-labelledby="access-title">
        <div class="section-heading">
          <p class="eyebrow">Access</p>
          <h2 id="access-title">Privado login saved</h2>
          <p>Credentials are present in the private config volume. They are never rendered into the dashboard.</p>
        </div>
        <form method="post" action="/restart">
          <button class="button button-primary" type="submit">Restart connection</button>
        </form>
        <details class="credential-editor">
          <summary>Replace saved login</summary>
          {render_credential_form("Replace login and reconnect")}
        </details>
      </section>
    """


def render_notice(notice):
    notices = {
        "credentials": ("success", "Login saved. The VPN connection is restarting now."),
        "restart": ("success", "Connection restart requested. Health checks will update automatically."),
        "restart-error": ("error", "The VPN process could not be restarted. Check container logs for details."),
    }
    if notice not in notices:
        return ""

    kind, message = notices[notice]
    return f'<div class="notice notice-{kind}" role="status">{html.escape(message)}</div>'


def render_dashboard(status, notice=""):
    handshake = status.get("handshake")
    transfer = status.get("transfer") or {}
    handshake_age = f"{handshake['ageSeconds']}s ago" if handshake else "No handshake"
    handshake_fresh = bool(handshake and handshake["ageSeconds"] < 180)
    generated = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(status["generatedAt"]))
    state = html.escape(status_label(status["state"]))
    state_class = html.escape(status["state"])
    message = html.escape(state_message(status["state"]))
    server_display = status["server"] or "Automatic"
    proxy_state = "Ready" if status["proxyUp"] else "Stopped"
    tunnel_state = "Up" if status["wireguardUp"] else "Down"
    main_process = status.get("mainProcess") or {
        "state": "unknown",
        "label": "Unknown",
        "detail": "Status unavailable",
    }
    wireguard_health = health_state(status["wireguardUp"], status["state"] == "connecting")
    proxy_health = health_state(status["proxyUp"], status["state"] == "connecting")
    handshake_health = health_state(handshake_fresh, status["state"] == "connecting")
    exit_health = health_state(status["publicIp"] != "Unavailable", status["state"] == "connecting")

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Privado VPN</title>
    <style>
      :root {{
        color-scheme: dark;
        --bg: #10110f;
        --surface: #181a17;
        --surface-raised: #20231f;
        --text: #f4f6f1;
        --muted: #a9afa4;
        --faint: #777e74;
        --line: #343832;
        --accent: #7dd3a5;
        --accent-ink: #07150d;
        --info: #79a9e8;
        --warning: #e5ba63;
        --danger: #ef7772;
        --radius: 6px;
        --focus: #a9c7ff;
      }}
      * {{ box-sizing: border-box; }}
      html {{ background: var(--bg); }}
      body {{
        margin: 0;
        min-height: 100vh;
        background: var(--bg);
        color: var(--text);
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        font-size: 15px;
        line-height: 1.5;
        letter-spacing: 0;
      }}
      a {{ color: inherit; }}
      button, input {{ font: inherit; letter-spacing: 0; }}
      button, summary, a {{ -webkit-tap-highlight-color: transparent; }}
      :focus-visible {{ outline: 3px solid var(--focus); outline-offset: 2px; }}
      .page {{
        width: min(1120px, calc(100% - 32px));
        margin: 0 auto;
        padding: 0 0 40px;
      }}
      .topbar {{
        min-height: 68px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 20px;
        border-bottom: 1px solid var(--line);
      }}
      .identity {{ display: flex; align-items: center; gap: 12px; min-width: 0; }}
      .brand-mark {{
        width: 34px;
        height: 34px;
        display: grid;
        place-items: center;
        border: 1px solid #4b5149;
        border-radius: var(--radius);
        background: var(--surface-raised);
        color: var(--accent);
        font-weight: 800;
      }}
      .product-name {{ margin: 0; font-size: 15px; font-weight: 750; }}
      .scope {{ margin: 1px 0 0; color: var(--muted); font-size: 12px; }}
      .topbar-actions {{ display: flex; align-items: center; gap: 12px; }}
      .freshness {{ color: var(--muted); font-size: 12px; white-space: nowrap; }}
      .button {{
        min-height: 42px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border: 1px solid transparent;
        border-radius: var(--radius);
        padding: 9px 14px;
        text-decoration: none;
        font-weight: 750;
        cursor: pointer;
      }}
      .button-primary {{ background: var(--accent); color: var(--accent-ink); }}
      .button-primary:hover {{ background: #96dfba; }}
      .button-secondary {{ border-color: var(--line); background: transparent; color: var(--text); }}
      .button-secondary:hover {{ background: var(--surface-raised); }}
      .notice {{
        margin-top: 18px;
        border-left: 3px solid var(--info);
        background: var(--surface);
        padding: 12px 14px;
        color: var(--text);
      }}
      .notice-success {{ border-left-color: var(--accent); }}
      .notice-error {{ border-left-color: var(--danger); }}
      .status-band {{
        display: grid;
        grid-template-columns: minmax(0, 1.35fr) minmax(360px, .65fr);
        gap: 28px;
        align-items: center;
        padding: 34px 0 30px;
        border-bottom: 1px solid var(--line);
      }}
      .eyebrow {{
        margin: 0 0 7px;
        color: var(--muted);
        font-size: 12px;
        font-weight: 750;
        text-transform: uppercase;
        letter-spacing: 0;
      }}
      h1, h2, p {{ overflow-wrap: anywhere; }}
      h1 {{ margin: 0; font-size: 34px; line-height: 1.15; letter-spacing: 0; }}
      h2 {{ margin: 0; font-size: 18px; line-height: 1.3; letter-spacing: 0; }}
      .status-line {{ display: flex; align-items: center; gap: 12px; }}
      .status-dot {{
        width: 12px;
        height: 12px;
        flex: 0 0 12px;
        border-radius: 50%;
        background: var(--faint);
        box-shadow: 0 0 0 4px rgba(119, 126, 116, .16);
      }}
      .state-healthy .status-dot {{ background: var(--accent); box-shadow: 0 0 0 4px rgba(125, 211, 165, .15); }}
      .state-connecting .status-dot {{ background: var(--info); box-shadow: 0 0 0 4px rgba(121, 169, 232, .15); }}
      .state-setup .status-dot, .state-degraded .status-dot {{ background: var(--warning); box-shadow: 0 0 0 4px rgba(229, 186, 99, .15); }}
      .state-down .status-dot {{ background: var(--danger); box-shadow: 0 0 0 4px rgba(239, 119, 114, .15); }}
      .status-message {{ max-width: 650px; margin: 12px 0 0; color: var(--muted); font-size: 16px; }}
      .readiness {{ margin: 0; display: grid; grid-template-columns: 1fr 1fr; border: 1px solid var(--line); border-radius: var(--radius); }}
      .readiness div {{ min-width: 0; padding: 14px; border-top: 1px solid var(--line); }}
      .readiness div:nth-child(-n+2) {{ border-top: 0; }}
      .readiness div:nth-child(even) {{ border-left: 1px solid var(--line); }}
      .readiness dt {{ color: var(--muted); font-size: 12px; }}
      .readiness dd {{ margin: 4px 0 0; font-weight: 750; overflow-wrap: anywhere; }}
      .health-strip {{
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        border-bottom: 1px solid var(--line);
      }}
      .health-item {{ min-width: 0; padding: 18px 18px 18px 0; }}
      .health-item + .health-item {{ border-left: 1px solid var(--line); padding-left: 18px; }}
      .health-label {{ display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 12px; font-weight: 700; }}
      .mini-dot {{ width: 7px; height: 7px; flex: 0 0 7px; border-radius: 50%; background: var(--faint); }}
      [data-health="healthy"] .mini-dot {{ background: var(--accent); }}
      [data-health="pending"] .mini-dot {{ background: var(--warning); }}
      [data-health="down"] .mini-dot {{ background: var(--danger); }}
      .health-value {{ display: block; margin-top: 9px; font-size: 18px; font-weight: 750; overflow-wrap: anywhere; }}
      .health-help {{ display: block; margin-top: 3px; color: var(--faint); font-size: 12px; }}
      .workspace {{
        display: grid;
        grid-template-columns: minmax(0, 1.4fr) minmax(300px, .6fr);
        gap: 18px;
        margin-top: 18px;
        align-items: start;
      }}
      .surface {{ border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); }}
      .section-heading {{ padding: 18px; border-bottom: 1px solid var(--line); }}
      .section-heading p:last-child {{ margin: 7px 0 0; color: var(--muted); }}
      .detail-list {{ margin: 0; }}
      .detail-row {{
        display: grid;
        grid-template-columns: minmax(140px, .65fr) minmax(0, 1fr);
        gap: 18px;
        align-items: start;
        padding: 14px 18px;
        border-top: 1px solid var(--line);
      }}
      .detail-row:first-child {{ border-top: 0; }}
      .detail-row dt {{ color: var(--muted); }}
      .detail-row dd {{ margin: 0; font-weight: 700; overflow-wrap: anywhere; text-align: right; }}
      .detail-row small {{ display: block; margin-top: 2px; color: var(--faint); font-weight: 400; }}
      .setup-surface {{ overflow: hidden; }}
      .credential-form {{ display: grid; gap: 8px; padding: 18px; }}
      .credential-form label {{ margin-top: 4px; color: var(--muted); font-size: 13px; font-weight: 700; }}
      input {{
        width: 100%;
        min-height: 44px;
        border: 1px solid var(--line);
        border-radius: var(--radius);
        background: var(--surface-raised);
        color: var(--text);
        padding: 10px 12px;
      }}
      input:hover {{ border-color: #50564e; }}
      input:focus {{ border-color: var(--focus); }}
      .field-help {{ margin: 3px 0 8px; color: var(--faint); font-size: 12px; }}
      .setup-surface > form {{ padding: 18px; }}
      .setup-surface > form .button {{ width: 100%; }}
      .credential-editor {{ border-top: 1px solid var(--line); }}
      .credential-editor summary {{ min-height: 44px; padding: 13px 18px; cursor: pointer; color: var(--muted); font-weight: 700; }}
      .credential-editor[open] summary {{ border-bottom: 1px solid var(--line); color: var(--text); }}
      .runtime-details {{ margin-top: 18px; border-top: 1px solid var(--line); }}
      .runtime-details summary {{ min-height: 44px; padding: 13px 0; cursor: pointer; color: var(--muted); font-weight: 700; }}
      .runtime-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; padding: 4px 0 16px; }}
      .runtime-grid div {{ min-width: 0; }}
      .runtime-grid span {{ display: block; color: var(--faint); font-size: 12px; }}
      .runtime-grid strong, code {{ overflow-wrap: anywhere; }}
      code {{ color: #dce7d8; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 12px; }}
      footer {{ display: flex; justify-content: space-between; gap: 18px; margin-top: 20px; color: var(--faint); font-size: 12px; }}
      @media (max-width: 820px) {{
        .status-band, .workspace {{ grid-template-columns: 1fr; }}
        .status-band {{ gap: 20px; }}
        .health-strip {{ grid-template-columns: 1fr 1fr; }}
        .health-item:nth-child(3) {{ border-left: 0; padding-left: 0; }}
        .health-item:nth-child(n+3) {{ border-top: 1px solid var(--line); }}
      }}
      @media (max-width: 560px) {{
        .page {{ width: min(100% - 24px, 1120px); padding-bottom: 28px; }}
        .topbar {{ align-items: flex-start; padding: 14px 0; }}
        .topbar-actions {{ align-items: flex-end; flex-direction: column; gap: 6px; }}
        .freshness {{ white-space: normal; text-align: right; }}
        .button-secondary {{ min-height: 38px; padding: 7px 10px; }}
        .status-band {{ padding: 26px 0 24px; }}
        h1 {{ font-size: 28px; }}
        .status-message {{ font-size: 15px; }}
        .readiness {{ grid-template-columns: 1fr; }}
        .readiness div:nth-child(2) {{ border-top: 1px solid var(--line); }}
        .readiness div:nth-child(even) {{ border-left: 0; }}
        .health-strip {{ grid-template-columns: 1fr; }}
        .health-item, .health-item + .health-item, .health-item:nth-child(3) {{ padding: 14px 0; border-left: 0; border-top: 1px solid var(--line); }}
        .health-item:first-child {{ border-top: 0; }}
        .detail-row {{ grid-template-columns: 1fr; gap: 4px; }}
        .detail-row dd {{ text-align: left; }}
        .runtime-grid {{ grid-template-columns: 1fr; }}
        footer {{ flex-direction: column; gap: 5px; }}
      }}
      @media (prefers-reduced-motion: reduce) {{ * {{ scroll-behavior: auto !important; }} }}
    </style>
  </head>
  <body>
    <main class="page">
      <nav class="topbar" aria-label="Dashboard context">
        <div class="identity">
          <div class="brand-mark" aria-hidden="true">P</div>
          <div>
            <p class="product-name">Privado VPN</p>
            <p class="scope">Umbrel · Shared media proxy</p>
          </div>
        </div>
        <div class="topbar-actions">
          <span class="freshness">Updated {html.escape(generated)}</span>
          <a class="button button-secondary" href="/">Refresh status</a>
        </div>
      </nav>

      {render_notice(notice)}

      <section class="status-band state-{state_class}" data-state="{state_class}" aria-labelledby="connection-state" aria-live="polite">
        <div>
          <p class="eyebrow">Private route</p>
          <div class="status-line">
            <span class="status-dot" aria-hidden="true"></span>
            <h1 id="connection-state">{state}</h1>
          </div>
          <p class="status-message">{message}</p>
        </div>
        <dl class="readiness">
          <div><dt>Login</dt><dd>{html.escape("Saved" if status["credentialsConfigured"] else "Required")}</dd></div>
          <div><dt>Server</dt><dd>{html.escape(server_display)}</dd></div>
          <div><dt>Main process</dt><dd>{html.escape(main_process["label"])}</dd></div>
          <div><dt>Refresh</dt><dd>{REFRESH_SECONDS} seconds</dd></div>
        </dl>
      </section>

      <section class="health-strip" aria-label="Connection health">
        <div class="health-item" data-health="{wireguard_health}">
          <span class="health-label"><span class="mini-dot" aria-hidden="true"></span>WireGuard</span>
          <strong class="health-value">{html.escape(tunnel_state)}</strong>
          <span class="health-help">Tunnel interface</span>
        </div>
        <div class="health-item" data-health="{proxy_health}">
          <span class="health-label"><span class="mini-dot" aria-hidden="true"></span>SOCKS5</span>
          <strong class="health-value">{html.escape(proxy_state)}</strong>
          <span class="health-help">Port {html.escape(status["socksPort"])}</span>
        </div>
        <div class="health-item" data-health="{handshake_health}">
          <span class="health-label"><span class="mini-dot" aria-hidden="true"></span>Handshake</span>
          <strong class="health-value">{html.escape(handshake_age)}</strong>
          <span class="health-help">Healthy under 180 seconds</span>
        </div>
        <div class="health-item" data-health="{exit_health}">
          <span class="health-label"><span class="mini-dot" aria-hidden="true"></span>Exit IP</span>
          <strong class="health-value">{html.escape(status["publicIp"])}</strong>
          <span class="health-help">Observed through the tunnel</span>
        </div>
      </section>

      <div class="workspace">
        <section class="surface" aria-labelledby="details-title">
          <div class="section-heading">
            <p class="eyebrow">Connection</p>
            <h2 id="details-title">Tunnel details</h2>
            <p>Current runtime state for the route shared with download and indexer apps.</p>
          </div>
          <dl class="detail-list">
            <div class="detail-row"><dt>Server selection</dt><dd>{html.escape(server_display)}<small>No location is required.</small></dd></div>
            <div class="detail-row"><dt>Data received</dt><dd>{html.escape(human_bytes(transfer.get("receivedBytes")))}</dd></div>
            <div class="detail-row"><dt>Data sent</dt><dd>{html.escape(human_bytes(transfer.get("sentBytes")))}</dd></div>
            <div class="detail-row"><dt>VPN process</dt><dd>{html.escape(main_process["label"])}<small>{html.escape(main_process["detail"] or "No additional process detail")}</small></dd></div>
          </dl>
        </section>

        {render_setup_panel(status)}
      </div>

      <details class="runtime-details">
        <summary>Runtime information</summary>
        <div class="runtime-grid">
          <div><span>Status API</span><strong><a href="/api/status"><code>/api/status</code></a></strong></div>
          <div><span>Config file</span><strong><code>{html.escape(status["configFile"])}</code></strong></div>
          <div><span>Dashboard</span><strong>{html.escape("Enabled" if status["dashboardEnabled"] else "Disabled")}</strong></div>
          <div><span>Refresh policy</span><strong>Automatic when idle</strong></div>
        </div>
      </details>

      <footer>
        <span>Credentials remain private and are never included in status responses.</span>
        <span>Times shown in UTC.</span>
      </footer>
    </main>
    <script>
      (() => {{
        const forms = Array.from(document.querySelectorAll("form[data-preserve-input]"));
        let dirty = false;
        forms.forEach((form) => form.addEventListener("input", () => {{ dirty = true; }}));

        const refreshWhenIdle = () => {{
          const editing = forms.some((form) => form.contains(document.activeElement));
          if (dirty || editing || document.visibilityState !== "visible") {{
            window.setTimeout(refreshWhenIdle, 5000);
            return;
          }}
          window.location.reload();
        }};

        window.setTimeout(refreshWhenIdle, {REFRESH_SECONDS * 1000});
      }})();
    </script>
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
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def do_GET(self):
        request = urlparse(self.path)
        if request.path == "/api/status":
            self.send_body("application/json", json.dumps(get_status(), indent=2))
            return

        if request.path in {"/", "/index.html"}:
            query = parse_qs(request.query)
            notice = query.get("notice", [""])[0]
            self.send_body(
                "text/html; charset=utf-8",
                render_dashboard(get_status(), notice=notice),
            )
            return

        self.send_body("text/plain; charset=utf-8", "not found\n", status=404)

    def do_POST(self):
        request = urlparse(self.path)
        if request.path == "/restart":
            if not credentials_configured(os.environ.get("CONFIG_FILE", "/config/privado.env")):
                self.send_body(
                    "text/plain; charset=utf-8",
                    "save a Privado login before restarting\n",
                    status=409,
                )
                return
            _, _, code = restart_main()
            self.redirect("/?notice=restart" if code == 0 else "/?notice=restart-error")
            return

        if request.path != "/setup":
            self.send_body("text/plain; charset=utf-8", "not found\n", status=404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        if length > 16_384:
            self.send_body(
                "text/plain; charset=utf-8",
                "request body is too large\n",
                status=413,
            )
            return
        values = parse_qs(self.rfile.read(length).decode("utf-8"))
        username = values.get("username", [""])[0].strip()
        password = values.get("password", [""])[0]

        if not username or not password:
            self.send_body(
                "text/plain; charset=utf-8",
                "username and password are required\n",
                status=400,
            )
            return

        save_config(username, password)
        _, _, code = restart_main()
        self.redirect("/?notice=credentials" if code == 0 else "/?notice=restart-error")


def main():
    port = int(os.environ.get("DASHBOARD_PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
