"""
Shared fixtures for the test suite.
"""
import os
import tempfile

import pytest

# Point to a temp config so tests never touch the real tfg.conf
os.environ.setdefault("TERRAFORM_GRAPHICAL_BACKEND_LOCAL_PATH", tempfile.mkdtemp())


@pytest.fixture(scope="session")
def tmp_conf(tmp_path_factory):
    """A minimal tfg.conf in a temp directory."""
    conf_dir = tmp_path_factory.mktemp("conf")
    conf_path = conf_dir / "tfg.conf"
    conf_path.write_text(
        "[workspaces]\n"
        f"repos_root = {conf_dir}\n"
        "[ui]\n"
        "site_name = Test TGM\n"
        "repo_url = https://example.com/repo\n"
        "[execution]\n"
        "max_concurrent = 2\n"
        "[terraform]\n"
        "default_version = system\n",
        encoding="utf-8",
    )
    return str(conf_path)


@pytest.fixture(scope="session")
def flask_app(tmp_conf):
    """A Flask test application backed by the temp config."""
    from app.app import create_app
    application = create_app(config_path=tmp_conf)
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(flask_app):
    """Flask test client."""
    with flask_app.test_client() as c:
        yield c
