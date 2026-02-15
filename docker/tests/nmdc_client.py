#!/usr/bin/env python3
"""
NMDC Protocol Client for Verlihub Integration Testing

This client connects to a Verlihub DC++ hub using the NMDC protocol,
authenticates, and can send commands for integration testing.

Usage:
    python nmdc_client.py --host localhost --port 4111 --nick admin --password admin
"""

import socket
import time
import re
import argparse
import sys
from typing import Optional, Callable


class NMDCClient:
    """Simple NMDC protocol client for integration testing"""
    
    def __init__(self, host: str, port: int, nick: str, password: str = None,
                 share: int = 0, slots: int = 1, description: str = "Test Client"):
        self.host = host
        self.port = port
        self.nick = nick
        self.password = password
        self.share = share
        self.slots = slots
        self.description = description
        self.sock: Optional[socket.socket] = None
        self.buffer = ""
        self.connected = False
        self.logged_in = False
        self.hub_name = ""
        self.lock_key = ""
        self.debug = False
        
    def connect(self, timeout: float = 30.0) -> bool:
        """Connect to the hub and complete handshake"""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(timeout)
            self.sock.connect((self.host, self.port))
            self.connected = True
            
            if self.debug:
                print(f"[NMDC] Connected to {self.host}:{self.port}")
            
            # Wait for initial messages from hub
            if not self._process_handshake():
                return False
                
            return self.logged_in
            
        except Exception as e:
            print(f"[NMDC] Connection error: {e}")
            return False
    
    def _process_handshake(self) -> bool:
        """Process the NMDC handshake sequence"""
        # Read initial messages
        start_time = time.time()
        while time.time() - start_time < 30:  # 30 second timeout
            try:
                data = self.sock.recv(4096).decode('utf-8', errors='replace')
                if not data:
                    print("[NMDC] Connection closed by hub")
                    return False
                
                self.buffer += data
                
                # Process complete messages (ending with |)
                while '|' in self.buffer:
                    msg, self.buffer = self.buffer.split('|', 1)
                    if self.debug:
                        print(f"[NMDC] <- {msg}")
                    
                    if not self._handle_message(msg):
                        return False
                    
                    if self.logged_in:
                        return True
                        
            except socket.timeout:
                continue
        
        print("[NMDC] Handshake timeout")
        return False
    
    def _handle_message(self, msg: str) -> bool:
        """Handle a single NMDC message"""
        if msg.startswith('$Lock '):
            # $Lock EXTENDEDPROTOCOCABCABC Pk=server
            lock_data = msg[6:].split(' ', 1)[0]
            self.lock_key = self._calculate_key(lock_data)
            
            # Send key and identify
            supports = "$Supports UserCommand NoGetINFO NoHello UserIP2 BotINFO HubINFO ZPipe0"
            key = f"$Key {self.lock_key}"
            validatenick = f"$ValidateNick {self.nick}"
            
            self._send(supports)
            self._send(key)
            self._send(validatenick)
            return True
            
        elif msg.startswith('$HubName '):
            self.hub_name = msg[9:]
            if self.debug:
                print(f"[NMDC] Hub name: {self.hub_name}")
            return True
            
        elif msg.startswith('$Hello '):
            hello_nick = msg[7:]
            if hello_nick == self.nick:
                # Hub accepted our nick, now send password if needed or MyINFO
                return True
            return True
            
        elif msg.startswith('$GetPass'):
            # Hub requires password
            if self.password:
                self._send(f"$MyPass {self.password}")
            else:
                print("[NMDC] Password required but not provided")
                return False
            return True
            
        elif msg.startswith('$BadPass'):
            print("[NMDC] Bad password!")
            return False
            
        elif msg.startswith('$LogedIn'):
            # Successfully logged in as operator
            if self.debug:
                print("[NMDC] Logged in as operator")
            # Now send MyINFO
            myinfo = self._build_myinfo()
            self._send(myinfo)
            self._send("$GetNickList")
            self.logged_in = True
            return True
            
        elif msg.startswith('$ValidateDenide'):
            print(f"[NMDC] Nick validation denied: {self.nick}")
            return False
            
        elif msg.startswith('$Supports'):
            # Hub supports response - we can continue
            return True
            
        elif msg.startswith('$HubINFO') or msg.startswith('$UserIP'):
            return True
            
        elif msg.startswith('$NickList') or msg.startswith('$OpList'):
            return True
            
        elif msg.startswith('<'):
            # Chat message
            if self.debug:
                print(f"[NMDC] Chat: {msg}")
            return True
            
        return True
    
    def _calculate_key(self, lock: str) -> str:
        """Calculate lock-to-key response"""
        key = []
        for i in range(len(lock)):
            if i == 0:
                key.append(lock[0] ^ lock[-1] ^ lock[-2] ^ 5)
            else:
                key.append(lock[i] ^ lock[i-1])
        
        # Escape special characters
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
        # $MyINFO $ALL nick description$ $speed\x01$email$share$|
        desc = self.description
        speed = f"Bot\x01"
        email = "bot@test"
        share = str(self.share)
        
        return f"$MyINFO $ALL {self.nick} {desc}<Bot V:1.0,M:A,H:1/0/0,S:{self.slots}>$ ${speed}${email}${share}$"
    
    def _send(self, msg: str):
        """Send a message to the hub"""
        if self.debug:
            print(f"[NMDC] -> {msg}")
        self.sock.sendall((msg + '|').encode('utf-8'))
    
    def send_chat(self, message: str):
        """Send a main chat message"""
        # Format: <nick> message|
        self._send(f"<{self.nick}> {message}")
    
    def send_pm(self, to_nick: str, message: str):
        """Send a private message"""
        # Format: $To: nick From: from_nick $<from_nick> message|
        self._send(f"$To: {to_nick} From: {self.nick} $<{self.nick}> {message}")
    
    def wait_for_response(self, pattern: str = None, timeout: float = 10.0) -> list:
        """Wait for and collect response messages"""
        messages = []
        start_time = time.time()
        
        self.sock.settimeout(1.0)
        
        while time.time() - start_time < timeout:
            try:
                data = self.sock.recv(4096).decode('utf-8', errors='replace')
                if data:
                    self.buffer += data
                    
                    while '|' in self.buffer:
                        msg, self.buffer = self.buffer.split('|', 1)
                        messages.append(msg)
                        if self.debug:
                            print(f"[NMDC] <- {msg}")
                        
                        if pattern and re.search(pattern, msg):
                            return messages
                            
            except socket.timeout:
                continue
        
        return messages
    
    def execute_command(self, command: str, wait_time: float = 2.0) -> list:
        """Execute a hub command and return responses"""
        self.send_chat(command)
        return self.wait_for_response(timeout=wait_time)
    
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


def main():
    parser = argparse.ArgumentParser(description='NMDC Protocol Test Client')
    parser.add_argument('--host', default='localhost', help='Hub hostname')
    parser.add_argument('--port', type=int, default=4111, help='Hub port')
    parser.add_argument('--nick', default='TestAdmin', help='Nickname')
    parser.add_argument('--password', default='', help='Password')
    parser.add_argument('--debug', action='store_true', help='Enable debug output')
    parser.add_argument('--command', help='Command to execute')
    
    args = parser.parse_args()
    
    client = NMDCClient(
        host=args.host,
        port=args.port,
        nick=args.nick,
        password=args.password
    )
    client.debug = args.debug
    
    print(f"Connecting to {args.host}:{args.port} as {args.nick}...")
    
    if client.connect():
        print("Connected and logged in!")
        
        if args.command:
            print(f"Executing command: {args.command}")
            responses = client.execute_command(args.command)
            for msg in responses:
                print(f"  Response: {msg}")
        else:
            print("Use --command to send a command")
        
        client.close()
    else:
        print("Failed to connect")
        sys.exit(1)


if __name__ == '__main__':
    main()
