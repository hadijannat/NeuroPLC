import base64
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Tuple


@dataclass
class CircuitBreaker:
    """Circuit breaker for BaSyx API calls."""
    failure_count: int = 0
    last_failure_at: float = 0.0
    threshold: int = field(default_factory=lambda: int(os.environ.get("BASYX_CIRCUIT_THRESHOLD", "5")))
    cooldown_s: float = field(default_factory=lambda: float(os.environ.get("BASYX_CIRCUIT_COOLDOWN_S", "30")))

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_at = time.time()

    def record_success(self) -> None:
        self.failure_count = 0

    def is_open(self) -> bool:
        if self.failure_count < self.threshold:
            return False
        return (time.time() - self.last_failure_at) < self.cooldown_s


@dataclass(frozen=True)
class BasyxConfig:
    base_url: str = "http://localhost:8081"
    aas_id: str = "urn:neuroplc:aas:motor:001"
    asset_id: str = "urn:neuroplc:asset:motor:001"
    operational_submodel_id: str = "urn:neuroplc:sm:operational-data:001"
    ai_submodel_id: str = "urn:neuroplc:sm:ai-recommendation:001"
    safety_submodel_id: str = "urn:neuroplc:sm:safety-parameters:001"
    operational_semantic_id: str = "urn:neuroplc:sm:OperationalData:1:0"
    ai_semantic_id: str = "urn:neuroplc:sm:AIRecommendation:1:0"
    safety_semantic_id: str = "urn:neuroplc:sm:SafetyParameters:1:0"
    timeout_s: float = 2.0
    
    # IDTA Submodel Config
    nameplate_submodel_id: str = "urn:neuroplc:sm:nameplate:001"
    nameplate_semantic_id: str = "https://admin-shell.io/idta/nameplate/3/0/Nameplate"
    
    func_safety_submodel_id: str = "urn:neuroplc:sm:functional-safety:001"
    func_safety_semantic_id: str = "0112/2///62683#ACC007#001"


class BasyxAdapter:
    def __init__(self, config: BasyxConfig):
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self._circuit_breaker = CircuitBreaker()

    def ensure_models(self) -> None:
        self._ensure_aas()
        self._ensure_submodel(
            self.config.operational_submodel_id,
            "OperationalData",
            [
                self._prop("MotorSpeedRPM", "DOUBLE", 0.0),
                self._prop("MotorTemperatureC", "DOUBLE", 25.0),
                self._prop("SystemPressureBar", "DOUBLE", 1.0),
                self._prop("LastUpdate", "DATE_TIME", self._now_iso()),
                self._prop("CycleCount", "LONG", 0),
                self._prop("IsHealthy", "BOOLEAN", True),
                self._prop("CycleJitterUs", "LONG", 0),
                self._prop("SafetyState", "STRING", "normal"),
            ],
            semantic_id=self.config.operational_semantic_id,
        )
        self._ensure_submodel(
            self.config.ai_submodel_id,
            "AIRecommendation",
            [
                self._prop("RecommendedSpeedRPM", "DOUBLE", 0.0),
                self._prop("ConfidenceScore", "DOUBLE", 0.0),
                self._prop("ReasoningHash", "STRING", ""),
                self._prop("RecommendationTimestamp", "DATE_TIME", self._now_iso()),
            ],
            semantic_id=self.config.ai_semantic_id,
        )
        self._ensure_submodel(
            self.config.safety_submodel_id,
            "SafetyParameters",
            [
                self._prop("MaxSpeedRPM", "DOUBLE", 3000.0),
                self._prop("MinSpeedRPM", "DOUBLE", 0.0),
                self._prop("MaxTemperatureC", "DOUBLE", 80.0),
                self._prop("MaxRateChangeRPM", "DOUBLE", 50.0),
            ],
            semantic_id=self.config.safety_semantic_id,
        )
        
        # IDTA Digital Nameplate
        self._ensure_submodel(
            self.config.nameplate_submodel_id,
            "Nameplate",
            [
                {
                    "idShort": "ManufacturerName",
                    "modelType": "MultiLanguageProperty",
                    "value": [
                        {"language": "en", "text": "NeuroPLC Project"},
                        {"language": "de", "text": "NeuroPLC Projekt"}
                    ],
                    "semanticId": {"keys": [{"type": "CONCEPT_DESCRIPTION", "value": "0173-1#02-AAO677#002"}]}
                },
                self._prop("SerialNumber", "STRING", "NPLC-2024-001"),
                self._prop("YearOfConstruction", "STRING", "2024"),
                {
                    "idShort": "ContactInformation",
                    "modelType": "SubmodelElementCollection",
                    "value": [
                         self._prop("RoleOfContactPerson", "STRING", "technical support"),
                         self._prop("Email", "STRING", "support@neuroplc.example"),
                    ]
                }
            ],
            semantic_id=self.config.nameplate_semantic_id,
        )

        # IDTA Functional Safety
        self._ensure_submodel(
            self.config.func_safety_submodel_id,
            "FunctionalSafety",
            [
                self._prop("SafetyIntegrityLevel", "STRING", "SIL2"),
                {
                    "idShort": "SafetyFunction",
                    "modelType": "SubmodelElementCollection",
                    "value": [
                        self._prop("Name", "STRING", "OverspeedProtection"),
                        self._prop("Description", "STRING", "Prevents motor speed > 3000 RPM"),
                        self._prop("TriggerCondition", "STRING", "speed_rpm > 3000"),
                        self._prop("SafeState", "STRING", "Reject setpoint"),
                    ]
                },
                {
                    "idShort": "TemperatureInterlockFunction",
                    "modelType": "SubmodelElementCollection",
                    "value": [
                        self._prop("Name", "STRING", "TemperatureInterlock"),
                        self._prop("TriggerCondition", "STRING", "motor_temp_c > 80"),
                        self._prop("SafeState", "STRING", "Block speed increase"),
                    ]
                }
            ],
            semantic_id=self.config.func_safety_semantic_id,
        )

        self._ensure_submodel_link(self.config.operational_submodel_id)
        self._ensure_submodel_link(self.config.ai_submodel_id)
        self._ensure_submodel_link(self.config.safety_submodel_id)
        self._ensure_submodel_link(self.config.nameplate_submodel_id)
        self._ensure_submodel_link(self.config.func_safety_submodel_id)

    def update_operational(self, state: dict, cycle_count: int, is_healthy: bool) -> None:
        submodel_id = self.config.operational_submodel_id
        self._put_property(submodel_id, "MotorSpeedRPM", "DOUBLE", float(state.get("motor_speed_rpm", 0.0)))
        self._put_property(
            submodel_id, "MotorTemperatureC", "DOUBLE", float(state.get("motor_temp_c", 0.0))
        )
        self._put_property(
            submodel_id, "SystemPressureBar", "DOUBLE", float(state.get("pressure_bar", 0.0))
        )
        self._put_property(submodel_id, "LastUpdate", "DATE_TIME", self._now_iso())
        self._put_property(submodel_id, "CycleCount", "LONG", int(cycle_count))
        self._put_property(submodel_id, "IsHealthy", "BOOLEAN", bool(is_healthy))
        self._put_property(
            submodel_id, "CycleJitterUs", "LONG", int(state.get("cycle_jitter_us", 0))
        )
        self._put_property(
            submodel_id,
            "SafetyState",
            "STRING",
            str(state.get("safety_state", "unknown")),
        )

    def update_recommendation(self, target_speed: float, confidence: float, reasoning_hash: str) -> None:
        submodel_id = self.config.ai_submodel_id
        self._put_property(submodel_id, "RecommendedSpeedRPM", "DOUBLE", float(target_speed))
        self._put_property(submodel_id, "ConfidenceScore", "DOUBLE", float(confidence))
        self._put_property(submodel_id, "ReasoningHash", "STRING", reasoning_hash)
        self._put_property(submodel_id, "RecommendationTimestamp", "DATE_TIME", self._now_iso())

    # --- Read Methods ---

    def get_property(self, submodel_id: str, id_short: str) -> Tuple[int, Any]:
        """Read a single property value from a submodel using $value endpoint.

        Returns:
            Tuple of (status_code, value). Value is the raw property value on success.
        """
        path = f"/submodels/{self._b64(submodel_id)}/submodel-elements/{id_short}/$value"
        return self._request_json("GET", path)

    def get_submodel(self, submodel_id: str) -> Tuple[int, Optional[dict]]:
        """Read entire submodel with all elements.

        Returns:
            Tuple of (status_code, submodel_dict).
        """
        return self._request_json("GET", f"/submodels/{self._b64(submodel_id)}")

    def read_safety_property(self, prop_name: str) -> Optional[Any]:
        """Convenience method for reading safety submodel properties.

        Returns:
            The property value, or None if not found/error.
        """
        status, value = self.get_property(self.config.safety_submodel_id, prop_name)
        return value if status == 200 else None

    def read_nameplate_property(self, prop_name: str) -> Optional[Any]:
        """Convenience method for reading nameplate submodel properties.

        Returns:
            The property value, or None if not found/error.
        """
        status, value = self.get_property(self.config.nameplate_submodel_id, prop_name)
        return value if status == 200 else None

    def read_functional_safety_property(self, prop_name: str) -> Optional[Any]:
        """Convenience method for reading functional safety submodel properties.

        Returns:
            The property value, or None if not found/error.
        """
        status, value = self.get_property(self.config.func_safety_submodel_id, prop_name)
        return value if status == 200 else None

    def is_circuit_open(self) -> bool:
        """Check if the circuit breaker is currently open (too many failures)."""
        return self._circuit_breaker.is_open()

    def _ensure_aas(self) -> None:
        status, _ = self._request_json("GET", f"/shells/{self._b64(self.config.aas_id)}")
        if status == 404:
            aas = {
                "id": self.config.aas_id,
                "idShort": "NeuroPLC",
                "assetInformation": {
                    "assetKind": "INSTANCE",
                    "globalAssetId": self.config.asset_id,
                },
                "submodels": [],
            }
            self._request_json("POST", "/shells", aas)

    def _ensure_submodel(
        self,
        submodel_id: str,
        id_short: str,
        elements: list[dict],
        semantic_id: Optional[str] = None,
    ) -> None:
        status, _ = self._request_json("GET", f"/submodels/{self._b64(submodel_id)}")
        if status == 404:
            submodel = {
                "id": submodel_id,
                "idShort": id_short,
                "kind": "INSTANCE",
                "submodelElements": elements,
            }
            if semantic_id:
                submodel["semanticId"] = {
                    "keys": [{"type": "GLOBAL_REFERENCE", "value": semantic_id}]
                }
            self._request_json("POST", "/submodels", submodel)

    def _ensure_submodel_link(self, submodel_id: str) -> None:
        status, aas = self._request_json("GET", f"/shells/{self._b64(self.config.aas_id)}")
        if status != 200 or not isinstance(aas, dict):
            return
        refs = aas.get("submodels") or []
        for ref in refs:
            keys = ref.get("keys", []) if isinstance(ref, dict) else []
            for key in keys:
                if key.get("type") == "SUBMODEL" and key.get("value") == submodel_id:
                    return
        refs.append(
            {
                "type": "MODEL_REFERENCE",
                "keys": [{"type": "SUBMODEL", "value": submodel_id}],
            }
        )
        aas["submodels"] = refs
        self._request_json("PUT", f"/shells/{self._b64(self.config.aas_id)}", aas)

    def _put_property(self, submodel_id: str, id_short: str, value_type: str, value: Any) -> None:
        prop = self._prop(id_short, value_type, value)
        self._request_json(
            "PUT",
            f"/submodels/{self._b64(submodel_id)}/submodel-elements/{id_short}",
            prop,
        )

    def _request_json(self, method: str, path: str, payload: Optional[dict] = None) -> Tuple[int, Any]:
        # Check circuit breaker for read operations
        if method == "GET" and self._circuit_breaker.is_open():
            return 503, {"error": "circuit_breaker_open"}

        url = self.base_url + path
        data = None
        headers = {"Content-Type": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_s) as resp:
                raw = resp.read()
                self._circuit_breaker.record_success()
                if not raw:
                    return resp.status, None
                return resp.status, json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as err:
            # Don't count 404 as failures (expected for missing resources)
            if err.code != 404:
                self._circuit_breaker.record_failure()
            raw = err.read()
            if raw:
                try:
                    return err.code, json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    return err.code, raw.decode("utf-8", errors="ignore")
            return err.code, None
        except (urllib.error.URLError, TimeoutError):
            self._circuit_breaker.record_failure()
            return 503, {"error": "connection_failed"}

    @staticmethod
    def _b64(value: str) -> str:
        return base64.urlsafe_b64encode(value.encode("utf-8")).decode("utf-8")

    @staticmethod
    def _prop(id_short: str, value_type: str, value: Any) -> dict:
        return {
            "idShort": id_short,
            "modelType": "Property",
            "valueType": value_type,
            "value": value,
        }

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
