# ===== Stage 1: Rust Build =====
FROM rust:1.75-bookworm AS rust-builder

WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY crates ./crates

# Build with all features for production
RUN cargo build --release --features "opcua rerun"

# ===== Stage 2: Python Build =====
FROM python:3.11-slim-bookworm AS python-builder

WORKDIR /build
COPY python-cortex/pyproject.toml ./
COPY python-cortex/src ./src

RUN pip install --no-cache-dir build && \
    python -m build --wheel

# ===== Stage 3: Runtime =====
FROM debian:bookworm-slim AS runtime

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libssl3 \
    python3 \
    python3-pip \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Rust binary
COPY --from=rust-builder /build/target/release/neuro-plc /usr/local/bin/

# Copy Python wheel and install
COPY --from=python-builder /build/dist/*.whl /tmp/
RUN pip3 install --no-cache-dir /tmp/*.whl onnxruntime numpy scikit-learn --break-system-packages && \
    rm /tmp/*.whl

# Create non-root user
RUN useradd -r -s /bin/false neuroplc
USER neuroplc

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:9090/health || exit 1

EXPOSE 7000 4840 9090

ENTRYPOINT ["/usr/local/bin/neuro-plc"]
CMD ["--metrics-addr", "0.0.0.0:9090"]
