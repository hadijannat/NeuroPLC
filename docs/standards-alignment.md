# Standards Alignment (IDTA AAS)

## Target AAS specification release

NeuroPLC aligns documentation and integration targets to the IDTA Asset Administration Shell (AAS)
Specifications **Release 25-01** (Parts 1-5). This keeps the repo pinned to a concrete version
for reproducibility while we grow submodel coverage.

- Part 1: Meta-model
- Part 2: API
- Part 3: Data Specification Templates
- Part 4: Security
- Part 5: AASX Package Format

## Submodel template semantic IDs (pinned)

The following semantic IDs are pinned in this repo for standards alignment. They represent the
canonical identifiers for IDTA submodel templates we target or plan to implement.

| Submodel template | SemanticId (Submodel) | IDTA document |
| --- | --- | --- |
| Digital Nameplate (industrial equipment) | `https://admin-shell.io/idta/nameplate/3/0/Nameplate` | IDTA 02006-3-0 |
| Functional Safety | `0112/2///62683#ACC007#001` | IDTA 02014-1-0 |
| AI Model Nameplate | `https://admin-shell.io/idta/SubmodelTemplate/AIModelNameplate/1/0` | IDTA 02060-1-0 |
| Provision of Simulation Models | `https://admin-shell.io/idta/SimulationModels/SimulationModels/1/0` | IDTA 02005-1-0 |

## Implementation status

- Current live demo AAS (BaSyx) publishes three custom submodels:
  - OperationalData
  - AIRecommendation
  - SafetyParameters

- The IDTA semantic IDs above are pinned for alignment. Full template-compliant submodels are
  tracked as roadmap work, and will be introduced incrementally to avoid over-claiming conformance.
