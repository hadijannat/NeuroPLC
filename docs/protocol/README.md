# NeuroPLC Protocol (v1)

This folder captures the stable wire contract between the cortex and spine.
All messages are JSON lines (`\n` delimited) over the TCP bridge.

## Message types

- `hello` (optional handshake)
- `recommendation` (agent → spine)
- `state` (spine → agent)

## Handshake

If the spine is started with `--require-handshake`, the first message from the
client must be a `hello` message.

See: `hello-v1.schema.json`

## Recommendation

The recommendation message is versioned and includes TTL + sequence ordering.

See: `recommendation-v1.schema.json`

## State

The spine publishes state on a fixed interval. The schema is forward-compatible:
clients should ignore unknown fields.

