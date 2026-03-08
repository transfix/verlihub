#!/bin/bash
# Verlihub Production Runner
# Launches a production instance with configuration from a YAML file
#
# Usage:
#   # Legacy verlihub with MySQL (default)
#   sg docker -c "./run_production.sh --config production.yml"
#
#   # verlihub-py with PostgreSQL
#   sg docker -c "./run_production.sh --config production.yml --edition py"
#
#   # Auto-detect edition and database from YAML
#   sg docker -c "./run_production.sh --config production.yml"
#
#   # Lifecycle
#   sg docker -c "./run_production.sh --stop"
#   sg docker -c "./run_production.sh --logs"
#   sg docker -c "./run_production.sh --status"

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Default config file
CONFIG_FILE="production.yml"
ACTION="start"
REBUILD=false
SKIP_COMMANDS=false
DEBUG=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --config|-c)
            CONFIG_FILE="$2"
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
            echo "  --config, -c FILE  Use specified YAML config (default: production.yml)"
            echo "  --stop             Stop the production instance"
            echo "  --restart          Restart the production instance"
            echo "  --logs             Show container logs"
            echo "  --status           Show container status"
            echo "  --rebuild          Force rebuild of Docker images"
            echo "  --skip-commands    Skip running startup commands"
            echo "  --debug            Enable debug output"
            echo "  -h, --help         Show this help message"
            echo ""
            echo "Examples:"
            echo "  sg docker -c \"$0\"                                  # Default (legacy + MySQL)"
            echo "  sg docker -c \"$0 --edition py\"                     # verlihub-py + auto DB"
            echo "  sg docker -c \"$0 --edition py --config hub.yml\"    # verlihub-py + custom config"
            echo "  sg docker -c \"$0 --stop\"                           # Stop instance"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

log_info() {
    echo -e "${BLUE}→ $1${NC}"
}

log_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

log_warn() {
    echo -e "${YELLOW}! $1${NC}"
}

log_error() {
    echo -e "${RED}✗ $1${NC}"
}

# Check for required tools
check_dependencies() {
    if ! command -v python3 &> /dev/null; then
        log_error "python3 not found"
        exit 1
    fi
    
    if ! command -v docker &> /dev/null; then
        log_error "docker not found"
        exit 1
    fi
    
    # Check for PyYAML
    if ! python3 -c "import yaml" 2>/dev/null; then
        log_warn "PyYAML not installed, installing..."
        pip3 install --user pyyaml
    fi
}

# Parse YAML config using Python
parse_config() {
    python3 << EOF
import yaml
import sys
import os

config_file = "$CONFIG_FILE"
if not os.path.exists(config_file):
    print(f"ERROR: Config file not found: {config_file}", file=sys.stderr)
    sys.exit(1)

with open(config_file, 'r') as f:
    config = yaml.safe_load(f)

# Output shell variables
db = config.get('database', {})
hub = config.get('hub', {})
docker_cfg = config.get('docker', {})
api = config.get('api', {})
matterbridge = config.get('matterbridge', {})

# Users structure - support both old 'admin' and new 'users' format
users = config.get('users', {})
if users:
    # New format: users.masters, users.admins, etc.
    masters = users.get('masters', [])
    if masters:
        admin_nick = masters[0].get('nick', 'admin')
        admin_pass = masters[0].get('password', 'admin')
    else:
        admin_nick = 'admin'
        admin_pass = 'admin'
else:
    # Old format: admin.nick, admin.password
    admin = config.get('admin', {})
    admin_nick = admin.get('nick', 'admin')
    admin_pass = admin.get('password', 'admin')

print(f"DB_HOST={db.get('host', 'mysql')}")
print(f"DB_USER={db.get('user', 'verlihub')}")
print(f"DB_PASS={db.get('password', 'verlihub')}")
print(f"DB_NAME={db.get('name', 'verlihub')}")
print(f"HUB_NAME={hub.get('name', 'My Hub')}")
print(f"HUB_DESC={hub.get('description', '')}")
print(f"HUB_PORT={hub.get('port', 4111)}")
print(f"MOTD_FILE={hub.get('motd_file', '')}")
print(f"ADMIN_NICK={admin_nick}")
print(f"ADMIN_PASS={admin_pass}")
print(f"PYTHON_MODE={config.get('python_mode', 'single')}")
print(f"API_ENABLED={str(api.get('enabled', True)).lower()}")
print(f"API_PORT={api.get('port', 30000)}")
print(f"CONFIG_VOLUME={docker_cfg.get('config_volume', 'verlihub-prod-config')}")
print(f"MYSQL_VOLUME={docker_cfg.get('mysql_volume', 'verlihub-prod-mysql')}")
print(f"NETWORK={docker_cfg.get('network', 'verlihub-prod-net')}")
print(f"CONTAINER_PREFIX={docker_cfg.get('container_prefix', 'vh-prod')}")
print(f"RESTART_POLICY={docker_cfg.get('restart_policy', 'unless-stopped')}")

# CORS origins as space-separated
cors = api.get('cors_origins', [])
print(f"CORS_ORIGINS={' '.join(cors)}")

# Matterbridge config
print(f"MATTERBRIDGE_ENABLED={str(matterbridge.get('enabled', False)).lower()}")
print(f"MATTERBRIDGE_URL={matterbridge.get('api_url', 'http://matterbridge:4242')}")
print(f"MATTERBRIDGE_TOKEN={matterbridge.get('api_token', '')}")
print(f"MATTERBRIDGE_GATEWAY={matterbridge.get('gateway', 'verlihub')}")
print(f"MATTERBRIDGE_CHANNEL={matterbridge.get('channel', '#general')}")

# Matterbridge
print(f"MATTERBRIDGE_ENABLED={q(str(mb.get('enabled', False)).lower())}")
print(f"MATTERBRIDGE_URL={q(mb.get('api_url', 'http://matterbridge:4242'))}")
print(f"MATTERBRIDGE_TOKEN={q(mb.get('api_token', ''))}")
print(f"MATTERBRIDGE_GATEWAY={q(mb.get('gateway', 'verlihub'))}")
print(f"MATTERBRIDGE_CHANNEL={q(mb.get('channel', '#general'))}")

# LLM (Ollama sidecar)
llm = config.get("llm", {})
llm_enabled = str(llm.get('enabled', False)).lower() == 'true'
llm_base_url = llm.get('base_url', 'http://ollama:11434/v1')
# Only spin up the Ollama container when the base_url points at the local sidecar
needs_ollama = llm_enabled and ('ollama' in llm_base_url and ('localhost' in llm_base_url or '127.0.0.1' in llm_base_url or 'vh-prod-ollama' in llm_base_url or llm_base_url == 'http://ollama:11434/v1'))
print(f"LLM_ENABLED={q(str(llm_enabled).lower())}")
print(f"LLM_MODEL={q(llm.get('model', 'qwen2.5:7b'))}")
print(f"LLM_BASE_URL={q(llm_base_url)}")
print(f"LLM_PORT={q(llm.get('ollama_port', 11434))}")
print(f"NEEDS_OLLAMA={q(str(needs_ollama).lower())}")

# Startup commands
startup_cmds = config.get("startup_commands", [])
plugin_cmds  = config.get("plugin_commands", [])
all_cmds = startup_cmds + plugin_cmds
print(f"HAS_COMMANDS={'true' if all_cmds else 'false'}")
print(f"CMD_COUNT={len(all_cmds)}")

# Count users by class
user_counts = {
    'masters': len(users.get('masters', [])) if users else (1 if config.get('admin') else 0),
    'admins': len(users.get('admins', [])) if users else 0,
    'operators': len(users.get('operators', [])) if users else 0,
    'vips': len(users.get('vips', [])) if users else 0,
    'registered': len(users.get('registered', [])) if users else 0,
}
print(f"USER_COUNTS={user_counts['masters']},{user_counts['admins']},{user_counts['operators']},{user_counts['vips']},{user_counts['registered']}")

# TLS configuration
tls = config.get('tls', {})
print(f"TLS_ENABLED={str(tls.get('enabled', False)).lower()}")
print(f"TLS_INTERNAL_PORT={tls.get('internal_port', 411)}")
print(f"TLS_ONLY_MODE={str(tls.get('only_mode', False)).lower()}")
print(f"TLS_MIN_VERSION={tls.get('min_version', 2)}")
print(f"TLS_CERT_FILE={tls.get('cert_file', '')}")
print(f"TLS_KEY_FILE={tls.get('key_file', '')}")
print(f"TLS_CERT_ORG={tls.get('cert_org', 'Verlihub')}")
print(f"TLS_CERT_EMAIL={tls.get('cert_email', 'verlihub@localhost')}")
EOF
}

# Generate docker-compose file for production
generate_compose() {
    local compose_file="docker-compose.production.yml"
    
    log_info "Generating $compose_file..."
    
    cat > "$compose_file" << EOF
# Verlihub Production Docker Compose
# Generated from: $CONFIG_FILE
# Generated at: $(date -Iseconds)
#
# DO NOT EDIT - regenerate with: ./run_production.sh --config $CONFIG_FILE

services:
  # MySQL database
  ${CONTAINER_PREFIX}-mysql:
    image: mysql:8.0
    container_name: ${CONTAINER_PREFIX}-mysql
    environment:
      MYSQL_ROOT_PASSWORD: ${DB_PASS}_root
      MYSQL_DATABASE: ${DB_NAME}
      MYSQL_USER: ${DB_USER}
      MYSQL_PASSWORD: ${DB_PASS}
    volumes:
      - ${MYSQL_VOLUME}:/var/lib/mysql
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-u\${MYSQL_USER}", "-p\${MYSQL_PASSWORD}"]
      interval: 10s
      timeout: 5s
      retries: 10
    networks:
      ${NETWORK}:
        aliases:
          - ${DB_HOST}
    restart: ${RESTART_POLICY}

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
      ${NETWORK}:
        aliases:
          - ${DB_HOST}
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
      ${CONTAINER_PREFIX}-mysql:
        condition: service_healthy
    environment:
      VH_DB_HOST: ${CONTAINER_PREFIX}-mysql
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

    # Add API port if enabled
    if [ "$API_ENABLED" = "true" ] && [ "$PYTHON_MODE" = "single" ]; then
        cat >> "$compose_file" << EOF
      - "${API_PORT}:${API_PORT}"
EOF
    fi

    cat >> "$compose_file" << EOF
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
    if [ "$DB_TYPE" = "sqlite" ]; then
        # SQLite — no db container dependency, mount a volume for the DB file
        local sqlite_vol="${CONFIG_VOLUME}-sqlite"
        local sqlite_mount="/data"
        local sqlite_file="${DB_PATH:-/data/verlihub.db}"
        cat << EOF
  # Verlihub-py Hub (Python) — SQLite
  ${CONTAINER_PREFIX}-hub:
    build:
      context: .
      dockerfile: docker/Dockerfile.verlihub-py
    container_name: ${CONTAINER_PREFIX}-hub
    command: >
      python3 -m verlihub.server
        -c /config/production.yml
        --mode both
        --host 0.0.0.0
    environment:
      PYTHONUNBUFFERED: "1"
    ports:
      - "${HUB_PORT}:${HUB_PORT}"
      - "${API_PORT}:${API_PORT}"
    volumes:
      - ./${CONFIG_FILE}:/config/production.yml:ro
      - ${sqlite_vol}:${sqlite_mount}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:${API_PORT}/health"]
      interval: 10s
      timeout: 5s
      retries: 15
      start_period: 10s
    networks:
      - ${NETWORK}
    restart: ${RESTART_POLICY}
EOF
    else
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
      python3 -m verlihub.server
        -c /config/production.yml
        --mode both
        --host 0.0.0.0
    environment:
      PYTHONUNBUFFERED: "1"
    ports:
      - "${HUB_PORT}:${HUB_PORT}"
      - "${API_PORT}:${API_PORT}"
    volumes:
      - ./${CONFIG_FILE}:/config/production.yml:ro
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:${API_PORT}/health"]
      interval: 10s
      timeout: 5s
      retries: 15
      start_period: 10s
    networks:
      - ${NETWORK}
    restart: ${RESTART_POLICY}
EOF
    fi
}

# ── Compose generation — Ollama LLM sidecar ──────────────────────────────────

_compose_ollama_service() {
    cat << EOF
  # Ollama LLM inference server
  ${CONTAINER_PREFIX}-ollama:
    image: ollama/ollama:latest
    container_name: ${CONTAINER_PREFIX}-ollama
    environment:
      OLLAMA_HOST: "0.0.0.0"
    volumes:
      - ${CONTAINER_PREFIX}-ollama-models:/root/.ollama
    healthcheck:
      test: ["CMD", "ollama", "list"]
      interval: 10s
      timeout: 10s
      retries: 30
      start_period: 15s
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

    # Database service (not needed for SQLite)
    if [ "$DB_TYPE" = "sqlite" ]; then
        log_info "SQLite mode — no database container needed"
    elif [ "$DB_TYPE" = "postgresql" ]; then
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

    # Ollama LLM sidecar (only when base_url points at the local sidecar)
    if [ "$NEEDS_OLLAMA" = "true" ] && [ "$EDITION" = "py" ]; then
        echo "" >> "$compose_file"
        _compose_ollama_service >> "$compose_file"
    fi

    # Volumes and network
    if [ "$DB_TYPE" = "sqlite" ]; then
        local sqlite_vol="${CONFIG_VOLUME}-sqlite"
        local ollama_vol_line=""
        [ "$NEEDS_OLLAMA" = "true" ] && [ "$EDITION" = "py" ] && \
            ollama_vol_line=$'\n'"  ${CONTAINER_PREFIX}-ollama-models:"
        cat >> "$compose_file" << EOF

volumes:
  ${CONFIG_VOLUME}:
  ${sqlite_vol}:${ollama_vol_line}

networks:
  ${NETWORK}:
    driver: bridge
EOF
    else
        local ollama_vol_line=""
        [ "$NEEDS_OLLAMA" = "true" ] && [ "$EDITION" = "py" ] && \
            ollama_vol_line=$'\n'"  ${CONTAINER_PREFIX}-ollama-models:"
        cat >> "$compose_file" << EOF

volumes:
  ${DB_VOLUME}:
  ${CONFIG_VOLUME}:${ollama_vol_line}

networks:
  ${NETWORK}:
    driver: bridge
EOF

    log_success "Generated $compose_file"
}

# Check if database is already initialized
is_initialized() {
    local container="${CONTAINER_PREFIX}-mysql"
    
    # Check if mysql container is running
    if ! docker ps --format '{{.Names}}' | grep -q "^${container}$"; then
        return 1
    fi
    
    # Check if reglist table exists (indicates initialization completed)
    if docker exec "$container" mysql -u"$DB_USER" -p"$DB_PASS" "$DB_NAME" -e "SELECT 1 FROM reglist LIMIT 1" &>/dev/null; then
        return 0
    fi
    
    return 1
}

# Wait for hub to be ready
wait_for_hub() {
    local max_attempts=60
    local attempt=1
    local hub_container="${CONTAINER_PREFIX}-hub"
    
    log_info "Waiting for hub to be ready..."
    
    while [ $attempt -le $max_attempts ]; do
        # Check if container is running
        if ! docker ps --format '{{.Names}}' | grep -q "^${hub_container}$"; then
            log_warn "Hub container not running, waiting..."
            sleep 2
            attempt=$((attempt + 1))
            continue
        fi
        
        # Check if port is open
        if docker exec "$hub_container" nc -z 127.0.0.1 "$HUB_PORT" 2>/dev/null; then
            log_success "Hub is listening on port $HUB_PORT"
            return 0
        fi
        
        echo -n "."
        sleep 2
        attempt=$((attempt + 1))
    done
    
    echo ""
    log_error "Hub did not become ready in time"
    return 1
}

# Update hub settings in database (idempotent)
update_hub_settings() {
    log_info "Applying configuration to database..."
    
    local mysql_container="${CONTAINER_PREFIX}-mysql"
    
    # Check if MySQL is available
    if ! docker ps --format '{{.Names}}' | grep -q "^${mysql_container}$"; then
        log_warn "MySQL container not running, skipping settings update"
        return 1
    fi
    
    # Generate SQL from YAML config using apply_config.py
    local sql
    sql=$(python3 docker/apply_config.py --config "$CONFIG_FILE" --dry-run 2>/dev/null | sed -n '/--- SQL/,/--- End SQL/p' | sed '1d;$d')
    
    if [ -z "$sql" ]; then
        log_info "No configuration changes to apply"
        return 0
    fi
    
    # Execute SQL
    echo "$sql" | docker exec -i "$mysql_container" mysql -u"$DB_USER" -p"$DB_PASS" "$DB_NAME" 2>/dev/null
    
    if [ $? -eq 0 ]; then
        log_success "Configuration applied to database"
        return 0
    else
        log_warn "Failed to apply some configuration"
        return 1
    fi
}

# Copy MOTD file to config volume
update_motd() {
    if [ -z "$MOTD_FILE" ]; then
        log_info "No MOTD file configured"
        return 0
    fi
    
    if [ ! -f "$MOTD_FILE" ]; then
        log_warn "MOTD file not found: $MOTD_FILE"
        return 1
    fi
    
    log_info "Updating MOTD from: $MOTD_FILE"
    
    local hub_container="${CONTAINER_PREFIX}-hub"
    
    # Check if hub container is running
    if ! docker ps --format '{{.Names}}' | grep -q "^${hub_container}$"; then
        log_warn "Hub container not running, skipping MOTD update"
        return 1
    fi
    
    # Copy MOTD file into the container's config directory
    docker cp "$MOTD_FILE" "${hub_container}:/etc/verlihub/motd"
    
    if [ $? -eq 0 ]; then
        log_success "MOTD file updated"
        return 0
    else
        log_warn "Failed to copy MOTD file"
        return 1
    fi
}

# Copy TLS certificate files to config volume
update_tls_certs() {
    if [ "$TLS_ENABLED" != "true" ]; then
        return 0
    fi
    
    local hub_container="${CONTAINER_PREFIX}-hub"
    
    # Check if hub container is running
    if ! docker ps --format '{{.Names}}' | grep -q "^${hub_container}$"; then
        log_warn "Hub container not running, skipping TLS cert update"
        return 1
    fi
    
    # Copy certificate file if specified
    if [ -n "$TLS_CERT_FILE" ]; then
        if [ ! -f "$TLS_CERT_FILE" ]; then
            log_warn "TLS certificate file not found: $TLS_CERT_FILE"
            log_info "Verlihub will generate a self-signed certificate"
        else
            log_info "Copying TLS certificate: $TLS_CERT_FILE"
            docker cp "$TLS_CERT_FILE" "${hub_container}:/etc/verlihub/hub.crt"
            if [ $? -ne 0 ]; then
                log_warn "Failed to copy TLS certificate"
                return 1
            fi
        fi
    fi
    
    # Copy key file if specified
    if [ -n "$TLS_KEY_FILE" ]; then
        if [ ! -f "$TLS_KEY_FILE" ]; then
            log_warn "TLS key file not found: $TLS_KEY_FILE"
            log_info "Verlihub will generate a self-signed certificate"
        else
            log_info "Copying TLS key: $TLS_KEY_FILE"
            docker cp "$TLS_KEY_FILE" "${hub_container}:/etc/verlihub/hub.key"
            if [ $? -ne 0 ]; then
                log_warn "Failed to copy TLS key"
                return 1
            fi
            # Set proper permissions on key file
            docker exec "${hub_container}" chmod 600 /etc/verlihub/hub.key
        fi
    fi
    
    if [ -n "$TLS_CERT_FILE" ] && [ -n "$TLS_KEY_FILE" ]; then
        log_success "TLS certificates copied"
    else
        log_info "TLS enabled - Verlihub will generate self-signed certificate"
    fi
    
    return 0
}

# Register users from config
register_users() {
    log_info "Registering users from config..."
    
    # Generate SQL and execute via docker exec
    local mysql_container="${CONTAINER_PREFIX}-mysql"
    
    # Check if we're running locally or via docker
    if docker ps --format '{{.Names}}' | grep -q "^${mysql_container}$"; then
        # Use docker exec to run mysql command
        local sql
        sql=$(python3 docker/register_users.py \
            --config "$CONFIG_FILE" \
            --dry-run 2>/dev/null | sed -n '/--- SQL/,/--- End SQL/p' | sed '1d;$d')
        
        if [ -n "$sql" ]; then
            echo "$sql" | docker exec -i "$mysql_container" mysql -u"$DB_USER" -p"$DB_PASS" "$DB_NAME"
            local exit_code=$?
            
            if [ $exit_code -eq 0 ]; then
                log_success "Users registered"
            else
                log_warn "User registration may have had issues (exit code: $exit_code)"
            fi
            return $exit_code
        else
            log_info "No additional users to register"
            return 0
        fi
    else
        log_warn "MySQL container not running, skipping user registration"
        return 1
    fi
}

# Run startup commands
run_startup_commands() {
    if [ "$SKIP_COMMANDS" = "true" ]; then
        log_info "Skipping startup commands (--skip-commands)"
        return 0
    fi
    
    if [ "$HAS_COMMANDS" != "true" ]; then
        log_info "No startup commands configured"
        return 0
    fi
    
    log_info "Running $CMD_COUNT startup command(s)..."
    
    # Build debug flag
    local debug_flag=""
    if [ "$DEBUG" = "true" ]; then
        debug_flag="--debug"
    fi
    
    # Run commands using the Python script
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

# Start production instance
start_production() {
    log_info "Starting Verlihub production instance..."
    log_info "Config: $CONFIG_FILE"
    
    # Check if config exists
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
    
    # Parse user counts
    IFS=',' read -r MASTERS ADMINS OPS VIPS REGS <<< "$USER_COUNTS"
    
    echo ""
    echo "Configuration:"
    echo "  Hub Name: $HUB_NAME"
    echo "  Hub Port: $HUB_PORT"
    if [ -n "$HUB_DESC" ]; then
        echo "  Hub Desc: $HUB_DESC"
    fi
    if [ -n "$MOTD_FILE" ]; then
        echo "  MOTD File: $MOTD_FILE"
    fi
    echo "  Login User: $ADMIN_NICK (first master)"
    echo "  Python Mode: $PYTHON_MODE"
    echo "  API Enabled: $API_ENABLED (port $API_PORT)"
    echo "  TLS Enabled: $TLS_ENABLED"
    if [ "$TLS_ENABLED" = "true" ]; then
        echo "    TLS Only Mode: $TLS_ONLY_MODE"
        echo "    TLS Min Version: 1.$TLS_MIN_VERSION"
        if [ -n "$TLS_CERT_FILE" ]; then
            echo "    Certificate: $TLS_CERT_FILE"
        else
            echo "    Certificate: (self-signed)"
        fi
    fi
    echo "  Matterbridge: $MATTERBRIDGE_ENABLED"
    if [ "$EDITION" = "py" ]; then
        echo "  LLM (Ollama): $LLM_ENABLED"
        [ "$LLM_ENABLED" = "true" ] && echo "    Model: $LLM_MODEL"
    fi
    echo "  Container Prefix: $CONTAINER_PREFIX"
    echo "  Startup Commands: $CMD_COUNT"
    echo ""
    echo "Users to register:"
    echo "  Masters: $MASTERS, Admins: $ADMINS, Operators: $OPS, VIPs: $VIPS, Registered: $REGS"
    echo ""
    
    # Generate docker-compose file
    generate_compose
    
    # Check if already initialized
    local first_run=true
    if is_initialized; then
        log_info "Database already initialized, skipping initial setup"
        first_run=false
    fi
    
    # Build if needed
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

    # Pull LLM model if local Ollama sidecar is running
    if [ "$NEEDS_OLLAMA" = "true" ] && [ "$EDITION" = "py" ]; then
        log_info "Pulling LLM model '${LLM_MODEL}' via Ollama (this may take a while on first run)..."
        local ollama_container="${CONTAINER_PREFIX}-ollama"
        docker exec "$ollama_container" ollama pull "$LLM_MODEL" 2>&1 | tail -5
        if [ $? -eq 0 ]; then
            log_success "Model '$LLM_MODEL' is ready"
        else
            log_warn "Failed to pull model '$LLM_MODEL' — LLM chat may not work"
        fi
    elif [ "$LLM_ENABLED" = "true" ] && [ "$EDITION" = "py" ]; then
        log_info "LLM enabled with remote endpoint: ${LLM_BASE_URL}"
        log_info "Skipping Ollama sidecar (not needed for remote providers)"
    fi

    # Post-start steps (legacy only — verlihub-py handles all of this from YAML)
    if [ "$EDITION" = "legacy" ]; then
        update_hub_settings || true
        update_motd || true
        update_tls_certs || true
        register_users || true

        if [ "$first_run" = "true" ] || [ "$HAS_COMMANDS" = "true" ]; then
            run_startup_commands || true
        fi
        
        # Auto-load Lua scripts after startup commands (which load the Lua plugin)
        run_lua_autoload || true
    fi
    
    echo ""
    log_success "Verlihub production instance is running!"
    echo ""
    echo "  Hub: dc://$HOSTNAME:$HUB_PORT"
    if [ "$TLS_ENABLED" = "true" ]; then
        echo "  Hub (TLS): nmdcs://$HOSTNAME:$HUB_PORT"
    fi
    if [ "$API_ENABLED" = "true" ] && [ "$PYTHON_MODE" = "single" ]; then
        echo "  API: http://$HOSTNAME:$API_PORT"
        echo "  Web App: http://$HOSTNAME:$API_PORT/app"
    fi
    if [ "$LLM_ENABLED" = "true" ] && [ "$EDITION" = "py" ]; then
        echo "  LLM Chat:  http://$HOSTNAME:$API_PORT/api/v1/llm/chat"
        echo "  LLM Model: $LLM_MODEL (via Ollama)"
    fi
    echo ""
    echo "Commands:"
    echo "  View logs:    $0 --logs"
    echo "  Stop:         $0 --stop"
    echo "  Restart:      $0 --restart"
    echo "  Status:       $0 --status"
}

# Stop production instance
stop_production() {
    log_info "Stopping Verlihub production instance..."
    
    if [ ! -f "docker-compose.production.yml" ]; then
        log_warn "No docker-compose.production.yml found"
        # Try to stop by container prefix
        if [ -f "$CONFIG_FILE" ]; then
            eval "$(parse_config)"
            log_info "Stopping containers with prefix: $CONTAINER_PREFIX"
            docker stop "${CONTAINER_PREFIX}-hub" "${CONTAINER_PREFIX}-mysql" 2>/dev/null || true
        fi
        return
    fi
    
    docker compose -f docker-compose.production.yml down
    log_success "Production instance stopped"
}

# Show logs
show_logs() {
    if [ ! -f "docker-compose.production.yml" ]; then
        log_error "No docker-compose.production.yml found. Start the instance first."
        exit 1
    fi
    
    docker compose -f docker-compose.production.yml logs -f
}

# Show status
show_status() {
    if [ ! -f "docker-compose.production.yml" ]; then
        log_warn "No docker-compose.production.yml found"
    else
        docker compose -f docker-compose.production.yml ps
    fi
}

# Main
main() {
    check_dependencies
    
    case "$ACTION" in
        start)
            start_production
            ;;
        stop)
            stop_production
            ;;
        restart)
            stop_production
            sleep 2
            start_production
            ;;
        logs)
            show_logs
            ;;
        status)
            show_status
            ;;
    esac
}

main
