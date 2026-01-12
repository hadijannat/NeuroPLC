//! Authentication and authorization for the bridge.
//!
//! This module provides HMAC-based token validation for agent recommendations.

use hmac::{Hmac, Mac};
use sha2::Sha256;
use std::time::{SystemTime, UNIX_EPOCH};
use thiserror::Error;

type HmacSha256 = Hmac<Sha256>;

/// Errors that can occur during authentication
#[derive(Debug, Error)]
pub enum AuthError {
    #[error("Token has expired (age: {age_secs}s, max: {max_secs}s)")]
    TokenExpired { age_secs: u64, max_secs: u64 },

    #[error("Invalid token format")]
    InvalidFormat,

    #[error("Token signature verification failed")]
    InvalidSignature,

    #[error("Token decode error: {0}")]
    DecodeError(String),
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
}

impl Default for AuthConfig {
    fn default() -> Self {
        Self {
            secret: Vec::new(),
            max_age_secs: 300, // 5 minutes
            enabled: false,
        }
    }
}

/// Token validator using HMAC-SHA256
pub struct TokenValidator {
    secret: Vec<u8>,
    max_age_secs: u64,
}

impl TokenValidator {
    /// Create a new token validator with the given secret and max age
    pub fn new(secret: Vec<u8>, max_age_secs: u64) -> Self {
        Self {
            secret,
            max_age_secs,
        }
    }

    /// Create from an AuthConfig
    pub fn from_config(config: &AuthConfig) -> Self {
        Self::new(config.secret.clone(), config.max_age_secs)
    }

    /// Validate a token string
    /// Token format: base64(timestamp_secs:hmac(timestamp_secs:client_id))
    pub fn validate(&self, token: &str) -> Result<TokenClaims, AuthError> {
        use base64::Engine;
        let engine = base64::engine::general_purpose::STANDARD;

        let decoded = engine
            .decode(token)
            .map_err(|e| AuthError::DecodeError(e.to_string()))?;

        // Split at first colon to get timestamp and signature
        let colon_pos = decoded
            .iter()
            .position(|&b| b == b':')
            .ok_or(AuthError::InvalidFormat)?;

        let timestamp_bytes = &decoded[..colon_pos];
        let signature = &decoded[colon_pos + 1..];

        // Parse timestamp
        let timestamp_str =
            std::str::from_utf8(timestamp_bytes).map_err(|_| AuthError::InvalidFormat)?;
        let token_timestamp: u64 = timestamp_str
            .parse()
            .map_err(|_| AuthError::InvalidFormat)?;

        // Check token age
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();

        let age = now.saturating_sub(token_timestamp);
        if age > self.max_age_secs {
            return Err(AuthError::TokenExpired {
                age_secs: age,
                max_secs: self.max_age_secs,
            });
        }

        // Verify HMAC signature
        let mut mac =
            HmacSha256::new_from_slice(&self.secret).expect("HMAC can take key of any size");
        mac.update(timestamp_bytes);

        mac.verify_slice(signature)
            .map_err(|_| AuthError::InvalidSignature)?;

        Ok(TokenClaims {
            issued_at: token_timestamp,
            age_secs: age,
        })
    }

    /// Generate a new token for testing/development
    #[allow(dead_code)]
    pub fn generate_token(&self) -> String {
        use base64::Engine;
        let engine = base64::engine::general_purpose::STANDARD;

        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();

        let timestamp_str = now.to_string();

        let mut mac =
            HmacSha256::new_from_slice(&self.secret).expect("HMAC can take key of any size");
        mac.update(timestamp_str.as_bytes());
        let signature = mac.finalize().into_bytes();

        let mut token_data = timestamp_str.into_bytes();
        token_data.push(b':');
        token_data.extend_from_slice(&signature);

        engine.encode(&token_data)
    }
}

/// Claims extracted from a validated token
#[allow(dead_code)]
#[derive(Debug, Clone)]
pub struct TokenClaims {
    /// Unix timestamp when the token was issued
    pub issued_at: u64,
    /// Age of the token in seconds
    pub age_secs: u64,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_secret() -> Vec<u8> {
        b"test-secret-key-for-hmac".to_vec()
    }

    #[test]
    fn test_generate_and_validate_token() {
        let validator = TokenValidator::new(test_secret(), 300);

        let token = validator.generate_token();
        let claims = validator.validate(&token).unwrap();

        assert!(claims.age_secs < 5); // Should be very fresh
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
        let validator = TokenValidator::new(test_secret(), 0); // 0 second max age

        // Generate token, then immediately validate with 0 max age
        // This should fail because even 0 seconds is < current time
        let other_validator = TokenValidator::new(test_secret(), 300);
        let token = other_validator.generate_token();

        std::thread::sleep(std::time::Duration::from_millis(1100));
        let result = validator.validate(&token);

        assert!(matches!(result, Err(AuthError::TokenExpired { .. })));
    }

    #[test]
    fn test_invalid_format_rejected() {
        let validator = TokenValidator::new(test_secret(), 300);

        // Not valid base64
        assert!(matches!(
            validator.validate("not-valid-base64!!!"),
            Err(AuthError::DecodeError(_))
        ));

        // Valid base64 but no colon
        use base64::Engine;
        let engine = base64::engine::general_purpose::STANDARD;
        let invalid = engine.encode(b"no-colon-here");
        assert!(matches!(
            validator.validate(&invalid),
            Err(AuthError::InvalidFormat)
        ));
    }
}
