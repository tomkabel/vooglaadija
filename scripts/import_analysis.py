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


def analyze_project(project_root: Path) -> list[ImportViolation]:
    """Analyze source files and return import-boundary violations."""
    violations: list[ImportViolation] = []
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
