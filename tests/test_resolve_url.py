from __future__ import annotations

import os
import importlib.util
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME = PROJECT_ROOT / "runtime"
PYTHON = Path(sys.executable)
RESOLVER = RUNTIME / "resolve_url.py"
UTF8_ENV = {**os.environ, "PYTHONUTF8": "1"}


def load_resolver():
    spec = importlib.util.spec_from_file_location("url_resolver", RESOLVER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_embedded_m3u8_variants_are_normalized_against_page_url():
    resolver = load_resolver()
    page = "https://site.example/show/episode.html"
    cases = [
        (
            r'var src="https:\/\/cdn.example\/v\/index.m3u8?a=1&amp;b=2";',
            "https://cdn.example/v/index.m3u8?a=1&b=2",
        ),
        (
            'src="//cdn.example/live/index.m3u8?token=x"',
            "https://cdn.example/live/index.m3u8?token=x",
        ),
        (
            'src="/media/index.m3u8?token=x"',
            "https://site.example/media/index.m3u8?token=x",
        ),
        (
            'src="streams/index.m3u8?token=x"',
            "https://site.example/show/streams/index.m3u8?token=x",
        ),
    ]
    for html_text, expected in cases:
        assert resolver.extract_embedded_m3u8(page, html_text) == expected


class RouteFallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/vod-play/123/ep1":
            payload = b'<script>var url="/broken/_fetch_p/123/ep1";</script>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif self.path == "/_fetch_p/123/ep1":
            payload = (
                b'{"playcfgs":['
                b'{"url":"https://cdn.example/fallback.m3u8"},'
                b'{"url":"https://backup.example/second.m3u8"}'
                b']}'
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        else:
            payload = b"broken route"
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


class UnicodePageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = "测试页面：正常".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


def test_ssl_eof_is_eligible_for_windows_network_fallback():
    resolver = load_resolver()
    assert resolver.should_use_windows_fallback(
        OSError("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred")
    )
    assert not resolver.should_use_windows_fallback(
        OSError("[Errno 11001] getaddrinfo failed")
    )


def test_windows_network_fallback_reads_utf8_page_with_size_limit():
    resolver = load_resolver()
    server = ThreadingHTTPServer(("127.0.0.1", 0), UnicodePageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/page"
        content = resolver.fetch_with_windows_network(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            detect_media=False,
            max_bytes=1024,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert content == "测试页面：正常"


def test_ssl_failed_host_reuses_windows_network_without_second_tls_delay():
    resolver = load_resolver()
    resolver.WINDOWS_FALLBACK_HOSTS.clear()
    primary_calls = 0
    fallback_calls = 0

    def failing_urlopen(request, timeout):
        nonlocal primary_calls
        primary_calls += 1
        raise OSError("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred")

    def successful_fallback(url, **kwargs):
        nonlocal fallback_calls
        fallback_calls += 1
        return "ok"

    original_urlopen = resolver.urllib.request.urlopen
    original_fallback = resolver.fetch_with_windows_network
    resolver.urllib.request.urlopen = failing_urlopen
    resolver.fetch_with_windows_network = successful_fallback
    try:
        assert resolver.fetch("https://same.example/page1") == "ok"
        assert resolver.fetch("https://same.example/page2") == "ok"
    finally:
        resolver.urllib.request.urlopen = original_urlopen
        resolver.fetch_with_windows_network = original_fallback
        resolver.WINDOWS_FALLBACK_HOSTS.clear()
    assert primary_calls == 1
    assert fallback_calls == 2


def test_broken_custom_route_falls_back_to_standard_tvcat_route():
    resolver = load_resolver()
    server = ThreadingHTTPServer(("127.0.0.1", 0), RouteFallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        page = f"http://127.0.0.1:{server.server_port}/vod-play/123/ep1"
        assert resolver.resolve_page(page) == "https://cdn.example/fallback.m3u8"
        assert resolver.resolve_candidates(page) == [
            "https://cdn.example/fallback.m3u8",
            "https://backup.example/second.m3u8",
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_direct_mp4_url_returns_without_network_access():
    url = "https://never-resolves.invalid/large-video.MP4?token=fixture"
    result = subprocess.run(
        [str(PYTHON), str(RESOLVER), url],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=UTF8_ENV,
        timeout=5,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == url


class OversizedPageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = b"x" * (16 * 1024 * 1024 + 1)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


def test_oversized_html_is_rejected_instead_of_fully_parsed():
    server = ThreadingHTTPServer(("127.0.0.1", 0), OversizedPageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/huge"
        result = subprocess.run(
            [str(PYTHON), str(RESOLVER), url],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=UTF8_ENV,
            timeout=10,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert result.returncode != 0
    assert "页面内容过大" in result.stdout + result.stderr


if __name__ == "__main__":
    test_ssl_eof_is_eligible_for_windows_network_fallback()
    test_windows_network_fallback_reads_utf8_page_with_size_limit()
    test_ssl_failed_host_reuses_windows_network_without_second_tls_delay()
    test_embedded_m3u8_variants_are_normalized_against_page_url()
    test_broken_custom_route_falls_back_to_standard_tvcat_route()
    test_direct_mp4_url_returns_without_network_access()
    test_oversized_html_is_rejected_instead_of_fully_parsed()
    print("PASS: resolver direct-media and memory protection tests")
