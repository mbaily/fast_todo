#!/usr/bin/env bash
set -euo pipefail

# Generate a self-signed certificate and key for local testing.
# Writes to <target>/.tls/fast_todo.crt and fast_todo.key

TARGET_DIR=${1:-/opt/gpt5_fast_todo}
TLS_DIR="$TARGET_DIR/.tls"

mkdir -p "$TLS_DIR"

CRT="$TLS_DIR/fast_todo.crt"
KEY="$TLS_DIR/fast_todo.key"

# Use openssl to create a self-signed cert valid for 365 days
openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout "$KEY" -out "$CRT" -days 365 \
  -subj "/C=US/ST=State/L=City/O=Org/OU=Unit/CN=localhost"

chmod 644 "$CRT"
chmod 600 "$KEY"

echo "Wrote: $CRT"
echo "Wrote: $KEY"
