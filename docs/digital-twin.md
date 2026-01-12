# Digital Twin (BaSyx + AAS)

## Start the stack

```bash
docker compose -f docker/docker-compose.digitaltwin.yml up -d
```

BaSyx AAS Environment: http://localhost:8081
BaSyx AAS Web UI: http://localhost:3000

See `docs/standards-alignment.md` for the pinned AAS release and semantic IDs.

## Connect the Python cortex

```bash
export BASYX_URL=http://localhost:8081
python3 python-cortex/run_supervisor.py
```

The cortex will create the AAS and three submodels on first connection:
- OperationalData
- AIRecommendation
- SafetyParameters

It will then push live updates every `BASYX_UPDATE_INTERVAL` seconds (default: 1.0).

## Verify with curl

```bash
curl -s http://localhost:8081/shells | jq .
```

You should see the `urn:neuroplc:aas:motor:001` shell and linked submodels.

## Export an AASX package

This uses the BaSyx serialization endpoint to emit an AASX file.

```bash
python3 scripts/export_aasx.py --output examples/aasx/neuroplc.aasx
```

You can upload the resulting `examples/aasx/neuroplc.aasx` through the BaSyx Web UI
for inspection.
