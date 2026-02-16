# Verlihub-py Quick Start Guide

Verlihub-py is the Python-based management server for Verlihub DC++ hubs.
It provides a REST API, web dashboard, and Python scripting capabilities.

## Installation

```bash
# From PyPI (when published)
pip install verlihub-py

# From source
cd python
pip install -e .
```

## Running with Defaults

Verlihub-py can run with no configuration - it uses sensible defaults:

```bash
# Start with defaults (SQLite in current directory)
verlihub-server

# Or with Python module syntax
python -m verlihub.server
```

**Default behavior:**
- Uses current working directory as config directory
- Creates `verlihub.db` SQLite database in config directory
- Runs API server on `127.0.0.1:8000`
- Runs in development mode (API only, no hub)

## Configuration

### Command Line Options

```bash
verlihub-server [OPTIONS]

Options:
  -c, --config FILE      Path to YAML configuration file
  --config-dir DIR       Directory containing config.yml and hub data
  --env ENVIRONMENT      Environment mode: development, qa, production
  --mode MODE            Run mode: api, hub, both
  --host HOST            API server host (overrides config)
  --port PORT            API server port (overrides config)
  --hub-port PORT        Hub NMDC port (overrides config)
  --workers N            Number of worker processes (production only)
  --reload               Enable auto-reload (development only)
  -v, --verbose          Increase verbosity (use -vv for debug)
  -q, --quiet            Suppress banner output
  --validate             Validate configuration and exit
  --version              Show version and exit
```

### Configuration Search Order

1. Explicit `--config` file path
2. `config.yml` or `verlihub.yml` in `--config-dir` (or current directory)
3. `~/.verlihub/config.yml`
4. `/etc/verlihub/config.yml`
5. Environment variables only (defaults to SQLite)

### Environment Variables

| Variable | Description |
|----------|-------------|
| `VH_CONFIG_FILE` | Path to YAML config file |
| `VH_CONFIG_DIR` | Config directory path |
| `VH_DB_TYPE` | Database type: sqlite, mysql, postgresql |
| `VH_DB_URL` | Direct database URL (async driver required) |
| `VH_DB_PATH` | SQLite database file path |
| `VH_API_HOST` | API server bind host |
| `VH_API_PORT` | API server port |
| `VH_API_USERNAME` | API admin username |
| `VH_API_PASSWORD` | API admin password |
| `VH_ENV` | Environment: development, qa, production |
| `VH_MODE` | Run mode: api, hub, both |

## Example Configurations

### Minimal SQLite (config.yml)

```yaml
# Uses SQLite database, default settings
environment: development
mode: api

database:
  type: sqlite
  # path: optional, defaults to config_dir/verlihub.db

api:
  host: "127.0.0.1"
  port: 8000
  username: admin
  password: changeme
```

### MySQL Production

```yaml
environment: production
mode: api

database:
  type: mysql
  host: localhost
  port: 3306
  name: verlihub
  user: verlihub
  password: secret

api:
  host: "0.0.0.0"
  port: 8000
  username: admin
  password: strongpassword
  secret: your-jwt-secret-here
  secure_cookies: true
  cors_origins:
    - "https://your-domain.com"
```

### PostgreSQL

```yaml
environment: production
mode: api

database:
  type: postgresql
  host: localhost
  port: 5432
  name: verlihub
  user: verlihub
  password: secret

api:
  host: "0.0.0.0"
  port: 8000
```

### Direct URL (Advanced)

```yaml
database:
  # Must use async-compatible driver
  url: "mysql+asyncmy://user:pass@host:3306/dbname"
```

## Database Backends

### SQLite (Default)

Best for development and small deployments:
- No external database server required
- Single file storage
- Uses `aiosqlite` async driver

```bash
# Default: uses verlihub.db in current directory
verlihub-server

# Explicit path
VH_DB_TYPE=sqlite VH_DB_PATH=/data/mydb.sqlite verlihub-server
```

### MySQL

Production-ready with replication support:
- Requires MySQL 8.0+
- Uses `asyncmy` async driver

```bash
VH_DB_TYPE=mysql \
  VERLIHUB_DB_HOST=localhost \
  VERLIHUB_DB_USER=verlihub \
  VERLIHUB_DB_PASSWORD=secret \
  VERLIHUB_DB_NAME=verlihub \
  verlihub-server
```

### PostgreSQL

Enterprise-grade reliability:
- Requires PostgreSQL 13+
- Uses `asyncpg` async driver

```bash
VH_DB_TYPE=postgresql \
  VERLIHUB_DB_HOST=localhost \
  VERLIHUB_DB_PORT=5432 \
  VERLIHUB_DB_USER=verlihub \
  VERLIHUB_DB_PASSWORD=secret \
  VERLIHUB_DB_NAME=verlihub \
  verlihub-server
```

## Docker Usage

### Quick Start

```bash
# Run with defaults (SQLite)
docker run -p 8000:8000 verlihub/verlihub-py

# With MySQL
docker run -p 8000:8000 \
  -e VH_DB_TYPE=mysql \
  -e VERLIHUB_DB_HOST=host.docker.internal \
  -e VERLIHUB_DB_USER=verlihub \
  -e VERLIHUB_DB_PASSWORD=secret \
  verlihub/verlihub-py
```

### Docker Compose

```yaml
version: "3.8"

services:
  verlihub:
    image: verlihub/verlihub-py
    ports:
      - "8000:8000"
    environment:
      VH_DB_TYPE: sqlite
      VH_API_USERNAME: admin
      VH_API_PASSWORD: changeme
    volumes:
      - data:/data

volumes:
  data:
```

## API Access

Once running, access:
- **Dashboard**: http://localhost:8000/dashboard/
- **API Docs**: http://localhost:8000/docs
- **Health Check**: http://localhost:8000/api/health

### Authentication

```bash
# Get auth token
curl -X POST http://localhost:8000/api/auth/login \
  -d "username=admin&password=changeme"

# Use token
curl http://localhost:8000/api/hub/info \
  -H "Authorization: Bearer <token>"
```

## Troubleshooting

### Server won't start

1. Check if port is already in use: `lsof -i :8000`
2. Verify database connectivity
3. Check file permissions for SQLite path

### Database connection errors

1. Verify database server is running
2. Check credentials in config/environment
3. Ensure async driver is installed:
   ```bash
   pip install aiosqlite asyncmy asyncpg
   ```

### Authentication failures

1. Verify username/password in config
2. Check JWT secret is set (for production)
3. Ensure cookies are enabled in browser (for dashboard)

## See Also

- [TESTING.md](TESTING.md) - Testing guide
- [config.example.yml](../python/config.example.yml) - Full configuration example
