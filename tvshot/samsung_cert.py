"""Create a Samsung (VD/TV) author + distributor certificate without Tizen Studio.

Same flow as Tizen Studio's Certificate Manager (ported from reisxd/tizen.js):
  1. you sign in to your Samsung account at SIGNIN_URL and copy the JSON it shows
  2. we generate RSA keys + CSRs (distributor CSR carries the TV's DUID)
  3. Samsung's CA (svdca.samsungqbe.com) signs them
  4. we bundle author.p12 / distributor.p12 (+ device-profile.xml for the TV)
"""
from __future__ import annotations

import json
import secrets
import urllib.request
import uuid
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

SIGNIN_URL = "https://account.samsung.com/accounts/TDC/signInGate?clientId=v285zxnl3h&tokenType=TOKEN"
CA_DIR = Path(__file__).with_name("ca")
API = "https://svdca.samsungqbe.com/apis/v3"


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _csr_author(key, name: str) -> bytes:
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
           .sign(key, hashes.SHA512()))
    return csr.public_bytes(serialization.Encoding.PEM)


def _csr_distributor(key, email: str, duids: list[str]) -> bytes:
    sans = [x509.UniformResourceIdentifier("URN:tizen:packageid=")] + \
           [x509.UniformResourceIdentifier(f"URN:tizen:deviceid={d}") for d in duids]
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TizenSDK"),
                                    x509.NameAttribute(NameOID.EMAIL_ADDRESS, email)]))
           .add_extension(x509.SubjectAlternativeName(sans), critical=False)
           .sign(key, hashes.SHA512()))
    return csr.public_bytes(serialization.Encoding.PEM)


def _post_multipart(url: str, fields: dict, file_field: str, filename: str, file_data: bytes) -> str:
    boundary = "----tvshot" + uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
                 f'Content-Type: application/octet-stream\r\n\r\n'.encode() + file_data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(url, data=b"".join(parts), method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{url} -> HTTP {e.code}: {e.read().decode(errors='ignore')[:500]}")


def _first_cert(text: str) -> x509.Certificate:
    start = text.index("-----BEGIN CERTIFICATE-----")
    end = text.index("-----END CERTIFICATE-----", start) + len("-----END CERTIFICATE-----")
    return x509.load_pem_x509_certificate(text[start:end].encode())


def _p12(key, cert, cas, password: str) -> bytes:
    enc = (serialization.PrivateFormat.PKCS12.encryption_builder()
           .kdf_rounds(2048)
           .key_cert_algorithm(pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC)
           .hmac_hash(hashes.SHA1())
           .build(password.encode()))
    return pkcs12.serialize_key_and_certificates(b"UserCertificate", key, cert, cas, enc)


def create(access: dict, duid: str, email: str, name: str, out_dir: Path) -> dict:
    """access = JSON from SIGNIN_URL (needs access_token + userId)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    token, user_id = access["access_token"], access["userId"]
    password = secrets.token_urlsafe(12)

    a_key, d_key = _key(), _key()
    common = {"access_token": token, "user_id": user_id, "platform": "VD"}
    a_resp = _post_multipart(f"{API}/authors", common, "csr", "author.csr", _csr_author(a_key, name))
    d_resp = _post_multipart(f"{API}/distributors",
                             dict(common, privilege_level="Public", developer_type="Individual"),
                             "csr", "distributor.csr", _csr_distributor(d_key, email, [duid]))
    (out_dir / "author_response.txt").write_text(a_resp)
    (out_dir / "distributor_response.txt").write_text(d_resp)

    author_ca = x509.load_pem_x509_certificate((CA_DIR / "vd_tizen_dev_author_ca.cer").read_bytes())
    public_ca = x509.load_pem_x509_certificate((CA_DIR / "vd_tizen_dev_public2.crt").read_bytes())
    (out_dir / "author.p12").write_bytes(_p12(a_key, _first_cert(a_resp), [author_ca], password))
    (out_dir / "distributor.p12").write_bytes(_p12(d_key, _first_cert(d_resp), [public_ca], password))
    # tizen.js pushes the distributor response to the TV as device-profile.xml
    (out_dir / "device-profile.xml").write_text(d_resp)
    (out_dir / "password.txt").write_text(password)
    for f in out_dir.iterdir():
        f.chmod(0o600)
    return {"dir": str(out_dir), "password": password}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 5:
        raise SystemExit(f"usage: samsung_cert.py <access.json> <duid> <email> <name>\nsign in: {SIGNIN_URL}")
    access = json.loads(Path(sys.argv[1]).read_text())
    print(create(access, sys.argv[2], sys.argv[3], sys.argv[4], Path(__file__).with_name("cert")))
