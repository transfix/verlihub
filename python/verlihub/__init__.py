# Thin Verlihub - Python FastAPI wrapper for Verlihub C++ core
#
# This package provides:
# - FastAPI-based REST API for hub management
# - SQLModel-based database models
# - Python wrapper for C++ core via SWIG
# - YAML configuration support
#
# Copyright (C) 2006-2026 Verlihub Team, info at verlihub dot net
# Licensed under GPL v3

__version__ = "0.1.0"

# Import configuration module
from verlihub.config import VerlihubConfig, load_config

# Import SWIG core module when available
# Build outputs should be symlinked for development: 
#   ln -sf ../../build/python/verlihub/verlihub_core.py python/verlihub/
#   ln -sf ../../build/python/verlihub/_verlihub_core.so python/verlihub/
try:
    from verlihub import verlihub_core
except ImportError:
    verlihub_core = None

# Import core bridge when available
try:
    from verlihub.core import HubContext, create_hub
    __all__ = ["HubContext", "create_hub", "VerlihubConfig", "load_config", 
               "verlihub_core", "__version__"]
except (ImportError, AttributeError):
    # SWIG module not yet built
    __all__ = ["VerlihubConfig", "load_config", "verlihub_core", "__version__"]
