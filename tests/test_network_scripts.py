import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]


def write_executable(path, contents):
    path.write_text(textwrap.dedent(contents), encoding="utf-8")
    path.chmod(0o755)


class NetworkCleanupTests(unittest.TestCase):
    def test_cleanup_recovers_gateway_from_stale_endpoint_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            command_log = root / "commands.log"

            write_executable(
                bin_dir / "ip",
                """
                #!/bin/bash
                echo "ip $*" >> "${COMMAND_LOG}"
                case "$*" in
                  "-4 route show default")
                    echo "default dev wg0 scope link"
                    ;;
                  "-4 route show table main 198.51.100.4/32")
                    echo "198.51.100.4 via 172.17.0.1 dev eth0"
                    ;;
                  "-o -4 addr show dev wg0")
                    echo "7: wg0 inet 10.0.0.2/32 scope global wg0"
                    ;;
                  "-4 route replace default via 172.17.0.1 dev eth0")
                    exit 0
                    ;;
                esac
                exit 1
                """,
            )
            write_executable(
                bin_dir / "wg",
                """
                #!/bin/bash
                echo "wg $*" >> "${COMMAND_LOG}"
                case "$*" in
                  "show wg0 endpoints") echo "peer 198.51.100.4:51820" ;;
                  "show wg0 fwmark") echo "51820" ;;
                esac
                """,
            )
            for command in ("wg-quick", "iptables"):
                write_executable(
                    bin_dir / command,
                    f"""
                    #!/bin/bash
                    echo "{command} $*" >> "${{COMMAND_LOG}}"
                    exit 1
                    """,
                )

            environment = os.environ.copy()
            environment.update(
                {
                    "COMMAND_LOG": str(command_log),
                    "DATA_DIR": str(root / "run"),
                    "PATH": f"{bin_dir}:{environment['PATH']}",
                    "WG_CONFIG": str(root / "wg0.conf"),
                }
            )
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    f'log() {{ :; }}; source "{REPOSITORY / "scripts/privado.sh"}"; '
                    "cleanup_wireguard_state",
                ],
                capture_output=True,
                check=False,
                env=environment,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            commands = command_log.read_text(encoding="utf-8")
            self.assertIn("wg-quick down wg0", commands)
            self.assertIn("ip -4 route flush table 51820", commands)
            self.assertIn(
                "ip -4 route replace default via 172.17.0.1 dev eth0",
                commands,
            )
            self.assertIn("ip -4 route del 198.51.100.4/32", commands)


class HealthcheckTests(unittest.TestCase):
    def run_healthcheck(self, curl_exit="0"):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        command_log = root / "commands.log"
        vars_file = root / "vars.sh"
        vars_file.write_text(
            "\n".join(
                [
                    "SOCK_PORT=1080",
                    "HEALTHCHECK_URL=https://api.ipify.org",
                    "HEALTHCHECK_TIMEOUT=20",
                    "PRIVADO_USERNAME=test-user",
                    "PRIVADO_PASSWORD=test-password",
                ]
            ),
            encoding="utf-8",
        )

        write_executable(bin_dir / "ip", "#!/bin/bash\nexit 0\n")
        write_executable(bin_dir / "pgrep", "#!/bin/bash\nexit 0\n")
        write_executable(
            bin_dir / "wg",
            """
            #!/bin/bash
            echo "peer $(date +%s)"
            """,
        )
        write_executable(
            bin_dir / "curl",
            """
            #!/bin/bash
            echo "curl $*" >> "${COMMAND_LOG}"
            exit "${CURL_EXIT}"
            """,
        )
        write_executable(
            bin_dir / "supervisorctl",
            """
            #!/bin/bash
            echo "supervisorctl $*" >> "${COMMAND_LOG}"
            if [[ "$*" == "status main" ]]; then
              echo "main EXITED"
            fi
            exit 0
            """,
        )

        environment = os.environ.copy()
        environment.update(
            {
                "COMMAND_LOG": str(command_log),
                "CURL_EXIT": curl_exit,
                "PATH": f"{bin_dir}:{environment['PATH']}",
                "VARS_FILE": str(vars_file),
            }
        )
        result = subprocess.run(
            ["bash", str(REPOSITORY / "scripts/healthcheck.sh")],
            capture_output=True,
            check=False,
            env=environment,
            text=True,
        )
        return result, command_log.read_text(encoding="utf-8")

    def test_healthcheck_proves_remote_dns_and_https_through_socks(self):
        result, commands = self.run_healthcheck()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SOCKS DNS and HTTPS succeeded", result.stdout)
        self.assertIn("--socks5-hostname 127.0.0.1:1080", commands)
        self.assertIn("https://api.ipify.org", commands)

    def test_failed_end_to_end_probe_requests_clean_reconnect(self):
        result, commands = self.run_healthcheck(curl_exit="7")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SOCKS hostname request failed", result.stdout)
        self.assertIn("supervisorctl status main", commands)
        self.assertIn("supervisorctl start main", commands)


class ImageTrustTests(unittest.TestCase):
    def test_docker_build_asserts_ca_bundle(self):
        dockerfile = (REPOSITORY / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("ca-certificates", dockerfile)
        self.assertIn("update-ca-certificates", dockerfile)
        self.assertIn("test -s /etc/ssl/certs/ca-certificates.crt", dockerfile)


if __name__ == "__main__":
    unittest.main()
