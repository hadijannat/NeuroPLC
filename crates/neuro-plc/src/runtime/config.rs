use std::path::PathBuf;

#[derive(Debug, Clone)]
pub struct RuntimeConfig {
    pub show_help: bool,
    pub run_seconds: Option<u64>,
    pub bind_addr: String,
    pub bridge_enabled: bool,
    pub json_logs: bool,
    pub metrics_addr: Option<String>,
    pub audit_path: Option<PathBuf>,
    pub tls_cert: Option<String>,
    pub tls_key: Option<String>,
    pub tls_client_ca: Option<String>,
    pub tls_require_client_cert: bool,
    pub auth_secret: Option<String>,
    pub auth_max_age_secs: u64,
    pub auth_issuer: String,
    pub auth_audience: String,
    pub auth_scope: Option<String>,
    pub bridge_require_handshake: bool,
    pub bridge_protocol: String,
    pub modbus_addr: Option<String>,
    #[cfg(feature = "opcua")]
    pub opcua_enabled: bool,
    #[cfg(feature = "opcua")]
    pub opcua_endpoint: String,
    #[cfg(feature = "opcua")]
    pub opcua_secure_only: bool,
    #[cfg(feature = "opcua")]
    pub opcua_allow_anonymous: bool,
    #[cfg(feature = "opcua")]
    pub opcua_user: Option<String>,
    #[cfg(feature = "opcua")]
    pub opcua_password: Option<String>,
    #[cfg(feature = "opcua")]
    pub opcua_allow_write: bool,
    #[cfg(feature = "opcua")]
    pub opcua_pki_dir: String,
    #[cfg(feature = "opcua")]
    pub opcua_create_sample_keypair: bool,
    #[cfg(feature = "rerun")]
    pub rerun_enabled: bool,
    #[cfg(feature = "rerun")]
    pub rerun_save_path: Option<String>,
}

impl Default for RuntimeConfig {
    fn default() -> Self {
        Self {
            show_help: false,
            run_seconds: None,
            bind_addr: "127.0.0.1:7000".to_string(),
            bridge_enabled: true,
            json_logs: false,
            metrics_addr: None,
            audit_path: None,
            tls_cert: None,
            tls_key: None,
            tls_client_ca: None,
            tls_require_client_cert: false,
            auth_secret: None,
            auth_max_age_secs: 300,
            auth_issuer: "neuroplc".to_string(),
            auth_audience: "neuroplc-spine".to_string(),
            auth_scope: None,
            bridge_require_handshake: false,
            bridge_protocol: "json".to_string(),
            modbus_addr: None,
            #[cfg(feature = "opcua")]
            opcua_enabled: false,
            #[cfg(feature = "opcua")]
            opcua_endpoint: "opc.tcp://0.0.0.0:4840".to_string(),
            #[cfg(feature = "opcua")]
            opcua_secure_only: false,
            #[cfg(feature = "opcua")]
            opcua_allow_anonymous: true,
            #[cfg(feature = "opcua")]
            opcua_user: None,
            #[cfg(feature = "opcua")]
            opcua_password: None,
            #[cfg(feature = "opcua")]
            opcua_allow_write: false,
            #[cfg(feature = "opcua")]
            opcua_pki_dir: "./pki-server".to_string(),
            #[cfg(feature = "opcua")]
            opcua_create_sample_keypair: true,
            #[cfg(feature = "rerun")]
            rerun_enabled: false,
            #[cfg(feature = "rerun")]
            rerun_save_path: None,
        }
    }
}

impl RuntimeConfig {
    pub fn from_env() -> Self {
        let args: Vec<String> = std::env::args().collect();
        Self::from_args(&args)
    }

    pub fn from_args(args: &[String]) -> Self {
        let mut cfg = RuntimeConfig::default();
        let mut i = 1;
        while i < args.len() {
            match args[i].as_str() {
                "--run-seconds" => {
                    if i + 1 < args.len() {
                        cfg.run_seconds = args[i + 1].parse::<u64>().ok();
                        i += 1;
                    }
                }
                "--bind" => {
                    if i + 1 < args.len() {
                        cfg.bind_addr = args[i + 1].clone();
                        i += 1;
                    }
                }
                "--no-bridge" => {
                    cfg.bridge_enabled = false;
                }
                "--json-logs" => {
                    cfg.json_logs = true;
                }
                "--metrics-addr" => {
                    if i + 1 < args.len() {
                        cfg.metrics_addr = Some(args[i + 1].clone());
                        i += 1;
                    }
                }
                "--audit-log" => {
                    if i + 1 < args.len() {
                        cfg.audit_path = Some(PathBuf::from(&args[i + 1]));
                        i += 1;
                    }
                }
                "--tls-cert" => {
                    if i + 1 < args.len() {
                        cfg.tls_cert = Some(args[i + 1].clone());
                        i += 1;
                    }
                }
                "--tls-key" => {
                    if i + 1 < args.len() {
                        cfg.tls_key = Some(args[i + 1].clone());
                        i += 1;
                    }
                }
                "--tls-client-ca" => {
                    if i + 1 < args.len() {
                        cfg.tls_client_ca = Some(args[i + 1].clone());
                        i += 1;
                    }
                }
                "--tls-require-client-cert" => {
                    cfg.tls_require_client_cert = true;
                }
                "--auth-secret" => {
                    if i + 1 < args.len() {
                        cfg.auth_secret = Some(args[i + 1].clone());
                        i += 1;
                    }
                }
                "--auth-max-age" => {
                    if i + 1 < args.len() {
                        cfg.auth_max_age_secs = args[i + 1].parse().unwrap_or(300);
                        i += 1;
                    }
                }
                "--auth-issuer" => {
                    if i + 1 < args.len() {
                        cfg.auth_issuer = args[i + 1].clone();
                        i += 1;
                    }
                }
                "--auth-audience" => {
                    if i + 1 < args.len() {
                        cfg.auth_audience = args[i + 1].clone();
                        i += 1;
                    }
                }
                "--auth-scope" => {
                    if i + 1 < args.len() {
                        cfg.auth_scope = Some(args[i + 1].clone());
                        i += 1;
                    }
                }
                "--require-handshake" => {
                    cfg.bridge_require_handshake = true;
                }
                "--protocol" => {
                    if i + 1 < args.len() {
                        cfg.bridge_protocol = args[i + 1].clone();
                        i += 1;
                    }
                }
                "--modbus" => {
                    if i + 1 < args.len() {
                        cfg.modbus_addr = Some(args[i + 1].clone());
                        i += 1;
                    }
                }
                #[cfg(feature = "opcua")]
                "--opcua" => {
                    cfg.opcua_enabled = true;
                }
                #[cfg(feature = "opcua")]
                "--opcua-endpoint" => {
                    if i + 1 < args.len() {
                        cfg.opcua_endpoint = args[i + 1].clone();
                        i += 1;
                    }
                }
                #[cfg(feature = "opcua")]
                "--opcua-secure-only" => {
                    cfg.opcua_secure_only = true;
                }
                #[cfg(feature = "opcua")]
                "--opcua-allow-anon" => {
                    cfg.opcua_allow_anonymous = true;
                }
                #[cfg(feature = "opcua")]
                "--opcua-no-anon" => {
                    cfg.opcua_allow_anonymous = false;
                }
                #[cfg(feature = "opcua")]
                "--opcua-user" => {
                    if i + 1 < args.len() {
                        cfg.opcua_user = Some(args[i + 1].clone());
                        i += 1;
                    }
                }
                #[cfg(feature = "opcua")]
                "--opcua-password" => {
                    if i + 1 < args.len() {
                        cfg.opcua_password = Some(args[i + 1].clone());
                        i += 1;
                    }
                }
                #[cfg(feature = "opcua")]
                "--opcua-allow-write" => {
                    cfg.opcua_allow_write = true;
                }
                #[cfg(feature = "opcua")]
                "--opcua-pki-dir" => {
                    if i + 1 < args.len() {
                        cfg.opcua_pki_dir = args[i + 1].clone();
                        i += 1;
                    }
                }
                #[cfg(feature = "opcua")]
                "--opcua-no-sample-keypair" => {
                    cfg.opcua_create_sample_keypair = false;
                }
                #[cfg(feature = "rerun")]
                "--rerun" => {
                    cfg.rerun_enabled = true;
                }
                #[cfg(feature = "rerun")]
                "--rerun-save" => {
                    if i + 1 < args.len() {
                        cfg.rerun_enabled = true;
                        cfg.rerun_save_path = Some(args[i + 1].clone());
                        i += 1;
                    }
                }
                "--help" | "-h" => {
                    cfg.show_help = true;
                    break;
                }
                _ => {}
            }
            i += 1;
        }
        cfg
    }

    pub fn print_help() {
        println!(
            r#"NeuroPLC - Safety-first agentic industrial controller

USAGE:
    neuro-plc [OPTIONS]

OPTIONS:
    --bind <ADDR>           Bridge TCP bind address [default: 127.0.0.1:7000]
    --no-bridge             Disable the TCP bridge (standalone simulation)
    --run-seconds <SECS>    Run for a fixed duration then exit
    --json-logs             Output logs in JSON format (for log aggregation)
    --metrics-addr <ADDR>   Enable Prometheus metrics server on address (e.g., 0.0.0.0:9090)
    --audit-log <PATH>      Enable audit logging to specified JSONL file
    --tls-cert <PATH>       Path to TLS certificate (PEM) for bridge security
    --tls-key <PATH>        Path to TLS private key (PEM)
    --tls-client-ca <PATH>  Path to client CA bundle (PEM) for mTLS
    --tls-require-client-cert Require client certificates for TLS
    --auth-secret <STR>     Shared secret for HMAC token authentication
    --auth-max-age <SECS>   Maximum age for auth tokens in seconds [default: 300]
    --auth-issuer <STR>     Expected token issuer [default: neuroplc]
    --auth-audience <STR>   Expected token audience [default: neuroplc-spine]
    --auth-scope <STR>      Required scope for recommendations (optional)
    --require-handshake     Require a protocol handshake before accepting recommendations
    --protocol <NAME>       Bridge protocol (json|proto) [default: json]
    --modbus <ADDR>         Connect to real hardware via Modbus TCP (e.g. 192.168.1.10:502)
    --opcua                 Enable OPC UA server (requires 'opcua' feature)
    --opcua-endpoint <URL>  OPC UA endpoint URL [default: opc.tcp://0.0.0.0:4840]
    --opcua-secure-only     Disable insecure OPC UA endpoints (no SecurityMode=None)
    --opcua-allow-anon      Allow anonymous OPC UA user token (default)
    --opcua-no-anon         Disable anonymous OPC UA user token
    --opcua-user <USER>     OPC UA username for password auth
    --opcua-password <PW>   OPC UA password for user auth
    --opcua-allow-write     Allow OPC UA write access (default: read-only)
    --opcua-pki-dir <PATH>  OPC UA PKI directory [default: ./pki-server]
    --opcua-no-sample-keypair Disable generating sample OPC UA keypair
    --rerun                 Enable Rerun visualization (requires 'rerun' feature)
    --rerun-save <PATH>     Save Rerun recording to file
    -h, --help              Print this help message

ENVIRONMENT VARIABLES:
    RUST_LOG                Set log filter (e.g., RUST_LOG=debug,neuro_plc=trace)

EXAMPLES:
    # Basic run with metrics
    neuro-plc --metrics-addr 0.0.0.0:9090

    # Production run with all observability
    neuro-plc --json-logs --metrics-addr 0.0.0.0:9090 --audit-log /var/log/neuroplc/audit.jsonl

    # Short test run
    neuro-plc --run-seconds 10 --no-bridge
"#
        );
    }
}
