# AASX Examples

Generate a fresh AASX package from a running BaSyx AAS Environment:

```bash
python3 scripts/export_aasx.py --output examples/aasx/neuroplc.aasx
```

The export uses the BaSyx `/serialization` endpoint and includes the NeuroPLC AAS
plus its linked submodels.
