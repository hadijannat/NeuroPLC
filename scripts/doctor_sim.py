#!/usr/bin/env python3
import base64
import hashlib
import hmac
import json
import os
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Optional, List, Tuple
import secrets
import shutil


@dataclass
class ScenarioResult:
    name: str
    expected: str
    observed: str
    status: str


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_token(secret: str, issuer: str, audience: str, scope: str, max_age: int) -> str:
    now_s = int(time.time())
    claims = {
        "iss": issuer,
        "sub": "doctor-sim",
        "aud": audience,
        "scope": [scope],
        "iat": now_s,
        "exp": now_s + max_age,
        "nonce": secrets.token_hex(16),
    }
    payload = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return f"{b64url(payload)}.{b64url(signature)}"


def pick_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def ensure_binary(force: bool = False) -> str:
    path = os.path.join("target", "release", "neuro-plc")
    if force or not os.path.exists(path):
        subprocess.check_call(["cargo", "build", "--release", "--bin", "neuro-plc"])
    return path


def ensure_opcua_binaries() -> None:
    subprocess.check_call(
        ["cargo", "build", "--release", "--features", "opcua", "--bin", "neuro-plc", "--bin", "opcua_smoke"]
    )


def start_spine(port: int, auth_secret: Optional[str], extra_args: Optional[List[str]] = None) -> subprocess.Popen:
    binary = ensure_binary()
    cmd = [
        binary,
        "--bind",
        f"127.0.0.1:{port}",
        "--run-seconds",
        "14",
    ]
    if auth_secret:
        cmd += [
            "--auth-secret",
            auth_secret,
            "--auth-issuer",
            "neuroplc",
            "--auth-audience",
            "neuroplc-spine",
            "--auth-scope",
            "cortex:recommend",
            "--auth-max-age",
            "300",
        ]
    if extra_args:
        cmd.extend(extra_args)
    env = os.environ.copy()
    env["RUST_LOG"] = "info,neuro_io=debug"
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def read_logs(proc: subprocess.Popen, sink: List[str]) -> None:
    if not proc.stdout:
        return
    for line in proc.stdout:
        sink.append(line.rstrip())


def connect_plain(port: int) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.settimeout(2.0)
    return sock


def connect_tls(port: int, ca_cert: str, client_cert: Optional[str] = None, client_key: Optional[str] = None) -> ssl.SSLSocket:
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca_cert)
    if client_cert and client_key:
        context.load_cert_chain(certfile=client_cert, keyfile=client_key)
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    tls_sock = context.wrap_socket(sock, server_hostname="localhost")
    tls_sock.settimeout(2.0)
    return tls_sock


def read_state(sock: socket.socket) -> Optional[dict]:
    try:
        line = sock.recv(4096)
    except socket.timeout:
        return None
    if not line:
        return None
    try:
        payload = line.decode("utf-8").strip().split("\n")[-1]
        data = json.loads(payload)
        if data.get("type") == "state":
            return data
    except Exception:
        return None
    return None


def wait_for_state(sock: socket.socket, timeout_s: float) -> Optional[dict]:
    end = time.time() + timeout_s
    while time.time() < end:
        state = read_state(sock)
        if state:
            return state
    return None


def wait_for_speed(sock: socket.socket, baseline: float, timeout_s: float) -> float:
    end = time.time() + timeout_s
    last_speed = baseline
    while time.time() < end:
        state = read_state(sock)
        if state:
            last_speed = float(state.get("motor_speed_rpm", last_speed))
            if last_speed > baseline:
                break
    return last_speed


def send_recommendation(sock: socket.socket, msg: dict) -> None:
    wire = json.dumps(msg) + "\n"
    sock.sendall(wire.encode("utf-8"))


def openssl_available() -> bool:
    return shutil.which("openssl") is not None


def generate_mtls_materials(tmpdir: str) -> Optional[Tuple[str, str, str, str, str]]:
    if not openssl_available():
        return None
    ca_key = os.path.join(tmpdir, "ca.key")
    ca_crt = os.path.join(tmpdir, "ca.crt")
    server_key = os.path.join(tmpdir, "server.key")
    server_csr = os.path.join(tmpdir, "server.csr")
    server_crt = os.path.join(tmpdir, "server.crt")
    client_key = os.path.join(tmpdir, "client.key")
    client_csr = os.path.join(tmpdir, "client.csr")
    client_crt = os.path.join(tmpdir, "client.crt")

    subprocess.check_call([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-days", "1", "-nodes",
        "-keyout", ca_key, "-out", ca_crt, "-subj", "/CN=NeuroPLC Test CA"
    ])
    subprocess.check_call([
        "openssl", "req", "-newkey", "rsa:2048", "-nodes",
        "-keyout", server_key, "-out", server_csr, "-subj", "/CN=localhost"
    ])
    subprocess.check_call([
        "openssl", "x509", "-req", "-in", server_csr, "-CA", ca_crt, "-CAkey", ca_key,
        "-CAcreateserial", "-out", server_crt, "-days", "1"
    ])
    subprocess.check_call([
        "openssl", "req", "-newkey", "rsa:2048", "-nodes",
        "-keyout", client_key, "-out", client_csr, "-subj", "/CN=doctor-client"
    ])
    subprocess.check_call([
        "openssl", "x509", "-req", "-in", client_csr, "-CA", ca_crt, "-CAkey", ca_key,
        "-CAcreateserial", "-out", client_crt, "-days", "1"
    ])

    return ca_crt, server_crt, server_key, client_crt, client_key


def normalize_value(value):
    if isinstance(value, dict):
        return {k: normalize_value(value[k]) for k in sorted(value.keys())}
    if isinstance(value, list):
        return [normalize_value(v) for v in value]
    return value


def verify_audit_chain(path: str) -> Tuple[bool, str]:
    if not os.path.exists(path):
        return False, "audit log missing"
    prev_hash = "0"
    with open(path, "r", encoding="utf-8") as handle:
        for idx, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            entry = record.get("entry")
            entry_hash = record.get("entry_hash")
            record_prev = record.get("prev_hash")
            if entry is None or entry_hash is None or record_prev is None:
                return False, f"malformed record at line {idx}"
            if record_prev != prev_hash:
                return False, f"prev_hash mismatch at line {idx}"
            entry_ordered = {
                "timestamp_us": entry.get("timestamp_us"),
                "unix_us": entry.get("unix_us"),
                "event_type": entry.get("event_type"),
                "details": normalize_value(entry.get("details")),
            }
            entry_json = json.dumps(entry_ordered, separators=(",", ":"), ensure_ascii=False)
            digest = hashlib.sha256((prev_hash + entry_json).encode("utf-8")).hexdigest()
            if digest != entry_hash:
                return False, f"hash mismatch at line {idx}"
            prev_hash = entry_hash
    return True, "hash chain ok"


def run() -> None:
    ensure_binary(force=True)
    port = pick_port()
    auth_secret = "doctor-secret"
    issuer = "neuroplc"
    audience = "neuroplc-spine"
    scope = "cortex:recommend"
    max_age = 300

    results: List[ScenarioResult] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = os.path.join(tmpdir, "audit.jsonl")
        logs: List[str] = []
        proc = start_spine(port, auth_secret, extra_args=["--audit-log", audit_path])
        log_thread = threading.Thread(target=read_logs, args=(proc, logs), daemon=True)
        log_thread.start()

        time.sleep(1.0)
        sock = connect_plain(port)

        # Read initial state
        initial_state = read_state(sock)
        baseline_speed = float(initial_state.get("motor_speed_rpm", 0.0)) if initial_state else 0.0

        # Scenario 1: Valid recommendation
        issued_at_us = int(time.time() * 1_000_000)
        valid_token = make_token(auth_secret, issuer, audience, scope, max_age)
        msg = {
            "type": "recommendation",
            "protocol_version": {"major": 1, "minor": 0},
            "sequence": 1,
            "issued_at_unix_us": issued_at_us,
            "ttl_ms": 1000,
            "target_speed_rpm": 500.0,
            "confidence": 0.9,
            "reasoning_hash": "a" * 64,
            "client_unix_us": issued_at_us,
            "auth_token": valid_token,
        }
        send_recommendation(sock, msg)
        speed = wait_for_speed(sock, baseline_speed, 1.2)
        status = "OK" if speed >= baseline_speed else "CHECK"
        results.append(
            ScenarioResult(
                "Valid recommendation",
                "Accepted, speed increases or remains stable",
                f"motor_speed_rpm={speed:.2f}",
                status,
            )
        )

        # Scenario 2: Expired TTL
        expired_issued = int((time.time() - 5) * 1_000_000)
        msg = {
            "type": "recommendation",
            "protocol_version": {"major": 1, "minor": 0},
            "sequence": 2,
            "issued_at_unix_us": expired_issued,
            "ttl_ms": 100,
            "target_speed_rpm": 600.0,
            "confidence": 0.9,
            "reasoning_hash": "b" * 64,
            "client_unix_us": expired_issued,
            "auth_token": valid_token,
        }
        send_recommendation(sock, msg)
        time.sleep(0.2)
        results.append(
            ScenarioResult(
                "Expired TTL",
                "Rejected with 'Recommendation expired' log",
                "see logs",
                "OK" if any("Recommendation expired" in line for line in logs) else "CHECK",
            )
        )

        # Scenario 3: Out-of-order sequence
        msg = {
            "type": "recommendation",
            "protocol_version": {"major": 1, "minor": 0},
            "sequence": 1,
            "issued_at_unix_us": int(time.time() * 1_000_000),
            "ttl_ms": 1000,
            "target_speed_rpm": 700.0,
            "confidence": 0.9,
            "reasoning_hash": "c" * 64,
            "client_unix_us": issued_at_us,
            "auth_token": valid_token,
        }
        send_recommendation(sock, msg)
        time.sleep(0.2)
        results.append(
            ScenarioResult(
                "Out-of-order sequence",
                "Rejected with out-of-order log",
                "see logs",
                "OK" if any("Out-of-order recommendation sequence" in line for line in logs) else "CHECK",
            )
        )

        # Scenario 4: Missing auth token
        msg = {
            "type": "recommendation",
            "protocol_version": {"major": 1, "minor": 0},
            "sequence": 3,
            "issued_at_unix_us": int(time.time() * 1_000_000),
            "ttl_ms": 1000,
            "target_speed_rpm": 500.0,
            "confidence": 0.9,
            "reasoning_hash": "d" * 64,
            "client_unix_us": issued_at_us,
        }
        send_recommendation(sock, msg)
        time.sleep(0.2)
        results.append(
            ScenarioResult(
                "Missing auth token",
                "Rejected with missing token log",
                "see logs",
                "OK" if any("Missing auth token" in line for line in logs) else "CHECK",
            )
        )

        # Scenario 5: Invalid auth token
        bad_token = make_token("wrong-secret", issuer, audience, scope, max_age)
        msg = {
            "type": "recommendation",
            "protocol_version": {"major": 1, "minor": 0},
            "sequence": 4,
            "issued_at_unix_us": int(time.time() * 1_000_000),
            "ttl_ms": 1000,
            "target_speed_rpm": 500.0,
            "confidence": 0.9,
            "reasoning_hash": "e" * 64,
            "client_unix_us": issued_at_us,
            "auth_token": bad_token,
        }
        send_recommendation(sock, msg)
        time.sleep(0.2)
        results.append(
            ScenarioResult(
                "Invalid auth token",
                "Rejected with invalid auth token log",
                "see logs",
                "OK" if any("Invalid auth token" in line for line in logs) else "CHECK",
            )
        )

        # Scenario 6: Unsafe recommendation
        msg = {
            "type": "recommendation",
            "protocol_version": {"major": 1, "minor": 0},
            "sequence": 5,
            "issued_at_unix_us": int(time.time() * 1_000_000),
            "ttl_ms": 1000,
            "target_speed_rpm": 5000.0,
            "confidence": 0.9,
            "reasoning_hash": "f" * 64,
            "client_unix_us": issued_at_us,
            "auth_token": valid_token,
        }
        send_recommendation(sock, msg)
        time.sleep(0.6)
        state = read_state(sock) or {}
        speed = float(state.get("motor_speed_rpm", 0.0))
        results.append(
            ScenarioResult(
                "Unsafe recommendation",
                "Safety supervisor trips, speed stays <= 3000",
                f"motor_speed_rpm={speed:.2f}",
                "OK" if speed <= 3000.0 else "CHECK",
            )
        )

        sock.close()
        proc.wait(timeout=20)

        ok, detail = verify_audit_chain(audit_path)
        results.append(
            ScenarioResult(
                "Audit hash chain",
                "Hash chain intact",
                detail,
                "OK" if ok else "CHECK",
            )
        )

        print("\n=== NeuroPLC Doctor Report ===")
        for result in results:
            print(f"- {result.name}: {result.status}\n  expected: {result.expected}\n  observed: {result.observed}")

        print("\n=== Log Highlights ===")
        for line in logs:
            if any(
                needle in line
                for needle in [
                    "Recommendation expired",
                    "Out-of-order recommendation sequence",
                    "Missing auth token",
                    "Invalid auth token",
                    "Starting IronThread control loop",
                    "Bridge listening",
                ]
            ):
                print(f"{line}")

    # Scenario 7: mTLS enforcement
    if openssl_available():
        with tempfile.TemporaryDirectory() as tmpdir:
            materials = generate_mtls_materials(tmpdir)
            if materials:
                ca_crt, server_crt, server_key, client_crt, client_key = materials
                mtls_port = pick_port()
                logs: List[str] = []
                proc = start_spine(
                    mtls_port,
                    auth_secret,
                    extra_args=[
                        "--tls-cert",
                        server_crt,
                        "--tls-key",
                        server_key,
                        "--tls-client-ca",
                        ca_crt,
                        "--tls-require-client-cert",
                        "--run-seconds",
                        "10",
                    ],
                )
                log_thread = threading.Thread(target=read_logs, args=(proc, logs), daemon=True)
                log_thread.start()
                time.sleep(1.0)

                mtls_results: List[ScenarioResult] = []

                try:
                    tls_sock = connect_tls(mtls_port, ca_crt)
                    state = wait_for_state(tls_sock, 1.5)
                    if state:
                        observed = "connected and received state"
                        status = "CHECK"
                    else:
                        observed = "no state (likely rejected)"
                        status = "OK"
                    mtls_results.append(
                        ScenarioResult(
                            "mTLS missing client cert",
                            "Handshake fails",
                            observed,
                            status,
                        )
                    )
                    tls_sock.close()
                except ssl.SSLError:
                    mtls_results.append(
                        ScenarioResult(
                            "mTLS missing client cert",
                            "Handshake fails",
                            "ssl error as expected",
                            "OK",
                        )
                    )

                try:
                    tls_sock = connect_tls(mtls_port, ca_crt, client_crt, client_key)
                    state = wait_for_state(tls_sock, 1.5) or {}
                    mtls_results.append(
                        ScenarioResult(
                            "mTLS with client cert",
                            "Handshake succeeds",
                            f"state_type={state.get('type')}",
                            "OK" if state.get("type") == "state" else "CHECK",
                        )
                    )
                    tls_sock.close()
                except ssl.SSLError as exc:
                    mtls_results.append(
                        ScenarioResult(
                            "mTLS with client cert",
                            "Handshake succeeds",
                            f"ssl error: {exc}",
                            "CHECK",
                        )
                    )

                proc.wait(timeout=15)

                print("\n=== mTLS Doctor Report ===")
                for result in mtls_results:
                    print(
                        f"- {result.name}: {result.status}\n  expected: {result.expected}\n  observed: {result.observed}"
                    )
                for line in logs:
                    if any(
                        needle in line
                        for needle in [
                            "Bridge listening",
                            "Bridge write error",
                            "Bridge read error",
                            "Bridge client disconnected",
                            "Failed to configure TLS",
                        ]
                    ):
                        print(f"  log: {line}")
            else:
                print("\n=== mTLS Doctor Report ===\n- SKIP: openssl unavailable")
    else:
        print("\n=== mTLS Doctor Report ===\n- SKIP: openssl unavailable")

    # Scenario 8: OPC UA secure-only enforcement
    try:
        ensure_opcua_binaries()
        opcua_port = 4840
        cmd = [
            os.path.join("target", "release", "neuro-plc"),
            "--opcua",
            "--opcua-secure-only",
            "--no-bridge",
            "--run-seconds",
            "6",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        time.sleep(1.0)
        smoke = subprocess.run(
            [os.path.join("target", "release", "opcua_smoke"), f"opc.tcp://127.0.0.1:{opcua_port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        proc.wait(timeout=10)
        status = "OK" if smoke.returncode != 0 else "CHECK"
        print("\n=== OPC UA Secure-Only Doctor Report ===")
        print(
            f"- Secure-only None-mode connection: {status}\n  expected: opcua_smoke fails with SecurityMode=None\n  observed: returncode={smoke.returncode}"
        )
        if smoke.stdout:
            print("  output:", smoke.stdout.strip().split("\n")[-1])
    except Exception as exc:
        print("\n=== OPC UA Secure-Only Doctor Report ===")
        print(f"- CHECK: failed to run OPC UA test: {exc}")


if __name__ == "__main__":
    run()
