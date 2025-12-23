# Privado VPN + SOCKS5 Proxy

A Docker image that connects to Privado VPN using WireGuard and exposes a SOCKS5 proxy on port 1080. Includes automatic health checks to ensure the VPN connection stays active.

## Features

- Fast WireGuard-based VPN connection
- SOCKS5 proxy on port 1080
- Automatic reconnection on connection loss
- Docker secrets support for credentials
- Multi-platform support (amd64, arm64, arm/v7, arm/v6)

## Quick Start

```bash
docker run -d \
  --cap-add NET_ADMIN \
  -e PRIVADO_USERNAME="your_username" \
  -e PRIVADO_PASSWORD="your_password" \
  -e PRIVADO_SERVER="nl" \
  -p 1080:1080 \
  ghcr.io/lucasilverentand/privado-proxy
```

## Docker Compose

```yaml
version: "3.8"

services:
  privado:
    image: ghcr.io/lucasilverentand/privado-proxy
    container_name: privado-proxy
    restart: unless-stopped
    cap_add:
      - NET_ADMIN
    environment:
      - PRIVADO_USERNAME=your_username
      - PRIVADO_PASSWORD=your_password
      - PRIVADO_SERVER=nl
    ports:
      - 1080:1080
```

### With Docker Secrets

```yaml
version: "3.8"

services:
  privado:
    image: ghcr.io/lucasilverentand/privado-proxy
    container_name: privado-proxy
    restart: unless-stopped
    cap_add:
      - NET_ADMIN
    environment:
      - PRIVADO_SERVER=nl
    secrets:
      - privado_username
      - privado_password
    ports:
      - 1080:1080

secrets:
  privado_username:
    file: ./secrets/privado_username.txt
  privado_password:
    file: ./secrets/privado_password.txt
```

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `PRIVADO_USERNAME` | Your Privado VPN username | (required) |
| `PRIVADO_PASSWORD` | Your Privado VPN password | (required) |
| `PRIVADO_SERVER` | Server to connect to (country code or hostname) | (required) |
| `PRIVADO_USERNAME_FILE` | File path for username (Docker secrets) | `/run/secrets/privado_username` |
| `PRIVADO_PASSWORD_FILE` | File path for password (Docker secrets) | `/run/secrets/privado_password` |
| `TZ` | Container timezone | `UTC` |
| `SOCK_PORT` | SOCKS5 proxy port | `1080` |
| `DNS` | DNS servers to use | `193.110.81.0,185.253.5.0` |
| `DOCKER_NET` | Docker network subnet | auto-detected |
| `LOCAL_SUBNETS` | Local subnets to bypass VPN | `192.168.0.0/16,172.16.0.0/12,10.0.0.0/8` |

### Server Selection

You can specify the server using:
- **Country code**: `nl`, `us`, `de`, `uk`, etc.
- **Country name**: `netherlands`, `germany`
- **Country-city format**: `nl-ams`, `us-nyc`, `de-fra` (country code or name, followed by city code or name)
- **Server hostname**: `nl.privadovpn.com`

## Requirements

- Privado VPN Premium account
- Docker with `NET_ADMIN` capability
- WireGuard kernel module (included in most modern Linux kernels)

## Testing the Proxy

```bash
# Check your exit IP
curl --proxy socks5h://localhost:1080 https://api.ipify.org

# Test DNS resolution through proxy
curl --proxy socks5h://localhost:1080 https://ifconfig.me
```

## Troubleshooting

### Container exits immediately
Check logs with `docker logs <container>`. Common issues:
- Missing credentials (PRIVADO_USERNAME, PRIVADO_PASSWORD, PRIVADO_SERVER)
- Invalid Privado VPN credentials
- Server not found (check country code spelling)

### Connection drops
The health check automatically attempts to reconnect. If issues persist:
- Check Privado VPN service status
- Try a different server location
- Verify your Privado VPN subscription is active

### "Operation not permitted" errors
Ensure you have the required capabilities:
```bash
--cap-add NET_ADMIN
```

## License

MIT
