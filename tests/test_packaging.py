"""Properties the packaged artefacts depend on.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVE = os.path.abspath(os.sep)
AGENT = os.path.join(ROOT, "agent")
SERVER = os.path.join(ROOT, "server")


def top_level_modules(directory):
    return {f[:-3] for f in os.listdir(directory) if f.endswith(".py")}


def test_agent_and_server_module_names_do_not_collide():
    """Both components are flat top-level modules, and the test suite puts
    both directories on sys.path.
    """
    shared = top_level_modules(AGENT) & top_level_modules(SERVER)
    assert shared == set(), f"module names defined by both components: {sorted(shared)}"


class TestAgentPaths:
    """The agent must distinguish bundled resources from durable state.
    """

    def test_config_is_not_derived_from_dunder_file(self):
        """__file__ points into the temporary extraction directory when
        frozen."""
        source = open(os.path.join(AGENT, "agent_config.py"), encoding="utf-8").read()
        assert "CONFIG_PATH = agent_paths.config_path()" in source
        assert "__file__" not in source.split("CONFIG_PATH")[1][:200]

    def test_durable_state_follows_the_executable_when_frozen(self, monkeypatch):
        sys.path.insert(0, AGENT)
        import agent_paths

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", os.path.join(DRIVE, "Program Files", "op", "a.exe"))

        assert agent_paths.install_dir() == os.path.join(DRIVE, "Program Files", "op")
        assert agent_paths.config_path() == os.path.join(DRIVE, "Program Files", "op", "config.ini")

    def test_bundled_scripts_follow_the_extraction_directory_when_frozen(self, monkeypatch):
        sys.path.insert(0, AGENT)
        import agent_paths

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", os.path.join(DRIVE, "Temp", "_MEI123"), raising=False)

        assert agent_paths.scripts_dir() == os.path.join(DRIVE, "Temp", "_MEI123", "scripts")

    def test_a_bundled_ca_follows_the_extraction_directory_when_frozen(
        self, monkeypatch, tmp_path
    ):
        """It ships inside the executable, so it resolves like the PowerShell
        rather than like config.ini - install_dir() would look beside the exe
        for a file that is only ever inside it."""
        sys.path.insert(0, AGENT)
        import agent_paths

        (tmp_path / "ca.crt").write_text("-----BEGIN CERTIFICATE-----\n")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

        assert agent_paths.bundled_ca_path() == os.path.join(str(tmp_path), "ca.crt")

    def test_a_build_without_a_ca_reports_none_rather_than_a_missing_path(
        self, monkeypatch, tmp_path
    ):

        sys.path.insert(0, AGENT)
        import agent_paths

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

        assert agent_paths.bundled_ca_path() == ""

    def test_state_and_bundle_are_the_same_place_from_source(self, monkeypatch):
        sys.path.insert(0, AGENT)
        import agent_paths

        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)

        assert agent_paths.install_dir() == agent_paths.bundle_dir()

    def test_the_program_names_itself_correctly_when_frozen(self, monkeypatch):
        sys.path.insert(0, AGENT)
        import agent_paths

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", os.path.join(DRIVE, "op", "openpatch-agent.exe"))

        assert agent_paths.program_name() == "openpatch-agent.exe"


class TestSpecFiles:
    """PyInstaller only bundles what the spec names."""

    def _spec(self, name):
        return open(os.path.join(ROOT, "packaging", name), encoding="utf-8").read()

    def test_the_agent_bundles_its_powershell(self):
        """Those scripts are the agent's remediation logic."""
        assert '"scripts"' in self._spec("agent.spec")

    def test_the_agent_bundles_a_ca_when_one_exists(self):
        """Carrying the CA inside the executable is what lets the root of
        trust reach an endpoint at all."""
        spec = self._spec("agent.spec")

        assert "ca.crt" in spec
        assert "OPENPATCH_BUILD_CA" in spec, "a build server's CA lives elsewhere"

    def test_bundling_a_ca_is_optional(self):
        """A checkout that has never generated certificates must still build
        an agent."""
        spec = self._spec("agent.spec")

        assert "os.path.exists(CA_SOURCE)" in spec

    def test_the_agent_declares_its_com_imports(self):
        """pywin32 is imported lazily inside functions, so static analysis
        does not see it."""
        spec = self._spec("agent.spec")
        for module in ("win32com.client", "win32api", "pythoncom"):
            assert module in spec

    def test_the_server_bundles_its_migrations(self):
        """run.py migrates on startup, so an executable without them cannot
        create its own schema."""
        assert '"alembic"' in self._spec("server.spec")

    def test_the_agent_excludes_the_server_stack(self):
        """Every megabyte here is copied to every managed endpoint."""
        spec = self._spec("agent.spec")
        for unwanted in ("streamlit", "fastapi", "pandas", "tkinter"):
            assert unwanted in spec, f"{unwanted} should be named in excludes"


class TestServerPaths:
    def test_the_data_directory_is_overridable(self, monkeypatch):
        """This is what lets a container mount it as a volume."""
        sys.path.insert(0, SERVER)
        import importlib

        monkeypatch.setenv("OPENPATCH_DATA_DIR", os.path.join(DRIVE, "mounted"))
        server_paths = importlib.import_module("paths")

        assert server_paths.data_dir() == os.path.join(DRIVE, "mounted")

    def test_the_default_database_url_is_absolute(self, monkeypatch):
        """A relative default resolves against the working directory, so the
        same deployment opens a different database depending on where it was
        started from."""
        sys.path.insert(0, SERVER)
        import importlib

        monkeypatch.delenv("OPENPATCH_DATA_DIR", raising=False)
        server_paths = importlib.import_module("paths")
        url = server_paths.default_database_url()

        assert url.startswith("sqlite:///")
        assert "./" not in url


class TestDeployingDoesNotRequireACheckout:

    def _compose(self, name="docker-compose.yml"):
        return open(os.path.join(ROOT, name), encoding="utf-8").read()

    def test_the_deployment_file_pulls_rather_than_builds(self):
        compose = self._compose()

        assert "build:" not in compose, "a deployment would need the source tree"
        assert compose.count("image: ${OPENPATCH_IMAGE:-") == 2, "api and dashboard"

    def test_the_image_is_overridable(self):
        """The path is derived from the GitHub repository this is published
        under."""
        assert "OPENPATCH_IMAGE" in self._compose()

    def test_building_from_source_is_still_one_command(self):
        """Development, and any operator that would rather build the server it
        runs than trust a registry."""
        dev = self._compose("docker-compose.dev.yml")

        assert dev.count("build: .") == 2
        assert "openpatch-server:dev" in dev, "a local tag, not the published name"

    def test_the_override_only_changes_the_build(self):
        """Ports, volumes, environment and TLS stay in one file, so the two
        paths cannot drift into deploying different things."""
        dev = self._compose("docker-compose.dev.yml")

        for elsewhere in ("ports:", "volumes:", "environment:", "healthcheck:"):
            assert elsewhere not in dev, elsewhere

    def test_both_files_are_validated_in_ci(self):
        workflow = open(
            os.path.join(ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8"
        ).read()

        assert "docker-compose.dev.yml config --quiet" in workflow


def test_docker_compose_keeps_state_on_a_volume():
    """The named volume is the difference between a fleet that survives an
    image upgrade and one that does not."""
    compose = open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8").read()
    assert "openpatch-data:/data" in compose
    assert "OPENPATCH_DATA_DIR: /data" in compose


class TestTlsSurvivesTheContainerBoundary:
    """Everything needed to serve HTTPS from compose, which was mounted but
    never wired."""

    def _compose(self):
        return open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8").read()

    def test_the_api_receives_the_tls_variables(self):
        compose = self._compose()

        for variable in ("OPENPATCH_SSL_CERTFILE", "OPENPATCH_SSL_KEYFILE"):
            assert f"{variable}: ${{{variable}:-}}" in compose, variable

    def test_the_api_receives_the_self_issuing_variables(self):
        compose = self._compose()

        for variable in ("OPENPATCH_TLS_AUTO", "OPENPATCH_PUBLIC_HOST"):
            assert f"{variable}: ${{{variable}:-}}" in compose, variable

    def test_the_healthcheck_follows_both_ways_of_enabling_tls(self):
        dockerfile = open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8").read()
        healthcheck = dockerfile.split("HEALTHCHECK")[1]

        assert "OPENPATCH_SSL_CERTFILE" in healthcheck
        assert "OPENPATCH_TLS_AUTO" in healthcheck
        assert "https://127.0.0.1" in healthcheck
        assert "http://127.0.0.1" in healthcheck

    def test_the_dashboard_cannot_read_the_private_keys(self):
        compose = self._compose()
        dashboard = compose.split("  dashboard:")[1]

        assert "OPENPATCH_TLS_DIR: /tls" in compose.split("  dashboard:")[0]
        assert "openpatch-tls:/tls" not in dashboard
        assert "./server/certs:/certs:ro" not in dashboard

    def test_the_image_ships_the_agent(self):
        """So a stock deployment hands out a complete bundle with nothing
        built and nothing uploaded. """
        dockerfile = open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8").read()
        payload = os.path.join(ROOT, "packaging", "agent-payload")

        assert "packaging/agent-payload/ /app/agent-payload/" in dockerfile
        assert os.path.isdir(payload)
        assert os.path.exists(os.path.join(payload, "README.md")), "keeps it in git"

        ignore = open(os.path.join(ROOT, ".dockerignore"), encoding="utf-8").read()
        assert "packaging/" not in ignore, "it has to reach the build context"

    def test_ci_puts_the_windows_build_in_that_directory(self):
        workflow = open(
            os.path.join(ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8"
        ).read()
        image_job = workflow.split("  image:")[1].split("\n  release:")[0]

        assert "needs: [test, executables]" in image_job
        assert "download-artifact" in image_job
        assert "packaging/agent-payload/" in image_job
        assert "/app/agent-payload/openpatch-agent.exe" in image_job, "smoke tested"

    def test_a_built_agent_is_not_committed(self):
        ignore = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()

        assert "packaging/agent-payload/*.exe" in ignore

    def test_the_image_carries_what_it_needs_to_issue_a_certificate(self):
        """server/tls.py shells out to openssl.
        """
        dockerfile = open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8").read()

        assert "curl openssl" in dockerfile

    def test_the_dashboard_defaults_to_the_scheme_the_api_serves(self):
        compose = self._compose()

        assert "OPENPATCH_SERVER_URL: ${OPENPATCH_SERVER_URL:-https://api:8000}" in compose
        assert "OPENPATCH_CA_BUNDLE: ${OPENPATCH_CA_BUNDLE:-}" in compose
        assert compose.count("./server/certs:/certs:ro") == 1

    def test_the_dashboard_does_not_inherit_the_api_healthcheck(self):
        assert "_stcore/health" in self._compose()

    def test_the_certificate_covers_the_name_the_dashboard_dials(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import generate_certs

        assert "DNS:api" in generate_certs.build_san([])


def test_the_image_does_not_run_as_root():
    dockerfile = open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8").read()
    assert "USER openpatch" in dockerfile


def test_the_build_context_excludes_local_state():
    ignore = open(os.path.join(ROOT, ".dockerignore"), encoding="utf-8").read()
    for entry in (".env", "server/data/", "server/certs/", "*.db"):
        assert entry in ignore


def test_the_image_base_supports_the_pinned_dependencies():
    dockerfile = open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8").read()
    bases = [line for line in dockerfile.splitlines() if line.startswith("FROM python:")]

    assert bases, "no python base image found"
    for line in bases:
        version = line.split("python:")[1].split("-")[0]
        major, minor = (int(part) for part in version.split(".")[:2])
        assert (major, minor) >= (3, 13), f"{line} is older than the dependencies allow"


def requirement_lines(filename):
    text = open(os.path.join(ROOT, filename), encoding="utf-8").read()
    return [
        line.strip() for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_the_server_requirements_exclude_windows_only_packages():
    requirements = requirement_lines("requirements-server.txt")
    for windows_only in ("pywin32", "psutil"):
        assert not any(line.startswith(windows_only) for line in requirements), windows_only


def test_the_agent_requirements_include_pywin32():
    requirements = requirement_lines("requirements-agent.txt")
    assert any(line.startswith("pywin32") for line in requirements)


def test_the_agent_requirements_exclude_the_server_stack():
    """Every megabyte here is copied to every endpoint."""
    requirements = requirement_lines("requirements-agent.txt")
    for server_only in ("fastapi", "streamlit", "pandas", "sqlalchemy", "alembic", "uvicorn"):
        assert not any(line.startswith(server_only) for line in requirements), server_only


class TestHelpNamesTheProgram:

    def test_the_server_names_itself_when_frozen(self, monkeypatch):
        sys.path.insert(0, SERVER)
        import importlib

        server_paths = importlib.import_module("paths")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(
            sys, "executable", os.path.join(DRIVE, "op", "openpatch-server.exe")
        )

        assert server_paths.program_name() == "openpatch-server.exe"

    def test_the_server_names_the_interpreter_from_source(self, monkeypatch):
        sys.path.insert(0, SERVER)
        import importlib

        server_paths = importlib.import_module("paths")
        monkeypatch.delattr(sys, "frozen", raising=False)

        assert server_paths.program_name() == "python run.py"

    def test_the_help_text_uses_that_name(self, monkeypatch):
        sys.path.insert(0, SERVER)
        import importlib

        server_paths = importlib.import_module("paths")
        run = importlib.import_module("run")
        monkeypatch.setattr(server_paths, "program_name", lambda: "openpatch-server.exe")

        text = run.usage()

        assert "openpatch-server.exe migrate" in text
        assert "python run.py" not in text

    def test_certificate_advice_is_actionable_for_the_deployment(self, monkeypatch):
        sys.path.insert(0, SERVER)
        import importlib

        server_paths = importlib.import_module("paths")
        run = importlib.import_module("run")

        monkeypatch.setattr(server_paths, "has_source_tree", lambda: True)
        from_source = run._certificate_advice()
        assert from_source.startswith("Run scripts/generate_certs.py")

        monkeypatch.setattr(server_paths, "has_source_tree", lambda: False)
        packaged = run._certificate_advice()
        assert "OpenPatch" in packaged and "repository" in packaged
        assert packaged != from_source

    def test_a_packaged_deployment_has_no_source_tree(self, monkeypatch):
        sys.path.insert(0, SERVER)
        import importlib

        server_paths = importlib.import_module("paths")
        monkeypatch.setattr(sys, "frozen", True, raising=False)

        assert server_paths.has_source_tree() is False


def test_neither_program_hardcodes_the_other_deployments_name():
    for module, literal in (
        (os.path.join(SERVER, "run.py"), 'prog="openpatch-server"'),
        (os.path.join(AGENT, "enrollment.py"), 'prog="agent_service.py enroll"'),
    ):
        source = open(module, encoding="utf-8").read()
        assert literal not in source, f"{module} hardcodes its program name"


class TestFindingTheRepositoryFromASourceRun:

    def test_a_checkout_reports_its_root(self, monkeypatch):
        sys.path.insert(0, SERVER)
        import importlib

        server_paths = importlib.import_module("paths")
        monkeypatch.delattr(sys, "frozen", raising=False)

        assert server_paths.source_root() == ROOT
        assert os.path.isdir(os.path.join(server_paths.source_root(), "packaging"))

    def test_a_frozen_build_has_none(self, monkeypatch):
        sys.path.insert(0, SERVER)
        import importlib

        server_paths = importlib.import_module("paths")
        monkeypatch.setattr(sys, "frozen", True, raising=False)

        assert server_paths.source_root() == ""
