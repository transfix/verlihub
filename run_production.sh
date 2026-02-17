#!/bin/bash
# Verlihub Production Runner
#
# Launches a production instance of either legacy verlihub (C++) or verlihub-py
# (Python reimplementation) with configuration from a YAML file. Supports both
# MySQL and PostgreSQL backends.
#
# Usage:
#   # Legacy verlihub with MySQL (default)
#   sg docker ./run_production.sh --config production.yml
#
#   # verlihub-py with PostgreSQL
#   sg docker ./run_production.sh --config production.yml --edition py
#
#   # Auto-detect edition and database from YAML
#   sg docker ./run_production.sh --config production.yml
#
#   # Lifecycle
#   sg docker ./run_production.sh --stop
#   sg docker ./run_production.sh --logs
#   sg docker ./run_production.sh --status

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Defaults
CONFIG_FILE="production.yml"
ACTION="start"
EDITION=""          # "legacy" or "py" — auto-detected if not set
REBUILD=false
SKIP_COMMANDS=false
DEBUG=false

# ── Argument parsing ─────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case $1 in
        --config|-c)
            CONFIG_FILE="$2"
            shift 2
            ;;
        --edition|-e)
            EDITION="$2"
            shift 2
            ;;
        --stop)
            ACTION="stop"
            shift
            ;;
        --restart)
            ACTION="restart"
            shift
            ;;
        --logs)
            ACTION="logs"
            shift
            ;;
        --status)
            ACTION="status"
            shift
            ;;
        --rebuild)
            REBUILD=true
            shift
            ;;
        --skip-commands)
            SKIP_COMMANDS=true
            shift
            ;;
        --debug)
            DEBUG=true
            shift
            ;;
        -h|--help)
            echo "Verlihub Production Runner"
            echo ""
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  --config, -c FILE   YAML config file (default: production.yml)"
            echo "  --edition, -e ED    'legacy' (C++ hub) or 'py' (Python hub)"
            echo "                      Auto-detected from YAML if not set"
            echo "  --stop              Stop the production instance"
            echo "  --restart           Restart the production instance"
            echo "  --logs              Show container logs"
            echo "  --status            Show container status"
            echo "  --rebuild           Force rebuild of Docker images"
            echo "  --skip-commands     Skip running startup commands"
            echo "  --debug             Enable debug output"
            echo "  -h, --help          Show this help message"
            echo ""
            echo "Edition notes:"
            echo "  legacy  — Original C++ Verlihub, MySQL only"
            echo "  py      — verlihub-py Python reimplementation, MySQL or PostgreSQL"
            echo ""
            echo "Examples:"
            echo "  sg docker $0                                  # Default (legacy + MySQL)"
            echo "  sg docker $0 --edition py                     # verlihub-py + auto DB"
            echo "  sg docker $0 --edition py --config hub.yml    # verlihub-py + custom config"
            echo "  sg docker $0 --stop                           # Stop instance"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# ── Helpers ──────────────────────────────────────────────────────────────────

log_info()    { echo -e "${BLUE}→ $1${NC}"; }
log_success() { echo -e "${GREEN}✓ $1${NC}"; }
log_warn()    { echo -e "${YELLOW}! $1${NC}"; }
log_error()   { echo -e "${RED}✗ $1${NC}"; }

check_dependencies() {
    if ! command -v python3 &>/dev/null; then
        log_error "python3 not found"
        exit 1
    fi
    if ! command -v docker &>/dev/null; then
        log_error "docker not found"
        exit 1
    fi
    if ! python3 -c "import yaml" 2>/dev/null; then
        log_warn "PyYAML not installed, installing..."
        pip3 install --user pyyaml
    fi
}

# ── YAML → shell variables ──────────────────────────────────────────────────

parse_config() {
    _CONFIG_FILE="$CONFIG_FILE" python3 << 'PYEOF'
import yaml, sys, os, shlex

config_file = os.environ.get("_CONFIG_FILE", "production.yml")
if not os.path.exists(config_file):
    print(f"ERROR: Config file not found: {config_file}", file=sys.stderr)
    sys.exit(1)

with open(config_file) as f:
    config = yaml.safe_load(f)

def q(val):
    """Shell-quote a value so eval handles spaces and special chars safely."""
    return shlex.quote(str(val))

db   = config.get("database", {})
hub  = config.get("hub", {})
dcfg = config.get("docker", {})
api  = config.get("api", {})
mb   = config.get("matterbridge", {})

# Database type (mysql or postgresql)
db_type = db.get("type", "mysql").lower()
# Normalise aliases
if db_type in ("postgres", "pg"):
    db_type = "postgresql"

print(f"DB_TYPE={q(db_type)}")
print(f"DB_HOST={q(db.get('host', 'mysql' if db_type == 'mysql' else 'postgres'))}")
print(f"DB_PORT={q(db.get('port', 3306 if db_type == 'mysql' else 5432))}")
print(f"DB_USER={q(db.get('user', 'verlihub'))}")
print(f"DB_PASS={q(db.get('password', 'verlihub'))}")
print(f"DB_NAME={q(db.get('name', 'verlihub'))}")

print(f"HUB_NAME={q(hub.get('name', 'My Hub'))}")
print(f"HUB_DESC={q(hub.get('description', ''))}")
print(f"HUB_PORT={q(hub.get('port', 4111))}")
print(f"MOTD_FILE={q(hub.get('motd_file', ''))}")

# Users — both new and legacy format
users = config.get("users", {})
if users:
    masters = users.get("masters", [])
    admin_nick = masters[0]["nick"] if masters else "admin"
    admin_pass = masters[0]["password"] if masters else "admin"
else:
    admin = config.get("admin", {})
    admin_nick = admin.get("nick", "admin")
    admin_pass = admin.get("password", "admin")

print(f"ADMIN_NICK={q(admin_nick)}")
print(f"ADMIN_PASS={q(admin_pass)}")
print(f"PYTHON_MODE={q(config.get('python_mode', 'single'))}")
print(f"API_ENABLED={q(str(api.get('enabled', True)).lower())}")
print(f"API_PORT={q(api.get('port', 30000))}")

# Docker section
print(f"CONFIG_VOLUME={q(dcfg.get('config_volume', 'verlihub-prod-config'))}")
print(f"DB_VOLUME={q(dcfg.get('db_volume', dcfg.get('mysql_volume', 'verlihub-prod-db')))}")
print(f"NETWORK={q(dcfg.get('network', 'verlihub-prod-net'))}")
print(f"CONTAINER_PREFIX={q(dcfg.get('container_prefix', 'vh-prod'))}")
print(f"RESTART_POLICY={q(dcfg.get('restart_policy', 'unless-stopped'))}")

cors = api.get("cors_origins", [])
print(f"CORS_ORIGINS={q(' '.join(cors))}")

# Matterbridge
print(f"MATTERBRIDGE_ENABLED={q(str(mb.get('enabled', False)).lower())}")
print(f"MATTERBRIDGE_URL={q(mb.get('api_url', 'http://matterbridge:4242'))}")
print(f"MATTERBRIDGE_TOKEN={q(mb.get('api_token', ''))}")
print(f"MATTERBRIDGE_GATEWAY={q(mb.get('gateway', 'verlihub'))}")
print(f"MATTERBRIDGE_CHANNEL={q(mb.get('channel', '#general'))}")

# Startup commands
startup_cmds = config.get("startup_commands", [])
plugin_cmds  = config.get("plugin_commands", [])
all_cmds = startup_cmds + plugin_cmds
print(f"HAS_COMMANDS={q('true' if all_cmds else 'false')}")
print(f"CMD_COUNT={q(len(all_cmds))}")

# User counts
uc = {
    "masters":    len(users.get("masters", []))    if users else (1 if config.get("admin") else 0),
    "admins":     len(users.get("admins", []))     if users else 0,
    "operators":  len(users.get("operators", []))  if users else 0,
    "vips":       len(users.get("vips", []))       if users else 0,
    "registered": len(users.get("registered", [])) if users else 0,
}
print(f"USER_COUNTS={q(str(uc['masters'])+','+str(uc['admins'])+','+str(uc['operators'])+','+str(uc['vips'])+','+str(uc['registered']))}")

# TLS
tls = config.get("tls", {})
print(f"TLS_ENABLED={q(str(tls.get('enabled', False)).lower())}")
print(f"TLS_INTERNAL_PORT={q(tls.get('internal_port', 411))}")
print(f"TLS_ONLY_MODE={q(str(tls.get('only_mode', False)).lower())}")
print(f"TLS_MIN_VERSION={q(tls.get('min_version', 2))}")
print(f"TLS_CERT_FILE={q(tls.get('cert_file', ''))}")
print(f"TLS_KEY_FILE={q(tls.get('key_file', ''))}")
print(f"TLS_CERT_ORG={q(tls.get('cert_org', 'Verlihub'))}")
print(f"TLS_CERT_EMAIL={q(tls.get('cert_email', 'verlihub@localhost'))}")

# Edition hint (if present in YAML)
print(f"YAML_EDITION={q(config.get('edition', ''))}")
PYEOF
}

# ── Compose generation — MySQL service block ─────────────────────────────────

_compose_mysql_service() {
    cat << EOF
  # MySQL database
  ${CONTAINER_PREFIX}-db:
    image: mysql:8.0
    container_name: ${CONTAINER_PREFIX}-db
    environment:
      MYSQL_ROOT_PASSWORD: ${DB_PASS}_root
      MYSQL_DATABASE: ${DB_NAME}
      MYSQL_USER: ${DB_USER}
      MYSQL_PASSWORD: ${DB_PASS}
    volumes:
      - ${DB_VOLUME}:/var/lib/mysql
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-u${DB_USER}", "-p${DB_PASS}"]
      interval: 10s
      timeout: 5s
      retries: 10
    networks:
      - ${NETWORK}
    restart: ${RESTART_POLICY}
EOF
}

# ── Compose generation — PostgreSQL service block ────────────────────────────

_compose_postgres_service() {
    cat << EOF
  # PostgreSQL database
  ${CONTAINER_PREFIX}-db:
    image: postgres:16
    container_name: ${CONTAINER_PREFIX}-db
    environment:
      POSTGRES_DB: ${DB_NAME}
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASS}
    volumes:
      - ${DB_VOLUME}:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER} -d ${DB_NAME}"]
      interval: 10s
      timeout: 5s
      retries: 10
    networks:
      - ${NETWORK}
    restart: ${RESTART_POLICY}
EOF
}

# ── Compose generation — legacy verlihub hub ─────────────────────────────────

_compose_legacy_hub() {
    cat << EOF
  # Verlihub Legacy Hub (C++)
  ${CONTAINER_PREFIX}-hub:
    build:
      context: .
      dockerfile: docker/Dockerfile
      args:
        PYTHON_MODE: ${PYTHON_MODE}
    container_name: ${CONTAINER_PREFIX}-hub
    depends_on:
      ${CONTAINER_PREFIX}-db:
        condition: service_healthy
    environment:
      VH_DB_HOST: ${CONTAINER_PREFIX}-db
      VH_DB_USER: ${DB_USER}
      VH_DB_PASS: ${DB_PASS}
      VH_DB_NAME: ${DB_NAME}
      VH_HUB_NAME: "${HUB_NAME}"
      VH_HUB_PORT: "${HUB_PORT}"
      VH_ADMIN_NICK: ${ADMIN_NICK}
      VH_ADMIN_PASS: ${ADMIN_PASS}
      VERLIHUB_PYTHON_VENV: /opt/verlihub-venv
      PYTHON_MODE: ${PYTHON_MODE}
      CORS_ORIGINS: "${CORS_ORIGINS}"
    ports:
      - "${HUB_PORT}:${HUB_PORT}"
EOF

    if [ "$API_ENABLED" = "true" ] && [ "$PYTHON_MODE" = "single" ]; then
        echo "      - \"${API_PORT}:${API_PORT}\""
    fi

    cat << EOF
    volumes:
      - ${CONFIG_VOLUME}:/etc/verlihub
      - ./plugins/python/scripts:/usr/local/share/verlihub/scripts:ro
    networks:
      - ${NETWORK}
    restart: ${RESTART_POLICY}
EOF
}

# ── Compose generation — verlihub-py hub ─────────────────────────────────────

_compose_py_hub() {
    cat << EOF
  # Verlihub-py Hub (Python)
  ${CONTAINER_PREFIX}-hub:
    build:
      context: .
      dockerfile: docker/Dockerfile.verlihub-py
    container_name: ${CONTAINER_PREFIX}-hub
    depends_on:
      ${CONTAINER_PREFIX}-db:
        condition: service_healthy
    command: >
      python -m verlihub.server
        -c /config/production.yml
        --mode api
    environment:
      PYTHONUNBUFFERED: "1"
    ports:
      - "${HUB_PORT}:${HUB_PORT}"
      - "${API_PORT}:${API_PORT}"
    volumes:
      - ./${CONFIG_FILE}:/config/production.yml:ro
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:${API_PORT}/api/health"]
      interval: 10s
      timeout: 5s
      retries: 15
      start_period: 10s
    networks:
      - ${NETWORK}
    restart: ${RESTART_POLICY}
EOF
}

# ── Full compose file generation ─────────────────────────────────────────────

generate_compose() {
    local compose_file="docker-compose.production.yml"

    log_info "Generating $compose_file (edition=${EDITION}, db=${DB_TYPE})..."

    # Header
    cat > "$compose_file" << EOF
# Verlihub Production Docker Compose
# Edition: ${EDITION}
# Database: ${DB_TYPE}
# Generated from: ${CONFIG_FILE}
# Generated at: $(date -Iseconds)
#
# DO NOT EDIT — regenerate with: ./run_production.sh --config ${CONFIG_FILE} --edition ${EDITION}

services:
EOF

    # Database service
    if [ "$DB_TYPE" = "postgresql" ]; then
        _compose_postgres_service >> "$compose_file"
    else
        _compose_mysql_service >> "$compose_file"
    fi

    echo "" >> "$compose_file"

    # Hub service
    if [ "$EDITION" = "py" ]; then
        _compose_py_hub >> "$compose_file"
    else
        _compose_legacy_hub >> "$compose_file"
    fi

    # Volumes and network
    cat >> "$compose_file" << EOF

volumes:
  ${DB_VOLUME}:
  ${CONFIG_VOLUME}:

networks:
  ${NETWORK}:
    driver: bridge
EOF

    log_success "Generated $compose_file"
}

# ── Database readiness (used by legacy edition) ─────────────────────────────

is_initialized() {
    local container="${CONTAINER_PREFIX}-db"

    if ! docker ps --format '{{.Names}}' | grep -q "^${container}$"; then
        return 1
    fi

    if [ "$DB_TYPE" = "postgresql" ]; then
        docker exec "$container" psql -U "$DB_USER" -d "$DB_NAME" \
            -c "SELECT 1 FROM reglist LIMIT 1" &>/dev/null
    else
        docker exec "$container" mysql -u"$DB_USER" -p"$DB_PASS" "$DB_NAME" \
            -e "SELECT 1 FROM reglist LIMIT 1" &>/dev/null
    fi
}

# ── Wait for hub ────────────────────────────────────────────────────────────

wait_for_hub() {
    local max_attempts=60
    local attempt=1
    local hub_container="${CONTAINER_PREFIX}-hub"

    log_info "Waiting for hub to be ready..."

    while [ $attempt -le $max_attempts ]; do
        if ! docker ps --format '{{.Names}}' | grep -q "^${hub_container}$"; then
            sleep 2
            attempt=$((attempt + 1))
            continue
        fi

        if [ "$EDITION" = "py" ]; then
            # verlihub-py: check API health endpoint
            if docker exec "$hub_container" curl -sf "http://localhost:${API_PORT}/api/health" >/dev/null 2>&1; then
                log_success "Hub API is healthy"
                return 0
            fi
        else
            # Legacy: check NMDC port
            if docker exec "$hub_container" nc -z 127.0.0.1 "$HUB_PORT" 2>/dev/null; then
                log_success "Hub is listening on port $HUB_PORT"
                return 0
            fi
        fi

        echo -n "."
        sleep 2
        attempt=$((attempt + 1))
    done

    echo ""
    log_error "Hub did not become ready in time"
    return 1
}

# ── Legacy-only: apply config via SQL ────────────────────────────────────────

update_hub_settings() {
    if [ "$EDITION" = "py" ]; then
        log_info "verlihub-py reads settings directly from YAML — skipping SQL config"
        return 0
    fi

    log_info "Applying configuration to database..."

    local db_container="${CONTAINER_PREFIX}-db"

    if ! docker ps --format '{{.Names}}' | grep -q "^${db_container}$"; then
        log_warn "DB container not running, skipping settings update"
        return 1
    fi

    local sql
    sql=$(python3 docker/apply_config.py --config "$CONFIG_FILE" --dry-run 2>/dev/null \
          | sed -n '/--- SQL/,/--- End SQL/p' | sed '1d;$d')

    if [ -z "$sql" ]; then
        log_info "No configuration changes to apply"
        return 0
    fi

    echo "$sql" | docker exec -i "$db_container" \
        mysql -u"$DB_USER" -p"$DB_PASS" "$DB_NAME" 2>/dev/null

    if [ $? -eq 0 ]; then
        log_success "Configuration applied to database"
    else
        log_warn "Failed to apply some configuration"
    fi
}

# ── MOTD file copy (legacy only) ────────────────────────────────────────────

update_motd() {
    if [ "$EDITION" = "py" ]; then
        return 0  # verlihub-py reads motd_file from YAML
    fi

    if [ -z "$MOTD_FILE" ]; then
        return 0
    fi

    if [ ! -f "$MOTD_FILE" ]; then
        log_warn "MOTD file not found: $MOTD_FILE"
        return 1
    fi

    log_info "Updating MOTD from: $MOTD_FILE"
    local hub_container="${CONTAINER_PREFIX}-hub"

    if ! docker ps --format '{{.Names}}' | grep -q "^${hub_container}$"; then
        log_warn "Hub container not running, skipping MOTD update"
        return 1
    fi

    docker cp "$MOTD_FILE" "${hub_container}:/etc/verlihub/motd"
    if [ $? -eq 0 ]; then
        log_success "MOTD file updated"
    else
        log_warn "Failed to copy MOTD file"
        return 1
    fi
}

# ── TLS cert copy (legacy only) ─────────────────────────────────────────────

update_tls_certs() {
    if [ "$TLS_ENABLED" != "true" ] || [ "$EDITION" = "py" ]; then
        return 0
    fi

    local hub_container="${CONTAINER_PREFIX}-hub"

    if ! docker ps --format '{{.Names}}' | grep -q "^${hub_container}$"; then
        log_warn "Hub container not running, skipping TLS cert update"
        return 1
    fi

    if [ -n "$TLS_CERT_FILE" ] && [ -f "$TLS_CERT_FILE" ]; then
        log_info "Copying TLS certificate: $TLS_CERT_FILE"
        docker cp "$TLS_CERT_FILE" "${hub_container}:/etc/verlihub/hub.crt"
    fi

    if [ -n "$TLS_KEY_FILE" ] && [ -f "$TLS_KEY_FILE" ]; then
        log_info "Copying TLS key: $TLS_KEY_FILE"
        docker cp "$TLS_KEY_FILE" "${hub_container}:/etc/verlihub/hub.key"
        docker exec "${hub_container}" chmod 600 /etc/verlihub/hub.key
    fi

    if [ -n "$TLS_CERT_FILE" ] && [ -n "$TLS_KEY_FILE" ]; then
        log_success "TLS certificates copied"
    else
        log_info "TLS enabled — Verlihub will generate self-signed certificate"
    fi
}

# ── Legacy-only: register users via SQL ──────────────────────────────────────

register_users() {
    if [ "$EDITION" = "py" ]; then
        log_info "verlihub-py registers users from YAML — skipping SQL registration"
        return 0
    fi

    log_info "Registering users from config..."

    local db_container="${CONTAINER_PREFIX}-db"

    if ! docker ps --format '{{.Names}}' | grep -q "^${db_container}$"; then
        log_warn "DB container not running, skipping user registration"
        return 1
    fi

    local sql
    sql=$(python3 docker/register_users.py \
        --config "$CONFIG_FILE" \
        --dry-run 2>/dev/null | sed -n '/--- SQL/,/--- End SQL/p' | sed '1d;$d')

    if [ -z "$sql" ]; then
        log_info "No additional users to register"
        return 0
    fi

    echo "$sql" | docker exec -i "$db_container" \
        mysql -u"$DB_USER" -p"$DB_PASS" "$DB_NAME"

    local exit_code=$?
    if [ $exit_code -eq 0 ]; then
        log_success "Users registered"
    else
        log_warn "User registration may have had issues (exit code: $exit_code)"
    fi
    return $exit_code
}

# ── Startup commands (legacy only — verlihub-py handles from YAML) ───────────

run_startup_commands() {
    if [ "$SKIP_COMMANDS" = "true" ]; then
        log_info "Skipping startup commands (--skip-commands)"
        return 0
    fi

    if [ "$HAS_COMMANDS" != "true" ]; then
        log_info "No startup commands configured"
        return 0
    fi

    if [ "$EDITION" = "py" ]; then
        log_info "verlihub-py handles startup commands from YAML — skipping NMDC commands"
        return 0
    fi

    log_info "Running $CMD_COUNT startup command(s)..."

    local debug_flag=""
    [ "$DEBUG" = "true" ] && debug_flag="--debug"

    python3 docker/run_commands.py \
        --config "$CONFIG_FILE" \
        --host localhost \
        --port "$HUB_PORT" \
        --nick "$ADMIN_NICK" \
        --password "$ADMIN_PASS" \
        --retries 30 \
        --delay 1.0 \
        $debug_flag

    local exit_code=$?
    if [ $exit_code -eq 0 ]; then
        log_success "All startup commands executed"
    else
        log_warn "Some commands may have failed (exit code: $exit_code)"
    fi
    return $exit_code
}

# ── Start ────────────────────────────────────────────────────────────────────

start_production() {
    log_info "Starting Verlihub production instance..."
    log_info "Config: $CONFIG_FILE"

    if [ ! -f "$CONFIG_FILE" ]; then
        log_error "Config file not found: $CONFIG_FILE"
        log_info "Copy production.example.yml to $CONFIG_FILE and customize it"
        exit 1
    fi

    # Parse configuration
    log_info "Parsing configuration..."
    eval "$(parse_config)"

    if [ $? -ne 0 ]; then
        log_error "Failed to parse config file"
        exit 1
    fi

    # Auto-detect edition if not set
    if [ -z "$EDITION" ]; then
        if [ -n "$YAML_EDITION" ]; then
            EDITION="$YAML_EDITION"
        elif [ "$DB_TYPE" = "postgresql" ]; then
            # PostgreSQL → must be verlihub-py (legacy doesn't support it)
            EDITION="py"
        else
            EDITION="legacy"
        fi
    fi

    # Validate edition + DB combo
    if [ "$EDITION" = "legacy" ] && [ "$DB_TYPE" = "postgresql" ]; then
        log_error "Legacy verlihub does not support PostgreSQL."
        log_info  "Use --edition py or change database.type to mysql in your YAML."
        exit 1
    fi

    # API port for verlihub-py comes from the YAML api.port
    if [ "$EDITION" = "py" ]; then
        local py_api_port
        py_api_port=$(python3 -c "
import yaml
with open('$CONFIG_FILE') as f:
    c = yaml.safe_load(f)
print(c.get('api', {}).get('port', 8000))
" 2>/dev/null)
        if [ -n "$py_api_port" ]; then
            API_PORT="$py_api_port"
        fi
    fi

    # Parse user counts
    IFS=',' read -r MASTERS ADMINS OPS VIPS REGS <<< "$USER_COUNTS"

    echo ""
    echo "Configuration:"
    echo "  Edition:    $EDITION"
    echo "  Database:   $DB_TYPE"
    echo "  Hub Name:   $HUB_NAME"
    echo "  Hub Port:   $HUB_PORT"
    [ -n "$HUB_DESC" ] && echo "  Hub Desc:   $HUB_DESC"
    [ -n "$MOTD_FILE" ] && echo "  MOTD File:  $MOTD_FILE"
    echo "  Login User: $ADMIN_NICK"
    if [ "$EDITION" = "legacy" ]; then
        echo "  Python Mode: $PYTHON_MODE"
    fi
    echo "  API Enabled: $API_ENABLED (port $API_PORT)"
    echo "  TLS Enabled: $TLS_ENABLED"
    if [ "$TLS_ENABLED" = "true" ]; then
        echo "    TLS Only: $TLS_ONLY_MODE"
        echo "    TLS Min Version: 1.$TLS_MIN_VERSION"
        [ -n "$TLS_CERT_FILE" ] && echo "    Certificate: $TLS_CERT_FILE" || echo "    Certificate: (self-signed)"
    fi
    echo "  Matterbridge: $MATTERBRIDGE_ENABLED"
    echo "  Container Prefix: $CONTAINER_PREFIX"
    if [ "$EDITION" = "legacy" ]; then
        echo "  Startup Commands: $CMD_COUNT"
    fi
    echo ""
    echo "Users to register:"
    echo "  Masters: $MASTERS, Admins: $ADMINS, Operators: $OPS, VIPs: $VIPS, Registered: $REGS"
    echo ""

    # Generate docker-compose file
    generate_compose

    # Check if already initialized (legacy only)
    local first_run=true
    if [ "$EDITION" = "legacy" ] && is_initialized; then
        log_info "Database already initialized, skipping initial setup"
        first_run=false
    fi

    # Build
    if [ "$REBUILD" = "true" ]; then
        log_info "Rebuilding Docker images..."
        docker compose -f docker-compose.production.yml build --no-cache
    else
        log_info "Building Docker images (use --rebuild to force)..."
        docker compose -f docker-compose.production.yml build
    fi

    # Start containers
    log_info "Starting containers..."
    docker compose -f docker-compose.production.yml up -d

    # Wait for hub to be ready
    if ! wait_for_hub; then
        log_error "Hub failed to start. Check logs with: $0 --logs"
        exit 1
    fi

    # Give hub a moment to fully initialize
    sleep 5

    # Post-start steps (legacy only — verlihub-py handles all of this from YAML)
    if [ "$EDITION" = "legacy" ]; then
        update_hub_settings || true
        update_motd || true
        update_tls_certs || true
        register_users || true

        if [ "$first_run" = "true" ] || [ "$HAS_COMMANDS" = "true" ]; then
            run_startup_commands || true
        fi
    fi

    echo ""
    log_success "Verlihub production instance is running!"
    echo ""
    echo "  Edition: $EDITION"
    echo "  Hub:     dc://$HOSTNAME:$HUB_PORT"
    if [ "$TLS_ENABLED" = "true" ]; then
        echo "  Hub TLS: nmdcs://$HOSTNAME:$HUB_PORT"
    fi
    if [ "$API_ENABLED" = "true" ]; then
        if [ "$EDITION" = "py" ]; then
            echo "  API:       http://$HOSTNAME:$API_PORT"
            echo "  Dashboard: http://$HOSTNAME:$API_PORT/dashboard/spa"
        elif [ "$PYTHON_MODE" = "single" ]; then
            echo "  API:       http://$HOSTNAME:$API_PORT"
            echo "  Web App:   http://$HOSTNAME:$API_PORT/app"
        fi
    fi
    echo ""
    echo "Commands:"
    echo "  View logs:  $0 --logs"
    echo "  Stop:       $0 --stop"
    echo "  Restart:    $0 --restart"
    echo "  Status:     $0 --status"
}

# ── Stop ─────────────────────────────────────────────────────────────────────

stop_production() {
    log_info "Stopping Verlihub production instance..."

    if [ ! -f "docker-compose.production.yml" ]; then
        log_warn "No docker-compose.production.yml found"
        if [ -f "$CONFIG_FILE" ]; then
            eval "$(parse_config)"
            log_info "Stopping containers with prefix: $CONTAINER_PREFIX"
            docker stop "${CONTAINER_PREFIX}-hub" "${CONTAINER_PREFIX}-db" 2>/dev/null || true
        fi
        return
    fi

    docker compose -f docker-compose.production.yml down
    log_success "Production instance stopped"
}

# ── Logs / Status ────────────────────────────────────────────────────────────

show_logs() {
    if [ ! -f "docker-compose.production.yml" ]; then
        log_error "No docker-compose.production.yml found. Start the instance first."
        exit 1
    fi
    docker compose -f docker-compose.production.yml logs -f
}

show_status() {
    if [ ! -f "docker-compose.production.yml" ]; then
        log_warn "No docker-compose.production.yml found"
    else
        docker compose -f docker-compose.production.yml ps
    fi
}

# ── Main ─────────────────────────────────────────────────────────────────────

main() {
    check_dependencies

    case "$ACTION" in
        start)   start_production ;;
        stop)    stop_production ;;
        restart) stop_production; sleep 2; start_production ;;
        logs)    show_logs ;;
        status)  show_status ;;
    esac
}

main
