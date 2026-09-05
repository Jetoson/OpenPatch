
import os
import paths
import env_file


# Loaded before the first os.environ read below; real env vars still win.
env_file.load()

# Derived from the install location, not the working directory, so the same
# deployment always opens the same database.
DATABASE_URL = os.environ.get("OPENPATCH_DATABASE_URL") or paths.default_database_url()

# Optional: raises NVD API rate limits from 5/30s to 50/30s when set.
# https://nvd.nist.gov/developers/request-an-api-key
NVD_API_KEY = os.environ.get("OPENPATCH_NVD_API_KEY")

# Shared secret protecting agent enrollment. If left unset,
# the server generates one on first start.
ENROLLMENT_SECRET = os.environ.get("OPENPATCH_ENROLLMENT_SECRET")

# Explicit setting to enrol without a secret (for simple  settings or home-labs)
ENROLLMENT_OPEN = os.environ.get("OPENPATCH_ENROLLMENT_OPEN", "") not in (
    "", "0", "false", "False",
)

# Admin API key secures every operator route. It protects endpoints to queue
# work on other endpoints. If left unset, the generates one.
ADMIN_API_KEY = os.environ.get("OPENPATCH_ADMIN_API_KEY")
ADMIN_KEY_FILE = os.environ.get("OPENPATCH_ADMIN_KEY_FILE") or os.path.join(
    paths.data_dir(), "admin_key"
)

# HMACs the task list sent to agents to authenticate the server.
# If left unset, the server generates one on first start.
TASK_SIGNING_SECRET = os.environ.get("OPENPATCH_TASK_SIGNING_SECRET")

# TLS
SSL_CERTFILE = os.environ.get("OPENPATCH_SSL_CERTFILE")
SSL_KEYFILE = os.environ.get("OPENPATCH_SSL_KEYFILE")

# On by default: the server issues its own CA and certificate on first start
# and serves them. OPENPATCH_TLS_AUTO=0 opts out,
# e.g. behind a reverse proxy that terminates TLS itself.
_TLS_AUTO_RAW = os.environ.get("OPENPATCH_TLS_AUTO", "")
TLS_AUTO = _TLS_AUTO_RAW not in ("0", "false", "False")
TLS_AUTO_EXPLICIT = bool(_TLS_AUTO_RAW.strip())

# Where self-issued TLS material is kept; defaults to certs/ in the data
# directory.
TLS_DIR = os.environ.get("OPENPATCH_TLS_DIR", "").strip()

# Names and addresses endpoints will dial, put in the self-issued
# certificate.
PUBLIC_HOSTS = [
    host.strip()
    for host in os.environ.get("OPENPATCH_PUBLIC_HOST", "").split(",")
    if host.strip()
]
HOST = os.environ.get("OPENPATCH_HOST", "127.0.0.1")
PORT = int(os.environ.get("OPENPATCH_PORT", "8000"))

# Deployment rings, least to most risky to patch, served to the dashboard so
# the API and the UI dropdowns cannot drift apart.
DEPLOYMENT_RINGS = ["Test-Ring", "Production-Ring"]
DEFAULT_DEPLOYMENT_RING = DEPLOYMENT_RINGS[0]


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


# Fleet scale
# How often agents heartbeat.
AGENT_POLL_INTERVAL_SECONDS = _int_env("OPENPATCH_AGENT_POLL_INTERVAL", 30)

# An endpoint counts as online while its last heartbeat is newer than this.
ONLINE_THRESHOLD_SECONDS = _int_env(
    "OPENPATCH_ONLINE_THRESHOLD", AGENT_POLL_INTERVAL_SECONDS * 3
)

# A telemetry history row is written at most this often per device.
TELEMETRY_SAMPLE_INTERVAL_SECONDS = _int_env("OPENPATCH_TELEMETRY_INTERVAL", 300)

# Telemetry history older than this is pruned by the maintenance loop.
TELEMETRY_RETENTION_DAYS = _int_env("OPENPATCH_TELEMETRY_RETENTION_DAYS", 14)

# Completed task rows older than this are pruned.
TASK_RETENTION_DAYS = _int_env("OPENPATCH_TASK_RETENTION_DAYS", 90)

# How often the maintenance loop runs (set 0 to disable it).
MAINTENANCE_INTERVAL_SECONDS = _int_env("OPENPATCH_MAINTENANCE_INTERVAL", 3600)

# Default and maximum page size for list routes.
DEFAULT_PAGE_SIZE = _int_env("OPENPATCH_DEFAULT_PAGE_SIZE", 200)
MAX_PAGE_SIZE = _int_env("OPENPATCH_MAX_PAGE_SIZE", 1000)

# External lookup caching
# How long a resolved CPE match is trusted before we ask NVD again.
CPE_CACHE_TTL_DAYS = _int_env("OPENPATCH_CPE_CACHE_TTL_DAYS", 30)

# A product with no CPE today may have one next month, and re-checking a
# miss is cheaper than a hit.
CPE_MISS_CACHE_TTL_DAYS = _int_env("OPENPATCH_CPE_MISS_CACHE_TTL_DAYS", 7)

# How long CVE findings for a product are trusted
CVE_CACHE_TTL_DAYS = _int_env("OPENPATCH_CVE_CACHE_TTL_DAYS", 7)

# endoflife.date responses.
EOL_CACHE_TTL_HOURS = _int_env("OPENPATCH_EOL_CACHE_TTL_HOURS", 24)

# How long a computed fleet-wide findings/summary rollup is reused.
FINDINGS_CACHE_TTL_SECONDS = _int_env("OPENPATCH_FINDINGS_CACHE_TTL", 60)

#Automatic vulnerability scanning
# Resolves software to CPEs and looks up their CVEs on a schedule.
SCAN_ENABLED = os.environ.get("OPENPATCH_SCAN_ENABLED", "1") not in ("0", "false", "False")
SCAN_INTERVAL_SECONDS = _int_env("OPENPATCH_SCAN_INTERVAL", 3600)

# How long after startup the first scan begins.
SCAN_STARTUP_DELAY_SECONDS = _int_env("OPENPATCH_SCAN_STARTUP_DELAY", 30)

# Limits on external work per cycle.
SCAN_MAX_RESOLVE_PER_CYCLE = _int_env("OPENPATCH_SCAN_MAX_RESOLVE", 200)
SCAN_MAX_CVE_PER_CYCLE = _int_env("OPENPATCH_SCAN_MAX_CVE", 100)
