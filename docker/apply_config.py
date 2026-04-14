#!/usr/bin/env python3
"""
Apply YAML configuration to Verlihub database.

Generates SQL statements to update SetupList with configuration values.
Uses INSERT...ON DUPLICATE KEY UPDATE for idempotent updates.
"""

import argparse
import sys
import yaml
from typing import Any, Dict, List, Optional, Tuple


# Mapping from YAML config paths to Verlihub database variables
# Format: (yaml_path, db_var, default_value, transform_func)
CONFIG_MAPPINGS: List[Tuple[str, str, Any, Optional[callable]]] = [
    # Hub Identity
    ('hub.name', 'hub_name', None, None),
    ('hub.description', 'hub_desc', None, None),
    ('hub.host', 'hub_host', None, None),
    ('hub.owner', 'hub_owner', None, None),
    ('hub.topic', 'hub_topic', None, None),
    ('hub.category', 'hub_category', None, None),
    ('hub.icon_url', 'hub_icon_url', None, None),
    ('hub.logo_url', 'hub_logo_url', None, None),
    ('hub.encoding', 'hub_encoding', None, None),
    ('hub.extra_ports', 'extra_listen_ports', None, None),
    
    # Bots
    ('bots.security.nick', 'hub_security', None, None),
    ('bots.security.description', 'hub_security_desc', None, None),
    ('bots.opchat.nick', 'opchat_name', None, None),
    ('bots.opchat.description', 'opchat_desc', None, None),
    ('bots.opchat.min_class', 'opchat_class', None, None),
    
    # Connection Limits
    ('limits.max_users', 'max_users', None, None),
    ('limits.max_users_per_ip', 'max_users_from_ip', None, None),
    ('limits.max_passive_users', 'max_users_passive', None, None),
    ('limits.max_users_by_class.guest', 'max_users0', None, None),
    ('limits.max_users_by_class.registered', 'max_users1', None, None),
    ('limits.max_users_by_class.vip', 'max_users2', None, None),
    ('limits.max_users_by_class.operator', 'max_users3', None, None),
    ('limits.max_users_by_class.cheef', 'max_users4', None, None),
    ('limits.max_users_by_class.admin', 'max_users5', None, None),
    ('limits.max_users_by_class.master', 'max_users6', None, None),
    
    # Share Requirements
    ('share.min_share', 'min_share', None, None),
    ('share.max_share', 'max_share', None, None),
    ('share.min_share_by_class.registered', 'min_share_reg', None, None),
    ('share.min_share_by_class.vip', 'min_share_vip', None, None),
    ('share.min_share_by_class.operator', 'min_share_ops', None, None),
    ('share.passive_multiplier', 'min_share_factor_passive', None, None),
    
    # Nick Requirements
    ('nick.min_length', 'min_nick', None, None),
    ('nick.max_length', 'max_nick', None, None),
    ('nick.allowed_chars', 'nick_chars', None, None),
    ('nick.prefix', 'nick_prefix', None, None),
    ('nick.autoreg_prefix', 'nick_prefix_autoreg', None, None),
    
    # Chat Settings
    ('chat.max_message_length', 'max_chat_msg', None, None),
    ('chat.max_pm_length', 'max_pm_msg', None, None),
    ('chat.max_lines_per_message', 'max_chat_lines', None, None),
    ('chat.min_class', 'mainchat_class', None, None),
    ('chat.disable_me', 'disable_me_cmd', None, lambda x: '1' if x else '0'),
    ('chat.default_enabled', 'chat_default_on', None, lambda x: '1' if x else '0'),
    
    # Search Settings
    ('search.min_chars', 'min_search_chars', None, None),
    ('search.interval.guest', 'int_search', None, None),
    ('search.interval.registered', 'int_search_reg', None, None),
    ('search.interval.vip', 'int_search_vip', None, None),
    ('search.interval.operator', 'int_search_op', None, None),
    ('search.interval.passive', 'int_search_pas', None, None),
    
    # Security Settings
    ('security.min_password_length', 'password_min_len', None, None),
    ('security.password_encryption', 'default_password_encryption', None, None),
    ('security.clone_detection.count', 'clone_detect_count', None, None),
    ('security.clone_detection.report', 'clone_detect_report', None, lambda x: '1' if x else '0'),
    ('security.clone_detection.ban_time', 'clone_det_tban_time', None, None),
    ('security.flood_protection.ban_time', 'proto_flood_tban_time', None, None),
    ('security.flood_protection.report', 'proto_flood_report', None, lambda x: '1' if x else '0'),
    ('security.hide_kicks', 'hide_all_kicks', None, lambda x: '1' if x else '0'),
    ('security.kick_ban_time', 'tban_kick', None, None),
    
    # Registration Settings
    ('registration.auto_register_class', 'autoreg_class', None, None),
    ('registration.min_class_to_register', 'min_class_register', None, None),
    ('registration.request_password', 'send_pass_request', None, lambda x: '1' if x else '0'),
    ('registration.allow_password_change', 'pwd_change', None, lambda x: '1' if x else '0'),
    ('registration.disable_regme', 'disable_regme_cmd', None, lambda x: '1' if x else '0'),
    
    # Hublist Settings
    ('hublist.host', 'hublist_host', None, None),
    ('hublist.port', 'hublist_port', None, None),
    ('hublist.interval', 'timer_hublist_period', None, None),
    ('hublist.send_address', 'hublist_send_listhost', None, lambda x: '1' if x else '0'),
    ('hublist.send_min_share', 'hublist_send_minshare', None, lambda x: '1' if x else '0'),
    
    # Permissions
    ('permissions.oplist_class', 'oplist_class', None, None),
    ('permissions.user_ip_class', 'user_ip_class', None, None),
    ('permissions.ban_bypass_class', 'ban_bypass_class', None, None),
    ('permissions.topic_mod_class', 'topic_mod_class', None, None),
    ('permissions.plugin_mod_class', 'plugin_mod_class', None, None),
    ('permissions.broadcast_class', 'min_class_bc', None, None),
    ('permissions.redirect_class', 'min_class_redir', None, None),
    ('permissions.class_differences.kick', 'classdif_kick', None, None),
    ('permissions.class_differences.register', 'classdif_reg', None, None),
    ('permissions.class_differences.pm', 'classdif_pm', None, None),
    ('permissions.class_differences.download', 'classdif_download', None, None),
    
    # Advanced Settings
    ('advanced.dns_lookup', 'dns_lookup', None, lambda x: '1' if x else '0'),
    ('advanced.extended_welcome', 'extended_welcome_message', None, lambda x: '1' if x else '0'),
    ('advanced.zlib_enabled', 'disable_zlib', None, lambda x: '0' if x else '1'),  # Inverted
    ('advanced.log_level', 'log_level', None, None),
    ('advanced.extjson_enabled', 'disable_extjson', None, lambda x: '0' if x else '1'),  # Inverted
    ('advanced.allow_same_user', 'allow_same_user', None, lambda x: '1' if x else '0'),
    ('advanced.filter_lan_requests', 'filter_lan_requests', None, lambda x: '1' if x else '0'),
    
    # TLS Settings (handled separately but included for completeness)
    ('tls.internal_port', 'tls_listen_port', None, None),
    ('tls.only_mode', 'tls_only_mode', None, lambda x: '1' if x else '0'),
    ('tls.min_version', 'tls_min_ver', None, None),
    ('tls.cert_org', 'tls_cert_org', None, None),
    ('tls.cert_email', 'tls_cert_mail', None, None),
]


def get_nested_value(config: Dict, path: str) -> Any:
    """Get a value from nested dict using dot notation path."""
    keys = path.split('.')
    value = config
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
        if value is None:
            return None
    return value


def escape_sql(value: str) -> str:
    """Escape single quotes for SQL."""
    if value is None:
        return ''
    return str(value).replace("'", "''")


def generate_settings_sql(config: Dict, tls_enabled: bool = False) -> str:
    """Generate SQL to update SetupList with config values."""
    values = []
    
    for yaml_path, db_var, default, transform in CONFIG_MAPPINGS:
        value = get_nested_value(config, yaml_path)
        
        # Skip if value not specified (use Verlihub defaults)
        if value is None:
            continue
        
        # Skip empty strings (treat as "not configured")
        if isinstance(value, str) and value == '':
            continue
            
        # Skip TLS settings if TLS not enabled (except for disabling)
        if yaml_path.startswith('tls.') and not tls_enabled:
            if yaml_path == 'tls.internal_port':
                # Set to 0 to disable TLS
                values.append(f"('config', '{db_var}', '0')")
            continue
        
        # Apply transformation if specified
        if transform:
            value = transform(value)
        
        # Add to values
        escaped_value = escape_sql(value)
        values.append(f"('config', '{db_var}', '{escaped_value}')")
    
    if not values:
        return ""
    
    sql = "INSERT INTO SetupList (file, var, val) VALUES\n"
    sql += ",\n".join(values)
    sql += "\nON DUPLICATE KEY UPDATE val = VALUES(val);"
    
    return sql


def main():
    parser = argparse.ArgumentParser(description='Generate SQL for Verlihub config')
    parser.add_argument('--config', '-c', help='YAML config file')
    parser.add_argument('--dry-run', '-n', action='store_true', help='Print SQL without executing')
    parser.add_argument('--list-mappings', action='store_true', help='List all config mappings')
    args = parser.parse_args()
    
    if args.list_mappings:
        print("YAML Path -> Database Variable")
        print("=" * 60)
        for yaml_path, db_var, default, transform in sorted(CONFIG_MAPPINGS):
            print(f"{yaml_path:45} -> {db_var}")
        return 0
    
    if not args.config:
        parser.error("--config is required unless using --list-mappings")
    
    try:
        with open(args.config, 'r') as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"Error reading config: {e}", file=sys.stderr)
        return 1
    
    # Check if TLS is enabled
    tls_config = config.get('tls', {})
    tls_enabled = tls_config.get('enabled', False)
    
    sql = generate_settings_sql(config, tls_enabled)
    
    if args.dry_run or not sql:
        if sql:
            print("--- SQL ---")
            print(sql)
            print("--- End SQL ---")
        else:
            print("No settings to update")
        return 0
    
    # Output SQL for piping to mysql
    print(sql)
    return 0


if __name__ == '__main__':
    sys.exit(main())
