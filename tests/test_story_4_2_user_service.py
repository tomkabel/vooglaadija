import pytest

"""Story 4.2 ownership guardrails for UserService."""

import ast
from pathlib import Path

import pytest

from app.main import app
from tests.test_route_introspection import iter_api_routes

pytestmark = pytest.mark.slow



PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = PROJECT_ROOT / "app/services/user_service.py"
REST_AUTH_PATH = PROJECT_ROOT / "app/api/routes/auth.py"
WEB_AUTH_PATH = PROJECT_ROOT / "app/api/routes/web/web_auth.py"
WEB_AUTH_HELPERS_PATH = PROJECT_ROOT / "app/api/routes/web/web_auth_helpers.py"
WEB_SETTINGS_PATH = PROJECT_ROOT / "app/api/routes/web/web_settings.py"
WEB_PACKAGE_INIT_PATH = PROJECT_ROOT / "app/api/routes/web/__init__.py"


def _imported_modules(path: Path) -> set[str]:
    """Return module names imported by a Python source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")

    return modules


@pytest.mark.unit
def test_user_service_exists_and_exposes_required_api() -> None:
    """UserService exists and exposes the epic-required public methods."""
    from app.services.user_service import UserService


    for method_name in ("register", "change_password", "delete_account", "update_username"):
        assert callable(getattr(UserService, method_name))


@pytest.mark.unit
def test_user_service_stays_api_independent() -> None:
    """UserService does not import FastAPI, routes, schemas, templates, or token helpers."""
    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))
    forbidden_prefixes = ("fastapi", "app.api", "app.schemas")
    forbidden_names = {
        "Request",
        "Response",
        "HTMLResponse",
        "RedirectResponse",
        "HTTPException",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(forbidden_prefixes)
                assert alias.name not in forbidden_names
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith(forbidden_prefixes)
            assert module != "fastapi"
            for alias in node.names:
                assert alias.name not in forbidden_names

    service_source = SERVICE_PATH.read_text(encoding="utf-8")
    for forbidden in ("create_access_token", "create_refresh_token", "set_token_cookies"):
        assert forbidden not in service_source


@pytest.mark.unit
def test_routes_delegate_user_management_work_to_service() -> None:
    """REST/Web auth and settings modules delegate user-management work to UserService."""
    assert "UserService" in REST_AUTH_PATH.read_text(encoding="utf-8")
    assert "UserService" in WEB_AUTH_HELPERS_PATH.read_text(encoding="utf-8")
    assert "UserService" in WEB_SETTINGS_PATH.read_text(encoding="utf-8")


@pytest.mark.unit
def test_routes_no_longer_own_user_management_mutations_or_cleanup() -> None:
    """Route/helper modules no longer own moved password, username, or account cleanup logic."""
    rest_source = REST_AUTH_PATH.read_text(encoding="utf-8")
    web_auth_helpers_source = WEB_AUTH_HELPERS_PATH.read_text(encoding="utf-8")
    web_settings_source = WEB_SETTINGS_PATH.read_text(encoding="utf-8")

    assert "hash_password" not in rest_source
    for forbidden in (
        "hash_password",
        "validate_password",
        "token_version +=",
        "password_hash =",
        "default_username_from_email",
    ):
        assert forbidden not in web_auth_helpers_source

    for forbidden in (
        "verify_password",
        "DownloadJob",
        "validate_path",
        "os.remove",
        "current_user.username =",
        "db.delete",
        "_cleanup_job_files",
    ):
        assert forbidden not in web_settings_source


@pytest.mark.unit
def test_routes_do_not_import_moved_user_domain_owners() -> None:
    """Routes avoid importing password hashing and account cleanup infrastructure directly."""
    assert _imported_modules(WEB_AUTH_HELPERS_PATH).isdisjoint(
        {"app.services.auth_service", "app.utils.validators"}
    )
    assert _imported_modules(WEB_SETTINGS_PATH).isdisjoint(
        {
            "app.services.auth_service",
            "core.models.download_job",
            "core.utils.security",
            "sqlalchemy",
            "os",
        }
    )
    assert "app.services.user_service" not in _imported_modules(WEB_PACKAGE_INIT_PATH)


@pytest.mark.unit
def test_existing_web_route_ownership_stays_unchanged() -> None:
    """Story 3.1 and 3.3 Web route ownership remains unchanged after service extraction."""
    observed_routes = [
        (method, route.path, route.endpoint.__module__)
        for route in iter_api_routes(app)
        for method in route.methods
        if route.path
        in {
            "/web/settings/password",
            "/web/settings",
            "/web/settings/username",
            "/web/settings/delete-account",
        }
    ]

    assert sorted(observed_routes) == sorted(
        [
            ("POST", "/web/settings/password", "app.api.routes.web.web_auth"),
            ("GET", "/web/settings", "app.api.routes.web.web_settings"),
            ("POST", "/web/settings/username", "app.api.routes.web.web_settings"),
            ("POST", "/web/settings/delete-account", "app.api.routes.web.web_settings"),
        ]
    )
