# Verlihub Python API

A FastAPI-based REST API and web dashboard for managing Verlihub DC hubs.

> **Fork notice:** `verlihub-py` is a **modified fork** of
> [Verlihub](https://github.com/verlihub/verlihub), maintained at
> [github.com/transfix/verlihub](https://github.com/transfix/verlihub). It is not the
> original project. Licensed GPL-3.0-or-later; all upstream copyrights are preserved.

## Features

- **REST API** - Full hub management via HTTP endpoints
- **Web Dashboard** - Browser-based administration UI
- **WebSocket** - Real-time hub events and log streaming
- **CLI Tools** - Command-line management utilities
- **Performance Benchmarks** - Load testing and performance analysis
- **Remote Client** - Python client library for hub administration

## Quick Start

### Installation

```bash
cd python
pip install -e ".[dev]"
```

### Running the API Server

```bash
# Start the API server
verlihub-api

# Or with custom settings
verlihub-api --host 0.0.0.0 --port 8000
```

### Using the CLI

```bash
# Check hub status
verlihub-cli status

# List online users
verlihub-cli users

# Execute a hub command
verlihub-cli command "!help"

# Login and save token
verlihub-cli login admin password123
```

## API Endpoints

### Public Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /api/v1/hub/stats` | Hub statistics |
| `GET /api/v1/hub/info` | Hub information |

### Authenticated Endpoints

| Endpoint | Min Class | Description |
|----------|-----------|-------------|
| `GET /api/v1/users/online` | 1 | List online users |
| `GET /api/v1/users/registered` | 3 | List registered users |
| `POST /api/v1/users/{nick}/kick` | 3 | Kick a user |
| `GET /api/v1/bans/` | 3 | List bans |
| `POST /api/v1/bans/` | 4 | Create a ban |
| `POST /api/v1/console/execute` | 3 | Execute hub command |
| `GET /api/v1/config/` | 10 | Get configuration |

### WebSocket Endpoints

| Endpoint | Description |
|----------|-------------|
| `/ws/hub` | Real-time hub events (joins, parts, chat) |
| `/ws/logs` | Real-time log streaming |

## Web Dashboard

Access the dashboard at `http://localhost:8000/dashboard/`

### Pages

- **Dashboard** - Hub overview with stats and quick actions
- **Users** - Online and registered user management
- **Bans** - Ban list and management
- **Logs** - Real-time log viewer
- **Console** - Command execution interface
- **Config** - Hub configuration (Master only)
- **Plugins** - Plugin management (Admin only)

## Performance Benchmarks

The benchmark suite measures API performance including latency percentiles and throughput.

### Running Benchmarks

```bash
# Quick health check
verlihub-bench quick

# Full API benchmark suite
verlihub-bench api --requests 1000 --concurrency 20

# Stress test
verlihub-bench stress --requests 5000 --concurrency 50

# Detailed latency profile
verlihub-bench latency --endpoint /api/v1/hub/stats --samples 1000

# Full benchmark with all tests
verlihub-bench full --stress --websocket --output results.json
```

### Benchmark Options

```
--url, -u           API base URL (default: http://localhost:8000)
--requests, -n      Requests per benchmark (default: 100)
--concurrency, -c   Concurrent connections (default: 10)
--output, -o        Output file for results (JSON)
--token, -t         Authentication token
--username          Username for auth
--password          Password for auth
```

### Sample Output

```
======================================================================
Benchmark: GET /api/v1/hub/stats
======================================================================

Summary:
  Total Requests:    1,000
  Successful:        1,000 (100.0%)
  Failed:            0
  Total Time:        2.34s
  Throughput:        427.35 req/s

Latency (ms):
  Min:               1.234
  Max:               45.678
  Avg:               2.341
  Std Dev:           1.567
  p50 (median):      2.123
  p95:               4.567
  p99:               8.901
======================================================================
```

### Programmatic Usage

```python
import asyncio
from verlihub.benchmarks import BenchmarkRunner, BenchmarkSuite

async def run_benchmarks():
    # Create a custom benchmark suite
    suite = BenchmarkSuite("My Benchmarks")
    suite.add_endpoint("Health", "GET", "/health", num_requests=500)
    suite.add_endpoint("Stats", "GET", "/api/v1/hub/stats", num_requests=500)
    
    # Run benchmarks
    results = await suite.run(base_url="http://localhost:8000")
    
    # Print summary
    print(suite.summary())
    
    # Export to JSON
    with open("results.json", "w") as f:
        f.write(suite.to_json())

asyncio.run(run_benchmarks())
```

## Client Library

### Synchronous Client

```python
from verlihub.client import HubClient

client = HubClient("http://localhost:8000")
client.login("admin", "password")

# Get hub stats
stats = client.get_stats()
print(f"Users online: {stats['user_count']}")

# List online users
users = client.get_online_users()
for user in users:
    print(f"  {user['nick']} - {user['share']}")

# Kick a user
client.kick_user("baduser", reason="Spamming")
```

### Asynchronous Client

```python
import asyncio
from verlihub.client import AsyncHubClient

async def main():
    async with AsyncHubClient("http://localhost:8000") as client:
        await client.login("admin", "password")
        stats = await client.get_stats()
        print(stats)

asyncio.run(main())
```

### NMDC Protocol Client

```python
from verlihub.client import NMDCClient

# Connect directly to hub via NMDC protocol
client = NMDCClient("192.168.1.100", 4111)
client.connect("BotNick", "password")

# Send a message
client.send_chat("Hello from Python!")

# Get user list
users = client.get_users()

client.disconnect()
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `VH_API_HOST` | API bind host | `0.0.0.0` |
| `VH_API_PORT` | API bind port | `8000` |
| `VH_DB_HOST` | Database host | `localhost` |
| `VH_DB_PORT` | Database port | `3306` |
| `VH_DB_NAME` | Database name | `verlihub` |
| `VH_DB_USER` | Database user | `verlihub` |
| `VH_DB_PASS` | Database password | - |
| `VH_JWT_SECRET` | JWT signing key | (auto-generated) |
| `VH_JWT_EXPIRE_MINUTES` | Token expiration | `60` |

### Database

The API supports both MySQL and SQLite:

```python
# MySQL (production)
from verlihub.models.database import DatabaseConfig

config = DatabaseConfig(
    driver="mysql",
    host="localhost",
    port=3306,
    database="verlihub",
    username="verlihub",
    password="secret",
)

# SQLite (testing)
config = DatabaseConfig(
    driver="sqlite",
    database=":memory:",  # or path to file
)
```

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=verlihub --cov-report=html

# Run specific test file
pytest tests/test_api_endpoints.py -v

# Run benchmark tests
pytest tests/test_benchmarks.py -v
```

### Test Results

Current test status: **188 passed, 21 skipped**

Skipped tests require C++ SWIG bindings to be built.

## Development

### Project Structure

```
python/
├── verlihub/
│   ├── api/           # FastAPI routes and app
│   ├── dashboard/     # Web dashboard templates
│   ├── client/        # Remote client library
│   ├── models/        # SQLModel database models
│   ├── benchmarks/    # Performance testing
│   └── cli.py         # Command-line interface
├── tests/             # Test suite
└── pyproject.toml     # Project configuration
```

### Adding New Endpoints

1. Create route in `verlihub/api/routes/`
2. Add to router in `verlihub/api/__init__.py`
3. Add tests in `tests/test_api_endpoints.py`
4. Update API documentation

### Adding Dashboard Pages

1. Create template in `verlihub/dashboard/templates/`
2. Add route in `verlihub/dashboard/routes.py`
3. Add navigation link in `base.html`
4. Add tests in `tests/test_dashboard.py`

## License

GPL-3.0-or-later - See [LICENSE](../License.md) for details.
