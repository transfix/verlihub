# VerliHub Python Module Migration Guide

This guide helps you migrate from an existing VerliHub installation to use the new Python management module.

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Migration Steps](#migration-steps)
4. [Configuration Migration](#configuration-migration)
5. [Database Compatibility](#database-compatibility)
6. [API Migration](#api-migration)
7. [Script Migration](#script-migration)
8. [Troubleshooting](#troubleshooting)
9. [Rollback Procedure](#rollback-procedure)

---

## Overview

The VerliHub Python module provides:
- REST API for hub management
- Web dashboard for monitoring
- Python client library for automation
- JWT-based authentication
- Benchmark and testing tools

This module works **alongside** your existing VerliHub installation, providing additional management capabilities without replacing the core hub functionality.

---

## Prerequisites

### System Requirements

- Python 3.9 or higher
- Existing VerliHub installation (0.9.8+)
- MySQL/MariaDB database
- Network access to hub ports

### Pre-Migration Checklist

- [ ] Backup your database
- [ ] Document current configuration
- [ ] Note any custom scripts using legacy tools
- [ ] Verify Python 3.9+ is installed
- [ ] Plan maintenance window

---

## Migration Steps

### Step 1: Backup

```bash
# Backup database
mysqldump -u verlihub -p verlihub > verlihub_backup_$(date +%Y%m%d).sql

# Backup configuration
cp /etc/verlihub/dbconfig /etc/verlihub/dbconfig.bak
cp -r ~/.verlihub ~/.verlihub.bak
```

### Step 2: Install Python Module

```bash
# Navigate to verlihub source
cd /path/to/verlihub/python

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install module
pip install -e .
```

### Step 3: Configure Database Connection

Create a `.env` file or set environment variables:

```bash
# Option 1: Environment variables
export VERLIHUB_DB_HOST=localhost
export VERLIHUB_DB_PORT=3306
export VERLIHUB_DB_NAME=verlihub
export VERLIHUB_DB_USER=verlihub
export VERLIHUB_DB_PASSWORD=your_password

# Option 2: .env file
cat > .env << EOF
VERLIHUB_DB_HOST=localhost
VERLIHUB_DB_PORT=3306
VERLIHUB_DB_NAME=verlihub
VERLIHUB_DB_USER=verlihub
VERLIHUB_DB_PASSWORD=your_password
EOF
```

### Step 4: Configure API Security

```bash
# Generate secure JWT secret
export VERLIHUB_API_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# Set admin credentials
export VERLIHUB_API_USERNAME=admin
export VERLIHUB_API_PASSWORD=secure_password_here
```

### Step 5: Start Services

```bash
# Start API server
verlihub-api &

# Or with specific host/port
uvicorn verlihub.api:app --host 0.0.0.0 --port 8080
```

### Step 6: Verify Installation

```bash
# Health check
curl http://localhost:8000/api/health

# Login and get token
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "your_password"}'

# Access dashboard
open http://localhost:8000/dashboard
```

---

## Configuration Migration

### Legacy Configuration Files

| Legacy File | New Equivalent |
|-------------|----------------|
| `/etc/verlihub/dbconfig` | Environment variables |
| Custom shell scripts | Python client library |
| Cron-based maintenance | Python automation scripts |

### Environment Variables Reference

| Variable | Legacy Equivalent | Description |
|----------|-------------------|-------------|
| `VERLIHUB_DB_HOST` | db_host in dbconfig | Database hostname |
| `VERLIHUB_DB_PORT` | db_port in dbconfig | Database port |
| `VERLIHUB_DB_NAME` | db_name in dbconfig | Database name |
| `VERLIHUB_DB_USER` | db_user in dbconfig | Database username |
| `VERLIHUB_DB_PASSWORD` | db_pass in dbconfig | Database password |
| `VERLIHUB_HUB_HOST` | - | Hub NMDC host |
| `VERLIHUB_HUB_PORT` | - | Hub NMDC port |
| `VERLIHUB_API_SECRET` | - | JWT secret key |
| `VERLIHUB_API_USERNAME` | - | API admin username |
| `VERLIHUB_API_PASSWORD` | - | API admin password |

### Migrating from dbconfig

```bash
# Read legacy config and convert
eval $(grep -E '^db_' /etc/verlihub/dbconfig | sed 's/^/export VERLIHUB_/')

# Or create migration script
cat > migrate_config.py << EOF
import re

def migrate_dbconfig(filepath='/etc/verlihub/dbconfig'):
    """Migrate legacy dbconfig to environment variables."""
    mapping = {
        'db_host': 'VERLIHUB_DB_HOST',
        'db_port': 'VERLIHUB_DB_PORT', 
        'db_name': 'VERLIHUB_DB_NAME',
        'db_user': 'VERLIHUB_DB_USER',
        'db_pass': 'VERLIHUB_DB_PASSWORD',
    }
    
    env_vars = {}
    with open(filepath) as f:
        for line in f:
            match = re.match(r'^(\w+)\s*=\s*(.+)$', line.strip())
            if match:
                key, value = match.groups()
                if key in mapping:
                    env_vars[mapping[key]] = value.strip('"\'')
    
    return env_vars

if __name__ == '__main__':
    for key, value in migrate_dbconfig().items():
        print(f'export {key}="{value}"')
EOF

# Run and source
python3 migrate_config.py > .env.migrated
source .env.migrated
```

---

## Database Compatibility

The Python module is designed to work with existing VerliHub database schemas.

### Supported Schema Versions

- VerliHub 0.9.8+
- VerliHub 1.0+

### Schema Compatibility Check

```python
from verlihub.db import DatabaseConfig, get_database

async def check_compatibility():
    """Check database schema compatibility."""
    async with get_database() as db:
        result = await db.execute("SHOW TABLES")
        tables = [row[0] for row in result.fetchall()]
        
        required_tables = [
            'reglist',
            'banlist', 
            'SetupList',
            'conn_types',
        ]
        
        missing = [t for t in required_tables if t not in tables]
        if missing:
            print(f"Warning: Missing tables: {missing}")
            return False
        
        print("Database schema is compatible")
        return True
```

### Read-Only Operations

By default, the Python module performs read-only operations. Write operations require explicit configuration:

```python
# Read-only mode (default)
from verlihub.api.config import settings
settings.READONLY_MODE = True

# Enable write operations
settings.READONLY_MODE = False
```

---

## API Migration

### Migrating from Shell Scripts to API

#### Before (Shell Script)
```bash
#!/bin/bash
# Add user to hub
mysql -u verlihub -p verlihub -e \
  "INSERT INTO reglist (nick, class, login_pwd) VALUES ('newuser', 1, 'password')"
```

#### After (Python API)
```python
import httpx

async def add_user(nick: str, password: str, class_level: int = 1):
    """Add user via API."""
    async with httpx.AsyncClient() as client:
        # Login
        response = await client.post(
            "http://localhost:8000/api/auth/login",
            json={"username": "admin", "password": "admin_pass"}
        )
        token = response.json()["access_token"]
        
        # Add user
        response = await client.post(
            "http://localhost:8000/api/users",
            headers={"Authorization": f"Bearer {token}"},
            json={"nick": nick, "password": password, "class": class_level}
        )
        return response.json()
```

### Migrating from Direct Database Access

#### Before (Direct MySQL)
```bash
mysql -u verlihub -p verlihub -e "SELECT * FROM reglist WHERE class >= 5"
```

#### After (Python Client)
```python
from verlihub.client import HubClient

async def get_ops():
    """Get operators via client library."""
    client = HubClient(api_url="http://localhost:8000")
    await client.login("admin", "admin_pass")
    
    users = await client.get_users()
    ops = [u for u in users if u.get("class", 0) >= 5]
    return ops
```

---

## Script Migration

### Common Script Patterns

#### User Management Scripts

```python
# migrate_user_scripts.py
"""Migration helpers for user management scripts."""

from verlihub.client import HubClient

client = HubClient()

async def migrate_add_user_script():
    """
    Replace: /usr/local/bin/vh_adduser.sh
    
    Old:
        #!/bin/bash
        echo "INSERT INTO reglist..." | mysql -u vh -p vh
    
    New:
        verlihub-cli user create <nick> --password <pass>
    """
    await client.login("admin", "password")
    
    # Add single user
    await client.create_user("newuser", "password", class_level=1)
    
    # Batch add from file
    with open("users.txt") as f:
        for line in f:
            nick, password = line.strip().split(":")
            await client.create_user(nick, password)

async def migrate_ban_script():
    """
    Replace: /usr/local/bin/vh_ban.sh
    
    Old:
        #!/bin/bash
        mysql -e "INSERT INTO banlist..."
    
    New:
        verlihub-cli ban add <nick/ip> --reason <reason>
    """
    await client.login("admin", "password")
    await client.add_ban(target="192.168.1.100", reason="Spam", duration=86400)
```

#### Monitoring Scripts

```python
# migrate_monitoring.py
"""Migration helpers for monitoring scripts."""

from verlihub.client import NMDCClient, HubClient

async def migrate_status_check():
    """
    Replace: /usr/local/bin/vh_status.sh
    
    Old:
        #!/bin/bash
        nc -z localhost 4111 && echo "UP" || echo "DOWN"
    
    New:
        verlihub-cli status
    """
    # NMDC protocol check
    client = NMDCClient("localhost", 4111)
    if client.connect():
        print("Hub is UP")
        client.disconnect()
    else:
        print("Hub is DOWN")

    # API health check
    api = HubClient()
    status = await api.health_check()
    print(f"API Status: {status}")
```

### CLI Command Mapping

| Old Command | New Command |
|-------------|-------------|
| `vh_adduser.sh <nick>` | `verlihub-cli user create <nick>` |
| `vh_deluser.sh <nick>` | `verlihub-cli user delete <nick>` |
| `vh_ban.sh <ip>` | `verlihub-cli ban add <ip>` |
| `vh_unban.sh <ip>` | `verlihub-cli ban remove <ip>` |
| `vh_kick.sh <nick>` | `verlihub-cli user kick <nick>` |
| `vh_status.sh` | `verlihub-cli status` |

---

## Troubleshooting

### Common Issues

#### 1. Database Connection Failed

```
Error: Can't connect to MySQL server
```

**Solutions:**
```bash
# Check MySQL is running
systemctl status mysql

# Verify credentials
mysql -u $VERLIHUB_DB_USER -p$VERLIHUB_DB_PASSWORD $VERLIHUB_DB_NAME -e "SELECT 1"

# Check environment variables
env | grep VERLIHUB_DB
```

#### 2. Authentication Failed

```
Error: 401 Unauthorized
```

**Solutions:**
```bash
# Verify API credentials are set
echo $VERLIHUB_API_USERNAME
echo $VERLIHUB_API_PASSWORD

# Check JWT secret is consistent
echo $VERLIHUB_API_SECRET

# Test login
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "'$VERLIHUB_API_USERNAME'", "password": "'$VERLIHUB_API_PASSWORD'"}'
```

#### 3. Module Import Errors

```
Error: ModuleNotFoundError: No module named 'verlihub'
```

**Solutions:**
```bash
# Ensure virtual environment is active
source /path/to/verlihub/python/.venv/bin/activate

# Reinstall module
pip install -e /path/to/verlihub/python
```

#### 4. Port Already in Use

```
Error: Address already in use
```

**Solutions:**
```bash
# Find process using port
lsof -i :8000
netstat -tlnp | grep 8000

# Use different port
verlihub-api --port 8080
```

#### 5. NMDC Connection Issues

```
Error: Connection refused
```

**Solutions:**
```bash
# Check hub is running
ps aux | grep verlihub

# Verify port
netstat -tlnp | grep 4111

# Test connection
nc -z localhost 4111
```

### Debug Mode

Enable debug logging for troubleshooting:

```python
import logging

logging.basicConfig(level=logging.DEBUG)

# Or set environment variable
# export VERLIHUB_LOG_LEVEL=DEBUG
```

### Health Check Script

```bash
#!/bin/bash
# health_check.sh - Verify migration is working

echo "=== VerliHub Python Module Health Check ==="

# Check API
echo -n "API Server: "
curl -sf http://localhost:8000/api/health > /dev/null && echo "OK" || echo "FAILED"

# Check database
echo -n "Database: "
python3 -c "
from verlihub.db import get_database
import asyncio
async def check():
    async with get_database() as db:
        await db.execute('SELECT 1')
        return True
print('OK' if asyncio.run(check()) else 'FAILED')
" 2>/dev/null || echo "FAILED"

# Check NMDC
echo -n "NMDC Hub: "
nc -z localhost ${VERLIHUB_HUB_PORT:-4111} && echo "OK" || echo "FAILED"

# Check CLI
echo -n "CLI Tools: "
verlihub-cli --help > /dev/null 2>&1 && echo "OK" || echo "FAILED"

echo "=== Check Complete ==="
```

---

## Rollback Procedure

If migration fails, follow these steps to rollback:

### Step 1: Stop Python Services

```bash
# Stop API server
pkill -f "verlihub-api"
pkill -f "uvicorn verlihub"
```

### Step 2: Restore Database (if modified)

```bash
# Restore from backup
mysql -u verlihub -p verlihub < verlihub_backup_YYYYMMDD.sql
```

### Step 3: Restore Configuration

```bash
# Restore legacy configs
cp /etc/verlihub/dbconfig.bak /etc/verlihub/dbconfig
cp -r ~/.verlihub.bak ~/.verlihub
```

### Step 4: Restart Legacy Services

```bash
# Restart hub with legacy configuration
systemctl restart verlihub
# or
service verlihub restart
```

### Step 5: Verify Rollback

```bash
# Test hub is working
telnet localhost 4111
# Should connect and receive hub info
```

---

## Support

For migration issues:

1. Check the [README.md](README.md) for general documentation
2. Review [SECURITY.md](SECURITY.md) for security-related issues
3. Run benchmark tests to verify performance: `verlihub-bench quick`
4. Open an issue on GitHub with debug logs and error messages

---

## Appendix: Complete Migration Checklist

- [ ] **Pre-Migration**
  - [ ] Backup database
  - [ ] Backup configuration files
  - [ ] Document custom scripts
  - [ ] Verify Python 3.9+
  - [ ] Plan maintenance window
  
- [ ] **Installation**
  - [ ] Create virtual environment
  - [ ] Install Python module
  - [ ] Configure environment variables
  - [ ] Generate JWT secret
  
- [ ] **Configuration**
  - [ ] Migrate database settings
  - [ ] Configure API authentication
  - [ ] Set up logging
  
- [ ] **Testing**
  - [ ] Run health check
  - [ ] Test API endpoints
  - [ ] Test NMDC connection
  - [ ] Verify dashboard access
  
- [ ] **Script Migration**
  - [ ] Convert user management scripts
  - [ ] Convert monitoring scripts
  - [ ] Update cron jobs
  
- [ ] **Post-Migration**
  - [ ] Monitor for errors
  - [ ] Run benchmark tests
  - [ ] Document any issues
  - [ ] Remove legacy scripts (optional)
