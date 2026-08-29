"""Validate first-party import boundaries for app, worker, and core."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

SOURCE_ROOTS = ("core", "app", "worker")
FIRST_PARTY_ROOTS = ("app", "core", "worker")
FORBIDDEN_WORKER_APP_PREFIXES = (
    "app.api",
    "app.schemas",
    "app.models",
    "app.config",
    "app.database",
    "app.metrics",
    "app.logging_config",
    "app.services.redis_client",
)
# Anti-corruption layer: yt-dlp types/exceptions must never leak past its
# facade. Enforced here (not only via ruff TID251) because TID251's
# per-file-ignores for worker/** would otherwise lift the ban for worker code.
YT_DLP_ACL_MODULE = "app/services/yt_dlp_service.py"
# The warm-pool driver is the yt-dlp execution boundary (sibling to the
# facade): it imports yt_dlp once and is fed jobs over stdin. It carries the
# same TID251 ACL exemption in pyproject.toml, so it is exempt here too.
YT_DLP_ACL_EXEMPT_MODULES = ("app/services/yt_dlp_worker_driver.py",)
YT_DLP_PACKAGE = "yt_dlp"
# No-shim rule (CODEBOUNDARIES.md "Removed Compatibility Shims"): these
# re-export modules were deleted when core/ was extracted. Recreating one hides
# the real boundary again, so their mere existence is a violation — the rule is
# a gate, not a review convention.
FORBIDDEN_SHIM_PATHS = (
    "app/config.py",
    "app/database.py",
    "app/metrics.py",
    "app/logging_config.py",
    "app/services/redis_client.py",
    "app/models/__init__.py",
)


@dataclass(frozen=True)
class ImportReference:
    """A direct import found in a source file."""

    line_number: int
    module: str
    statement: str


@dataclass(frozen=True)
class ImportViolation:
    """An import-boundary violation found in source."""

    path: Path
    line_number: int
    statement: str
    reason: str

    def format(self, project_root: Path) -> str:
        """Format the violation for deterministic CLI output."""
        relative_path = self.path.relative_to(project_root)
        return f"{relative_path}:{self.line_number}: {self.statement} ({self.reason})"


def _is_module_or_submodule(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def _is_first_party(module: str, prefix: str) -> bool:
    return _is_module_or_submodule(module, prefix)


def _resolved_import_from_modules(node: ast.ImportFrom) -> list[str]:
    base_module = node.module or ""
    if not base_module:
        return []

    modules: list[str] = []
    for alias in node.names:
        if alias.name == "*":
            modules.append(base_module)
        else:
            modules.append(f"{base_module}.{alias.name}")
    return modules


def imported_modules(path: Path) -> list[tuple[int, str]]:
    """Return direct imported modules from a Python file."""
    return [(reference.line_number, reference.module) for reference in import_references(path)]


def import_references(path: Path) -> list[ImportReference]:
    """Return direct import references from a Python file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[ImportReference] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(
                ImportReference(node.lineno, alias.name, f"import {alias.name}")
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            modules = _resolved_import_from_modules(node)
            statement = (
                f"from {node.module or ''} import {', '.join(alias.name for alias in node.names)}"
            )
            imports.extend(
                ImportReference(node.lineno, module, statement)
                for module in modules
                if node.level == 0
            )

    return sorted(imports, key=lambda reference: (reference.line_number, reference.module))


def iter_python_files(project_root: Path) -> list[Path]:
    """Return source Python files covered by the boundary rules."""
    files: list[Path] = []
    for source_root in SOURCE_ROOTS:
        root_path = project_root / source_root
        if root_path.exists():
            files.extend(root_path.rglob("*.py"))
    return sorted(files)


def violation_reason(source_root: str, module: str) -> str | None:
    """Return the boundary violation reason for a source-root import."""
    if source_root == "core":
        if _is_first_party(module, "app") or _is_first_party(module, "worker"):
            return "core must not import app or worker modules"
    elif source_root == "app":
        if _is_first_party(module, "worker"):
            return "app must not import worker modules"
    elif source_root == "worker":
        if any(_is_module_or_submodule(module, prefix) for prefix in FORBIDDEN_WORKER_APP_PREFIXES):
            return "worker must not import API, schema, model, or removed shim app modules"
        if _is_first_party(module, "app") and not _is_module_or_submodule(module, "app.services"):
            return "worker may import only core, worker, and API-independent app.services modules"
    return None


def yt_dlp_acl_reason(path: Path, project_root: Path | None = None) -> str | None:
    """Return a violation reason if a file bypasses the yt-dlp anti-corruption layer."""
    # Compare against the path *relative to the project root*: YT_DLP_ACL_MODULE
    # is a repo-relative path, so an absolute path (path.as_posix()) would never
    # match and the facade would be falsely flagged.
    relative = (
        path.relative_to(project_root).as_posix() if project_root is not None else path.as_posix()
    )
    if relative == YT_DLP_ACL_MODULE or relative in YT_DLP_ACL_EXEMPT_MODULES:
        return None
    for reference in import_references(path):
        if reference.module == YT_DLP_PACKAGE or reference.module.startswith(f"{YT_DLP_PACKAGE}."):
            return "yt_dlp may be imported only from app/services/yt_dlp_service.py (ACL)"
    return None


def shim_violations(project_root: Path) -> list[ImportViolation]:
    """Return violations for re-created re-export shim modules."""
    violations: list[ImportViolation] = []
    for relative_path in FORBIDDEN_SHIM_PATHS:
        path = project_root / relative_path
        if path.exists():
            violations.append(
                ImportViolation(
                    path=path,
                    line_number=1,
                    statement=f"module {relative_path}",
                    reason="removed compatibility shim must not be re-created; "
                    "import the canonical core.* module directly",
                )
            )
    return violations


def analyze_project(project_root: Path) -> list[ImportViolation]:
    """Analyze source files and return import-boundary violations."""
    violations: list[ImportViolation] = shim_violations(project_root)
    for path in iter_python_files(project_root):
        source_root = path.relative_to(project_root).parts[0]
        for reference in import_references(path):
            reason = violation_reason(source_root, reference.module)
            if reason is not None:
                violations.append(
                    ImportViolation(
                        path=path,
                        line_number=reference.line_number,
                        statement=reference.statement,
                        reason=reason,
                    )
                )
        acl_reason = yt_dlp_acl_reason(path, project_root)
        if acl_reason is not None:
            for reference in import_references(path):
                if reference.module == YT_DLP_PACKAGE or reference.module.startswith(
                    f"{YT_DLP_PACKAGE}."
                ):
                    violations.append(
                        ImportViolation(
                            path=path,
                            line_number=reference.line_number,
                            statement=reference.statement,
                            reason=acl_reason,
                        )
                    )
    return sorted(violations, key=lambda violation: (violation.path, violation.line_number))


def main() -> int:
    """Run import-boundary analysis for the current project."""
    project_root = Path.cwd()
    violations = analyze_project(project_root)

    if violations:
        for violation in violations:
            print(violation.format(project_root))
        return 1

    print("Import boundary check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
