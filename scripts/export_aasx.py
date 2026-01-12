#!/usr/bin/env python3
import argparse
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_SRC = REPO_ROOT / "python-cortex" / "src"

if str(PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(PYTHON_SRC))

try:
    from digital_twin.basyx_adapter import BasyxAdapter, BasyxConfig
except ImportError as exc:
    raise SystemExit(
        "Unable to import python-cortex modules. Run from repo root and ensure python-cortex exists."
    ) from exc


def build_query(aas_ids, submodel_ids):
    params = []
    for aas_id in aas_ids:
        params.append(("aasIds", aas_id))
    for submodel_id in submodel_ids:
        params.append(("submodelIds", submodel_id))
    return urllib.parse.urlencode(params, doseq=True)


def export_aasx(base_url, aas_id, submodel_ids, output_path, timeout_s):
    query = build_query([aas_id], submodel_ids)
    url = f"{base_url.rstrip('/')}/serialization"
    if query:
        url = f"{url}?{query}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/assetadministrationshell-package")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = resp.read()
        if not data:
            raise RuntimeError("BaSyx serialization returned an empty response")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)


def main():
    parser = argparse.ArgumentParser(description="Export NeuroPLC AASX from BaSyx")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("BASYX_URL", "http://localhost:8081"),
        help="BaSyx AAS Environment base URL",
    )
    parser.add_argument(
        "--aas-id",
        default=os.environ.get("AAS_ID", "urn:neuroplc:aas:motor:001"),
        help="AAS id to export",
    )
    parser.add_argument(
        "--submodel-id",
        action="append",
        default=None,
        help="Submodel id to include (repeatable)",
    )
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / "examples" / "aasx" / "neuroplc.aasx"),
        help="Output AASX file path",
    )
    parser.add_argument(
        "--no-ensure",
        action="store_true",
        help="Skip creating the AAS/submodels before export",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="HTTP timeout in seconds",
    )

    args = parser.parse_args()

    config = BasyxConfig(base_url=args.base_url, aas_id=args.aas_id)
    adapter = BasyxAdapter(config)

    if not args.no_ensure:
        adapter.ensure_models()

    submodel_ids = args.submodel_id
    if submodel_ids is None:
        submodel_ids = [
            config.operational_submodel_id,
            config.ai_submodel_id,
            config.safety_submodel_id,
        ]

    output_path = Path(args.output)
    export_aasx(config.base_url, config.aas_id, submodel_ids, output_path, args.timeout)
    print(f"Exported AASX -> {output_path}")


if __name__ == "__main__":
    main()
