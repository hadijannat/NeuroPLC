use neuro_io::protocol::{IncomingMessage, ProtocolVersion};

#[test]
fn parses_hello_message() {
    let raw = r#"{
        "type":"hello",
        "protocol_version":{"major":1,"minor":0},
        "capabilities":["recommendation.v1","auth.hmac-sha256"],
        "client_id":"test-client"
    }"#;

    let msg = IncomingMessage::parse(raw).expect("hello should parse");
    match msg {
        IncomingMessage::Hello(hello) => {
            assert!(hello.protocol_version.is_supported());
            assert_eq!(hello.client_id.as_deref(), Some("test-client"));
        }
        _ => panic!("expected hello message"),
    }
}

#[test]
fn parses_recommendation_message() {
    let raw = r#"{
        "type":"recommendation",
        "protocol_version":{"major":1,"minor":0},
        "sequence":1,
        "issued_at_unix_us":1700000000000000,
        "ttl_ms":1000,
        "target_speed_rpm":500.0,
        "confidence":0.9,
        "reasoning_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "client_unix_us":1700000000000000
    }"#;

    let msg = IncomingMessage::parse(raw).expect("recommendation should parse");
    match msg {
        IncomingMessage::Recommendation(rec) => {
            assert_eq!(rec.sequence, 1);
            assert_eq!(rec.protocol_version, ProtocolVersion::v1());
        }
        _ => panic!("expected recommendation message"),
    }
}
