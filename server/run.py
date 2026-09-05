"""Entry point for the OpenPatch server.

    <program>                 migrate if needed, then serve the API
    <program> serve           the same, stated explicitly
    <program> migrate         run migrations and exit
    <program> --no-migrate    serve without touching the schema

where <program> is "python run.py" from a checkout
"""

import os
import sys
import argparse

import migrations
import paths
import tls
import uvicorn
from config import HOST, PORT, SSL_CERTFILE, SSL_KEYFILE, TLS_AUTO


def usage() -> str:
    """The help text, naming the program the operator is actually running.
    """
    program = paths.program_name()
    return (
        "Entry point for the OpenPatch server.\n"
        "\n"
        f"  {program}                 migrate if needed, then serve the API\n"
        f"  {program} serve           the same, stated explicitly\n"
        f"  {program} migrate         run migrations and exit\n"
        f"  {program} --no-migrate    serve without touching the schema\n"
        "\n"
        "Migrating on startup by default is deliberate: a server that starts "
        "against a schema it does not understand fails in ways that look like "
        "data corruption rather than like a missed instruction."
    )


def _certificate_advice() -> str:
    """Help text on how to obtain TLS material, in terms the deployment can act on.
    """
    if paths.has_source_tree():
        return "Run scripts/generate_certs.py to create one."
    return (
        "Create one with scripts/generate_certs.py from the OpenPatch "
        "repository, or with your own certificate authority."
    )


def _tls_arguments(automatic: tuple | None) -> dict:
    """TLS settings for uvicorn, or an empty dict for plaintext.

    `automatic` is whatever tls.ensure_certificate() resolved before anything
    else ran.
    """
    if automatic:
        certfile, keyfile = automatic
        print(f"[*] TLS enabled - serving https://{HOST}:{PORT}", flush=True)
        return {"ssl_certfile": certfile, "ssl_keyfile": keyfile}

    if not (SSL_CERTFILE or SSL_KEYFILE):
        if TLS_AUTO:
            print(f"[!] TLS disabled - serving http://{HOST}:{PORT}", flush=True)
        else:
            print(
                f"[!] TLS disabled by OPENPATCH_TLS_AUTO=0 - serving "
                f"http://{HOST}:{PORT}\n"
                "    Agent bearer tokens will cross the network in plaintext "
                "unless something\n"
                "    in front of this server terminates TLS. Remove that "
                "setting to have the\n"
                "    server issue its own certificate, or provide one: "
                f"{_certificate_advice()}",
                flush=True,
            )
        return {}

    missing = [
        name for name, value in
        (("OPENPATCH_SSL_CERTFILE", SSL_CERTFILE), ("OPENPATCH_SSL_KEYFILE", SSL_KEYFILE))
        if not value
    ]
    if missing:
        sys.exit(f"TLS is half-configured: {', '.join(missing)} is not set.")

    for label, path in (("certificate", SSL_CERTFILE), ("private key", SSL_KEYFILE)):
        if not os.path.exists(path):
            sys.exit(
                f"TLS {label} not found: {path}\n"
                f"{_certificate_advice()}"
            )

    print(f"[*] TLS enabled - serving https://{HOST}:{PORT}", flush=True)
    return {"ssl_certfile": SSL_CERTFILE, "ssl_keyfile": SSL_KEYFILE}


def migrate() -> None:
    before = migrations.current_revision()
    migrations.upgrade_to_head()
    after = migrations.current_revision()
    if before == after:
        print(f"[*] Schema already at {after}", flush=True)
    else:
        print(f"[*] Schema migrated {before or 'empty'} -> {after}", flush=True)


def serve(workers: int | None = None, automatic_tls: tuple | None = None) -> None:
    from config import DATABASE_URL

    print(f"[*] Data directory: {paths.data_dir()}", flush=True)
    print(f"[*] Database: {DATABASE_URL}", flush=True)

    tls_options = _tls_arguments(automatic_tls)

    if workers and workers > 1:
        uvicorn.run("main:app", host=HOST, port=PORT, workers=workers, **tls_options)
        return

    from main import app

    uvicorn.run(app, host=HOST, port=PORT, **tls_options)


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(
        prog=paths.program_name(),
        description=usage(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "action", nargs="?", default="serve", choices=("serve", "migrate"),
        help="serve the API (default) or run migrations and exit",
    )
    parser.add_argument(
        "--no-migrate", action="store_true",
        help="serve without bringing the schema up to date first",
    )
    parser.add_argument(
        "--workers", type=int, default=None,
        help="number of uvicorn workers; only one may run the background loops",
    )
    args = parser.parse_args(argv)

    os.environ["OPENPATCH_ENTRYPOINT"] = "run.py"

    if args.action == "migrate":
        migrate()
        return 0

    # Before the schema: the dashboard reads the CA from here to hand to
    # endpoints
    automatic_tls = tls.ensure_certificate()

    if not args.no_migrate:
        migrate()
    serve(workers=args.workers, automatic_tls=automatic_tls)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
