"""Site Profile schema — load, validate, store, and WS push payload."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# Default keywords / selectors shared by new profiles (synthetic until Task 4.0 fixtures).
_DEFAULT_CONFIRM_KEYWORDS = ["ยืนยัน", "confirm", "อนุมัติ", "approve", "สำเร็จ"]
_DEFAULT_ALREADY_CONFIRMED = ["ยืนยันแล้ว", "confirmed", "สำเร็จแล้ว", "approved"]
_DEFAULT_LOGOUT = ["form[action*='login']", "input[type='password']"]
_DEFAULT_ROW_HINTS = ["tr", "[class*='order']", "[class*='row']", "li", "[class*='card']"]
_DEFAULT_CANARY = "table, [class*='order-list'], [class*='order']"

_REQUIRED_STRING_LISTS = (
    "domain_patterns",
    "row_selector_hints",
    "confirm_keywords",
    "already_confirmed_indicators",
    "logout_indicators",
)

_REQUIRED_STRINGS = (
    "profile_id",
    "order_page_url_hint",
    "order_list_canary_selector",
)


def default_profile(*, profile_id: str = "example_v1") -> dict[str, Any]:
    """Return a new profile with dry_run:true and schema defaults."""
    return {
        "profile_id": profile_id,
        "domain_patterns": ["https://admin.example.invalid/*"],
        "order_page_url_hint": "/orders",
        "row_selector_hints": list(_DEFAULT_ROW_HINTS),
        "confirm_keywords": list(_DEFAULT_CONFIRM_KEYWORDS),
        "already_confirmed_indicators": list(_DEFAULT_ALREADY_CONFIRMED),
        "logout_indicators": list(_DEFAULT_LOGOUT),
        "order_list_canary_selector": _DEFAULT_CANARY,
        "uses_iframe": False,
        "dry_run": True,
        "post_click_verify_timeout_ms": 15000,
        "click_wait_max_ms": 30000,
    }


def _expect_type(value: Any, expected: type | tuple[type, ...], path: str) -> None:
    if expected is int and isinstance(value, bool):
        raise ValueError(f"{path} must be int, got {type(value).__name__}")
    if not isinstance(value, expected):
        names = (
            expected.__name__
            if isinstance(expected, type)
            else " | ".join(t.__name__ for t in expected)
        )
        raise ValueError(f"{path} must be {names}, got {type(value).__name__}")


def _expect_string_list(value: Any, path: str, *, non_empty: bool = False) -> list[str]:
    _expect_type(value, list, path)
    if non_empty and len(value) == 0:
        raise ValueError(f"{path} must be a non-empty list")
    out: list[str] = []
    for i, item in enumerate(value):
        _expect_type(item, str, f"{path}[{i}]")
        out.append(item)
    return out


def validate_profile(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a site profile. Missing dry_run defaults to True."""
    if not isinstance(raw, Mapping):
        raise ValueError(f"profile must be object, got {type(raw).__name__}")

    profile = deepcopy(dict(raw))

    if "dry_run" not in profile:
        profile["dry_run"] = True

    for key in _REQUIRED_STRINGS:
        if key not in profile:
            raise ValueError(f"{key} is required")
        _expect_type(profile[key], str, key)
        if not str(profile[key]).strip():
            raise ValueError(f"{key} must be a non-empty string")

    for key in _REQUIRED_STRING_LISTS:
        if key not in profile:
            raise ValueError(f"{key} is required")
        non_empty = key == "domain_patterns"
        profile[key] = _expect_string_list(profile[key], key, non_empty=non_empty)

    _expect_type(profile.get("uses_iframe"), bool, "uses_iframe")
    _expect_type(profile.get("dry_run"), bool, "dry_run")
    _expect_type(profile.get("post_click_verify_timeout_ms"), int, "post_click_verify_timeout_ms")
    _expect_type(profile.get("click_wait_max_ms"), int, "click_wait_max_ms")

    if profile["post_click_verify_timeout_ms"] < 0:
        raise ValueError("post_click_verify_timeout_ms must be >= 0")
    if profile["click_wait_max_ms"] < 0:
        raise ValueError("click_wait_max_ms must be >= 0")

    if "api" in profile:
        profile["api"] = _validate_api_block(profile["api"])

    if "close_job_workflow" in profile:
        profile["close_job_workflow"] = _validate_close_job_workflow(
            profile["close_job_workflow"]
        )

    return profile


def _validate_api_endpoint(raw: Any, path: str) -> dict[str, Any]:
    _expect_type(raw, dict, path)
    endpoint = deepcopy(dict(raw))
    if "method" in endpoint:
        _expect_type(endpoint["method"], str, f"{path}.method")
    if "url_template" in endpoint:
        _expect_type(endpoint["url_template"], str, f"{path}.url_template")
    if "payload_template" in endpoint:
        _expect_type(endpoint["payload_template"], dict, f"{path}.payload_template")
    if "fields_map" in endpoint:
        _expect_type(endpoint["fields_map"], dict, f"{path}.fields_map")
        for key, value in endpoint["fields_map"].items():
            _expect_type(key, str, f"{path}.fields_map key")
            _expect_type(value, str, f"{path}.fields_map.{key}")
    return endpoint


def _validate_api_block(raw: Any) -> dict[str, Any]:
    """Optional in-session API adapter block (Task 4.3b)."""
    _expect_type(raw, dict, "api")
    api = deepcopy(dict(raw))
    if "enabled" not in api:
        api["enabled"] = False
    _expect_type(api["enabled"], bool, "api.enabled")
    if "list_pending" in api:
        api["list_pending"] = _validate_api_endpoint(api["list_pending"], "api.list_pending")
    if "approve" in api:
        api["approve"] = _validate_api_endpoint(api["approve"], "api.approve")
    return api


def _validate_close_job_workflow(raw: Any) -> list[dict[str, Any]]:
    """Optional multi-step DOM close-job workflow (Task 4.5)."""
    _expect_type(raw, list, "close_job_workflow")
    steps: list[dict[str, Any]] = []
    # Keep in sync with pc/chrome-extension/engine.js runStep switch.
    allowed = {
        "check",
        "click",
        "scroll_into_view",
        "select_option",
        "verify_or_fill",
        "verify_result",
        "wait_for",
    }
    for i, step in enumerate(raw):
        path = f"close_job_workflow[{i}]"
        _expect_type(step, dict, path)
        action = step.get("action")
        _expect_type(action, str, f"{path}.action")
        if action not in allowed:
            raise ValueError(f"{path}.action must be one of {sorted(allowed)}")
        steps.append(deepcopy(dict(step)))
    return steps


def _load_profile_file(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"profile root must be object in {path}, got {type(raw).__name__}")
    return validate_profile(raw)


def load_profiles(path: Path | str) -> list[dict[str, Any]]:
    """Load and validate profiles from a JSON file or a directory of ``*.json`` files."""
    root = Path(path)
    if root.is_file():
        return [_load_profile_file(root)]
    if not root.is_dir():
        raise FileNotFoundError(f"site profiles path not found: {root}")

    profiles: list[dict[str, Any]] = []
    for file_path in sorted(root.glob("*.json")):
        profiles.append(_load_profile_file(file_path))
    return profiles


def store_profiles(path: Path | str, profiles: Sequence[Mapping[str, Any]]) -> list[Path]:
    """Validate and write each profile to ``{path}/{profile_id}.json``."""
    out_dir = Path(path)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for raw in profiles:
        profile = validate_profile(raw)
        dest = out_dir / f"{profile['profile_id']}.json"
        dest.write_text(
            json.dumps(profile, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        written.append(dest)
    return written


def build_site_profiles_message(
    profiles: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the WS payload PC pushes to the Chrome extension."""
    validated = [validate_profile(p) for p in profiles]
    return {"type": "site_profiles", "profiles": validated}
