"""Issuing the server's TLS material at startup.
"""

import os
import stat
import shutil
import socket
import ipaddress
import subprocess
import contextlib

import paths
from config import (
    PUBLIC_HOSTS,
    SSL_CERTFILE,
    SSL_KEYFILE,
    TLS_AUTO,
    TLS_AUTO_EXPLICIT,
    TLS_DIR,
)

CA_DAYS = 3650      # internal CA: long-lived, since rotating it re-trusts every agent
SERVER_DAYS = 825   # server cert: kept under the 825-day limit clients enforce

OPENSSL_FALLBACKS = [
    r"C:\Program Files\Git\usr\bin\openssl.exe",
    r"C:\Program Files\Git\mingw64\bin\openssl.exe",
    r"C:\Program Files\OpenSSL-Win64\bin\openssl.exe",
]


class OpenSSLMissing(RuntimeError):
    """Raised rather than exiting, so that each caller can report it in its own way."""


def find_openssl() -> str:
    found = shutil.which("openssl")
    if found:
        return found
    for candidate in OPENSSL_FALLBACKS:
        if os.path.exists(candidate):
            return candidate
    raise OpenSSLMissing(
        "openssl was not found. It ships with Git for Windows - either add it "
        "to PATH or install OpenSSL."
    )


def restrict(path: str) -> None:
    """Owner-only, for a private key.
    """
    with contextlib.suppress(OSError):
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def run(openssl: str, args: list) -> None:
    result = subprocess.run([openssl, *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"openssl {' '.join(args[:2])} failed:\n{result.stderr.strip()}")


def local_ip() -> str | None:
    """The address this machine uses to reach the network."""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))  # no packets sent; just resolves the route
        address = probe.getsockname()[0]
        probe.close()
        return address
    except OSError:
        return None


def build_san(extra: list) -> str:
    """Subject Alternative Names. Certificates are matched on SAN alone."""
    # "api" is the compose service name the dashboard dials
    dns_names, ip_addresses = ["localhost", "api"], ["127.0.0.1"]

    hostname = socket.gethostname()
    if hostname and hostname not in dns_names:
        dns_names.append(hostname)

    address = local_ip()
    if address and address not in ip_addresses:
        ip_addresses.append(address)

    for value in extra:
        value = str(value).strip()
        if not value:
            continue
        try:
            ipaddress.ip_address(value)
            if value not in ip_addresses:
                ip_addresses.append(value)
        except ValueError:
            if value not in dns_names:
                dns_names.append(value)

    entries = [f"DNS:{n}" for n in dns_names] + [f"IP:{a}" for a in ip_addresses]
    return ",".join(entries)


def _write_ext(cert_dir: str, san: str) -> str:
    """openssl x509 -req needs the SAN and usage bits in a file; -addext only
    applies to req, so they would otherwise be dropped when signing."""
    path = os.path.join(cert_dir, "server.ext")
    with open(path, "w") as handle:
        handle.write(
            "basicConstraints=CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n"
            f"subjectAltName={san}\n"
        )
    return path


def generate(cert_dir: str, extra_names: list, announce=print) -> dict:
    """Issue a server certificate in cert_dir which creates the CA if absent.
    """
    openssl = find_openssl()
    os.makedirs(cert_dir, exist_ok=True)

    material = {
        name: os.path.join(cert_dir, filename)
        for name, filename in (
            ("ca_key", "ca.key"), ("ca_crt", "ca.crt"),
            ("server_key", "server.key"), ("server_crt", "server.crt"),
        )
    }
    csr = os.path.join(cert_dir, "server.csr")
    san = build_san(extra_names)
    announce(f"[*] Certificate names: {san}")

    if os.path.exists(material["ca_key"]) and os.path.exists(material["ca_crt"]):
        announce("[*] Reusing the existing internal CA")
    else:
        announce("[*] Creating an internal CA")
        run(openssl, ["genrsa", "-out", material["ca_key"], "4096"])
        restrict(material["ca_key"])
        run(openssl, [
            "req", "-x509", "-new", "-nodes", "-key", material["ca_key"], "-sha256",
            "-days", str(CA_DAYS), "-out", material["ca_crt"],
            "-subj", "/CN=OpenPatch Internal CA/O=OpenPatch",
            "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        ])

    announce("[*] Creating the server certificate")
    run(openssl, ["genrsa", "-out", material["server_key"], "2048"])
    restrict(material["server_key"])
    run(openssl, [
        "req", "-new", "-key", material["server_key"], "-out", csr,
        "-subj", "/CN=OpenPatch Server/O=OpenPatch",
    ])
    ext_file = _write_ext(cert_dir, san)
    run(openssl, [
        "x509", "-req", "-in", csr, "-CA", material["ca_crt"], "-CAkey", material["ca_key"],
        "-CAcreateserial", "-out", material["server_crt"], "-days", str(SERVER_DAYS),
        "-sha256", "-extfile", ext_file,
    ])
    os.remove(csr)
    os.remove(ext_file)
    return material


def auto_cert_dir() -> str:
    """Where self-issued material including the private keys gets saved.
    """
    return TLS_DIR or os.path.join(paths.data_dir(), "certs")


def published_ca_path() -> str:
    """The public CA where the dashboard looks for it.
    """
    return os.path.join(paths.data_dir(), "certs", "ca.crt")


def publish_ca(source: str) -> None:
    """Copy the public half to where the dashboard reads it.
    """
    destination = published_ca_path()
    if os.path.abspath(source) == os.path.abspath(destination):
        return
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    with open(source, "rb") as incoming:
        payload = incoming.read()
    try:
        with open(destination, "rb") as existing:
            if existing.read() == payload:
                return
    except OSError:
        pass
    with open(destination, "wb") as outgoing:
        outgoing.write(payload)


def ensure_certificate() -> tuple | None:
    """The startup hook. Returns (certfile, keyfile), or None to serve plain HTTP.
    """
    if SSL_CERTFILE or SSL_KEYFILE or not TLS_AUTO:
        return None

    cert_dir = auto_cert_dir()
    certfile = os.path.join(cert_dir, "server.crt")
    keyfile = os.path.join(cert_dir, "server.key")

    if not (os.path.exists(certfile) and os.path.exists(keyfile)):
        print("[*] No TLS certificate yet - issuing one.", flush=True)
        if not PUBLIC_HOSTS:
            print(
                "[!] OPENPATCH_PUBLIC_HOST is not set, so the certificate will "
                "cover only\n"
                "    this machine and localhost. An agent dialling any other "
                "name or address\n"
                "    will refuse it - set OPENPATCH_PUBLIC_HOST to the name "
                "your endpoints use.",
                flush=True,
            )
        try:
            generate(cert_dir, PUBLIC_HOSTS)
        except (OpenSSLMissing, RuntimeError) as exc:
            if TLS_AUTO_EXPLICIT:
                raise
            print(
                f"[!] Could not issue a certificate, so the API will serve "
                f"plain HTTP:\n"
                f"    {exc}\n"
                "    Every agent's bearer token will cross the network in the "
                "clear. Install\n"
                "    openssl, or provide a certificate with "
                "OPENPATCH_SSL_CERTFILE and\n"
                "    OPENPATCH_SSL_KEYFILE. Set OPENPATCH_TLS_AUTO=0 to stop "
                "this warning.",
                flush=True,
            )
            return None

    # Published every start
    publish_ca(os.path.join(cert_dir, "ca.crt"))

    print(
        f"[*] Agents must trust {published_ca_path()}\n"
        "    Download it from the dashboard (Deploy agents), or copy it out "
        "with:\n"
        "      docker compose cp api:/data/certs/ca.crt ./ca.crt",
        flush=True,
    )
    return certfile, keyfile
