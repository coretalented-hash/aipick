#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AIPICK 앱 서버 — 랜딩페이지(정적) + 진단 API를 한 프로세스에서 제공.

  GET /                  → index.html
  GET /assets/...        → 로고 등 정적 파일
  GET /healthz           → 헬스체크 (배포 플랫폼용)
  GET /api/diagnose?url= → diagnose.py 엔진 실행 → JSON 리포트

외부 패키지 없이 Python 표준 라이브러리만 사용합니다.

실행:  python server.py            # PORT 환경변수 또는 4321
배포:  컨테이너 PaaS(Render/Railway)가 $PORT 를 자동 주입합니다.

환경변수
  PORT                   바인딩 포트 (기본 4321; PaaS가 주입)
  AIPICK_MAX_CONCURRENT  동시 진단 수 (기본 4) — 외부 fetch 폭주 방지
  AIPICK_CACHE_TTL       진단 결과 캐시 TTL 초 (기본 600 = 10분)
  ANTHROPIC_API_KEY      [선택] 2층 LLM 모듈(--llm)용. 지금은 없어도 됩니다.
"""

import http.server
import json
import os
import socketserver
import sys
import threading
import time
import urllib.parse

# UTF-8 출력 (Windows 콘솔 대응)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "tools", "aipick-diagnose"))
import diagnose  # noqa: E402

PORT = int(os.environ.get("PORT") or (sys.argv[1] if len(sys.argv) > 1 else 4321))
MAX_CONCURRENT = int(os.environ.get("AIPICK_MAX_CONCURRENT", "4"))
CACHE_TTL = int(os.environ.get("AIPICK_CACHE_TTL", "600"))
CACHE_MAX = 500

_sem = threading.BoundedSemaphore(MAX_CONCURRENT)
_cache = {}                       # url -> (expires_at, payload, status)
_cache_lock = threading.Lock()

# 데모용 가벼운 SSRF 가드 — 내부/사설 대역은 진단 금지
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
_BLOCKED_PREFIXES = ("10.", "192.168.", "169.254.", "172.16.", "172.17.",
                     "172.18.", "172.19.", "172.2", "172.30.", "172.31.")


def _normalize(url: str) -> str:
    url = (url or "").strip()
    if url and not urllib.parse.urlparse(url).scheme:
        url = "https://" + url
    return url


def _validate(url: str):
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in ("http", "https"):
        return "http/https URL만 지원합니다."
    host = (parts.hostname or "").lower()
    if not host:
        return "올바른 URL이 아닙니다."
    if host in _BLOCKED_HOSTS or host.startswith(_BLOCKED_PREFIXES):
        return "내부/사설 주소는 진단할 수 없습니다."
    return None


def _cache_get(url):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(url)
        if hit and hit[0] > now:
            return hit[1], hit[2]
        if hit:
            _cache.pop(url, None)
    return None


def _cache_put(url, payload, status):
    with _cache_lock:
        if len(_cache) >= CACHE_MAX and url not in _cache:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            _cache.pop(oldest, None)
        _cache[url] = (time.monotonic() + CACHE_TTL, payload, status)


def diagnose_url(raw: str):
    url = _normalize(raw)
    if not url:
        return {"error": "URL이 필요합니다."}, 400
    err = _validate(url)
    if err:
        return {"error": err}, 400

    cached = _cache_get(url)
    if cached:
        payload = dict(cached[0])
        payload["cached"] = True
        return payload, cached[1]

    if not _sem.acquire(timeout=2):
        return {"error": "진단 요청이 많습니다. 잠시 후 다시 시도해 주세요."}, 503
    try:
        data = diagnose.run(url)
        data.pop("_parser", None)         # JSON 직렬화 불가 객체 제거
        _cache_put(url, data, 200)
        return data, 200
    except Exception as e:  # noqa: BLE001
        return {"error": "진단 중 오류: " + str(e)}, 500
    finally:
        _sem.release()


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def _json(self, payload, status):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == "/healthz":
            return self._json({"status": "ok", "service": "aipick"}, 200)
        if path == "/api/diagnose":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            url = (qs.get("url") or [""])[0]
            payload, status = diagnose_url(url)
            return self._json(payload, status)
        return super().do_GET()

    def end_headers(self):
        # 모든 응답에 보안 헤더 추가 (end_headers는 정적/JSON 경로 모두 거쳐 갑니다)
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, fmt, *args):  # 접근 로그 최소화
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    with ThreadingHTTPServer(("", PORT), Handler) as httpd:
        print(f"AIPICK app on http://0.0.0.0:{PORT}  "
              f"(concurrency={MAX_CONCURRENT}, cache_ttl={CACHE_TTL}s)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped.")
