from datetime import UTC, datetime

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from ghapp import (
    GitHubAppNotConfiguredError,
    load_private_key,
    make_app_jwt,
    sign_payload,
    verify_signature,
)


@pytest.fixture(scope="module")
def keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private_pem, public_pem


def test_app_jwt_is_rs256_signed_with_backdated_iat(keypair: tuple[str, str]) -> None:
    private_pem, public_pem = keypair
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    token = make_app_jwt("12345", private_pem, now=now)

    claims = jwt.decode(token, public_pem, algorithms=["RS256"], options={"verify_exp": False})
    assert claims["iss"] == "12345"
    assert claims["iat"] == int(now.timestamp()) - 60
    # GitHub rejects tokens whose lifetime exceeds 10 minutes.
    assert claims["exp"] - claims["iat"] <= 600
    assert claims["exp"] > int(now.timestamp())


def test_app_jwt_requires_app_id(keypair: tuple[str, str]) -> None:
    with pytest.raises(GitHubAppNotConfiguredError):
        make_app_jwt("", keypair[0])


def test_load_private_key_unescapes_inline_newlines() -> None:
    assert load_private_key(key_text="a\\nb") == "a\nb"


def test_load_private_key_prefers_path(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "key.pem"
    path.write_text("from-file", encoding="utf-8")
    assert load_private_key(key_text="inline", key_path=str(path)) == "from-file"


def test_load_private_key_missing_raises() -> None:
    with pytest.raises(GitHubAppNotConfiguredError):
        load_private_key()


def test_signature_round_trip() -> None:
    body = b'{"action":"opened"}'
    header = sign_payload("s3cret", body)
    assert header.startswith("sha256=")
    assert verify_signature("s3cret", body, header)


@pytest.mark.parametrize(
    ("secret", "body", "header"),
    [
        ("s3cret", b"tampered", sign_payload("s3cret", b"original")),
        ("other", b"x", sign_payload("s3cret", b"x")),
        ("s3cret", b"x", None),
        ("s3cret", b"x", "sha1=abc"),
        ("", b"x", sign_payload("", b"x")),  # an unset secret never verifies
    ],
)
def test_signature_rejects(secret: str, body: bytes, header: str | None) -> None:
    assert not verify_signature(secret, body, header)
