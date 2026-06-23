"""Helpers for inspecting FastAPI routes across eager and lazy router implementations."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from fastapi.routing import APIRoute


@dataclass(frozen=True)
class RouteSnapshot:
    """Minimal route metadata used by route ownership tests."""

    path: str
    methods: frozenset[str]
    endpoint: Callable[..., Any]


def iter_api_routes(router_or_routes: Any) -> Iterator[RouteSnapshot]:
    """Yield concrete API routes from FastAPI route containers.

    FastAPI 0.138+ may keep included routers as deferred `_IncludedRouter`
    placeholders instead of eagerly materializing `APIRoute` instances on
    `app.routes`. The tests only need concrete route metadata, so this helper
    recursively expands those placeholders when present.
    """

    routes = getattr(router_or_routes, "routes", router_or_routes)
    for route in routes:
        yield from _iter_api_routes(route, prefix="")


def _iter_api_routes(route: Any, *, prefix: str) -> Iterator[RouteSnapshot]:
    if isinstance(route, APIRoute):
        yield RouteSnapshot(
            path=_apply_prefix(prefix, route.path),
            methods=frozenset(route.methods or ()),
            endpoint=route.endpoint,
        )
        return

    original_router = getattr(route, "original_router", None)
    include_context = getattr(route, "include_context", None)
    if original_router is None or include_context is None:
        return

    child_prefix = getattr(include_context, "prefix", "") or ""
    nested_prefix = _combine_prefixes(prefix, child_prefix)
    nested_routes: Iterable[Any] = getattr(original_router, "routes", ())
    for nested_route in nested_routes:
        yield from _iter_api_routes(nested_route, prefix=nested_prefix)


def _combine_prefixes(prefix: str, child_prefix: str) -> str:
    if not prefix:
        return child_prefix
    if not child_prefix:
        return prefix
    return f"{prefix.rstrip('/')}/{child_prefix.lstrip('/')}"


def _apply_prefix(prefix: str, path: str) -> str:
    if not prefix:
        return path

    normalized_prefix = prefix.rstrip("/")
    normalized_path = path if path.startswith("/") else f"/{path}"
    if normalized_path == normalized_prefix or normalized_path.startswith(f"{normalized_prefix}/"):
        return normalized_path
    return f"{normalized_prefix}{normalized_path}"
