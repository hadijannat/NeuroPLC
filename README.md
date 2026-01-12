# NeuroPLC

Safety-first agentic industrial controller demo with a Rust real-time spine and a Python cortex.

## Quick start

Terminal 1:

```bash
cargo run --release
```

Terminal 2:

```bash
python3 python-cortex/run_supervisor.py
```

By default the Python cortex connects to `127.0.0.1:7000` and sends recommendations derived from the live state stream.
Use `--attack-mode` to periodically request unsafe speeds and verify the safety firewall rejects them.

If you want to run the spine without opening any TCP ports, add `--no-bridge`.

## Digital twin (BaSyx)

```bash
docker compose -f docker/docker-compose.digitaltwin.yml up -d
export BASYX_URL=http://localhost:8081
python3 python-cortex/run_supervisor.py
```

The cortex will create the AAS and submodels on first connect and update them periodically.
See `docs/digital-twin.md` for details.

## OPC UA (optional)

```bash
cargo run --release --features opcua -- --opcua
```

Default endpoint: `opc.tcp://localhost:4840`

The OPC UA server stores local PKI material under `pki-server/` and will create it on first run.
Keep that directory local (it is ignored by git) and do not commit private keys.

## Rerun visualization (optional)

```bash
cargo install rerun-cli --locked
cargo run --release --features rerun -- --rerun
```

Headless capture:

```bash
cargo run --release --features rerun -- --rerun-save neuroplc.rrd
```

## Notes

- The iron thread uses a preallocated triple buffer (no heap allocations in the control loop).
- Monotonic timestamps are based on a shared `TimeBase` to avoid staleness errors.
- Safety validation explicitly rejects non-finite values.
- Python audit hashes are stable SHA-256 over a canonical JSON envelope.

## Repository layout

- `crates/core-spine`: real-time control loop, safety, and HAL abstractions.
- `crates/neuro-io`: bridge I/O, Modbus HAL, auth/TLS, and metrics wiring.
- `crates/neuro-plc`: runtime orchestration + integrations (OPC UA, Rerun).
- `python-cortex`: Python supervisor/cortex.
