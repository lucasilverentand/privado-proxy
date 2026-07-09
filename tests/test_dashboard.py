import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "dashboard.py"
SPEC = importlib.util.spec_from_file_location("privado_dashboard", MODULE_PATH)
dashboard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dashboard)


def sample_status(**overrides):
    status = {
        "state": "setup",
        "wireguardUp": False,
        "proxyUp": False,
        "handshake": None,
        "transfer": None,
        "publicIp": "Unavailable",
        "server": "",
        "credentialsConfigured": False,
        "configFile": "/config/privado.env",
        "socksPort": "1080",
        "dashboardEnabled": True,
        "mainProcess": {
            "state": "running",
            "label": "Running",
            "detail": "pid 42, uptime 0:00:05",
        },
        "generatedAt": 1_788_800_000,
    }
    status.update(overrides)
    return status


class DashboardStatusTests(unittest.TestCase):
    def test_state_requires_setup_before_runtime_health(self):
        self.assertEqual(
            dashboard.derive_state(False, True, True, True, "running"),
            "setup",
        )

    def test_state_distinguishes_connecting_degraded_and_healthy(self):
        self.assertEqual(
            dashboard.derive_state(True, False, False, False, "running"),
            "connecting",
        )
        self.assertEqual(
            dashboard.derive_state(True, True, False, False, "running"),
            "degraded",
        )
        self.assertEqual(
            dashboard.derive_state(True, True, True, True, "running"),
            "healthy",
        )

    def test_persisted_config_counts_as_configured(self):
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "privado.env"
            config_file.write_text(
                "PRIVADO_USERNAME=user@example.com\nPRIVADO_PASSWORD='secret value'\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                self.assertTrue(dashboard.credentials_configured(str(config_file)))

    def test_save_config_uses_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "privado.env"
            with patch.dict(os.environ, {"CONFIG_FILE": str(config_file)}, clear=True):
                dashboard.save_config("user@example.com", "secret value")

            contents = config_file.read_text(encoding="utf-8")
            mode = stat.S_IMODE(config_file.stat().st_mode)
            self.assertIn("PRIVADO_USERNAME=user@example.com", contents)
            self.assertIn("PRIVADO_PASSWORD='secret value'", contents)
            self.assertEqual(mode, 0o600)


class DashboardRenderTests(unittest.TestCase):
    def test_setup_page_only_requires_login(self):
        rendered = dashboard.render_dashboard(sample_status())

        self.assertIn('name="username"', rendered)
        self.assertIn('name="password"', rendered)
        self.assertNotIn('name="server"', rendered)
        self.assertIn("Server selection is automatic", rendered)
        self.assertNotIn("http-equiv=\"refresh\"", rendered)

    def test_configured_page_exposes_restart_and_replace_actions(self):
        rendered = dashboard.render_dashboard(
            sample_status(
                state="connecting",
                credentialsConfigured=True,
            )
        )

        self.assertIn("Restart connection", rendered)
        self.assertIn("Replace saved login", rendered)
        self.assertIn('data-state="connecting"', rendered)

    def test_notice_is_rendered_as_status_feedback(self):
        rendered = dashboard.render_dashboard(
            sample_status(),
            notice="credentials",
        )

        self.assertIn('role="status"', rendered)
        self.assertIn("Login saved", rendered)


if __name__ == "__main__":
    unittest.main()
