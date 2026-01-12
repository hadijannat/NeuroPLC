//! TLS configuration and utilities for secure connections.
//!
//! This module provides TLS server configuration for the bridge.

use rustls::pki_types::{CertificateDer, PrivateKeyDer};
use rustls::server::WebPkiClientVerifier;
use rustls::{RootCertStore, ServerConfig};
use rustls_pemfile::{certs, private_key};
use std::fs::File;
use std::io::BufReader;
use std::path::Path;
use std::sync::Arc;
use thiserror::Error;

/// Errors that can occur during TLS configuration
#[derive(Debug, Error)]
pub enum TlsError {
    #[error("Failed to read certificate file: {0}")]
    CertReadError(#[from] std::io::Error),

    #[error("No certificates found in file")]
    NoCertificates,

    #[error("No private key found in file")]
    NoPrivateKey,

    #[error("Failed to build TLS config: {0}")]
    ConfigError(String),
}

/// TLS configuration for the bridge
#[derive(Clone, Debug, Default)]
pub struct TlsConfig {
    /// Path to the server certificate file (PEM format)
    pub cert_path: String,
    /// Path to the server private key file (PEM format)
    pub key_path: String,
    /// Whether TLS is enabled
    pub enabled: bool,
    /// Require client certificates (mTLS)
    pub require_client_auth: bool,
    /// Path to client CA bundle (PEM format)
    pub client_ca_path: String,
}

impl TlsConfig {
    /// Check if TLS is properly configured
    #[allow(dead_code)]
    pub fn is_configured(&self) -> bool {
        if !self.enabled {
            return false;
        }
        if self.cert_path.is_empty() || self.key_path.is_empty() {
            return false;
        }
        if self.require_client_auth && self.client_ca_path.is_empty() {
            return false;
        }
        true
    }
}

/// Load certificates from a PEM file
pub fn load_certs(path: &Path) -> Result<Vec<CertificateDer<'static>>, TlsError> {
    let file = File::open(path)?;
    let mut reader = BufReader::new(file);

    let certs_result: Result<Vec<_>, _> = certs(&mut reader).collect();
    let certs = certs_result.map_err(TlsError::CertReadError)?;

    if certs.is_empty() {
        return Err(TlsError::NoCertificates);
    }

    Ok(certs)
}

/// Load private key from a PEM file
pub fn load_private_key(path: &Path) -> Result<PrivateKeyDer<'static>, TlsError> {
    let file = File::open(path)?;
    let mut reader = BufReader::new(file);

    private_key(&mut reader)
        .map_err(TlsError::CertReadError)?
        .ok_or(TlsError::NoPrivateKey)
}

/// Build a rustls ServerConfig from certificate and key files
pub fn build_server_config(config: &TlsConfig) -> Result<Arc<ServerConfig>, TlsError> {
    let certs = load_certs(Path::new(&config.cert_path))?;
    let key = load_private_key(Path::new(&config.key_path))?;
    let builder = ServerConfig::builder();
    let server_config = if config.require_client_auth {
        let ca_certs = load_certs(Path::new(&config.client_ca_path))?;
        let mut roots = RootCertStore::empty();
        for cert in ca_certs {
            roots
                .add(cert)
                .map_err(|e| TlsError::ConfigError(format!("{e:?}")))?;
        }
        let verifier = WebPkiClientVerifier::builder(roots.into())
            .build()
            .map_err(|e| TlsError::ConfigError(e.to_string()))?;
        builder
            .with_client_cert_verifier(verifier)
            .with_single_cert(certs, key)
            .map_err(|e| TlsError::ConfigError(e.to_string()))?
    } else {
        builder
            .with_no_client_auth()
            .with_single_cert(certs, key)
            .map_err(|e| TlsError::ConfigError(e.to_string()))?
    };

    Ok(Arc::new(server_config))
}

/// Generate a self-signed certificate for development/testing
/// This uses the rcgen crate if available, otherwise returns an error
#[cfg(feature = "dev-certs")]
pub fn generate_dev_cert(output_cert: &Path, output_key: &Path) -> Result<(), TlsError> {
    use rcgen::{generate_simple_self_signed, CertifiedKey};

    let subject_alt_names = vec!["localhost".to_string(), "127.0.0.1".to_string()];
    let CertifiedKey { cert, key_pair } = generate_simple_self_signed(subject_alt_names)
        .map_err(|e| TlsError::ConfigError(e.to_string()))?;

    std::fs::write(output_cert, cert.pem())?;
    std::fs::write(output_key, key_pair.serialize_pem())?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tls_config_default() {
        let config = TlsConfig::default();
        assert!(!config.enabled);
        assert!(!config.is_configured());
    }

    #[test]
    fn test_tls_config_is_configured() {
        let config = TlsConfig {
            enabled: true,
            cert_path: "/path/to/cert.pem".to_string(),
            key_path: "/path/to/key.pem".to_string(),
            require_client_auth: false,
            client_ca_path: String::new(),
        };

        assert!(config.is_configured());
    }

    #[test]
    fn test_missing_cert_file() {
        let result = load_certs(Path::new("/nonexistent/cert.pem"));
        assert!(result.is_err());
    }

    #[test]
    fn test_missing_key_file() {
        let result = load_private_key(Path::new("/nonexistent/key.pem"));
        assert!(result.is_err());
    }
}
