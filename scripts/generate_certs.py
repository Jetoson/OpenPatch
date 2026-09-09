"""Generates the TLS material OpenPatch needs to serve its API over HTTPS.

Creates a small internal CA and a server certificate signed by it:
    server/certs/ca.crt      distribute to agents
    server/certs/ca.key      CA private key
    server/certs/server.crt  presented by the API
    server/certs/server.key  server private key

A CA rather than a bare self-signed certificate, because the server
certificate can then be rotated - renewal, a new IP - without touching the
trust store on every enrolled endpoint. Only ca.crt has to be distributed,
and only once.

    python scripts/generate_certs.py                    # localhost + this host
    python scripts/generate_certs.py patch.corp.local 10.0.0.5

The issuing itself lives in server/tls.py, which the server also calls at
startup when OPENPATCH_TLS_AUTO is set. One implementation, because two would
eventually issue different certificates and only one of them would be the one
agents had already trusted. If you would rather not run this at all, set that
variable and let the server issue its own - see the README.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "server")
if SERVER not in sys.path:
    sys.path.insert(0, SERVER)

import tls  # noqa: E402  - after the path insert above

build_san = tls.build_san
CERT_DIR = os.path.join(SERVER, "certs")


def main() -> None:
    print(f"output : {CERT_DIR}")
    try:
        material = tls.generate(CERT_DIR, sys.argv[1:])
    except (tls.OpenSSLMissing, RuntimeError) as exc:
        sys.exit(f"[X] {exc}")

    print("\nDone.\n")
    print("Server - set these and restart it:")
    print(f'  OPENPATCH_SSL_CERTFILE={material["server_crt"]}')
    print(f'  OPENPATCH_SSL_KEYFILE={material["server_key"]}')
    print("\nUnder Docker these are paths inside the container, and the")
    print("dashboard needs the scheme and the CA as well:")
    print("  OPENPATCH_SSL_CERTFILE=/certs/server.crt")
    print("  OPENPATCH_SSL_KEYFILE=/certs/server.key")
    print("  OPENPATCH_SERVER_URL=https://api:8000")
    print("  OPENPATCH_CA_BUNDLE=/certs/ca.crt")
    print("\nAgents - copy ca.crt to each endpoint and set:")
    print("  OPENPATCH_CA_BUNDLE=<path to ca.crt>")
    print("\nor build the agent from this checkout, which packages ca.crt")
    print("inside the executable:")
    print("  python packaging/build.py agent")
    print("\nca.crt is public and safe to copy. ca.key and server.key are private")
    print("and are gitignored - never put them on an endpoint.")


if __name__ == "__main__":
    main()
