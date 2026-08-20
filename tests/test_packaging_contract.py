"""Offline contract tests for frozen runtime and portable release packaging."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_SCRIPT = REPO_ROOT / "main.py"
SPEC_FILE = REPO_ROOT / "gmf_cmp_monitor.spec"
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_exe.ps1"
PACKAGE_SCRIPT = REPO_ROOT / "scripts" / "package_portable.ps1"
GITIGNORE = REPO_ROOT / ".gitignore"

STAGING_FOLDER = "GMF-CMP-Monitor-Portable"
EXPECTED_FORBIDDEN_PATTERNS = (
    r"\.env$",
    r"\.log$",
    "logs",
    r"\.git",
    "__pycache__",
    r"\.pyc$",
    "tests",
    r"\.pytest_cache",
)


def _read(path: Path) -> str:
    assert path.is_file(), f"Missing packaging contract file: {path}"
    return path.read_text(encoding="utf-8")


def _extract_array(content: str, variable_name: str) -> list[str]:
    match = re.search(
        rf"\${re.escape(variable_name)}\s*=\s*@\((.*?)^\s*\)",
        content,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match, f"Could not find PowerShell array ${variable_name}"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_main_uses_frozen_base_dir_and_bundled_browsers() -> None:
    content = _read(MAIN_SCRIPT)

    assert "import os" in content
    assert "getattr(sys, \"frozen\", False)" in content
    assert "Path(sys.executable).parent" in content
    assert "Path(__file__).resolve().parent" in content
    assert "BASE_DIR / \".env\"" in content
    assert "BASE_DIR / \"logs\"" in content
    assert "bundled_browsers = base_dir / \"browsers\"" in content
    assert "bundled_browsers.is_dir()" in content
    assert "PLAYWRIGHT_BROWSERS_PATH" in content
    assert "os.environ[\"PLAYWRIGHT_BROWSERS_PATH\"]" in content
    assert content.index("PLAYWRIGHT_BROWSERS_PATH") < content.index(
        "def main("
    )


def test_pyinstaller_spec_contract() -> None:
    content = _read(SPEC_FILE)

    assert "Analysis(" in content
    assert "['main.py']" in content or '["main.py"]' in content
    assert "collect_data_files(\"playwright\")" in content
    assert "playwright" in content
    assert "playwright.sync_api" in content
    assert "tzdata" in content
    assert "dotenv" in content
    assert "pytest" in content
    assert "console=True" in content
    assert "options = []" in content
    assert "('v', None, 'OPTION')" not in content
    assert "EXE(" in content
    assert "COLLECT(" in content
    assert "name=\"GMF-CMP-Monitor\"" in content


def test_build_script_contract() -> None:
    content = _read(BUILD_SCRIPT)

    assert "Get-Command pyinstaller" in content
    assert "Remove-Item" in content
    assert "build" in content
    assert "dist" in content
    assert "-y" in content
    assert "gmf_cmp_monitor.spec" in content
    assert "GMF-CMP-Monitor.exe" in content
    assert "%LOCALAPPDATA%" in content or "$env:LOCALAPPDATA" in content
    assert "ms-playwright" in content
    assert "firefox-*" in content
    assert "Copy-Item" in content
    assert "browsers" in content
    assert "firefox.exe" in content


def test_packaging_script_uses_dist_output_and_safe_staging() -> None:
    content = _read(PACKAGE_SCRIPT)

    assert "dist/GMF-CMP-Monitor" in content or "$DistDir" in content
    assert "GMF-CMP-Monitor.exe" in content
    assert "StagingParent" in content
    assert STAGING_FOLDER in content
    assert "Copy-Item" in content
    assert "Copy-Item -LiteralPath $Entry.FullName" in content
    assert "Get-ChildItem -LiteralPath $DistDir" in content
    assert "Copy-Item -Path $RootDir -Recurse" not in content
    assert ".env.example" in content
    assert "README.md" in content
    assert "start_monitor.bat" in content
    assert "GMF-CMP-Monitor.exe" in content
    assert "tar.exe" in content
    assert "-a -cf" in content
    assert "-tf" in content
    assert "release upload" in content
    assert "Get-Command gh" in content
    assert "Copy-Item -Path $RootDir -Recurse" not in content


def test_packaging_script_rejects_forbidden_entries() -> None:
    content = _read(PACKAGE_SCRIPT)

    forbidden_patterns = _extract_array(content, "ForbiddenPatterns")
    assert tuple(forbidden_patterns) == EXPECTED_FORBIDDEN_PATTERNS
    assert r"\.env$" in forbidden_patterns
    assert r"\.log$" in forbidden_patterns
    assert "logs" in forbidden_patterns
    assert "tests" in forbidden_patterns
    assert "Forbidden entries found in staging" in content
    assert "Forbidden entries found in ZIP" in content
    assert "exit 1" in content
    assert "segment" in content
    assert "IsAnchored" in content


def test_packaging_contract_keeps_example_config_but_not_real_config() -> None:
    content = _read(PACKAGE_SCRIPT)
    forbidden_patterns = _extract_array(content, "ForbiddenPatterns")

    assert ".env.example" in content
    assert "GMF-CMP-Monitor.exe" in content
    assert "browsers" in content
    assert "firefox.exe" in content
    assert "Required files are missing from the ZIP" in content
    assert "ZIP table of contents" in content
    assert r"\.env$" in forbidden_patterns
    assert not any(re.search(pattern, ".env.example") for pattern in forbidden_patterns)


def test_gitignore_excludes_build_and_release_artifacts() -> None:
    content = _read(GITIGNORE)

    for directory in ("build/", "dist/", "staging/", "release/"):
        assert directory in content
