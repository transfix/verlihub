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

# Import core bridge when available
try:
    from verlihub.core import HubContext, create_hub
    __all__ = ["HubContext", "create_hub", "VerlihubConfig", "load_config", "__version__"]
except ImportError:
    # SWIG module not yet built
    __all__ = ["VerlihubConfig", "load_config", "__version__"]
