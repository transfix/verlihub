# Verlihub Docker Setup

This directory contains Docker configuration for running Verlihub with MySQL for development, testing, and production.

## Quick Start

### Running the Hub

```bash
# Build and start the hub with MySQL
docker compose up -d

# View logs
docker compose logs -f verlihub

# Stop
docker compose down
```

The hub will be available at:
- **DC++ Port**: `localhost:4111`
- **FastAPI REST API**: `http://localhost:30000`
- **Dashboard**: `http://localhost:30000/dashboard`

### Running Integration Tests

The integration tests verify the full stack including NMDC protocol, Python plugin, and FastAPI:

```bash
# Run full integration test suite
docker compose --profile integration up integration-tests

# Or run locally against a running hub
cd docker/tests
./run_integration_tests.sh
```

Integration tests will:
1. Connect to the hub using NMDC protocol
2. Authenticate as admin
3. Enable the Python plugin (`!onplugin python`)
4. Start the FastAPI server (`!api start 30000 <cors-origins>`)
5. Run API endpoint tests
6. Verify dashboard HTML serving

### Running Unit Tests

```bash
# Run C++ unit tests
docker compose --profile test up test-runner

# Or run specific tests
docker compose --profile test run test-runner bash -c "cd /src/build && ctest -R mysql"
```

### Development Mode

For development with live code changes:

```bash
# Start MySQL only
docker compose up -d mysql

# Connect your local build to Docker MySQL
export VH_DB_HOST=localhost
export VH_DB_PORT=3307  # Docker MySQL exposed port
./build/verlihub -d ./test-config
```

## Configuration

Environment variables for the `verlihub` service:

| Variable | Default | Description |
|----------|---------|-------------|
| `VH_DB_HOST` | mysql | MySQL hostname |
| `VH_DB_USER` | verlihub | MySQL username |
| `VH_DB_PASS` | verlihub | MySQL password |
| `VH_DB_NAME` | verlihub | MySQL database name |
| `VH_HUB_NAME` | Docker Test Hub | Hub display name |
| `VH_HUB_PORT` | 4111 | Hub listening port |
| `VERLIHUB_PYTHON_VENV` | /opt/verlihub-venv | Python venv path |

## Testing MySQL Connection Stability

To test MySQL connection handling (including the "MySQL server gone" fix):

```bash
# Start the test environment
docker compose up -d mysql verlihub

# Simulate MySQL restart (tests reconnection)
docker compose restart mysql

# Check verlihub logs for reconnection
docker compose logs verlihub | grep -i "mysql\|reconnect"

# Simulate connection timeout (tests keepalive)
docker compose exec mysql mysql -uroot -prootpass -e "SET GLOBAL wait_timeout=10; SET GLOBAL interactive_timeout=10;"

# Wait 15 seconds, then check if verlihub reconnected
sleep 15
docker compose exec verlihub mysql -h mysql -uverlihub -pverlihub -e "SELECT 1"
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Docker Network                        │
│                                                          │
│  ┌──────────────┐     ┌────────────────────────────┐   │
│  │    MySQL     │◄────│      Verlihub Server       │   │
│  │   :3306      │     │  :4111 (DC++)              │   │
│  └──────────────┘     │  :8000 (FastAPI)           │   │
│                       │                             │   │
│                       │  ┌────────────────────┐    │   │
│                       │  │  Python Plugin     │    │   │
│                       │  │  (hub_api.py)      │    │   │
│                       │  └────────────────────┘    │   │
│                       └────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

## Troubleshooting

### "MySQL server gone" Errors

This Docker setup includes fixes for MySQL connection timeout issues:

1. **Keepalive ping**: Server pings MySQL every 5 minutes to prevent idle timeouts
2. **Reconnect counter fix**: Successfully reconnecting now resets the retry counter
3. **Extended timeouts**: MySQL configured with 8-hour wait_timeout

If you still see connection issues:

```bash
# Check MySQL status
docker compose exec mysql mysql -uroot -prootpass -e "SHOW STATUS LIKE '%connect%';"

# Check verlihub MySQL errors
docker compose logs verlihub 2>&1 | grep -i mysql

# Restart with fresh state
docker compose down -v && docker compose up -d
```

### FastAPI Not Starting

1. Ensure the Python venv is properly set up:
   ```bash
   docker compose exec verlihub ls -la /opt/verlihub-venv/
   ```

2. Check if FastAPI dependencies are installed:
   ```bash
   docker compose exec verlihub /opt/verlihub-venv/bin/pip list | grep -i fastapi
   ```

3. Start the API manually:
   ```bash
   docker compose exec verlihub sh -c "cd /etc/verlihub && verlihub"
   # Then in hub: !api start
   ```

### Build Issues

```bash
# Clean rebuild
docker compose build --no-cache verlihub

# Check build logs
docker compose build verlihub 2>&1 | less
```

## Files

- `Dockerfile` - Multi-stage build (builder + runtime)
- `entrypoint.sh` - Container initialization script
- `mysql-init/01-init.sql` - MySQL database setup
- `../docker-compose.yml` - Main compose file
- `tests/` - Integration test suite

## Apache/Nginx Proxy Configuration

To expose the dashboard via Apache with SSL:

### Apache Configuration

```apache
<VirtualHost *:443>
    ServerName dashboard.yourhub.com
    
    SSLEngine on
    SSLCertificateFile /path/to/cert.pem
    SSLCertificateKeyFile /path/to/key.pem
    
    # Proxy all API endpoints
    ProxyPreserveHost On
    ProxyPass / http://localhost:30000/
    ProxyPassReverse / http://localhost:30000/
    
    # WebSocket support (if needed)
    RewriteEngine On
    RewriteCond %{HTTP:Upgrade} websocket [NC]
    RewriteCond %{HTTP:Connection} upgrade [NC]
    RewriteRule ^/?(.*) "ws://localhost:30000/$1" [P,L]
</VirtualHost>
```

### Nginx Configuration

```nginx
server {
    listen 443 ssl;
    server_name dashboard.yourhub.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    location / {
        proxy_pass http://localhost:30000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Starting the API for Proxy Use

When starting the API, include your domain in the CORS origins:

```
!api start 30000 https://dashboard.yourhub.com https://www.yourhub.com
```

This enables:
- Dashboard accessible at `https://dashboard.yourhub.com/dashboard`
- API endpoints at `https://dashboard.yourhub.com/hub`, `/users`, etc.
- Proper CORS headers for cross-origin requests
