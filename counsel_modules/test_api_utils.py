from __future__ import annotations

import re
from typing import Any


_CURRICULUM_ALIASES = {
    "\uacf5\ud1b5\uc218\ud5591": "\uacf5\uc2181",
    "\uacf5\uc2181": "\uacf5\uc2181",
    "\uacf5\ud1b5\uc218\ud5592": "\uacf5\uc2182",
    "\uacf5\uc2182": "\uacf5\uc2182",
    "\ubbf8\uc801\ubd841": "\ubbf8\uc8011",
    "\ubbf8\uc8011": "\ubbf8\uc8011",
    "\ubbf8\uc801\ubd842": "\ubbf8\uc8012",
    "\ubbf8\uc8012": "\ubbf8\uc8012",
    "\ud655\ub960\uacfc\ud1b5\uacc4": "\ud655\ud1b5",
    "\ud655\ud1b5": "\ud655\ud1b5",
}


def clean_test_api_base_url(base_url: str, default: str = "") -> str:
    """Return a normalized test API base URL without a trailing slash."""
    value = str(base_url or "").strip()
    if not value:
        value = str(default or "").strip()
    return value.rstrip("/") if value else ""


def fetch_tests_config(base_url: str, timeout: int = 8) -> dict[str, Any]:
    """Fetch the read-only tests configuration endpoint."""
    base = clean_test_api_base_url(base_url)
    if not base:
        return _api_result(False, 0, {}, "base_url is empty")
    return _fetch_json(f"{base}/api/tests-config", timeout=timeout)


def fetch_tests_stats(base_url: str, recent_days: int = 30, timeout: int = 8) -> dict[str, Any]:
    """Fetch the read-only recent tests statistics endpoint."""
    base = clean_test_api_base_url(base_url)
    if not base:
        return _api_result(False, 0, {}, "base_url is empty")
    days = _safe_positive_int(recent_days, default=30)
    return _fetch_json(f"{base}/api/tests-stats", timeout=timeout, params={"recent_days": days})


def flatten_tests_config(config: dict[str, Any]) -> list[dict[str, object]]:
    """Flatten tests-config JSON into rows suitable for later UI use."""
    data = _unwrap_api_data(config)
    categories = data.get("categories") if isinstance(data, dict) else {}
    if not isinstance(categories, dict):
        return []

    rows: list[dict[str, object]] = []
    for category_key, category_payload in categories.items():
        if not isinstance(category_payload, dict):
            continue
        tests = category_payload.get("tests") or []
        if not isinstance(tests, list):
            continue
        category = str(category_key or "").strip()
        category_label = str(category_payload.get("label") or "").strip()
        for test in tests:
            if not isinstance(test, dict):
                continue
            rows.append(
                {
                    "category": category,
                    "category_label": category_label,
                    "test_id": str(test.get("id") or "").strip(),
                    "name": str(test.get("name") or "").strip(),
                    "curriculum": str(test.get("curriculum") or "").strip(),
                    "problems": test.get("problems"),
                    "has_answer_key": _has_meaningful_value(test.get("answerKey"))
                    or _has_meaningful_value(test.get("answerKeys")),
                }
            )
    return rows


def summarize_tests_stats(stats: dict[str, Any]) -> dict[str, object]:
    """Return compact counts from tests-stats JSON, tolerating empty responses."""
    data = _unwrap_api_data(stats)
    if not isinstance(data, dict):
        data = {}
    range_data = data.get("range") if isinstance(data.get("range"), dict) else {}

    unit_count = _count_rows(data.get("unit_rows"))
    final_count = _count_rows(data.get("final_rows"))
    hell_count = _count_rows(data.get("hell_rows"))
    by_test_count = _count_rows(data.get("by_test"))

    return {
        "range_start": str(range_data.get("start") or ""),
        "range_end": str(range_data.get("end") or ""),
        "updated_at": str(data.get("updatedAt") or ""),
        "unit_count": unit_count,
        "final_count": final_count,
        "hell_count": hell_count,
        "by_test_count": by_test_count,
        "has_data": any([unit_count, final_count, hell_count, by_test_count]),
    }


def filter_tests_by_curriculum(flat_tests: list[dict], curriculum: str) -> list[dict]:
    """Filter flattened test rows by normalized curriculum aliases."""
    target = normalize_test_curriculum(curriculum)
    if not target:
        return []
    return [
        row
        for row in flat_tests or []
        if normalize_test_curriculum(row.get("curriculum", "")) == target
    ]


def normalize_test_curriculum(value: str) -> str:
    """Normalize common curriculum aliases for test filtering."""
    compact = re.sub(r"\s+", "", str(value or "").strip())
    if not compact:
        return ""
    return _CURRICULUM_ALIASES.get(compact, compact)


def _fetch_json(url: str, timeout: int = 8, params: dict[str, Any] | None = None) -> dict[str, Any]:
    # Keep requests import lazy so importing this module does not slow app startup.
    try:
        import requests
    except Exception as exc:  # pragma: no cover - depends on environment
        return _api_result(False, 0, {}, f"requests import failed: {exc}")

    safe_timeout = _safe_positive_int(timeout, default=8)
    try:
        response = requests.get(url, params=params, timeout=safe_timeout)
        status_code = int(getattr(response, "status_code", 0) or 0)
        if not response.ok:
            detail = str(getattr(response, "text", "") or "").strip()[:500]
            return _api_result(False, status_code, {}, f"HTTP {status_code}: {detail}")
        try:
            data = response.json()
        except ValueError as exc:
            return _api_result(False, status_code, {}, f"JSON parse failed: {exc}")
        if not isinstance(data, dict):
            return _api_result(False, status_code, {}, "response JSON is not an object")
        return _api_result(True, status_code, data, "")
    except Exception as exc:  # pragma: no cover - network/environment dependent
        status_code = int(getattr(getattr(exc, "response", None), "status_code", 0) or 0)
        return _api_result(False, status_code, {}, str(exc))


def _api_result(ok: bool, status_code: int, data: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "ok": bool(ok),
        "status_code": int(status_code or 0),
        "data": data if isinstance(data, dict) else {},
        "error": str(error or ""),
    }


def _unwrap_api_data(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if {"ok", "status_code", "data", "error"}.issubset(value.keys()):
        data = value.get("data")
        return data if isinstance(data, dict) else {}
    return value


def _safe_positive_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(default)
    return max(1, number)


def _count_rows(value: Any) -> int:
    if isinstance(value, (list, tuple, set)):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    return 0


def _has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_meaningful_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_meaningful_value(item) for item in value)
    return True
