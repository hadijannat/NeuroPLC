# NeuroPLC Safety Case Document

## 1. System Definition

### 1.1 Scope
This document covers the safety-related aspects of the NeuroPLC industrial controller system, specifically the safety firewall implemented in the Rust "spine" component.

### 1.2 Safety Lifecycle Alignment (IEC 61508)
NeuroPLC follows a reduced IEC 61508 V-model lifecycle with the following artifacts mapped in-repo:

- Concept + HARA: **Section 2**
- Safety requirements: **Section 3**
- Architectural controls: **Section 3.2**
- Verification & validation: **Section 4**
- Evidence + auditability: **Section 5**

### 1.2 Safety Functions

| SF-ID | Function Name | Description | SIL Target |
|-------|--------------|-------------|------------|
| SF-01 | Overspeed Protection | Prevents motor speed > 3000 RPM | SIL 2 |
| SF-02 | Rate Limiting | Limits speed change to 50 RPM/cycle | SIL 2 |
| SF-03 | Temperature Interlock | Blocks increases when T > 80°C | SIL 2 |
| SF-04 | Non-Finite Rejection | Rejects NaN/Inf setpoints | SIL 2 |
| SF-05 | Watchdog | Emergency stop on timing overrun | SIL 2 |

## 2. Hazard Analysis

### 2.1 HAZOP Study Results

| Node | Deviation | Cause | Consequence | Safeguard |
|------|-----------|-------|-------------|-----------|
| Setpoint | More (speed) | AI malfunction | Mechanical damage | SF-01 |
| Setpoint | Less (speed) | AI malfunction | Process disruption | SF-02 (rate limit) |
| Setpoint | Other (NaN) | Network corruption | Undefined behavior | SF-04 |
| Environment | More (temp) | Cooling failure | Motor damage | SF-03 |
| Timing | Late | CPU overload | Control instability | SF-05 |

### 2.2 FMEA Summary (minimal)

| Failure Mode | Effect | Detection | Mitigation |
|--------------|--------|-----------|------------|
| Bridge timeout | Stale recommendations | Timeout age check | SF-05 + degraded mode |
| Jitter spikes | Control instability | Jitter threshold monitoring | Trip after repeated violation |
| Sensor non-finite | Undefined control | Sensor sanity checks | SF-04 |

## 3. Safety Requirements

### 3.1 Functional Requirements

| Req-ID | Requirement | Verification |
|--------|-------------|--------------|
| FR-01 | Setpoints > max_speed_rpm SHALL be rejected | Unit test, Proptest |
| FR-02 | Setpoints < min_speed_rpm SHALL be rejected | Unit test, Proptest |
| FR-03 | Rate of change > max_rate SHALL be rejected | Unit test, Proptest |
| FR-04 | Non-finite setpoints (NaN, Inf) SHALL be rejected | Unit test, Proptest |
| FR-05 | Temperature > max_temp SHALL trigger interlock | Unit test, Proptest |

### 3.2 Architectural Requirements

| Req-ID | Requirement | Implementation |
|--------|-------------|----------------|
| AR-01 | Safety logic SHALL be in separate, auditable module | `safety.rs` isolation |
| AR-02 | Safety validation SHALL use type-state pattern | `Setpoint<Validated>` |
| AR-03 | Control loop SHALL not allocate heap memory | Triple buffer preallocation |
| AR-04 | AI recommendations SHALL be untrusted inputs | Explicit validation boundary |
| AR-05 | Timing jitter SHALL be monitored and trigger degradation/trip | `SafetySupervisor::note_timing_jitter` |

## 4. Verification & Validation

### 4.1 Test Coverage

| Test Type | Coverage Target | Current Status |
|-----------|-----------------|----------------|
| Unit Tests | 100% of safety functions | 3/5 (60%) |
| Property Tests | 10,000 random inputs | Passed |
| Integration Tests | All message paths | Passed |
| Stress Tests | 24h continuous operation | TODO |

### 4.2 Static Analysis

- Clippy: All warnings as errors (`-D warnings`)
- Miri: Memory safety verification (TODO)
- Kani: Formal verification of safety properties (TODO)

### 4.3 Evidence Traceability

- Safety tests: `crates/core-spine/src/safety.rs`, `crates/core-spine/src/safety_proptest.rs`
- Runtime safety boundary: `crates/core-spine/src/safety_supervisor.rs`
- Audit trail (hash chain): `crates/neuro-plc/src/infra/audit.rs`

## 5. Lifecycle Data

### 5.1 Configuration Management

- Version control: Git with signed commits
- Release tagging: Semantic versioning
- Change process: PR review + CI gate

### 5.2 Audit Trail

- All AI recommendations logged with SHA-256 hash
- Safety rejections logged with violation type
- Timestamps from monotonic clock (prevents tampering)
