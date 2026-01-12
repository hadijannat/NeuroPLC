//! Authentication and authorization for the bridge.
//!
//! This module provides HMAC-based token validation for agent recommendations.

use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::collections::{HashSet, VecDeque};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};
use thiserror::Error;

type HmacSha256 = Hmac<Sha256>;

/// Errors that can occur during authentication
#[derive(Debug, Error)]
pub enum AuthError {
    #[error("Token has expired (age: {age_secs}s, max: {max_secs}s)")]
    TokenExpired { age_secs: u64, max_secs: u64 },

    #[error("Token is not yet valid")]
    TokenNotYetValid,

    #[error("Invalid token format")]
    InvalidFormat,

    #[error("Invalid token issuer")]
    InvalidIssuer,

    #[error("Invalid token audience")]
    InvalidAudience,

    #[error("Missing required scope")]
    MissingScope,

    #[error("Token signature verification failed")]
    InvalidSignature,

    #[error("Token replay detected")]
    ReplayDetected,

    #[error("Token nonce missing")]
    MissingNonce,

    #[error("Token decode error: {0}")]
    DecodeError(String),

    #[error("Token claims error: {0}")]
    InvalidClaims(String),
}

/// Configuration for token validation
#[derive(Clone, Debug)]
pub struct AuthConfig {
    /// Shared secret for HMAC signing
    pub secret: Vec<u8>,
    /// Maximum token age in seconds
    pub max_age_secs: u64,
    /// Whether authentication is required
    pub enabled: bool,
    /// Expected issuer claim
    pub issuer: String,
    /// Expected audience claim
    pub audience: String,
    /// Optional required scope
    pub required_scope: Option<String>,
    /// Number of nonces to keep for replay protection
    pub replay_window: usize,
    /// Allowed clock skew in seconds
    pub max_clock_skew_secs: u64,
}

impl Default for AuthConfig {
    fn default() -> Self {
        Self {
            secret: Vec::new(),
            max_age_secs: 300, // 5 minutes
            enabled: false,
            issuer: "neuroplc".to_string(),
            audience: "neuroplc-spine".to_string(),
            required_scope: None,
            replay_window: 1024,
            max_clock_skew_secs: 5,
        }
    }
}

/// Claims carried in a token
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenClaims {
    pub iss: String,
    pub sub: String,
    pub aud: String,
    pub scope: Vec<String>,
    pub iat: u64,
    pub exp: u64,
    #[serde(default)]
    pub nbf: Option<u64>,
    pub nonce: String,
}

struct ReplayWindow {
    order: VecDeque<String>,
    set: HashSet<String>,
    capacity: usize,
}

impl ReplayWindow {
    fn new(capacity: usize) -> Self {
        Self {
            order: VecDeque::new(),
            set: HashSet::new(),
            capacity: capacity.max(1),
        }
    }

    fn insert(&mut self, nonce: String) -> Result<(), AuthError> {
        if nonce.is_empty() {
            return Err(AuthError::MissingNonce);
        }
        if self.set.contains(&nonce) {
            return Err(AuthError::ReplayDetected);
        }
        self.order.push_back(nonce.clone());
        self.set.insert(nonce);
        while self.order.len() > self.capacity {
            if let Some(old) = self.order.pop_front() {
                self.set.remove(&old);
            }
        }
        Ok(())
    }
}

/// Token validator using HMAC-SHA256
pub struct TokenValidator {
    secret: Vec<u8>,
    max_age_secs: u64,
    issuer: String,
    audience: String,
    required_scope: Option<String>,
    max_clock_skew_secs: u64,
    replay: Mutex<ReplayWindow>,
}

impl TokenValidator {
    /// Create a new token validator with the given secret and max age
    pub fn new(secret: Vec<u8>, max_age_secs: u64) -> Self {
        Self {
            secret,
            max_age_secs,
            issuer: "neuroplc".to_string(),
            audience: "neuroplc-spine".to_string(),
            required_scope: None,
            max_clock_skew_secs: 5,
            replay: Mutex::new(ReplayWindow::new(1024)),
        }
    }

    /// Create from an AuthConfig
    pub fn from_config(config: &AuthConfig) -> Self {
        Self {
            secret: config.secret.clone(),
            max_age_secs: config.max_age_secs,
            issuer: config.issuer.clone(),
            audience: config.audience.clone(),
            required_scope: config.required_scope.clone(),
            max_clock_skew_secs: config.max_clock_skew_secs,
            replay: Mutex::new(ReplayWindow::new(config.replay_window)),
        }
    }

    /// Validate a token string
    /// Token format: base64url(payload).base64url(signature)
    pub fn validate(&self, token: &str) -> Result<TokenClaims, AuthError> {
        use base64::Engine;
        let engine = base64::engine::general_purpose::URL_SAFE_NO_PAD;

        let mut parts = token.split('.');
        let payload_b64 = parts.next().ok_or(AuthError::InvalidFormat)?;
        let sig_b64 = parts.next().ok_or(AuthError::InvalidFormat)?;
        if parts.next().is_some() {
            return Err(AuthError::InvalidFormat);
        }

        let payload = engine
            .decode(payload_b64)
            .map_err(|e| AuthError::DecodeError(e.to_string()))?;
        let signature = engine
            .decode(sig_b64)
            .map_err(|e| AuthError::DecodeError(e.to_string()))?;

        let mut mac =
            HmacSha256::new_from_slice(&self.secret).expect("HMAC can take key of any size");
        mac.update(&payload);
        mac.verify_slice(&signature)
            .map_err(|_| AuthError::InvalidSignature)?;

        let claims: TokenClaims = serde_json::from_slice(&payload)
            .map_err(|e| AuthError::InvalidClaims(e.to_string()))?;

        self.validate_claims(&claims)?;

        Ok(claims)
    }

    fn validate_claims(&self, claims: &TokenClaims) -> Result<(), AuthError> {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let skew = self.max_clock_skew_secs;

        if claims.iss != self.issuer {
            return Err(AuthError::InvalidIssuer);
        }
        if claims.aud != self.audience {
            return Err(AuthError::InvalidAudience);
        }
        if let Some(required) = &self.required_scope {
            if !claims.scope.iter().any(|scope| scope == required) {
                return Err(AuthError::MissingScope);
            }
        }
        if claims.iat > now.saturating_add(skew) {
            return Err(AuthError::TokenNotYetValid);
        }
        if let Some(nbf) = claims.nbf {
            if now + skew < nbf {
                return Err(AuthError::TokenNotYetValid);
            }
        }
        if now > claims.exp.saturating_add(skew) {
            let age = now.saturating_sub(claims.iat);
            return Err(AuthError::TokenExpired {
                age_secs: age,
                max_secs: self.max_age_secs,
            });
        }
        let age = now.saturating_sub(claims.iat);
        if age > self.max_age_secs {
            return Err(AuthError::TokenExpired {
                age_secs: age,
                max_secs: self.max_age_secs,
            });
        }

        let mut replay = self.replay.lock().unwrap();
        replay.insert(claims.nonce.clone())?;

        Ok(())
    }

    /// Generate a new token for testing/development
    #[allow(dead_code)]
    pub fn generate_token(&self) -> String {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();

        let claims = TokenClaims {
            iss: self.issuer.clone(),
            sub: "neuroplc-test".to_string(),
            aud: self.audience.clone(),
            scope: vec!["cortex:recommend".to_string()],
            iat: now,
            exp: now.saturating_add(self.max_age_secs),
            nbf: None,
            nonce: format!("nonce-{}", now),
        };

        self.generate_token_with_claims(&claims)
    }

    /// Generate a token with custom claims (for tests)
    #[allow(dead_code)]
    pub fn generate_token_with_claims(&self, claims: &TokenClaims) -> String {
        use base64::Engine;
        let engine = base64::engine::general_purpose::URL_SAFE_NO_PAD;
        let payload = serde_json::to_vec(claims).expect("failed to serialize claims");
        let payload_b64 = engine.encode(&payload);
        let mut mac =
            HmacSha256::new_from_slice(&self.secret).expect("HMAC can take key of any size");
        mac.update(&payload);
        let signature = mac.finalize().into_bytes();
        let sig_b64 = engine.encode(signature);
        format!("{}.{}", payload_b64, sig_b64)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_secret() -> Vec<u8> {
        b"test-secret-key-for-hmac".to_vec()
    }

    fn base_claims(validator: &TokenValidator) -> TokenClaims {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        TokenClaims {
            iss: validator.issuer.clone(),
            sub: "neuroplc-test".to_string(),
            aud: validator.audience.clone(),
            scope: vec!["cortex:recommend".to_string()],
            iat: now,
            exp: now + 60,
            nbf: None,
            nonce: format!("nonce-{}", now),
        }
    }

    #[test]
    fn test_generate_and_validate_token() {
        let validator = TokenValidator::new(test_secret(), 300);

        let token = validator.generate_token();
        let claims = validator.validate(&token).unwrap();

        assert!(claims.exp >= claims.iat);
    }

    #[test]
    fn test_invalid_signature_rejected() {
        let validator = TokenValidator::new(test_secret(), 300);
        let wrong_validator = TokenValidator::new(b"wrong-key".to_vec(), 300);

        let token = wrong_validator.generate_token();
        let result = validator.validate(&token);

        assert!(matches!(result, Err(AuthError::InvalidSignature)));
    }

    #[test]
    fn test_expired_token_rejected() {
        let validator = TokenValidator::new(test_secret(), 1);
        let mut claims = base_claims(&validator);
        claims.iat = claims.iat.saturating_sub(10);
        claims.exp = claims.iat + 1;

        let token = validator.generate_token_with_claims(&claims);
        std::thread::sleep(std::time::Duration::from_millis(1100));
        let result = validator.validate(&token);

        assert!(matches!(result, Err(AuthError::TokenExpired { .. })));
    }

    #[test]
    fn test_invalid_format_rejected() {
        let validator = TokenValidator::new(test_secret(), 300);

        assert!(matches!(
            validator.validate("not.valid.token"),
            Err(AuthError::DecodeError(_)) | Err(AuthError::InvalidFormat)
        ));
    }
}
