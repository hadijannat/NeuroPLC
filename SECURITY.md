# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please report them via GitHub Security Advisories:

1. Go to the repository's Security tab
2. Click "Report a vulnerability"
3. Fill in the details of the vulnerability

You can expect:
- Acknowledgment within 48 hours
- Status update within 7 days
- Coordinated disclosure timeline

## Threat Model

### Assets Protected

| Asset | Criticality | Protection Mechanism |
|-------|-------------|---------------------|
| Motor control setpoints | Critical | Type-state safety validation |
| HMAC authentication tokens | High | Time-limited, SHA-256 signed |
| TLS private keys | High | File system permissions |
| Audit logs | Medium | Append-only JSONL format |
| Process state data | Medium | Memory isolation |

### Trust Boundaries

```
+------------------+                    +------------------+
|   Iron Thread    |<-- Triple Buffer ->|  Bridge Thread   |
|  (1ms RT loop)   |   (same process)   |   (TCP/TLS)      |
+------------------+                    +------------------+
        |                                       |
        | HAL Interface                         | JSON-Lines + HMAC
        v                                       v
+------------------+                    +------------------+
|   MachineIO      |                    |  Python Cortex   |
|  (Sim/Modbus)    |                    | (AI Supervisor)  |
+------------------+                    +------------------+
                                               |
                                               | HTTP REST
                                               v
                                        +------------------+
                                        |   BaSyx AAS      |
                                        | (Digital Twin)   |
                                        +------------------+
```

**Boundary 1: Iron Thread <-> Bridge Thread**
- Same process, shared memory via `Arc<StateExchange>`
- Triple-buffer lock-free IPC (no locks in RT path)
- No authentication required (trusted intra-process)

**Boundary 2: Bridge <-> Python Cortex**
- TCP/TLS socket (localhost default, can be networked)
- JSON-lines protocol with schema validation
- HMAC-SHA256 token authentication (optional, recommended)
- All values validated: finite checks, bounds checks, format checks

**Boundary 3: OPC UA Server <-> External Clients**
- Certificate-based authentication (recommended)
- Anonymous access (development only)
- Security profiles: None, Basic256Sha256 SignAndEncrypt

**Boundary 4: BaSyx API <-> Cortex**
- HTTP REST (network isolation assumed in default config)
- No authentication in default BaSyx config

### Attack Vectors and Mitigations

| Attack Vector | Mitigation | Implementation |
|---------------|------------|----------------|
| Malicious AI recommendations (overspeed) | Safety validation firewall | `safety.rs:ExceedsMaxSpeed` |
| Malicious AI recommendations (rapid change) | Rate-of-change limiting | `safety.rs:RateOfChangeTooHigh` |
| Non-finite value injection (NaN/Inf) | Explicit `is_finite()` checks | `safety.rs:NonFiniteSetpoint` |
| Thermal runaway | Temperature interlock | `safety.rs:TemperatureInterlock` |
| Stale command replay | 500ms staleness timeout | `sync.rs:is_stale()` |
| Network interception | TLS encryption | `tls.rs` |
| Token replay | HMAC with timestamp, max-age check | `auth.rs` |
| Watchdog bypass | Independent watchdog timer | `control_loop.rs:100ms timeout` |

### Out of Scope

The following are explicitly **not** protected by NeuroPLC's security model:

1. **Physical security** of the host machine
2. **Operating system security** (privilege escalation, etc.)
3. **Supply chain attacks** on dependencies (mitigated by auditing)
4. **Side-channel attacks** on cryptographic operations
5. **Denial of service** via resource exhaustion (rate limiting not implemented)

## Security Features

### Authentication (`auth.rs`)

```
Token format: base64(timestamp_secs:hmac_sha256(timestamp_secs, secret))
```

- Timestamp prevents indefinite replay
- HMAC-SHA256 ensures integrity
- Configurable max-age (default: 300 seconds)

### TLS Support (`tls.rs`)

- Rustls-based implementation (no OpenSSL)
- PEM certificate/key loading
- Development certificate generation (`--features dev-certs`)
- Server-side only (no client certificate verification in current version)

### Safety Validation (`safety.rs`)

Type-state pattern ensures compile-time safety:

```rust
Setpoint<Unvalidated> -> validate() -> Result<Setpoint<Validated>, SafetyViolation>
```

Only `Setpoint<Validated>` can be written to actuators.

### Audit Logging (`audit.rs`)

All safety-relevant events logged in JSONL format:
- `RecommendationReceived`
- `RecommendationApplied`
- `SafetyRejection` (with reason)
- `ClientConnected` / `ClientDisconnected`
- `EmergencyStop`
- `WatchdogTimeout`

## Security Hardening Checklist

### Development Environment

- [ ] Use `--no-bridge` when not testing network features
- [ ] Use simulated motor (`hal_sim`) instead of real hardware
- [ ] Keep TLS disabled for localhost-only testing

### Production Environment

- [ ] Enable TLS (`--tls-cert`, `--tls-key`)
- [ ] Set strong auth secret (`--auth-secret`)
- [ ] Configure auth token max-age (`--auth-max-age`)
- [ ] Enable audit logging (`--audit-log`)
- [ ] Bind to specific interface, not `0.0.0.0`
- [ ] Use network segmentation (OT network isolation)
- [ ] Monitor Prometheus metrics for anomalies
- [ ] Rotate TLS certificates periodically
- [ ] Backup and protect audit logs

### OPC UA Hardening

- [ ] Set `OPCUA_ADMIN_PASSWORD` environment variable
- [ ] Generate production certificates (`scripts/generate_opcua_certs.sh`)
- [ ] Disable anonymous access endpoint
- [ ] Use `Basic256Sha256` security profile

## Dependency Security

### Rust Dependencies

Security-relevant crates:
- `rustls` - TLS implementation
- `hmac`, `sha2` - Cryptographic primitives
- `opcua` - OPC UA stack (optional)

Run periodic audits:
```bash
cargo audit
```

### Python Dependencies

The Python cortex uses only standard library modules:
- `socket`, `json`, `hashlib`, `urllib`

No external dependencies = minimal supply chain risk.

## Incident Response

If you discover a security incident:

1. **Isolate** the affected system from the network
2. **Preserve** audit logs (`--audit-log` output)
3. **Capture** Prometheus metrics snapshot
4. **Review** recent AI recommendations in logs
5. **Report** via GitHub Security Advisories

## Compliance Notes

NeuroPLC demonstrates security patterns aligned with:

- **IEC 62443** - Industrial automation security concepts
- **IDTA-01004** - AAS Security (ABAC-ready architecture)
- **OPC UA Security** - Certificate-based authentication model

This is a demonstration project; formal certification is not claimed.
