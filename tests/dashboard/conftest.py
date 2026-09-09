"""Dashboard suite setup.
"""

import ast
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP = os.path.join(ROOT, "server", "dashboard", "app.py")
LOGIN = os.path.join(ROOT, "server", "dashboard", "login.py")


def _extractor(path):
    """Build an extractor over one dashboard module's source.
    """
    name = os.path.basename(path)
    tree = ast.parse(open(path, encoding="utf-8").read())

    def is_initialiser(node):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            return False
        return not any(
            isinstance(inner, (ast.Name, ast.Call, ast.Attribute))
            for inner in ast.walk(node.value)
        )

    constants = [node for node in tree.body if is_initialiser(node)]

    def _extract(names, namespace):
        wanted = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in names
        ]
        missing = set(names) - {node.name for node in wanted}
        assert not missing, f"not found in {name}: {sorted(missing)}"
        module = ast.Module(body=constants + wanted, type_ignores=[])
        exec(compile(module, name, "exec"), namespace)
        return namespace

    return _extract


@pytest.fixture(scope="session")
def app_source():
    return open(APP, encoding="utf-8").read()


@pytest.fixture(scope="session")
def login_source():
    return open(LOGIN, encoding="utf-8").read()


@pytest.fixture(scope="session")
def app_functions():
    """Named top-level functions from the dashboard page, executed against a
    namespace the test supplies."""
    return _extractor(APP)


@pytest.fixture(scope="session")
def login_functions():
    """The same, for the login page."""
    return _extractor(LOGIN)
