# Docker

This folder contains the container build and local digital-twin stack.

## Files

- `Dockerfile` - Builds a runtime image with the Rust spine binary and the Python cortex.
- `docker-compose.digitaltwin.yml` - Local BaSyx AAS environment + GUI stack.
- `basyx-infra.yml` - Endpoint configuration consumed by the BaSyx GUI container.

## Local digital twin

```bash
docker compose -f docker/docker-compose.digitaltwin.yml up -d
```

Once running:
- AAS Environment: http://localhost:8081
- AAS Web UI: http://localhost:3000
