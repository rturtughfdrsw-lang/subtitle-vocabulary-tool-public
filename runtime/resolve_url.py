from __future__ import annotations

import base64
import json
import html as html_module
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request


MAX_PAGE_BYTES = 16 * 1024 * 1024
DIRECT_MEDIA_EXTENSIONS = {
    ".m3u8",
    ".mp4",
    ".webm",
    ".mov",
    ".mkv",
    ".m4v",
    ".ts",
    ".mp3",
    ".m4a",
}
MEDIA_CONTENT_TYPES = {
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "application/mpegurl",
}
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
WINDOWS_FALLBACK_HOSTS: set[str] = set()
WINDOWS_FETCH_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$url = $env:SUBTITLE_FETCH_URL
$headers = $env:SUBTITLE_FETCH_HEADERS | ConvertFrom-Json
$limit = [Int64]$env:SUBTITLE_FETCH_MAX
$detectMedia = $env:SUBTITLE_FETCH_DETECT -eq '1'
$request = [Net.HttpWebRequest]::Create($url)
$request.Method = 'GET'
$request.AllowAutoRedirect = $true
$request.Timeout = 30000
$request.ReadWriteTimeout = 30000
$request.AutomaticDecompression = [Net.DecompressionMethods]::GZip -bor [Net.DecompressionMethods]::Deflate
foreach ($property in $headers.PSObject.Properties) {
    $name = [string]$property.Name
    $value = [string]$property.Value
    if ($name -ieq 'User-Agent') { $request.UserAgent = $value }
    elseif ($name -ieq 'Referer') { $request.Referer = $value }
    else { $request.Headers[$name] = $value }
}
$response = $request.GetResponse()
try {
    $contentType = [string]$response.ContentType
    $simpleType = ($contentType -split ';',2)[0].Trim().ToLowerInvariant()
    $isMedia = $simpleType.StartsWith('video/') -or $simpleType.StartsWith('audio/') -or @(
        'application/vnd.apple.mpegurl',
        'application/x-mpegurl',
        'application/mpegurl'
    ) -contains $simpleType
    if ($detectMedia -and $isMedia) {
        [ordered]@{
            url = $response.ResponseUri.AbsoluteUri
            content_type = $contentType
            direct_media = $true
            encoding = 'utf-8'
            content_b64 = ''
        } | ConvertTo-Json -Compress
        exit 0
    }
    if ($response.ContentLength -gt $limit) { throw "PAGE_TOO_LARGE:$limit" }
    $stream = $response.GetResponseStream()
    $memory = [IO.MemoryStream]::new()
    try {
        $buffer = New-Object byte[] 8192
        $total = 0L
        while (($read = $stream.Read($buffer,0,$buffer.Length)) -gt 0) {
            $total += $read
            if ($total -gt $limit) { throw "PAGE_TOO_LARGE:$limit" }
            $memory.Write($buffer,0,$read)
        }
        $bytes = $memory.ToArray()
    } finally {
        $memory.Dispose()
        $stream.Dispose()
    }
    $encoding = if ([string]::IsNullOrWhiteSpace($response.CharacterSet)) { 'utf-8' } else { $response.CharacterSet }
    [ordered]@{
        url = $response.ResponseUri.AbsoluteUri
        content_type = $contentType
        direct_media = $false
        encoding = $encoding
        content_b64 = [Convert]::ToBase64String($bytes)
    } | ConvertTo-Json -Compress
} finally {
    $response.Dispose()
}
"""


class DirectMediaURL(RuntimeError):
    def __init__(self, url: str):
        super().__init__(url)
        self.url = url


def should_use_windows_fallback(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "ssl" in text
        and (
            "unexpected_eof" in text
            or "unexpected eof" in text
            or "handshake" in text
            or "tls" in text
        )
    )


def fetch_with_windows_network(
    url: str,
    *,
    headers: dict[str, str],
    detect_media: bool,
    max_bytes: int,
) -> str:
    limit = max(1, int(max_bytes))
    environment = os.environ.copy()
    environment.update(
        {
            "SUBTITLE_FETCH_URL": str(url),
            "SUBTITLE_FETCH_HEADERS": json.dumps(headers, ensure_ascii=False),
            "SUBTITLE_FETCH_MAX": str(limit),
            "SUBTITLE_FETCH_DETECT": "1" if detect_media else "0",
        }
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            WINDOWS_FETCH_SCRIPT,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        creationflags=CREATE_NO_WINDOW,
        timeout=40,
    )
    combined = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode != 0:
        if "PAGE_TOO_LARGE:" in combined:
            raise RuntimeError(
                f"页面内容过大（超过 {limit // 1024 // 1024} MB），"
                "请粘贴视频页面、m3u8 或 MP4 直链。"
            )
        raise RuntimeError("Windows 网络回退访问失败：" + combined[-500:])
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("Windows 网络回退没有返回内容。")
    payload = json.loads(lines[-1])
    if payload.get("direct_media"):
        raise DirectMediaURL(payload.get("url") or str(url))
    content = base64.b64decode(payload.get("content_b64") or "")
    encoding = payload.get("encoding") or "utf-8"
    try:
        return content.decode(encoding, errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


def is_direct_media_url(url: str) -> bool:
    path = urllib.parse.urlparse(str(url)).path.lower()
    return any(path.endswith(extension) for extension in DIRECT_MEDIA_EXTENSIONS)


def is_media_content_type(value: str) -> bool:
    content_type = str(value).split(";", 1)[0].strip().lower()
    return (
        content_type.startswith("video/")
        or content_type.startswith("audio/")
        or content_type in MEDIA_CONTENT_TYPES
    )


def normalize_embedded_media_url(page_url: str, candidate: str) -> str:
    cleaned = html_module.unescape(str(candidate)).replace("\\/", "/").strip()
    return urllib.parse.urljoin(page_url, cleaned)


def extract_embedded_m3u8(page_url: str, html_text: str) -> str | None:
    normalized = html_module.unescape(str(html_text)).replace("\\/", "/")
    match = re.search(
        r"(?P<url>"
        r"(?:(?:https?:)?//|/|(?:\.\.?/)?[A-Za-z0-9_.-]+/)"
        r"[^\s'\"<>]*?\.m3u8(?:\?[^\s'\"<>]*)?"
        r"|[A-Za-z0-9_.-]+\.m3u8(?:\?[^\s'\"<>]*)?"
        r")",
        normalized,
        re.I,
    )
    if not match:
        return None
    return normalize_embedded_media_url(page_url, match.group("url"))


def fetch(
    url,
    referer=None,
    ajax=False,
    *,
    detect_media=False,
    max_bytes=MAX_PAGE_BYTES,
):
    headers = {"User-Agent": "Mozilla/5.0"}
    if referer:
        headers["Referer"] = referer
    if ajax:
        headers["X-Requested-With"] = "XMLHttpRequest"
    host = (urllib.parse.urlparse(str(url)).hostname or "").lower()
    if os.name == "nt" and host in WINDOWS_FALLBACK_HOSTS:
        return fetch_with_windows_network(
            url,
            headers=headers,
            detect_media=detect_media,
            max_bytes=max_bytes,
        )
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            final_url = response.geturl()
            content_type = response.headers.get("Content-Type", "")
            if detect_media and is_media_content_type(content_type):
                raise DirectMediaURL(final_url)
            limit = max(1, int(max_bytes))
            declared = response.headers.get("Content-Length")
            if declared:
                try:
                    if int(declared) > limit:
                        raise RuntimeError(
                            f"页面内容过大（超过 {limit // 1024 // 1024} MB），"
                            "请粘贴视频页面、m3u8 或 MP4 直链。"
                        )
                except ValueError:
                    pass
            content = response.read(limit + 1)
            if len(content) > limit:
                raise RuntimeError(
                    f"页面内容过大（超过 {limit // 1024 // 1024} MB），"
                    "请粘贴视频页面、m3u8 或 MP4 直链。"
                )
            encoding = response.headers.get_content_charset() or "utf-8"
            return content.decode(encoding, errors="replace")
    except Exception as exc:
        if os.name == "nt" and should_use_windows_fallback(exc):
            content = fetch_with_windows_network(
                url,
                headers=headers,
                detect_media=detect_media,
                max_bytes=max_bytes,
            )
            if host:
                WINDOWS_FALLBACK_HOSTS.add(host)
            return content
        raise


def route_candidates(payload: dict, page_url: str) -> list[str]:
    found: list[str] = []
    for route in payload.get("playcfgs") or []:
        media_url = route.get("url", "")
        if ".m3u8" not in media_url.lower():
            continue
        normalized = normalize_embedded_media_url(page_url, media_url)
        if normalized not in found:
            found.append(normalized)
    return found


def resolve_candidates(page_url: str) -> list[str]:
    page_url = page_url.strip()
    if is_direct_media_url(page_url):
        return [page_url]

    try:
        html = fetch(page_url, detect_media=True)
    except DirectMediaURL as direct:
        return [direct.url]

    direct = extract_embedded_m3u8(page_url, html)
    if direct:
        return [direct]

    endpoint = re.search(r"['\"]([^'\"]*/_fetch_p/[^'\"]+)['\"]", html)
    if not endpoint:
        endpoint = re.search(
            r"var\s+url\s*=\s*['\"]([^'\"]+_fetch_p[^'\"]*)['\"]",
            html,
        )
    if endpoint:
        endpoint_path = endpoint.group(1)
        page_match = re.search(
            r"/vod-play/([^/]+)/([^/?#]+)",
            urllib.parse.urlparse(page_url).path,
        )
        if page_match:
            endpoint_path = endpoint_path.replace(
                "{0}",
                page_match.group(1),
            ).replace("{1}", page_match.group(2))
        api_url = urllib.parse.urljoin(page_url, endpoint_path)
        try:
            payload = json.loads(fetch(api_url, referer=page_url, ajax=True))
        except Exception:
            payload = {}
        candidates = route_candidates(payload, page_url)
        if candidates:
            return candidates

    parsed = urllib.parse.urlparse(page_url)
    match = re.search(r"/vod-play/([^/]+)/([^/?#]+)", parsed.path)
    if match:
        api_url = urllib.parse.urljoin(
            page_url,
            f"/_fetch_p/{match.group(1)}/{match.group(2)}",
        )
        payload = json.loads(fetch(api_url, referer=page_url, ajax=True))
        candidates = route_candidates(payload, page_url)
        if candidates:
            return candidates

    return [page_url]


def resolve_page(page_url: str) -> str:
    return resolve_candidates(page_url)[0]


def main(argv=None) -> int:
    arguments = sys.argv[1:] if argv is None else list(argv)
    if not arguments:
        raise RuntimeError("缺少视频网址。")
    for candidate in resolve_candidates(arguments[0]):
        print(candidate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
