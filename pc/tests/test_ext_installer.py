"""Tests for Chrome extension installer helper + update checker."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clipsync import ext_installer


@pytest.fixture
def extension_tree(tmp_path: Path) -> Path:
    ext = tmp_path / "chrome-extension"
    ext.mkdir()
    (ext / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": "ClipSync Auto Confirm",
                "version": "1.0.0",
            }
        ),
        encoding="utf-8",
    )
    return ext


def test_extension_dir_resolves_under_base(tmp_path: Path, extension_tree: Path):
    assert ext_installer.extension_dir(base=tmp_path) == extension_tree


def test_copy_extension_path_copies_absolute_path(
    tmp_path: Path, extension_tree: Path, monkeypatch: pytest.MonkeyPatch
):
    copied: list[str] = []
    monkeypatch.setattr(ext_installer.pyperclip, "copy", lambda value: copied.append(value))

    result = ext_installer.copy_extension_path(base=tmp_path)

    assert result == str(extension_tree.resolve())
    assert copied == [str(extension_tree.resolve())]


def test_open_chrome_extensions_launches_chrome(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[tuple] = []

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return MagicMock()

    monkeypatch.setattr(ext_installer.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ext_installer, "tk", None)
    monkeypatch.setattr(ext_installer, "messagebox", None)

    message = ext_installer.open_chrome_extensions()

    assert calls == [
        (["start", "chrome", "chrome://extensions/"], {"shell": True}),
    ]
    assert "Load unpacked" in message
    assert "clipboard" in message.lower()


def test_open_chrome_extensions_returns_fallback_when_popen_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    def boom(*_args, **_kwargs):
        raise OSError("no chrome")

    monkeypatch.setattr(ext_installer.subprocess, "Popen", boom)
    monkeypatch.setattr(ext_installer, "tk", None)
    monkeypatch.setattr(ext_installer, "messagebox", None)

    message = ext_installer.open_chrome_extensions()

    assert "chrome://extensions/" in message
    assert "Load unpacked" in message
    assert "manually" in message.lower()


def test_guide_install_copies_path_and_opens_chrome(
    tmp_path: Path, extension_tree: Path, monkeypatch: pytest.MonkeyPatch
):
    copied: list[str] = []
    monkeypatch.setattr(ext_installer.pyperclip, "copy", lambda value: copied.append(value))
    monkeypatch.setattr(
        ext_installer.subprocess,
        "Popen",
        lambda *_a, **_k: MagicMock(),
    )
    monkeypatch.setattr(ext_installer, "tk", None)

    message = ext_installer.guide_install(base=tmp_path)

    assert copied == [str(extension_tree.resolve())]
    assert str(extension_tree.resolve()) in message
    assert "chrome://extensions/" in message


def test_local_manifest_version(extension_tree: Path):
    assert ext_installer.local_manifest_version(extension_tree) == "1.0.0"


def test_check_extension_update_detects_newer_release(
    tmp_path: Path, extension_tree: Path
):
    release = tmp_path / "release"
    release.mkdir()
    version_json = release / "version.json"
    version_json.write_text(
        json.dumps(
            {
                "extension": {
                    "version": "1.1.0",
                    "download_url": "",
                }
            }
        ),
        encoding="utf-8",
    )

    result = ext_installer.check_extension_update(
        version_json_path=version_json,
        extension_path=extension_tree,
    )

    assert result["update_available"] is True
    assert result["local_version"] == "1.0.0"
    assert result["remote_version"] == "1.1.0"
    assert "TODO" in result["download_note"]
    assert "1.1.0" in result["message"]


def test_check_extension_update_up_to_date(tmp_path: Path, extension_tree: Path):
    release = tmp_path / "release"
    release.mkdir()
    version_json = release / "version.json"
    version_json.write_text(
        json.dumps({"extension": {"version": "1.0.0"}}),
        encoding="utf-8",
    )

    result = ext_installer.check_extension_update(
        version_json_path=version_json,
        extension_path=extension_tree,
    )

    assert result["update_available"] is False
    assert result["local_version"] == "1.0.0"
    assert result["remote_version"] == "1.0.0"


def test_check_extension_update_notes_download_url(tmp_path: Path, extension_tree: Path):
    release = tmp_path / "release"
    release.mkdir()
    version_json = release / "version.json"
    url = "https://example.com/ext-1.2.0.zip"
    version_json.write_text(
        json.dumps(
            {
                "extension": {
                    "version": "1.2.0",
                    "download_url": url,
                }
            }
        ),
        encoding="utf-8",
    )

    result = ext_installer.check_extension_update(
        version_json_path=version_json,
        extension_path=extension_tree,
    )

    assert result["update_available"] is True
    assert result["download_path"] == url
    assert url in result["download_note"]
