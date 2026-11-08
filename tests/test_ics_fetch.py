"""The iCalendar link fetch refuses internal targets, before the request and after redirects."""

from __future__ import annotations

import http.server
import threading
from urllib.parse import urlparse

import pytest

from solverforge_planner_plugin import engine_bin, tools  # noqa: E402

FEED = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"


@pytest.fixture
def server():
    """A loopback server: /feed serves iCalendar, /hop redirects to /internal, /internal records hits."""
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            if self.path == "/hop":
                self.send_response(302)
                self.send_header("Location", "/internal")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(FEED)

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", hits
    httpd.shutdown()


@pytest.fixture
def public_paths(monkeypatch):
    """Stand in for Hermes' is_safe_url: only /feed and /hop count as public addresses."""
    checked = []

    def safe(url):
        checked.append(url)
        return urlparse(url).path in ("/feed", "/hop")

    monkeypatch.setattr(tools, "_url_is_safe", safe)
    return checked


def test_a_public_feed_is_fetched(server, public_paths):
    base, hits = server
    assert tools._download_ics(f"{base}/feed").startswith("BEGIN:VCALENDAR")
    assert hits == ["/feed"]


def test_an_internal_url_is_refused_before_any_request(server, public_paths):
    base, hits = server
    with pytest.raises(ValueError, match="local, private or internal"):
        tools._download_ics(f"{base}/internal")
    assert hits == []


def test_a_redirect_to_an_internal_url_is_refused_and_not_followed(server, public_paths):
    base, hits = server
    with pytest.raises(ValueError, match="local, private or internal"):
        tools._download_ics(f"{base}/hop")
    assert hits == ["/hop"] and public_paths == [f"{base}/hop", f"{base}/internal"]


def test_only_web_schemes_are_fetched(public_paths):
    for url in ("file:///etc/passwd", "ftp://example.com/a.ics", "gopher://example.com/"):
        with pytest.raises(ValueError, match="https, http or webcal"):
            tools._download_ics(url)
    assert public_paths == []


def test_a_local_path_must_be_an_ics_file(tmp_path):
    planner = tools.Planner.__new__(tools.Planner)
    planner._source = lambda args: "s"
    other = tmp_path / "notes.txt"
    other.write_text("BEGIN:VCALENDAR")
    with pytest.raises(ValueError, match="ending in .ics"):
        planner.busy_from_ics({"source": "s", "path": str(other)})


def test_the_engine_gets_no_secrets_from_the_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-for-the-engine")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-not-for-the-engine")
    env = engine_bin.engine_env()
    assert "OPENAI_API_KEY" not in env and "GITHUB_TOKEN" not in env
    assert set(env) <= {"PATH", "SYSTEMROOT", "LANG"} and "PATH" in env
