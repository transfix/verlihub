# Verlihub Docker Build Image
# Multi-stage build for development and testing
#
# Build arguments:
#   PYTHON_MODE - "single" (default) or "multi"
#     single: Single-interpreter mode with dispatcher for FastAPI/threading
#     multi:  Sub-interpreter mode with script isolation (no FastAPI support)

FROM ubuntu:24.04 AS builder

# Build argument for interpreter mode
ARG PYTHON_MODE=single

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    g++ \
    git \
    libmysqlclient-dev \
    libssl-dev \
    libpcre3-dev \
    libmaxminddb-dev \
    libicu-dev \
    zlib1g-dev \
    gettext \
    libasprintf-dev \
    rapidjson-dev \
    lua5.4 \
    liblua5.4-dev \
    python3-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

# Copy source code
COPY . .

# Build verlihub - mode depends on PYTHON_MODE arg
RUN mkdir -p build && cd build && \
    if [ "$PYTHON_MODE" = "single" ]; then \
        echo "Building with SINGLE interpreter mode (FastAPI compatible)"; \
        cmake -DCMAKE_INSTALL_PREFIX=/usr/local \
              -DWITH_PLUGINS=ON \
              -DWITH_LUA=ON \
              -DWITH_PYTHON=ON \
              -DPYTHON_USE_SINGLE_INTERPRETER=ON \
              ..; \
    else \
        echo "Building with MULTI interpreter mode (script isolation)"; \
        cmake -DCMAKE_INSTALL_PREFIX=/usr/local \
              -DWITH_PLUGINS=ON \
              -DWITH_LUA=ON \
              -DWITH_PYTHON=ON \
              -DPYTHON_USE_SINGLE_INTERPRETER=OFF \
              ..; \
    fi && \
    make -j$(nproc) && \
    make install

# Runtime image
FROM ubuntu:24.04 AS runtime

# Carry forward the build arg
ARG PYTHON_MODE=single

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHON_MODE=${PYTHON_MODE}

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmysqlclient21 \
    libssl3 \
    libpcre3 \
    libmaxminddb0 \
    libicu74 \
    zlib1g \
    liblua5.4-0 \
    libasprintf0v5 \
    python3 \
    python3-pip \
    python3-venv \
    libpython3-dev \
    gettext \
    mysql-client \
    curl \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Copy installed files from builder
COPY --from=builder /usr/local /usr/local

# Create verlihub user
RUN useradd -r -s /bin/false verlihub && \
    mkdir -p /etc/verlihub /var/lib/verlihub /var/log/verlihub && \
    chown -R verlihub:verlihub /etc/verlihub /var/lib/verlihub /var/log/verlihub

# Update library cache
RUN ldconfig

# Create Python virtual environment
# For single mode: includes FastAPI/uvicorn for API server
# For multi mode: minimal venv (scripts are isolated)
RUN python3 -m venv /opt/verlihub-venv && \
    /opt/verlihub-venv/bin/pip install --upgrade pip && \
    if [ "$PYTHON_MODE" = "single" ]; then \
        echo "Installing FastAPI dependencies for single-interpreter mode"; \
        /opt/verlihub-venv/bin/pip install fastapi uvicorn requests; \
    else \
        echo "Minimal venv for multi-interpreter mode (script isolation)"; \
    fi

ENV VERLIHUB_PYTHON_VENV=/opt/verlihub-venv

WORKDIR /etc/verlihub

# Copy entrypoint script
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 4111 8000 30000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["verlihub"]
