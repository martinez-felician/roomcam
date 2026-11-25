"""Genera un par de claves VAPID para Web Push.

Uso:  python -m roomcam_server.keys
"""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def generate() -> tuple[str, str]:
    """Devuelve (clave pública, clave privada) en base64 URL-safe."""
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return _b64(public), _b64(private)


def main() -> None:
    public, private = generate()
    print(f"ROOMCAM_VAPID_PUBLIC_KEY={public}")
    print(f"ROOMCAM_VAPID_PRIVATE_KEY={private}")


if __name__ == "__main__":
    main()
