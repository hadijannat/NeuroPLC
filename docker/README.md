# Docker

This folder contains the container build and local digital-twin stack.

## Files

- `Dockerfile` - Builds a runtime image with the Rust spine binary and the Python cortex.
- `compose.dev.yml` - Full local development stack (NeuroPLC + BaSyx + Prometheus + Rerun).
- `compose.digitaltwin.yml` - Local BaSyx AAS environment + GUI stack.
- `compose.prod.yml` - Minimal production-oriented stack (NeuroPLC + metrics + audit log).
- `compose.simulation.yml` - Protocol + observability simulation stack (Modbus plant, OPC UA PLC, BaSyx, Prometheus, Grafana, Jaeger).
- `basyx-infra.yml` - Endpoint configuration consumed by the BaSyx GUI container.

## Local digital twin

```bash
docker compose -f docker/compose.digitaltwin.yml up -d
```

Once running:
- AAS Environment: http://localhost:8081
- AAS Web UI: http://localhost:3000

## Local development

```bash
docker compose -f docker/compose.dev.yml up --build
```

## Simulation stack

```bash
docker compose -f docker/compose.simulation.yml up -d --build
```

## Minimal production stack

```bash
docker compose -f docker/compose.prod.yml up --build
```
