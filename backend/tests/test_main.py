"""Tests for app/main.py — SPA serving must not leak files outside /dist."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.main import _safe_spa_path


@pytest.fixture()
def dist(tmp_path: Path) -> Path:
    d = tmp_path / "dist"
    d.mkdir()
    (d / "index.html").write_text("<html></html>", encoding="utf-8")
    (d / "favicon.ico").write_text("ico", encoding="utf-8")
    assets = d / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("alert(1)", encoding="utf-8")
    # A secret file at the same level as dist — used to prove traversal fails.
    (tmp_path / "secret.env").write_text("API_KEY=do-not-leak", encoding="utf-8")
    return d


def test_empty_path_returns_none_so_caller_serves_index(dist: Path) -> None:
    assert _safe_spa_path(dist, dist.resolve(), "") is None


def test_real_file_inside_dist_is_served(dist: Path) -> None:
    resolved = _safe_spa_path(dist, dist.resolve(), "favicon.ico")
    assert resolved is not None and resolved.is_file()
    assert resolved.name == "favicon.ico"


def test_real_nested_file_inside_dist_is_served(dist: Path) -> None:
    resolved = _safe_spa_path(dist, dist.resolve(), "assets/app.js")
    assert resolved is not None and resolved.name == "app.js"


def test_missing_file_inside_dist_returns_none(dist: Path) -> None:
    # Vite-routed deep links shouldn't 404 — the caller serves index.html.
    assert _safe_spa_path(dist, dist.resolve(), "settings") is None


def test_parent_traversal_is_refused(dist: Path) -> None:
    # If this returned a path, prod-mode would serve the .env file.
    assert _safe_spa_path(dist, dist.resolve(), "../secret.env") is None


def test_deep_parent_traversal_is_refused(dist: Path) -> None:
    assert _safe_spa_path(dist, dist.resolve(), "../../etc/passwd") is None


def test_traversal_via_nested_segments_is_refused(dist: Path) -> None:
    # `assets/../../secret.env` resolves above the dist root.
    assert _safe_spa_path(dist, dist.resolve(), "assets/../../secret.env") is None


def test_absolute_path_segment_is_refused(dist: Path) -> None:
    # Path("/etc/passwd") on Windows resolves to drive-rooted; on POSIX the
    # `/` arg replaces the prior — either way, the result is outside dist.
    assert _safe_spa_path(dist, dist.resolve(), "/etc/passwd") is None
