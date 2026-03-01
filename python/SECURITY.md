# VerliHub Python Module Security Guide

This document outlines security considerations, known issues, and best practices for the VerliHub Python module.

## Table of Contents

1. [Security Overview](#security-overview)
2. [Authentication](#authentication)
3. [Authorization](#authorization)
4. [API Security](#api-security)
5. [Configuration Security](#configuration-security)
6. [Known Issues and Mitigations](#known-issues-and-mitigations)
7. [Security Checklist](#security-checklist)
8. [Reporting Security Issues](#reporting-security-issues)

---

## Security Overview

The VerliHub Python module implements several security measures:

| Feature | Implementation |
|---------|---------------|
| Authentication | JWT tokens with configurable expiration |
| Password Storage | bcrypt hashing (direct) |
| SQL Injection | ORM-based queries (SQLModel/SQLAlchemy) |
| XSS Prevention | Jinja2 auto-escaping |
| CSRF Protection | SameSite cookies |
| Transport Security | Optional HTTPS support |

---

## Authentication

### JWT Token Configuration

The API uses JWT (JSON Web Tokens) for authentication.

**Required Environment Variables:**

```bash
# CRITICAL: Set a strong, unique secret key
export VH_JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# Token expiration in minutes (default: 30)
export VH_JWT_EXPIRE_MINUTES=30

# API admin credentials (seeded into DB at startup)
export VH_API_USERNAME=admin
export VH_API_PASSWORD=strong_password_here

# Self-registration (defaults shown)
export VH_REGISTRATION_ENABLED=1           # Set to 0 to disable
export VH_REGISTRATION_REQUIRE_INVITE=0    # Set to 1 to require invite codes
export VH_REGISTRATION_DEFAULT_CLASS=1     # 1=Registered, 2=VIP
```

**Security Notes:**
- `VH_JWT_SECRET` MUST be set in production - random generation on startup invalidates tokens on restart
- Use a minimum 32-byte (64 hex characters) secret
- Rotate secrets periodically
- Never commit secrets to version control
- Admin accounts are defined in the `users:` section of the config and seeded into the `RegUser` database table
- All users share the same `RegUser` table and authenticate via bcrypt
- Consider setting `VH_REGISTRATION_REQUIRE_INVITE=1` in production to control who can register

### Password Requirements

The module uses bcrypt for password hashing:

```python
import bcrypt
hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
```

**Best Practices:**
- Minimum 12 characters
- Mix of uppercase, lowercase, numbers, symbols
- No dictionary words or common patterns
- Different password for each service

### Session Security

Dashboard sessions use HTTP-only cookies:

| Attribute | Value | Purpose |
|-----------|-------|---------|
| `httponly` | `True` | Prevents JavaScript access |
| `samesite` | `lax` | CSRF protection |
| `secure` | Configurable | HTTPS-only transmission |
| `max_age` | 86400 | 24-hour expiration |

**Enable secure cookies in production:**

```bash
export VH_SECURE_COOKIES=1
```

---

## Authorization

### Permission Levels

The API uses class-based authorization matching VerliHub user classes:

| Class | Name | Permissions |
|-------|------|-------------|
| 0 | Guest | Read-only public endpoints |
| 1 | Regular | Basic operations |
| 3 | Operator | User management |
| 4 | Cheef | Ban management, configuration |
| 5 | Admin | Full access |
| 10 | Master | Super admin |
| 11 | NetOp | Network operations |

### Protecting Endpoints

All sensitive endpoints should require authentication:

```python
from verlihub.api.auth import require_permission, Permission

@router.get("/sensitive-data")
async def get_sensitive_data(
    user: TokenData = Depends(require_permission(Permission.ADMIN))
):
    # Only admins can access
    pass
```

### Public vs Protected Endpoints

| Endpoint | Authentication | Purpose |
|----------|---------------|---------|
| `GET /api/health` | None | Health check |
| `POST /api/auth/login` | None | Authentication |
| `POST /api/auth/register` | None | Self-registration (if enabled) |
| `GET /dashboard/register` | None | Registration page (if enabled) |
| `GET /api/users` | Required | User listing |
| `POST /api/bans` | Required | Ban creation |
| `DELETE /api/*` | Required | Destructive operations |

---

## API Security

### CORS Configuration

Configure CORS for production:

```bash
# Restrict to specific origins (comma-separated)
export VH_CORS_ORIGINS=https://dashboard.example.com,https://admin.example.com

# Do NOT use wildcard (*) with credentials in production
```

**Example secure configuration:**

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://dashboard.example.com"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
```

### Rate Limiting

Implement rate limiting for production deployments:

```bash
# Install slowapi
pip install slowapi

# Configure in your deployment
```

**Recommended limits:**

| Endpoint | Limit | Purpose |
|----------|-------|---------|
| `/api/auth/login` | 5/minute | Brute-force prevention |
| `/api/*` | 100/minute | General DoS protection |
| `/dashboard/*` | 60/minute | Dashboard flood protection |

### Network Binding

**Default (Development):**
```bash
export VH_API_HOST=127.0.0.1  # Localhost only
export VH_API_PORT=8000
```

**Production with Reverse Proxy:**
```bash
export VH_API_HOST=127.0.0.1  # Behind nginx/traefik
export VH_API_PORT=8000
```

**Direct External Access (Not Recommended):**
```bash
export VH_API_HOST=0.0.0.0  # All interfaces
export VH_API_PORT=8000
# Ensure firewall rules are in place!
```

---

## Configuration Security

### Environment Variables

Store sensitive configuration in environment variables, not files:

```bash
# Required for production
VH_JWT_SECRET=<64-char-hex>
VH_API_USERNAME=admin
VH_API_PASSWORD=<strong-password>

# Database
VERLIHUB_DB_HOST=localhost
VERLIHUB_DB_PASSWORD=<database-password>

# Optional security settings
VH_SECURE_COOKIES=1
VH_API_HOST=127.0.0.1
VH_CORS_ORIGINS=https://dashboard.example.com
```

### File Permissions

CLI configuration files should have restricted permissions:

```bash
# Token file permissions
chmod 600 ~/.verlihub-cli.json

# Environment file permissions
chmod 600 .env
```

### Docker Security

For Docker deployments:

```yaml
services:
  api:
    # Don't run as root
    user: "1000:1000"
    
    # Read-only filesystem
    read_only: true
    
    # Drop capabilities
    cap_drop:
      - ALL
    
    # Secrets via environment
    environment:
      - VH_JWT_SECRET_FILE=/run/secrets/jwt_secret
    secrets:
      - jwt_secret

secrets:
  jwt_secret:
    file: ./secrets/jwt_secret.txt
```

---

## Known Issues and Mitigations

### Issue 1: Ban Endpoints Authentication (CRITICAL) - FIXED

**Status:** ✅ Fixed

**Description:** Ban management endpoints were missing authentication, allowing unauthenticated users to list, create, and delete bans.

**Fix Applied:** Added `require_permission(Permission.OPERATOR)` to all GET endpoints and `require_permission(Permission.CHEEF)` to POST/DELETE endpoints in [verlihub/api/routes/bans.py](verlihub/api/routes/bans.py).

### Issue 2: Insecure Password Fallback (HIGH) - FIXED

**Status:** ✅ Fixed

**Description:** Password verification fell back to plain text comparison for "legacy support", creating a security risk.

**Fix Applied:** Removed plain text fallback in [verlihub/api/auth.py](verlihub/api/auth.py). Unrecognized password hash formats now log a warning and reject authentication.

### Issue 3: JWT Secret Generation (HIGH) - FIXED

**Status:** ✅ Fixed

**Description:** If `VH_JWT_SECRET` is not set, a random key is generated on each startup, invalidating existing tokens.

**Fix Applied:** Added warning log when `VH_JWT_SECRET` is not set. Production deployments should always set this variable.

### Issue 4: CLI Token File Permissions (HIGH) - FIXED

**Status:** ✅ Fixed

**Description:** CLI stored JWT token in plaintext JSON file with no permission restrictions.

**Fix Applied:** Added `chmod 600` (owner-only) to config file after saving in [verlihub/cli.py](verlihub/cli.py).

### Issue 5: CORS Wildcard (HIGH)

**Status:** Configuration warning

**Description:** Default CORS configuration allows wildcard origins.

**Mitigation:** Set `VH_CORS_ORIGINS` to specific allowed origins in production.

### Issue 6: No Rate Limiting (HIGH)

**Status:** Documented

**Description:** No built-in rate limiting.

**Mitigation:** 
1. Use a reverse proxy (nginx, traefik) with rate limiting
2. Install and configure slowapi
3. Deploy behind a CDN/WAF

### Issue 7: Hub Status Information Disclosure (MEDIUM)

**Status:** By Design

**Description:** Hub status endpoints are public for monitoring integration.

**Mitigation:** Use network-level access control if this is undesirable.

---

## Security Checklist

### Development

- [ ] Use Python 3.9+
- [ ] Install security updates regularly
- [ ] Run tests with `pytest`
- [ ] Check for vulnerabilities with `pip-audit`

### Pre-Production

- [ ] Set `VH_JWT_SECRET` to a strong random value
- [ ] Set strong `VH_API_PASSWORD`
- [ ] Review registration settings (`VH_REGISTRATION_ENABLED`, `VH_REGISTRATION_REQUIRE_INVITE`)
- [ ] Configure CORS origins explicitly
- [ ] Enable secure cookies (`VH_SECURE_COOKIES=1`)
- [ ] Bind to localhost only (`VH_API_HOST=127.0.0.1`)
- [ ] Set up reverse proxy with TLS
- [ ] Configure rate limiting
- [ ] Review firewall rules

### Production Monitoring

- [ ] Monitor authentication failures
- [ ] Log and alert on unusual access patterns
- [ ] Regularly rotate JWT secrets
- [ ] Keep dependencies updated
- [ ] Review access logs periodically

### Dependency Security

Run security audits regularly:

```bash
# Install audit tools
pip install pip-audit safety

# Check for vulnerabilities
pip-audit
safety check

# Update dependencies
pip install --upgrade -r requirements.txt
```

---

## Security Headers

For production deployment with a reverse proxy, add these headers:

```nginx
# Nginx example
add_header X-Frame-Options "DENY" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-XSS-Protection "1; mode=block" always;
add_header Content-Security-Policy "default-src 'self'" always;
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
```

---

## Incident Response

### If Credentials Are Compromised

1. **Rotate JWT secret immediately:**
   ```bash
   export VH_JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
   # Restart API server
   ```

2. **Change API admin password:**
   ```bash
   export VH_API_PASSWORD=new_strong_password
   # Restart with --force to update the password in the database
   verlihub-server --force
   ```

3. **Disable self-registration** if compromised via registration:
   ```bash
   export VH_REGISTRATION_ENABLED=0
   ```

4. **Review access logs** for unauthorized activity

4. **Check database** for unauthorized modifications

### If Database Is Compromised

1. Stop API server
2. Restore from backup
3. Rotate all credentials
4. Review and patch vulnerability
5. Reset all user sessions (rotate JWT secret)

---

## Reporting Security Issues

To report security vulnerabilities:

1. **Do NOT** open a public GitHub issue
2. Email security concerns to the maintainers privately
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

We aim to respond to security reports within 48 hours and release patches within 7 days for critical issues.

---

## Audit History

| Date | Auditor | Scope | Findings |
|------|---------|-------|----------|
| 2024 | Internal | Full module | 3 Critical, 4 High, 4 Medium, 3 Low |

---

## References

- [OWASP API Security Top 10](https://owasp.org/www-project-api-security/)
- [JWT Best Practices](https://tools.ietf.org/html/rfc8725)
- [Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
- [FastAPI Security Documentation](https://fastapi.tiangolo.com/tutorial/security/)
