"""The single HTTP session the agent uses to talk to the server."""

import os
import ssl
import requests
import agent_paths
import agent_config


def configured_ca_bundle() -> str:
    """Returns the path to the CA an administrator provisioned on this endpoint.
    """
    from_env = os.environ.get("OPENPATCH_CA_BUNDLE", "").strip()
    if from_env:
        return from_env
    return agent_config.get_option("ca_bundle")


def ca_bundle() -> str:
    """Return the path to the CA bundle."""
    return configured_ca_bundle() or agent_paths.bundled_ca_path()


class SystemTrustAdapter(requests.adapters.HTTPAdapter):
    """Makes a session verify against an SSLContext we built."""

    def __init__(self, context: ssl.SSLContext, **kwargs):
        self._context = context
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self._context
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs["ssl_context"] = self._context
        return super().proxy_manager_for(*args, **kwargs)


def system_trust_context() -> ssl.SSLContext:
    """Returns everything this machine trusts, plus the public CAs requests ships.
    """
    context = ssl.create_default_context()
    try:
        import certifi
        context.load_verify_locations(cafile=certifi.where())
    except (ImportError, OSError, ssl.SSLError):
        pass
    return context


def build_session() -> requests.Session:
    """Returns a new session built using the CA bundle."""
    session = requests.Session()

    configured = configured_ca_bundle()
    if configured and not os.path.exists(configured):
        raise FileNotFoundError(
            f"The CA bundle does not exist: {configured}\n"
            "    It comes from OPENPATCH_CA_BUNDLE or from ca_bundle in "
            f"{agent_config.CONFIG_PATH}.\n"
            "    Correct the path, or remove the setting to verify against "
            "this machine's\n"
            "    certificate store instead."
        )

    bundle = ca_bundle()
    if bundle:
        session.verify = bundle
        return session

    # No bundle: verify against the machine's own trust store (plus certifi),
    session.mount("https://", SystemTrustAdapter(system_trust_context()))
    return session


def describe_tls(server_url: str) -> str:
    """Returns a one line string describing the status of the TLS connection."""
    if not server_url.lower().startswith("https://"):
        return (
            "[!] HTTPS is not enabled"
        )
    if configured_ca_bundle():
        return f"[*] Transport: HTTPS, verifying against {configured_ca_bundle()}"
    if agent_paths.bundled_ca_path():
        return (
            "[*] Transport: HTTPS, verifying against the CA built into this "
            "agent.\n"
            "    Set OPENPATCH_CA_BUNDLE to override it without rebuilding."
        )
    return (
        "[*] Transport: HTTPS, verifying against this machine's certificate "
        "store\n"
        "    plus certifi's public CAs. Set OPENPATCH_CA_BUNDLE to trust one "
        "CA only."
    )
