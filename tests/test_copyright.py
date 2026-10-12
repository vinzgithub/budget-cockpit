# Copyright © 2026 Vincent Fournet
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Every source file carries the copyright notice and the licence identifier."""

from pathlib import Path

from fastapi.testclient import TestClient

from budget_cockpit.app import create_app
from budget_cockpit.config import Config

ROOT = Path(__file__).resolve().parents[1]
NOTICE = "Copyright © 2026 Vincent Fournet"
SPDX = "SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0"


def source_files():
    yield ROOT / "Dockerfile"
    yield ROOT / "docker-compose.yml"
    for folder in ("src/budget_cockpit", "tests", ".github"):
        for path in (ROOT / folder).rglob("*"):
            if path.suffix in {".py", ".js", ".css", ".html", ".yml", ".svg"} and "__pycache__" not in path.parts:
                yield path


def test_every_source_file_has_the_notice():
    missing = [str(p.relative_to(ROOT)) for p in source_files()
               if NOTICE not in (text := p.read_text(encoding="utf-8")) or SPDX not in text]
    assert missing == []


def test_the_notice_shows_in_the_footer(tmp_path):
    with TestClient(create_app(Config(data_dir=tmp_path))) as client:
        page = client.get("/setup").text
    assert "© 2026 Vincent Fournet" in page
    assert "Copyright" not in page  # the template comments themselves are never sent
