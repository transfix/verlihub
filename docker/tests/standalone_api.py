#!/usr/bin/env python3
"""
Standalone FastAPI server for integration testing.

This runs independently of the verlihub Python plugin to test the API endpoints.
"""

import os
import sys
import time
import argparse
import threading
import signal

# Find and add venv to path
venv_base = os.environ.get('VERLIHUB_PYTHON_VENV', '/opt/verlihub-venv')
if os.path.exists(venv_base):
    lib_dir = os.path.join(venv_base, 'lib')
    if os.path.exists(lib_dir):
        for item in os.listdir(lib_dir):
            if item.startswith('python'):
                site_packages = os.path.join(lib_dir, item, 'site-packages')
                if os.path.exists(site_packages):
                    sys.path.insert(0, site_packages)
                    break

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import uvicorn

# Create FastAPI app
app = FastAPI(
    title="Verlihub Hub API",
    description="REST API for querying hub information (standalone test version)",
    version="1.0.0-test"
)

# CORS configuration
cors_origins = []

def configure_cors(origins: list):
    """Configure CORS origins"""
    global cors_origins
    cors_origins = origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"]
    )
    print(f"[API] CORS enabled for: {origins}")


# Endpoints
@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "Verlihub Hub API (Standalone Test)",
        "version": "1.0.0-test",
        "endpoints": ["/", "/health", "/hub_info", "/stats", "/users"]
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy", "uptime": time.time()}


@app.get("/hub_info")
async def hub_info():
    """Hub information"""
    return {
        "name": "Docker Test Hub",
        "version": "1.7.0.0",
        "server": "verlihub",
        "uptime": 123,
        "port": 4111
    }


@app.get("/stats")
async def stats():
    """Hub statistics"""
    return {
        "users": {"total": 1, "ops": 1, "registered": 1},
        "share": {"total_bytes": 0},
        "traffic": {"upload": 0, "download": 0}
    }


@app.get("/users")
async def users():
    """List of users"""
    return {
        "count": 1,
        "users": [
            {
                "nick": "admin",
                "class": 10,
                "share": 0,
                "ip": "127.0.0.1"
            }
        ]
    }


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Dashboard HTML"""
    return """<!DOCTYPE html>
<html>
<head><title>Verlihub Dashboard</title></head>
<body><h1>Verlihub Dashboard (Test)</h1></body>
</html>"""


@app.get("/dashboard/embed", response_class=HTMLResponse)
async def dashboard_embed():
    """Embedded dashboard HTML"""
    return """<!DOCTYPE html>
<html>
<head><title>Verlihub Dashboard Embed</title></head>
<body><h1>Verlihub Dashboard Embed (Test)</h1></body>
</html>"""


def run_server(host: str, port: int, origins: list):
    """Run the API server"""
    if origins:
        configure_cors(origins)
    
    print(f"[API] Starting server on {host}:{port}...")
    uvicorn.run(app, host=host, port=port, log_level="info")


def main():
    parser = argparse.ArgumentParser(description="Standalone API server for integration testing")
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', type=int, default=30000, help='Port to listen on')
    parser.add_argument('--cors-origins', nargs='*', default=[], help='CORS origins')
    args = parser.parse_args()
    
    run_server(args.host, args.port, args.cors_origins)


if __name__ == "__main__":
    main()
