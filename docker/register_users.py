#!/usr/bin/env python3
"""
User Registration Script for Verlihub Production Setup

Registers users from the YAML configuration file into the Verlihub database.
Supports multiple user classes: masters, admins, operators, VIPs, registered.

Usage:
    python register_users.py --config production.yml --host mysql --db verlihub
"""

import argparse
import sys
import os
import subprocess

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# User class mappings (Verlihub class levels)
USER_CLASSES = {
    'masters': 10,      # Full hub control
    'admins': 5,        # User management, kicks, bans
    'operators': 3,     # Basic moderation
    'vips': 2,          # Extra privileges
    'registered': 1,    # Bypass guest limits
}


def load_config(config_path: str) -> dict:
    """Load configuration from YAML file"""
    if not HAS_YAML:
        print("ERROR: PyYAML not installed. Install with: pip install pyyaml")
        sys.exit(1)
    
    if not os.path.exists(config_path):
        print(f"ERROR: Config file not found: {config_path}")
        sys.exit(1)
    
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def generate_sql_for_users(config: dict) -> str:
    """Generate SQL INSERT statements for all configured users"""
    users_cfg = config.get('users', {})
    
    if not users_cfg:
        # Old format - single admin
        admin_cfg = config.get('admin', {})
        if admin_cfg:
            nick = admin_cfg.get('nick', 'admin')
            password = admin_cfg.get('password', 'admin')
            note = admin_cfg.get('note', 'From production config')
            return generate_user_sql(nick, password, 10, note)
        return ""
    
    sql_statements = []
    
    for class_name, class_level in USER_CLASSES.items():
        users = users_cfg.get(class_name, [])
        if not users:
            continue
        
        for user in users:
            nick = user.get('nick')
            password = user.get('password')
            note = user.get('note', f'From production config ({class_name})')
            
            if nick and password:
                sql_statements.append(generate_user_sql(nick, password, class_level, note))
    
    return '\n'.join(sql_statements)


def generate_user_sql(nick: str, password: str, user_class: int, note: str = '') -> str:
    """Generate SQL INSERT for a single user"""
    # Escape single quotes in strings
    nick = nick.replace("'", "''")
    password = password.replace("'", "''")
    note = note.replace("'", "''")
    
    class_protect = user_class  # Can only be protected by same or higher class
    class_hidekick = user_class  # Can hide kicks from same or lower class
    
    return f"""
INSERT INTO reglist (nick, class, class_protect, class_hidekick, hide_kick, hide_keys, show_keys, 
                     reg_date, reg_op, pwd_change, pwd_crypt, login_pwd, login_last, logout_last, 
                     login_cnt, login_ip, error_last, error_cnt, error_ip, enabled, note_op, 
                     note_usr, alternate_ip, auth_ip, fake_ip) 
VALUES ('{nick}', {user_class}, {class_protect}, {class_hidekick}, 0, 0, 0, 
        UNIX_TIMESTAMP(), 'production-setup', 0, 0, '{password}', 0, 0, 
        0, '', 0, 0, '', 1, '{note}', 
        '', '', '', '')
ON DUPLICATE KEY UPDATE 
    login_pwd = VALUES(login_pwd), 
    pwd_crypt = 0, 
    class = VALUES(class),
    class_protect = VALUES(class_protect),
    class_hidekick = VALUES(class_hidekick),
    note_op = VALUES(note_op);
"""


def execute_sql(sql: str, db_host: str, db_user: str, db_pass: str, db_name: str) -> bool:
    """Execute SQL against the database"""
    try:
        cmd = [
            'mysql',
            f'-h{db_host}',
            f'-u{db_user}',
            f'-p{db_pass}',
            db_name
        ]
        
        result = subprocess.run(
            cmd,
            input=sql,
            text=True,
            capture_output=True
        )
        
        if result.returncode != 0:
            print(f"ERROR: MySQL error: {result.stderr}")
            return False
        
        return True
        
    except FileNotFoundError:
        print("ERROR: mysql client not found")
        return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description='Register users from YAML config')
    parser.add_argument('--config', required=True, help='YAML config file path')
    parser.add_argument('--host', default='localhost', help='Database host')
    parser.add_argument('--user', default='verlihub', help='Database user')
    parser.add_argument('--password', default='verlihub', help='Database password')
    parser.add_argument('--database', default='verlihub', help='Database name')
    parser.add_argument('--dry-run', action='store_true', help='Print SQL without executing')
    
    args = parser.parse_args()
    
    # Load config
    config = load_config(args.config)
    
    # Get database credentials from config if available
    db_cfg = config.get('database', {})
    db_host = db_cfg.get('host', args.host)
    db_user = db_cfg.get('user', args.user)
    db_pass = db_cfg.get('password', args.password)
    db_name = db_cfg.get('name', args.database)
    
    # Override with container name if docker config present
    docker_cfg = config.get('docker', {})
    if docker_cfg.get('container_prefix'):
        db_host = f"{docker_cfg['container_prefix']}-mysql"
    
    # Generate SQL
    sql = generate_sql_for_users(config)
    
    if not sql.strip():
        print("No users to register")
        return
    
    # Count users
    users_cfg = config.get('users', {})
    total_users = 0
    for class_name in USER_CLASSES:
        total_users += len(users_cfg.get(class_name, []))
    
    if not users_cfg:
        total_users = 1 if config.get('admin') else 0
    
    print(f"Registering {total_users} user(s)...")
    
    if args.dry_run:
        print("\n--- SQL (dry run) ---")
        print(sql)
        print("--- End SQL ---\n")
        return
    
    # Execute SQL
    if execute_sql(sql, db_host, db_user, db_pass, db_name):
        print(f"Successfully registered {total_users} user(s)")
    else:
        print("Failed to register users")
        sys.exit(1)


if __name__ == '__main__':
    main()
