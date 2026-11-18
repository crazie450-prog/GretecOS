"""A stand-in for Tailscale Serve in the phone e2e test (WO-9.3): HTTPS on ``--listen`` with a
self-signed certificate for ``--name``, every connection piped byte for byte to the core on
``127.0.0.1:--to``. Like Serve it terminates TLS and keeps the browser's ``Host`` (the core sees
``<name>:<listen>``); unlike Serve it adds no ``X-Forwarded-*`` headers, which the core doesn't
read. Test-only: it never runs outside Playwright, and binds loopback only.

``uv run python dashboard/e2e/fake_serve.py --name phone.tail-e2e.ts.net --listen 8797 --to 8795``
prints ``listening`` once it accepts connections.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import ssl
import tempfile
from collections.abc import Sequence
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def certificate(name: str, folder: Path) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = folder / "cert.pem", folder / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def serve(name: str, listen: int, to: int) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cert, key = certificate(name, Path(tmp))
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(cert, key)

    async def handle(
        client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter
    ) -> None:
        try:
            core_r, core_w = await asyncio.open_connection("127.0.0.1", to)
        except OSError:
            client_w.close()
            return
        await asyncio.gather(pipe(client_r, core_w), pipe(core_r, client_w))

    server = await asyncio.start_server(handle, "127.0.0.1", listen, ssl=context)
    print("listening", flush=True)
    async with server:
        await server.serve_forever()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fake_serve.py")
    parser.add_argument("--name", required=True)
    parser.add_argument("--listen", type=int, required=True)
    parser.add_argument("--to", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        asyncio.run(serve(args.name, args.listen, args.to))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
