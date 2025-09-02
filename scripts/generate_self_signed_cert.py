#!/usr/bin/env python3
"""Generate a self-signed certificate and private key using cryptography.

This is a small helper intended for local development on Windows where OpenSSL
may not be available. It writes PEM private key and certificate files.

Usage: python generate_self_signed_cert.py -o .certs -k privkey.pem -c fullchain.pem -n localhost
"""
from __future__ import annotations
import argparse
import datetime
import ipaddress
import os
import sys

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
except Exception as e:
    print("ERROR: cryptography library is required to generate certificates: ", e, file=sys.stderr)
    sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("-o", "--outdir", default=".certs", help="Directory to write cert/key into")
    p.add_argument("-k", "--key", default="privkey.pem", help="Private key filename")
    p.add_argument("-c", "--cert", default="fullchain.pem", help="Certificate filename")
    p.add_argument("-n", "--name", default="localhost", help="Common Name / DNS name to include")
    p.add_argument("--days", type=int, default=365, help="Validity period in days")
    args = p.parse_args(argv)

    outdir = os.path.abspath(args.outdir)
    os.makedirs(outdir, exist_ok=True)

    key_path = os.path.join(outdir, args.key)
    cert_path = os.path.join(outdir, args.cert)

    # Generate private key
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Build subject/issuer
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, args.name),
    ])

    now = datetime.datetime.utcnow()
    cert_builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=args.days))
    )

    # Add SANs (DNS localhost and 127.0.0.1)
    san_list = [x509.DNSName(args.name)]
    try:
        san_list.append(x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")))
    except Exception:
        pass
    cert_builder = cert_builder.add_extension(x509.SubjectAlternativeName(san_list), critical=False)

    cert = cert_builder.sign(private_key=key, algorithm=hashes.SHA256())

    # Write key
    with open(key_path, "wb") as f:
        f.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    # Write cert
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    print(f"Wrote key -> {key_path}")
    print(f"Wrote cert -> {cert_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
