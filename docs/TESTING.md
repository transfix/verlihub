# Verlihub Testing Guide

This document describes the testing infrastructure and how to run tests at different levels of integration.

## Test Environment Variables

### Core Test Markers

| Variable | Values | Description |
|----------|--------|-------------|
| `VH_INTEGRATION_TESTS` | `1` | Enable integration tests that require a built hub (no running instance needed) |
| `VH_FULL_INTEGRATION` | `1` | Enable full integration tests that require a running hub instance |
| `VH_DB_BACKEND` | `sqlite`, `mysql`, `postgresql` | Database backend to test against |
| `VH_HUB_HOST` | hostname/IP | Hub hostname for remote testing |
| `VH_HUB_PORT` | port number | Hub port (default: 4111) |
| `VH_API_PORT` | port number | API port for REST tests (default: 30000) |

### Database Connection Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VH_MYSQL_HOST` | `localhost` | MySQL server hostname |
| `VH_MYSQL_PORT` | `3306` | MySQL server port |
| `VH_MYSQL_USER` | `verlihub` | MySQL username |
| `VH_MYSQL_PASSWORD` | `verlihub` | MySQL password |
| `VH_MYSQL_DATABASE` | `verlihub` | MySQL database name |
| `VH_POSTGRES_HOST` | `localhost` | PostgreSQL server hostname |
| `VH_POSTGRES_PORT` | `5432` | PostgreSQL server port |
| `VH_POSTGRES_USER` | `verlihub` | PostgreSQL username |
| `VH_POSTGRES_PASSWORD` | `verlihub` | PostgreSQL password |
| `VH_POSTGRES_DATABASE` | `verlihub` | PostgreSQL database name |

## Test Categories

### 1. Unit Tests (No special environment)

```bash
cd python
pytest tests/test_verlihub_core.py tests/test_client.py tests/test_auth.py -v
```

These tests:
- Use SQLite in-memory database
- Don't require a running hub
- Test Python bindings and data models

### 2. Integration Tests (`VH_INTEGRATION_TESTS=1`)

```bash
VH_INTEGRATION_TESTS=1 pytest tests/test_plugins.py tests/test_lua_plugin.py tests/test_python_plugin.py -v
```

These tests:
- Require built verlihub with plugins
- Create temporary hub contexts
- Test plugin loading/unloading
- Don't require a running hub instance

### 3. Full Integration Tests (`VH_FULL_INTEGRATION=1`)

```bash
VH_FULL_INTEGRATION=1 pytest tests/ -v
```

These tests:
- Require a running hub instance
- Test full lifecycle operations
- Include protocol tests with real connections

## Running Tests

### Quick Test (Unit Tests Only)

```bash
cd /path/to/verlihub
PYTHONPATH=build/python pytest python/tests/test_verlihub_core.py -v
```

### Full Test Suite with SQLite

```bash
cd /path/to/verlihub
VH_DB_BACKEND=sqlite VH_INTEGRATION_TESTS=1 \
    PYTHONPATH=build/python pytest python/tests/ -v --ignore=python/tests/test_docker_*.py
```

### Docker-Based Testing

#### MySQL Backend

```bash
docker compose -f docker/docker-compose.test.yml up --build mysql-tests
```

#### PostgreSQL Backend

```bash
docker compose -f docker/docker-compose.test.yml up --build postgres-tests
```

#### Multi-Database Testing

```bash
docker compose -f docker/docker-compose.test.yml up --build all-db-tests
```

#### Dual-Build Testing (Original + verlihub-py)

```bash
docker compose -f docker/docker-compose.dual-test.yml up --build
```

#### Smoke Tests (Server Startup with Different Databases)

The smoke test suite verifies verlihub-py can start and serve requests with different
database backends. This tests the full server startup path including:
- Default startup (no config file, uses SQLite in current directory)
- SQLite in-memory configuration
- SQLite file-based configuration  
- MySQL configuration
- PostgreSQL configuration

```bash
# Run all smoke tests
docker compose -f docker/docker-compose.smoke-tests.yml up --build

# Run specific backend only
docker compose -f docker/docker-compose.smoke-tests.yml up --build sqlite-memory-test
docker compose -f docker/docker-compose.smoke-tests.yml up --build mysql-test
docker compose -f docker/docker-compose.smoke-tests.yml up --build postgres-test

# Test default startup (no config file)
docker compose -f docker/docker-compose.smoke-tests.yml up --build default-startup-test
```

The smoke tests verify:
- Server starts successfully
- Health endpoint responds
- Authentication works  
- Hub info is accessible
- Dashboard is served
- API documentation is available

## Test File Structure

```
python/tests/
├── conftest.py              # Pytest fixtures and configuration
├── test_verlihub_core.py    # SWIG binding tests
├── test_plugins.py          # Generic plugin management tests
├── test_lua_plugin.py       # Lua plugin specific tests
├── test_python_plugin.py    # Python plugin specific tests  
├── test_client.py           # NMDC client tests
├── test_auth.py             # Authentication tests
├── test_auth_registration.py # Registration, admin seeding, dashboard auth
├── test_database.py         # Database model tests
├── test_integration.py      # Protocol integration tests
├── test_api_endpoints.py    # REST API tests
└── test_benchmarks.py       # Performance benchmarks

docker/
├── docker-compose.test.yml      # Database backend test configurations
├── docker-compose.dual-test.yml # Original + verlihub-py testing
└── tests/
    ├── run_integration_tests.sh
    ├── integration_test.py
    └── ...
```

## Writing New Tests

### Skip Markers

Use appropriate skip markers for environment requirements:

```python
import pytest
import os

# Skip unless integration tests are enabled
@pytest.mark.skipif(
    os.environ.get("VH_INTEGRATION_TESTS") != "1",
    reason="Requires VH_INTEGRATION_TESTS=1"
)
def test_plugin_loading():
    ...

# Skip unless full integration (running hub) is available
@pytest.mark.skipif(
    os.environ.get("VH_FULL_INTEGRATION") != "1", 
    reason="Requires VH_FULL_INTEGRATION=1"
)
def test_full_lifecycle():
    ...
```

### Database Backend Fixtures

```python
import pytest
from verlihub.models.database import DatabaseConfig

@pytest.fixture
def db_config():
    """Configure database based on environment."""
    backend = os.environ.get("VH_DB_BACKEND", "sqlite")
    
    if backend == "sqlite":
        return DatabaseConfig(db_type="sqlite")
    elif backend == "mysql":
        return DatabaseConfig(
            db_type="mysql",
            host=os.environ.get("VH_MYSQL_HOST", "localhost"),
            port=int(os.environ.get("VH_MYSQL_PORT", "3306")),
            user=os.environ.get("VH_MYSQL_USER", "verlihub"),
            password=os.environ.get("VH_MYSQL_PASSWORD", "verlihub"),
            database=os.environ.get("VH_MYSQL_DATABASE", "verlihub"),
        )
    elif backend == "postgresql":
        return DatabaseConfig(
            db_type="postgresql",
            host=os.environ.get("VH_POSTGRES_HOST", "localhost"),
            port=int(os.environ.get("VH_POSTGRES_PORT", "5432")),
            user=os.environ.get("VH_POSTGRES_USER", "verlihub"),
            password=os.environ.get("VH_POSTGRES_PASSWORD", "verlihub"),
            database=os.environ.get("VH_POSTGRES_DATABASE", "verlihub"),
        )
```

### Hub Context Fixtures

```python
import tempfile
import pytest

@pytest.fixture
def hub_context():
    """Create a temporary hub context for testing."""
    try:
        from verlihub import verlihub_core
    except ImportError:
        pytest.skip("verlihub_core module not available")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = verlihub_core.HubContext.Create(tmpdir)
        if ctx is None:
            pytest.skip("Could not create HubContext")
        yield ctx
```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Tests

on: [push, pull_request]

jobs:
  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build
        run: |
          mkdir build && cd build
          cmake .. && make -j$(nproc)
      - name: Unit Tests
        run: |
          PYTHONPATH=build/python pytest python/tests/test_verlihub_core.py -v

  integration-tests:
    runs-on: ubuntu-latest
    services:
      mysql:
        image: mysql:8.0
        env:
          MYSQL_ROOT_PASSWORD: root
          MYSQL_DATABASE: verlihub
          MYSQL_USER: verlihub
          MYSQL_PASSWORD: verlihub
        ports: ['3306:3306']
    steps:
      - uses: actions/checkout@v4
      - name: Build
        run: |
          mkdir build && cd build
          cmake .. && make -j$(nproc) && sudo make install
      - name: Integration Tests
        env:
          VH_INTEGRATION_TESTS: "1"
          VH_DB_BACKEND: mysql
          VH_MYSQL_HOST: localhost
        run: |
          PYTHONPATH=build/python pytest python/tests/ -v
```

## Troubleshooting

### Common Issues

1. **"verlihub_core module not available"**
   - Ensure `PYTHONPATH` includes `build/python`
   - Rebuild if libraries are missing

2. **"Could not create HubContext"**
   - Check that libverlihub.so is installed or in library path
   - Try: `LD_LIBRARY_PATH=build/src pytest ...`

3. **Plugin tests skipped**
   - Set `VH_INTEGRATION_TESTS=1`
   - Ensure plugins are built: `ls build/plugins/*/lib*.so`

4. **Database connection failures**
   - Verify database server is running
   - Check environment variables match server configuration

### Debug Mode

Enable verbose pytest output:

```bash
pytest -vvs --tb=long tests/
```

### Running Individual Tests

```bash
pytest tests/test_plugins.py::TestPluginManagement::test_load_plugin -v
```
