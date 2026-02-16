#!/usr/bin/env python3
"""
NMDC Command Runner for Verlihub Production Setup

Connects to a Verlihub hub and executes a sequence of commands.
Used by run_production.sh for post-startup configuration.

Usage:
    python run_commands.py --config production.yml
    python run_commands.py --host localhost --port 4111 --nick admin --password admin --commands "!onplug python" "!topic Hello"
"""

import socket
import time
import argparse
import sys
import os

# Try to import PyYAML
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


class NMDCCommandRunner:
    """NMDC client for running commands on Verlihub"""
    
    def __init__(self, host: str, port: int, nick: str, password: str = None, debug: bool = False):
        self.host = host
        self.port = port
        self.nick = nick
        self.password = password
        self.debug = debug
        self.sock = None
        self.buffer = ""
        self.connected = False
        self.logged_in = False
        
    def log(self, msg: str):
        if self.debug:
            print(f"[NMDC] {msg}")
            
    def connect(self, timeout: float = 60.0, retries: int = 30) -> bool:
        """Connect to the hub with retries"""
        for attempt in range(1, retries + 1):
            try:
                print(f"Connecting to {self.host}:{self.port}... (attempt {attempt}/{retries})")
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.settimeout(timeout / retries)
                self.sock.connect((self.host, self.port))
                self.connected = True
                self.log(f"Connected to {self.host}:{self.port}")
                
                if self._process_handshake():
                    return True
                    
            except socket.error as e:
                self.log(f"Connection error: {e}")
                if self.sock:
                    self.sock.close()
                    self.sock = None
                time.sleep(2)
        
        print(f"Failed to connect after {retries} attempts")
        return False
    
    def _process_handshake(self) -> bool:
        """Process the NMDC handshake sequence"""
        start_time = time.time()
        self.sock.settimeout(30)
        
        while time.time() - start_time < 60:
            try:
                data = self.sock.recv(4096).decode('utf-8', errors='replace')
                if not data:
                    self.log("Connection closed by hub")
                    return False
                
                self.buffer += data
                
                while '|' in self.buffer:
                    msg, self.buffer = self.buffer.split('|', 1)
                    self.log(f"<- {msg[:100]}...")
                    
                    if not self._handle_message(msg):
                        return False
                    
                    if self.logged_in:
                        return True
                        
            except socket.timeout:
                continue
        
        self.log("Handshake timeout")
        return False
    
    def _handle_message(self, msg: str) -> bool:
        """Handle a single NMDC message"""
        if msg.startswith('$Lock '):
            lock_data = msg[6:].split(' ', 1)[0]
            lock_key = self._calculate_key(lock_data)
            
            supports = "$Supports UserCommand NoGetINFO NoHello UserIP2 BotINFO HubINFO ZPipe0"
            key = f"$Key {lock_key}"
            validatenick = f"$ValidateNick {self.nick}"
            
            self._send(supports)
            self._send(key)
            self._send(validatenick)
            return True
            
        elif msg.startswith('$GetPass'):
            if self.password:
                self._send(f"$MyPass {self.password}")
            else:
                print("ERROR: Password required but not provided")
                return False
            return True
            
        elif msg.startswith('$BadPass'):
            print("ERROR: Bad password!")
            return False
            
        elif msg.startswith('$LogedIn'):
            self.log("Logged in as operator")
            myinfo = self._build_myinfo()
            self._send(myinfo)
            self._send("$GetNickList")
            self.logged_in = True
            return True
            
        elif msg.startswith('$ValidateDenide'):
            print(f"ERROR: Nick validation denied: {self.nick}")
            return False
            
        return True
    
    def _calculate_key(self, lock: str) -> str:
        """Calculate lock-to-key response"""
        key = []
        for i in range(len(lock)):
            if i == 0:
                key.append(ord(lock[0]) ^ ord(lock[-1]) ^ ord(lock[-2]) ^ 5)
            else:
                key.append(ord(lock[i]) ^ ord(lock[i-1]))
        
        result = ""
        for b in key:
            b = b & 0xFF
            if b in (0, 5, 36, 96, 124, 126):
                result += f"/%DCN{b:03d}%/"
            else:
                result += chr(b)
        
        return result
    
    def _build_myinfo(self) -> str:
        """Build MyINFO message"""
        return f"$MyINFO $ALL {self.nick} Production Setup Bot<Bot V:1.0,M:A,H:1/0/0,S:1>$ $Bot\x01$bot@production$0$"
    
    def _send(self, msg: str):
        """Send a message to the hub"""
        self.log(f"-> {msg[:100]}...")
        self.sock.sendall((msg + '|').encode('utf-8'))
    
    def send_chat(self, message: str):
        """Send a main chat message (command)"""
        self._send(f"<{self.nick}> {message}")
    
    def wait_for_response(self, timeout: float = 3.0) -> list:
        """Wait for and collect response messages"""
        messages = []
        start_time = time.time()
        
        self.sock.settimeout(0.5)
        
        while time.time() - start_time < timeout:
            try:
                data = self.sock.recv(4096).decode('utf-8', errors='replace')
                if data:
                    self.buffer += data
                    
                    while '|' in self.buffer:
                        msg, self.buffer = self.buffer.split('|', 1)
                        messages.append(msg)
                        self.log(f"<- {msg[:100]}...")
                            
            except socket.timeout:
                continue
        
        return messages
    
    def execute_command(self, command: str, wait_time: float = 2.0) -> list:
        """Execute a hub command and return responses"""
        print(f"  Executing: {command}")
        self.send_chat(command)
        responses = self.wait_for_response(timeout=wait_time)
        
        # Filter and display relevant responses (chat messages)
        for resp in responses:
            if resp.startswith('<') or resp.startswith('$To:'):
                # Extract message content
                if resp.startswith('<'):
                    content = resp
                else:
                    # PM format: $To: nick From: from_nick $<from_nick> message
                    if '$<' in resp:
                        content = resp.split('$<', 1)[1]
                        content = '<' + content
                    else:
                        content = resp
                print(f"    Response: {content[:200]}")
        
        return responses
    
    def run_commands(self, commands: list, delay: float = 1.0) -> bool:
        """Run a sequence of commands with optional delay between them"""
        if not self.logged_in:
            print("ERROR: Not logged in")
            return False
        
        print(f"\nRunning {len(commands)} command(s)...")
        
        for i, cmd in enumerate(commands, 1):
            cmd = cmd.strip()
            if not cmd or cmd.startswith('#'):
                continue
                
            print(f"\n[{i}/{len(commands)}] {cmd}")
            self.execute_command(cmd)
            
            if delay > 0 and i < len(commands):
                time.sleep(delay)
        
        return True
    
    def close(self):
        """Close the connection"""
        if self.sock:
            try:
                self._send("$Quit")
                self.sock.close()
            except:
                pass
            self.sock = None
            self.connected = False
            self.logged_in = False


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


def main():
    parser = argparse.ArgumentParser(description='NMDC Command Runner for Verlihub')
    parser.add_argument('--config', help='YAML config file path')
    parser.add_argument('--host', default='localhost', help='Hub hostname')
    parser.add_argument('--port', type=int, default=4111, help='Hub port')
    parser.add_argument('--nick', help='Admin nickname')
    parser.add_argument('--password', help='Admin password')
    parser.add_argument('--commands', nargs='+', help='Commands to execute')
    parser.add_argument('--command-file', help='File containing commands (one per line)')
    parser.add_argument('--delay', type=float, default=1.0, help='Delay between commands (seconds)')
    parser.add_argument('--debug', action='store_true', help='Enable debug output')
    parser.add_argument('--timeout', type=float, default=60.0, help='Connection timeout')
    parser.add_argument('--retries', type=int, default=30, help='Connection retries')
    
    args = parser.parse_args()
    
    # Load config from file if provided
    host = args.host
    port = args.port
    nick = args.nick
    password = args.password
    commands = args.commands or []
    
    if args.config:
        config = load_config(args.config)
        
        # Get host from config
        hub_cfg = config.get('hub', {})
        host = hub_cfg.get('host', args.host)
        port = hub_cfg.get('port', args.port)
        
        # For docker, use container name; for external, use host
        docker_cfg = config.get('docker', {})
        if docker_cfg.get('container_prefix'):
            host = f"{docker_cfg['container_prefix']}-hub"
        
        # Get admin credentials - support both old and new format
        users_cfg = config.get('users', {})
        if users_cfg:
            # New format: users.masters
            masters = users_cfg.get('masters', [])
            if masters:
                nick = masters[0].get('nick', args.nick)
                password = masters[0].get('password', args.password)
        else:
            # Old format: admin.nick, admin.password
            admin_cfg = config.get('admin', {})
            nick = admin_cfg.get('nick', args.nick)
            password = admin_cfg.get('password', args.password)
        
        # Collect commands from config
        commands = []
        commands.extend(config.get('startup_commands', []))
        commands.extend(config.get('plugin_commands', []))
        
        # Add matterbridge startup command if enabled
        matterbridge_cfg = config.get('matterbridge', {})
        if matterbridge_cfg.get('enabled', False):
            # Configure matterbridge before starting
            api_url = matterbridge_cfg.get('api_url', 'http://matterbridge:4242')
            api_token = matterbridge_cfg.get('api_token', '')
            gateway = matterbridge_cfg.get('gateway', 'verlihub')
            channel = matterbridge_cfg.get('channel', '#general')
            
            # Add configuration commands
            commands.append(f"!bridge config {api_url}")
            if api_token:
                commands.append(f"!bridge token {api_token}")
            commands.append(f"!bridge gateway {gateway}")
            commands.append(f"!bridge channel {channel}")
            commands.append("!bridge start")
    
    # Override with command line args if provided
    if args.host != 'localhost':
        host = args.host
    if args.port != 4111:
        port = args.port
    if args.nick:
        nick = args.nick
    if args.password:
        password = args.password
    if args.commands:
        commands = args.commands
    
    # Load commands from file if specified
    if args.command_file:
        if os.path.exists(args.command_file):
            with open(args.command_file, 'r') as f:
                commands.extend([line.strip() for line in f if line.strip() and not line.startswith('#')])
        else:
            print(f"ERROR: Command file not found: {args.command_file}")
            sys.exit(1)
    
    # Validate required args
    if not nick:
        print("ERROR: Admin nick required (--nick or in config file)")
        sys.exit(1)
    if not password:
        print("ERROR: Admin password required (--password or in config file)")
        sys.exit(1)
    if not commands:
        print("WARNING: No commands to execute")
        # Still connect to verify it works
    
    print(f"Verlihub NMDC Command Runner")
    print(f"  Hub: {host}:{port}")
    print(f"  Admin: {nick}")
    print(f"  Commands: {len(commands)}")
    
    runner = NMDCCommandRunner(
        host=host,
        port=port,
        nick=nick,
        password=password,
        debug=args.debug
    )
    
    if runner.connect(timeout=args.timeout, retries=args.retries):
        print("Connected and authenticated successfully!")
        
        if commands:
            runner.run_commands(commands, delay=args.delay)
            print("\nAll commands executed.")
        else:
            print("No commands to run. Connection test successful.")
        
        runner.close()
        sys.exit(0)
    else:
        print("Failed to connect to hub")
        sys.exit(1)


if __name__ == '__main__':
    main()
