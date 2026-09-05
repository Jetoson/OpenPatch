"""The deployment bundle: everything an endpoint needs to run the agent.
"""

import io
import os
import re
import socket
import urllib.parse
import zipfile


class UnsafeServerUrl(ValueError):
    """The address typed on the page cannot be put in the bundle."""


_NETLOC = re.compile(r"^(?:\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.\-]+)(?::\d{1,5})?$")


def validate_server_url(value: str) -> str:
    """The address, rebuilt from its parsed parts, or UnsafeServerUrl.
    """
    parsed = urllib.parse.urlparse((value or "").strip())

    if parsed.scheme not in ("http", "https"):
        raise UnsafeServerUrl(
            "The address must start with http:// or https:// - the agent "
            "takes a base URL, like https://patch.corp.local:8000"
        )
    if not _NETLOC.match(parsed.netloc):
        raise UnsafeServerUrl(
            "That is not a plain host and port. Use something like "
            "https://patch.corp.local:8000 - no path, no credentials, and "
            "no punctuation beyond the port."
        )
    return f"{parsed.scheme}://{parsed.netloc}"


# The scheduled task starts in the system directory.
INSTALL_PS1 = """# OpenPatch agent installer.
#
# Run from an elevated PowerShell, in the folder holding this script,
# openpatch-agent.exe and (if present) ca.crt:
#
#   powershell -ExecutionPolicy Bypass -File .\\install.ps1
#
# Everything it does is documented in README.txt

[CmdletBinding()]
param(
    # Prefer $env:OPENPATCH_ENROLLMENT_SECRET instead - a command-line
    # argument is visible to every user and logged verbatim. Declared here
    # anyway, or an unrecognised argument is silently ignored.
    [string]$EnrollmentSecret = $env:OPENPATCH_ENROLLMENT_SECRET
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$exe = Join-Path $here "openpatch-agent.exe"
$server = "{server_url}"

if (-not ([Security.Principal.WindowsPrincipal] `
        [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {{
    throw "Run this from an elevated PowerShell: install-task registers a task running as LOCAL SYSTEM."
}}

if (-not (Test-Path $exe)) {{
    throw "openpatch-agent.exe is not in $here. Copy it here and run this again."
}}

# Written before enrolment
$ca = Join-Path $here "ca.crt"
if (Test-Path $ca) {{
    $config = Join-Path $here "config.ini"
    if (-not (Test-Path $config)) {{
        [System.IO.File]::WriteAllLines(
            $config,
            [string[]]@("[agent]", "ca_bundle = $ca"),
            (New-Object System.Text.UTF8Encoding $false)
        )
        Write-Host "[*] Wrote $config"
    }} else {{
        Write-Host "[!] $config already exists - leaving it alone."
    }}
}}

# Handed to the agent through the environment.
if ($EnrollmentSecret) {{ $env:OPENPATCH_ENROLLMENT_SECRET = $EnrollmentSecret }}

Write-Host "[*] Enrolling against $server"
& $exe enroll --server $server
if ($LASTEXITCODE -ne 0) {{
    if (-not $EnrollmentSecret) {{
        Write-Host ""
        Write-Host "No enrolment secret was supplied. The server generates one unless"
        Write-Host "enrolment was deliberately opened, and prints it at startup"
        Write-Host "(docker compose logs api). Supply it either way:"
        Write-Host ""
        Write-Host "    `$env:OPENPATCH_ENROLLMENT_SECRET = '<secret>'"
        Write-Host "    powershell -ExecutionPolicy Bypass -File .\\install.ps1"
        Write-Host ""
        Write-Host "    ...or as an argument, which is visible in process listings:"
        Write-Host "    powershell -ExecutionPolicy Bypass -File .\\install.ps1 -EnrollmentSecret '<secret>'"
        Write-Host ""
    }}
    throw "Enrolment failed with exit code $LASTEXITCODE."
}}

Write-Host "[*] Installing the scheduled task"
& $exe install-task
if ($LASTEXITCODE -ne 0) {{ throw "install-task failed with exit code $LASTEXITCODE." }}

Write-Host "[OK] Done. The endpoint should appear in the dashboard within a poll interval."
"""

README_TXT = """OpenPatch agent deployment bundle
=================================

Server: {server_url}

Contents
--------
{contents}

What this is for
----------------
The agent verifies the server's certificate before it sends anything, and it
cannot learn which authority to trust from the server itself - an impostor
would simply present its own. ca.crt therefore travels this way instead:
downloaded by an operator who is already signed in, and copied to the
endpoint with the executable.

ca.crt is public. It is safe to copy, e-mail and store; the private key that
matches it never leaves the server.

Steps
-----
1. {step_one}

2. Copy the whole folder to the endpoint.

3. From an elevated PowerShell in that folder:

       powershell -ExecutionPolicy Bypass -File .\\install.ps1

   or, by hand:

       # required: the server generates one unless enrolment was
       # deliberately opened, and prints it in its startup log
       $env:OPENPATCH_ENROLLMENT_SECRET = "..."

       .\\openpatch-agent.exe enroll --server {server_url}
       .\\openpatch-agent.exe install-task

   Set that variable in the same shell before running install.ps1 and it is
   picked up automatically. The script also takes -EnrollmentSecret "<secret>"
   if you would rather pass it as an argument; the variable is preferred,
   because an argument is visible to every user on the machine and is written
   verbatim into the logs of whatever ran the deployment.

4. The endpoint appears in the dashboard within one poll interval.

Secrets
-------
Deliberately not in this bundle, because the dashboard does not hold them:

  OPENPATCH_ENROLLMENT_SECRET   set it in the environment before enrolling,
                                never on the command line - argv is visible
                                to every user on the machine.

  OPENPATCH_TASK_SIGNING_SECRET the agent verifies task lists with it. Set it
                                as a machine environment variable, or add
                                  task_signing_secret = <value>
                                to config.ini before enrolling. Without it the
                                agent executes tasks from anything that can
                                answer it.

Both are printed by the server when it starts - `docker compose logs api` -
and stored beside its database, in enrollment_secret and task_signing_secret.
They are not in this bundle because the dashboard is never given them.

An alternative to all of this
-----------------------------
Build the agent from the server's own checkout and ca.crt is packaged inside
the executable - nothing to copy alongside it, and no config.ini to write:

    python packaging/build.py agent
"""


def local_ip() -> str | None:
    """The address this machine uses to reach the network.
    """
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))  # no packets sent; just resolves the route
        address = probe.getsockname()[0]
        probe.close()
        return address
    except OSError:
        return None


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def suggested_url(public_url: str, public_hosts: str, server_url: str, port: str) -> str:
    """What to pre-fill as the address endpoints will dial.
    """
    if public_url.strip():
        return public_url.strip()

    hosts = [host.strip() for host in public_hosts.split(",") if host.strip()]
    if hosts:
        scheme = "https" if server_url.lower().startswith("https") else "http"
        return f"{scheme}://{hosts[0]}:{(port or '8000').strip()}"

    parsed = urllib.parse.urlparse(server_url)
    if parsed.hostname in _LOOPBACK_HOSTS:
        address = local_ip()
        if address:
            netloc = f"{address}:{parsed.port}" if parsed.port else address
            return urllib.parse.urlunparse(parsed._replace(netloc=netloc))

    return server_url


AGENT_EXE = "openpatch-agent.exe"


def agent_candidates(bundle_dir: str, source_root: str = "") -> list:
    """Where a bundle's executable can come from, in order - always a
    build's own output, never something handed to a running dashboard.
    """
    locations = [os.path.join(bundle_dir, "agent-payload", AGENT_EXE)]
    if source_root:
        locations.append(os.path.join(source_root, "packaging", "agent-payload", AGENT_EXE))
        locations.append(os.path.join(source_root, "dist", AGENT_EXE))
    return locations


def agent_path(bundle_dir: str, source_root: str = "") -> str:
    """The first candidate that exists, or "" when a build carried none."""
    for candidate in agent_candidates(bundle_dir, source_root):
        if os.path.exists(candidate):
            return candidate
    return ""


def bundle_name(server_url: str) -> str:
    """A filename that describes which fleet the bundle belongs to.
    """
    host = server_url.split("://")[-1].split("/")[0]
    safe = "".join(c if c.isalnum() or c in "-." else "-" for c in host)
    return f"openpatch-agent-{safe or 'bundle'}.zip"


def build_bundle(server_url: str, ca_path: str | None,
                 agent_path: str | None = None) -> bytes:
    """The zip, in memory. ca_path may be None when the API serves plain
    HTTP, in which case the bundle is just the instructions.
    """
    server_url = validate_server_url(server_url)
    contents = ["install.ps1  - elevated installer", "README.txt   - the same steps by hand"]
    if ca_path:
        contents.insert(0, "ca.crt       - the authority that signed the server's certificate")
    if agent_path:
        contents.insert(0, f"{AGENT_EXE}  - the agent itself")

    buffer = io.BytesIO()
    # Deterministic enough to be diffable: no timestamps of our own, and the
    # same input produces the same bytes.
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if ca_path:
            with open(ca_path, "rb") as handle:
                archive.writestr("ca.crt", handle.read())
        if agent_path:
            with open(agent_path, "rb") as handle:
                archive.writestr(AGENT_EXE, handle.read())
        archive.writestr("install.ps1", INSTALL_PS1.format(server_url=server_url))
        archive.writestr(
            "README.txt",
            README_TXT.format(
                server_url=server_url,
                contents="\n".join(f"  {line}" for line in contents),
                step_one=(
                    "Nothing - openpatch-agent.exe is already in this folder."
                    if agent_path else
                    "Put openpatch-agent.exe in this folder yourself: build it with\n"
                    "   `python packaging/build.py agent`, or use the one from a\n"
                    "   published image build. ca.crt here is its CA either way."
                ),
            ),
        )
    return buffer.getvalue()
