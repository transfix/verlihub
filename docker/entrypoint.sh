#!/bin/bash
set -e

# Verlihub Docker Entrypoint
# Handles database initialization and startup

# Default configuration
VH_DB_HOST="${VH_DB_HOST:-mysql}"
VH_DB_USER="${VH_DB_USER:-verlihub}"
VH_DB_PASS="${VH_DB_PASS:-verlihub}"
VH_DB_NAME="${VH_DB_NAME:-verlihub}"
VH_HUB_NAME="${VH_HUB_NAME:-Test Hub}"
VH_HUB_PORT="${VH_HUB_PORT:-4111}"
VH_ADMIN_NICK="${VH_ADMIN_NICK:-admin}"
VH_ADMIN_PASS="${VH_ADMIN_PASS:-admin}"

# Wait for MySQL to be ready
wait_for_mysql() {
    echo "[entrypoint] Waiting for MySQL at $VH_DB_HOST..."
    local max_attempts=30
    local attempt=1
    
    while [ $attempt -le $max_attempts ]; do
        if mysql -h"$VH_DB_HOST" -u"$VH_DB_USER" -p"$VH_DB_PASS" -e "SELECT 1" &>/dev/null; then
            echo "[entrypoint] MySQL is ready!"
            return 0
        fi
        echo "[entrypoint] MySQL not ready, attempt $attempt/$max_attempts..."
        sleep 2
        attempt=$((attempt + 1))
    done
    
    echo "[entrypoint] ERROR: MySQL did not become ready in time"
    exit 1
}

# Create database if not exists
create_database() {
    echo "[entrypoint] Checking database $VH_DB_NAME..."
    mysql -h"$VH_DB_HOST" -u"$VH_DB_USER" -p"$VH_DB_PASS" -e "CREATE DATABASE IF NOT EXISTS $VH_DB_NAME CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" || {
        echo "[entrypoint] Note: Could not create database (may already exist or require root)"
    }
}

# Initialize verlihub configuration if needed
init_config() {
    if [ ! -f /etc/verlihub/dbconfig ]; then
        echo "[entrypoint] Creating initial configuration..."
        
        # Create dbconfig file
        cat > /etc/verlihub/dbconfig << EOF
db_host = $VH_DB_HOST
db_user = $VH_DB_USER
db_pass = $VH_DB_PASS
db_data = $VH_DB_NAME
db_charset = utf8mb4
locale = en_US.UTF-8
config = /etc/verlihub
EOF
        
        # Run verlihub to create tables
        echo "[entrypoint] Initializing database tables..."
        timeout 10 verlihub --init 2>/dev/null || true
        
        # Configure basic hub settings and register admin user
        mysql -h"$VH_DB_HOST" -u"$VH_DB_USER" -p"$VH_DB_PASS" "$VH_DB_NAME" << EOF || true
-- Hub configuration
INSERT INTO SetupList (file, var, val) VALUES 
    ('config', 'hub_host', '0.0.0.0'),
    ('config', 'hub_name', '$VH_HUB_NAME'),
    ('config', 'listen_port', '$VH_HUB_PORT')
ON DUPLICATE KEY UPDATE val = VALUES(val);

-- Register admin user (class 10 = master)
INSERT INTO reglist (nick, class, class_protect, class_hidekick, hide_kick, hide_keys, show_keys, reg_date, reg_op, pwd_change, pwd_crypt, login_pwd, login_last, logout_last, login_cnt, login_ip, error_last, error_cnt, error_ip, enabled, email, note_op, note_usr, alternate_ip, auth_ip, fake_ip, flags) VALUES 
    ('$VH_ADMIN_NICK', 10, 10, 10, 0, 0, 0, UNIX_TIMESTAMP(), 'docker', 0, 1, '$VH_ADMIN_PASS', 0, 0, 0, '', 0, 0, '', 1, 'admin@localhost', 'Docker auto-created', '', '', '', '', 0)
ON DUPLICATE KEY UPDATE login_pwd = VALUES(login_pwd), class = 10;

-- Enable Python plugin
INSERT INTO pi_plug (nick, path, dest, detail, autoload) VALUES
    ('python', 'libpython_pi.so', '', 'Python scripting plugin', 1)
ON DUPLICATE KEY UPDATE autoload = 1;
EOF
        
        echo "[entrypoint] Configuration initialized with admin user: $VH_ADMIN_NICK"
    else
        echo "[entrypoint] Configuration already exists"
    fi
}

# Main entrypoint logic
main() {
    case "$1" in
        verlihub)
            wait_for_mysql
            create_database
            init_config
            echo "[entrypoint] Starting Verlihub..."
            exec verlihub -d /etc/verlihub
            ;;
        test)
            wait_for_mysql
            create_database
            init_config
            echo "[entrypoint] Running tests..."
            exec "$@"
            ;;
        shell|bash|sh)
            exec /bin/bash
            ;;
        *)
            exec "$@"
            ;;
    esac
}

main "$@"
