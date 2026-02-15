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
    # Set up plugin symlinks (always, even if config exists)
    mkdir -p /etc/verlihub/plugins
    if [ -f /usr/local/lib/libpython_pi.so ] && [ ! -f /etc/verlihub/plugins/libpython_pi.so ]; then
        echo "[entrypoint] Setting up Python plugin symlink..."
        ln -sf /usr/local/lib/libpython_pi.so /etc/verlihub/plugins/libpython_pi.so
    fi
    
    # Set up scripts symlinks for Python plugin
    if [ -d /usr/local/share/verlihub/scripts ]; then
        echo "[entrypoint] Setting up scripts symlinks..."
        mkdir -p /etc/verlihub/scripts
        ln -sf /usr/local/share/verlihub/scripts/* /etc/verlihub/scripts/ 2>/dev/null || true
    fi
    
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
        
        # Run verlihub briefly to create tables (it creates them when fully started)
        echo "[entrypoint] Starting verlihub to create tables..."
        verlihub -d /etc/verlihub &
        VH_PID=$!
        
        # Wait for verlihub to start listening (tables are created when it opens port)
        echo "[entrypoint] Waiting for verlihub to start..."
        for i in $(seq 1 60); do
            if nc -z 127.0.0.1 "$VH_HUB_PORT" 2>/dev/null; then
                echo "[entrypoint] Verlihub is listening on port $VH_HUB_PORT"
                sleep 2  # Give it a moment to complete table creation
                break
            fi
            echo "[entrypoint] Waiting for port $VH_HUB_PORT... ($i/60)"
            sleep 1
        done
        
        # Verify tables exist before proceeding
        echo "[entrypoint] Verifying database tables..."
        for i in $(seq 1 30); do
            if mysql -h"$VH_DB_HOST" -u"$VH_DB_USER" -p"$VH_DB_PASS" "$VH_DB_NAME" -e "SELECT 1 FROM reglist LIMIT 1" &>/dev/null; then
                echo "[entrypoint] Tables verified successfully"
                break
            fi
            echo "[entrypoint] Waiting for tables... ($i/30)"
            sleep 1
        done
        
        # Stop the temporary verlihub instance
        echo "[entrypoint] Stopping temporary verlihub..."
        kill $VH_PID 2>/dev/null || true
        wait $VH_PID 2>/dev/null || true
        sleep 2
        
        # Configure basic hub settings and register admin user
        echo "[entrypoint] Configuring hub and creating admin user..."
        mysql -h"$VH_DB_HOST" -u"$VH_DB_USER" -p"$VH_DB_PASS" "$VH_DB_NAME" << EOF || true
-- Hub configuration
INSERT INTO SetupList (file, var, val) VALUES 
    ('config', 'hub_host', '0.0.0.0'),
    ('config', 'hub_name', '$VH_HUB_NAME'),
    ('config', 'listen_port', '$VH_HUB_PORT')
ON DUPLICATE KEY UPDATE val = VALUES(val);

-- Register admin user (class 10 = master, pwd_crypt=0 for plaintext password)
INSERT INTO reglist (nick, class, class_protect, class_hidekick, hide_kick, hide_keys, show_keys, reg_date, reg_op, pwd_change, pwd_crypt, login_pwd, login_last, logout_last, login_cnt, login_ip, error_last, error_cnt, error_ip, enabled, note_op, note_usr, alternate_ip, auth_ip, fake_ip) VALUES 
    ('$VH_ADMIN_NICK', 10, 10, 10, 0, 0, 0, UNIX_TIMESTAMP(), 'docker', 0, 0, '$VH_ADMIN_PASS', 0, 0, 0, '', 0, 0, '', 1, 'Docker auto-created', '', '', '', '')
ON DUPLICATE KEY UPDATE login_pwd = VALUES(login_pwd), pwd_crypt = 0, class = 10;

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
