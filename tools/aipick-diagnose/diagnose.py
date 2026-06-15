#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AIPICK Diagnose — 코탤(COTAL) AI 검색 최적화 진단 엔진 (MVP)
=============================================================

랜딩페이지 "3단계면 AI가 내 이름을 말합니다"의 1·2단계(무료 진단 / 원인 분석)를
실제로 수행하는 엔진입니다. URL 하나만 넣으면:

  1. AI 크롤러 접근성   — robots.txt에서 GPTBot/ClaudeBot/Google-Extended/PerplexityBot 등
                          주요 AI 크롤러를 차단하고 있는지 검사
  2. 구조화 데이터       — JSON-LD(schema.org), Open Graph, 메타태그 상태
  3. 크롤링 기본기       — title / meta description / lang / canonical / sitemap / 제목 구조
  4. 콘텐츠 인용 가능성   — AI가 인용하기 좋은 "자기완결형 블록" 비율, 구체적 숫자, Q&A 구조
  5. AIPICK Score        — 위 항목을 0~100으로 합산 + 우선순위 개선 권고

이 모든 검사는 *결정론적*이며 외부 API 키가 필요 없습니다 (Python 표준 라이브러리만 사용).

[선택] 3개 AI가 내 브랜드를 아는지 확인 (--llm)
  "강남 세무사 추천해줘" 같은 추천형 질문을 실제 LLM에 던져 내 브랜드가 등장하는지 확인합니다.
  이 부분은 API 키와 비용이 필요합니다. Claude(Anthropic) 어댑터가 구현되어 있고
  ChatGPT/Gemini/Perplexity는 같은 인터페이스로 확장하면 됩니다.

사용법:
  python diagnose.py https://example.com
  python diagnose.py https://example.com --json
  python diagnose.py https://example.com --brand "코탤" --queries "강남 세무사 추천" "AI 마케팅 대행"
  python diagnose.py https://example.com --brand "코탤" --queries "강남 세무사 추천" --llm   # API 키 필요
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import urllib.request
import urllib.error
import urllib.robotparser
import zlib
from html.parser import HTMLParser
from urllib.parse import urlparse, urljoin

# Windows 콘솔(cp949) 등에서 한글/특수문자 출력이 깨지지 않도록 UTF-8로 강제
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # Python 3.7+
    except Exception:  # noqa: BLE001
        pass

# ---------------------------------------------------------------------------
# 주요 AI 크롤러 (User-Agent) — robots.txt에서 이 봇들을 차단하면 AI가 사이트를 못 읽습니다.
# ---------------------------------------------------------------------------
AI_CRAWLERS = {
    "GPTBot":            "OpenAI — ChatGPT 학습/색인 크롤러",
    "OAI-SearchBot":     "OpenAI — ChatGPT 검색(SearchGPT) 크롤러",
    "ChatGPT-User":      "OpenAI — ChatGPT 실시간 브라우징",
    "ClaudeBot":         "Anthropic — Claude 학습 크롤러",
    "Claude-Web":        "Anthropic — Claude 실시간 브라우징",
    "anthropic-ai":      "Anthropic — 레거시 크롤러",
    "Google-Extended":   "Google — Gemini 학습 데이터",
    "PerplexityBot":     "Perplexity — 색인 크롤러",
    "Perplexity-User":   "Perplexity — 실시간 인용",
    "CCBot":             "Common Crawl — 다수 LLM의 학습 데이터 원천",
    "Bytespider":        "ByteDance(Doubao) 크롤러",
    "Amazonbot":         "Amazon — Alexa/AI 크롤러",
    "Applebot-Extended": "Apple — AI 학습 데이터",
    "meta-externalagent": "Meta — Llama 학습 크롤러",
}

# AI 추천에 특히 유효한 schema.org 타입 (전문직/지역 비즈니스 중심)
VALUABLE_SCHEMA_TYPES = {
    "Organization", "LocalBusiness", "ProfessionalService", "LegalService",
    "MedicalBusiness", "Dentist", "Physician", "AccountingService",
    "Attorney", "MedicalClinic", "FAQPage", "QAPage", "Article",
    "BlogPosting", "BreadcrumbList", "WebSite", "Person", "Service",
}

USER_AGENT = ("Mozilla/5.0 (compatible; AIPICK-Diagnose/1.0; +https://cotal.kr) "
              "Chrome/124.0 Safari/537.36")
TIMEOUT = 8           # 웹 엔드포인트 응답성을 위해 fetch당 8초 (robots + page = 최대 ~16초)
MAX_BYTES = 3_000_000


# ===========================================================================
# 네트워크
# ===========================================================================
def _decompress(raw, encoding):
    """Content-Encoding 또는 매직바이트 기준으로 gzip/deflate 해제."""
    enc = (encoding or "").lower()
    if enc == "gzip" or raw[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(raw)
        except Exception:  # noqa: BLE001
            return raw
    if enc == "deflate":
        for wbits in (-zlib.MAX_WBITS, zlib.MAX_WBITS):
            try:
                return zlib.decompress(raw, wbits)
            except Exception:  # noqa: BLE001
                continue
    return raw


def fetch(url: str):
    """URL을 가져와 (status, headers, text, final_url) 반환. 실패 시 status=None."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,*/*",
        "Accept-Encoding": "gzip, deflate, identity",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read(MAX_BYTES)
            raw = _decompress(raw, resp.headers.get("Content-Encoding"))
            charset = resp.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            return resp.status, dict(resp.headers), text, resp.geturl()
    except urllib.error.HTTPError as e:
        return e.code, dict(getattr(e, "headers", {}) or {}), "", url
    except Exception as e:  # noqa: BLE001
        return None, {"_error": str(e)}, "", url


# ===========================================================================
# 1. AI 크롤러 접근성 (robots.txt)
# ===========================================================================
def check_ai_crawlers(base_url: str):
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    status, _, text, _ = fetch(robots_url)

    rp = urllib.robotparser.RobotFileParser()
    sitemaps = []
    robots_exists = bool(status and status == 200 and text.strip())

    if robots_exists:
        rp.parse(text.splitlines())
        sitemaps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", text)
    else:
        rp.parse([])  # robots.txt 없음 → 전부 허용으로 간주

    results = {}
    root = f"{parsed.scheme}://{parsed.netloc}/"
    for bot, desc in AI_CRAWLERS.items():
        try:
            allowed = rp.can_fetch(bot, root)
        except Exception:  # noqa: BLE001
            allowed = True
        results[bot] = {"allowed": allowed, "desc": desc}

    return {
        "robots_url": robots_url,
        "robots_exists": robots_exists,
        "sitemaps_declared": sitemaps,
        "bots": results,
    }


# ===========================================================================
# 2~4. HTML 분석 (JSON-LD, 메타, 인용 가능성)
# ===========================================================================
class PageParser(HTMLParser):
    BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "blockquote"}
    SKIP_TAGS = {"script", "style", "nav", "footer", "noscript", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.lang = ""
        self.metas = {}          # name/property -> content
        self.jsonld_types = []   # schema.org @type 값들
        self.has_microdata = False
        self.canonical = ""
        self.h1_count = 0
        self.headings = []
        self.blocks = []         # (tag, text) 블록 단위 본문
        self._in_title = False
        self._in_jsonld = False
        self._jsonld_buf = []
        self._skip_depth = 0
        self._cur_tag = None
        self._cur_text = []

    # --- 시작 태그 ---
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html" and a.get("lang"):
            self.lang = a["lang"]
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            key = a.get("name") or a.get("property")
            if key:
                self.metas[key.lower()] = a.get("content", "")
        if tag == "link" and a.get("rel", "").lower() == "canonical":
            self.canonical = a.get("href", "")
        if "itemscope" in a:
            self.has_microdata = True
        if tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_jsonld = True
            self._jsonld_buf = []
        if tag == "h1":
            self.h1_count += 1
        if tag in self.BLOCK_TAGS and self._skip_depth == 0:
            self._flush_block()
            self._cur_tag = tag
            self._cur_text = []

    # --- 끝 태그 ---
    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            self._consume_jsonld("".join(self._jsonld_buf))
        if tag in self.BLOCK_TAGS:
            if tag in ("h1", "h2", "h3", "h4") and self._cur_text:
                self.headings.append("".join(self._cur_text).strip())
            self._flush_block()
            self._cur_tag = None

    # --- 텍스트 ---
    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_jsonld:
            self._jsonld_buf.append(data)
        if self._cur_tag and self._skip_depth == 0:
            self._cur_text.append(data)

    def _flush_block(self):
        if self._cur_tag and self._cur_text:
            txt = re.sub(r"\s+", " ", "".join(self._cur_text)).strip()
            if txt:
                self.blocks.append((self._cur_tag, txt))
        self._cur_text = []

    def _consume_jsonld(self, raw):
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            return
        for t in _iter_types(data):
            self.jsonld_types.append(t)


def _iter_types(node):
    """JSON-LD에서 @type 값을 재귀적으로 수집."""
    if isinstance(node, dict):
        t = node.get("@type")
        if isinstance(t, str):
            yield t
        elif isinstance(t, list):
            for x in t:
                if isinstance(x, str):
                    yield x
        for v in node.values():
            yield from _iter_types(v)
    elif isinstance(node, list):
        for x in node:
            yield from _iter_types(x)


# 구체적 숫자/통계 (인용 가능성 신호)
NUM_RE = re.compile(r"\d+(?:[.,]\d+)?\s?(?:%|퍼센트|원|만원|억|개|명|배|점|위|년|개월|주|일|시간|분)")
# 한국어/영어 질문형 어미 (FAQ/Q&A 신호)
Q_RE = re.compile(r"(\?|까요|나요|할까|있나요|인가요|무엇|어떻게|왜|언제|어디)\s*$")


def analyze_content(blocks):
    """AI 인용 가능성 휴리스틱."""
    # 자기완결형 블록: 한국어 기준 대략 80~600자(영문 단어 ~40~300)의 문단
    well_sized = 0
    numeric_blocks = 0
    qa_like = 0
    total_chars = 0
    para_blocks = [(t, txt) for t, txt in blocks if t in ("p", "li", "blockquote")]

    for tag, txt in para_blocks:
        n = len(txt)
        total_chars += n
        if 80 <= n <= 600:
            well_sized += 1
        if NUM_RE.search(txt):
            numeric_blocks += 1

    for tag, txt in blocks:
        if tag in ("h2", "h3", "h4") and Q_RE.search(txt.strip()):
            qa_like += 1

    return {
        "paragraph_blocks": len(para_blocks),
        "well_sized_blocks": well_sized,
        "numeric_blocks": numeric_blocks,
        "qa_like_headings": qa_like,
        "total_text_chars": total_chars,
    }


# ===========================================================================
# 점수 계산 (AIPICK Score)
# ===========================================================================
def compute_score(crawlers, parser: PageParser, content, page_status):
    recs = []  # (priority, message)

    # --- 1) AI 크롤러 접근성 (가중치 35) ---
    bots = crawlers["bots"]
    allowed = sum(1 for b in bots.values() if b["allowed"])
    total = len(bots)
    crawler_pct = allowed / total
    crawler_score = crawler_pct * 35
    blocked = [name for name, b in bots.items() if not b["allowed"]]
    if blocked:
        key = [b for b in ("GPTBot", "ClaudeBot", "Google-Extended", "PerplexityBot")
               if b in blocked]
        recs.append((1, f"AI 크롤러 {len(blocked)}종 차단됨"
                        f"{' (' + ', '.join(key) + ' 포함)' if key else ''}. "
                        f"robots.txt에서 허용해야 ChatGPT·Gemini·Claude가 사이트를 읽습니다."))

    # --- 2) 구조화 데이터 (가중치 25) ---
    sd = 0.0
    jsonld = set(parser.jsonld_types)
    if jsonld:
        sd += 12
    else:
        recs.append((1, "JSON-LD 구조화 데이터(schema.org)가 없습니다. "
                        "Organization/LocalBusiness/FAQPage 스키마를 추가하면 "
                        "AI가 업종·위치·서비스를 정확히 인식합니다."))
    if jsonld & VALUABLE_SCHEMA_TYPES:
        sd += 8
    elif jsonld:
        recs.append((2, f"구조화 데이터는 있으나 추천에 유효한 타입이 부족합니다. "
                        f"현재: {', '.join(sorted(jsonld))[:80]}"))
    og = [k for k in parser.metas if k.startswith("og:")]
    if og:
        sd += 5
    else:
        recs.append((3, "Open Graph 태그가 없습니다. og:title/og:description을 추가하세요."))
    sd_score = min(sd, 25)

    # --- 3) 크롤링 기본기 (가중치 20) ---
    cb = 0.0
    if parser.title.strip():
        cb += 4
    else:
        recs.append((2, "<title>이 비어 있습니다."))
    if parser.metas.get("description"):
        cb += 4
    else:
        recs.append((2, "meta description이 없습니다. 페이지 요약을 1~2문장으로 넣으세요."))
    if parser.lang:
        cb += 2
    if parser.canonical:
        cb += 2
    if parser.metas.get("viewport"):
        cb += 2
    if crawlers["sitemaps_declared"]:
        cb += 3
    else:
        recs.append((3, "robots.txt에 sitemap.xml 선언이 없습니다."))
    if parser.h1_count == 1:
        cb += 3
    elif parser.h1_count == 0:
        recs.append((3, "<h1> 대제목이 없습니다."))
    else:
        recs.append((3, f"<h1>이 {parser.h1_count}개입니다. 페이지당 1개를 권장합니다."))
    cb_score = min(cb, 20)

    # --- 4) 콘텐츠 인용 가능성 (가중치 20) ---
    ct = 0.0
    if content["well_sized_blocks"] >= 3:
        ct += 8
    elif content["well_sized_blocks"] >= 1:
        ct += 4
    else:
        recs.append((2, "AI가 인용하기 좋은 '자기완결형 문단'이 거의 없습니다. "
                        "80~600자 길이의 독립적인 문단으로 콘텐츠를 재구성하세요."))
    if content["numeric_blocks"] >= 2:
        ct += 6
    elif content["numeric_blocks"] == 1:
        ct += 3
    else:
        recs.append((2, "구체적인 숫자/통계가 부족합니다. AI는 '연 30% 절감' 같은 "
                        "수치가 담긴 문장을 더 잘 인용합니다."))
    if content["qa_like_headings"] >= 1:
        ct += 6
    else:
        recs.append((2, "질문형 소제목(FAQ/Q&A 구조)이 없습니다. "
                        "'~하려면 어떻게 하나요?' 형태의 제목 + 자기완결형 답변을 추가하세요."))
    ct_score = min(ct, 20)

    total_score = round(crawler_score + sd_score + cb_score + ct_score)
    recs.sort(key=lambda x: x[0])

    return {
        "aipick_score": total_score,
        "grade": _grade(total_score),
        "categories": {
            "ai_crawler_access": {"score": round(crawler_score, 1), "max": 35,
                                   "allowed": allowed, "total": total},
            "structured_data":   {"score": round(sd_score, 1), "max": 25,
                                   "types": sorted(jsonld)},
            "crawlability":      {"score": round(cb_score, 1), "max": 20},
            "citability":        {"score": round(ct_score, 1), "max": 20,
                                   **content},
        },
        "recommendations": [m for _, m in recs],
    }


def _grade(s):
    if s >= 80:
        return "우수 — AI 추천 준비 완료"
    if s >= 60:
        return "양호 — 일부 보완 필요"
    if s >= 40:
        return "주의 — 전문직 사이트 평균 수준(약 32점) 부근"
    return "위험 — AI가 사이트를 거의 인식하지 못함"


# ===========================================================================
# [선택] LLM 브랜드 인식 검사 — Claude(Anthropic) 어댑터
# ===========================================================================
def llm_brand_check(brand: str, queries, model="claude-opus-4-8"):
    """
    추천형 질문을 실제 LLM에 던져 내 브랜드가 등장하는지 확인.
    공식 anthropic SDK를 사용합니다 (pip install anthropic, ANTHROPIC_API_KEY 필요).
    """
    try:
        import anthropic  # 공식 SDK
    except ImportError:
        return {"error": "anthropic SDK가 설치되지 않았습니다. `pip install anthropic` 후 "
                         "ANTHROPIC_API_KEY를 설정하세요. (이 부분만 API 키·비용이 필요합니다)"}

    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 환경변수 자동 사용
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["recommended_names", "brand_mentioned", "rank"],
        "properties": {
            "recommended_names": {"type": "array", "items": {"type": "string"}},
            "brand_mentioned": {"type": "boolean"},
            "rank": {"type": "integer"},  # 등장 순위(1-base), 미등장 시 0
        },
    }
    out = []
    for q in queries:
        prompt = (f'사용자가 "{q}"라고 물었다고 가정하세요. '
                  f"당신이 추천할 만한 곳/전문가 이름을 순서대로 나열하고, "
                  f'그 목록에 "{brand}"가 포함되는지 판단하세요.')
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=1024,
                thinking={"type": "adaptive"},
                output_config={"format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": prompt}],
            )
            text = next(b.text for b in resp.content if b.type == "text")
            data = json.loads(text)
            out.append({"query": q, "provider": "Claude", **data})
        except Exception as e:  # noqa: BLE001
            out.append({"query": q, "provider": "Claude", "error": str(e)})
    return {"results": out}
    # ChatGPT/Gemini/Perplexity 어댑터도 동일한 인터페이스로 추가하면
    # "3개 AI 동시 진단"이 완성됩니다. Perplexity는 실시간 검색 기반이라
    # '현재 AI가 내 브랜드를 추천하는가'를 가장 정확히 보여줍니다.


# ===========================================================================
# 리포트 출력
# ===========================================================================
def bar(score, width=24):
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled)


def print_report(url, crawlers, parser, result, llm=None):
    p = print
    p("\n" + "=" * 60)
    p(f"  AIPICK 진단 리포트  —  {url}")
    p("=" * 60)
    s = result["aipick_score"]
    p(f"\n  AIPICK Score :  {s} / 100   [{bar(s)}]")
    p(f"  등급          :  {result['grade']}\n")

    c = result["categories"]
    p("  카테고리별 점수")
    p("  " + "-" * 56)
    rows = [
        ("AI 크롤러 접근성", c["ai_crawler_access"],
         f"{c['ai_crawler_access']['allowed']}/{c['ai_crawler_access']['total']} 봇 허용"),
        ("구조화 데이터", c["structured_data"],
         ", ".join(c["structured_data"]["types"][:3]) or "없음"),
        ("크롤링 기본기", c["crawlability"], ""),
        ("콘텐츠 인용 가능성", c["citability"],
         f"자기완결형 {c['citability']['well_sized_blocks']}개 · "
         f"숫자 {c['citability']['numeric_blocks']}개 · "
         f"Q&A {c['citability']['qa_like_headings']}개"),
    ]
    for name, cat, note in rows:
        p(f"  {name:<16} {cat['score']:>5} / {cat['max']:<3}  {bar(cat['score']/cat['max']*100, 14)}  {note}")

    p("\n  AI 크롤러 상태")
    p("  " + "-" * 56)
    for bot, info in crawlers["bots"].items():
        mark = "✓ 허용" if info["allowed"] else "✗ 차단"
        p(f"  {mark:<7} {bot:<20} {info['desc']}")
    if not crawlers["robots_exists"]:
        p("  (robots.txt 없음 — 모든 봇 허용으로 간주)")

    if result["recommendations"]:
        p("\n  우선순위 개선 권고")
        p("  " + "-" * 56)
        for i, rec in enumerate(result["recommendations"], 1):
            p(f"  {i}. {rec}")

    if llm is not None:
        p("\n  3개 AI 브랜드 인식 (선택 모듈)")
        p("  " + "-" * 56)
        if "error" in llm:
            p(f"  · {llm['error']}")
        else:
            for r in llm["results"]:
                if "error" in r:
                    p(f"  · [{r['provider']}] \"{r['query']}\" → 오류: {r['error']}")
                else:
                    hit = "✓ 등장" if r.get("brand_mentioned") else "✗ 미등장"
                    rank = f"(#{r['rank']})" if r.get("rank") else ""
                    p(f"  · [{r['provider']}] \"{r['query']}\" → {hit} {rank}")
                    if r.get("recommended_names"):
                        p(f"      추천 목록: {', '.join(r['recommended_names'][:5])}")
    p("\n" + "=" * 60)
    p("  ※ 결정론적 항목(1~5)은 API 키 없이 동작합니다.")
    p("     3개 AI 인식 검사(--llm)만 API 키·비용이 필요합니다.")
    p("=" * 60 + "\n")


# ===========================================================================
# main
# ===========================================================================
def run(url, brand=None, queries=None, use_llm=False):
    if not urlparse(url).scheme:
        url = "https://" + url

    crawlers = check_ai_crawlers(url)
    status, _, html, final_url = fetch(url)
    parser = PageParser()
    if html:
        try:
            parser.feed(html)
        except Exception:  # noqa: BLE001
            pass
    content = analyze_content(parser.blocks)
    result = compute_score(crawlers, parser, content, status)

    llm = None
    if brand and queries and use_llm:
        llm = llm_brand_check(brand, queries)

    return {
        "url": final_url, "page_status": status,
        "crawlers": crawlers, "content": content,
        "result": result, "llm": llm, "_parser": parser,
    }


def main():
    ap = argparse.ArgumentParser(description="AIPICK Diagnose — AI 검색 최적화 진단 엔진")
    ap.add_argument("url", help="진단할 사이트 URL (예: cotal.kr)")
    ap.add_argument("--json", action="store_true", help="JSON으로 출력")
    ap.add_argument("--brand", help="내 브랜드명 (LLM 인식 검사용)")
    ap.add_argument("--queries", nargs="*", default=[],
                    help="추천형 질문들 (예: '강남 세무사 추천')")
    ap.add_argument("--llm", action="store_true",
                    help="3개 AI 브랜드 인식 검사 실행 (anthropic SDK + API 키 필요)")
    args = ap.parse_args()

    data = run(args.url, brand=args.brand, queries=args.queries, use_llm=args.llm)

    if args.json:
        data.pop("_parser", None)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print_report(data["url"], data["crawlers"], data["_parser"],
                     data["result"], data["llm"])


if __name__ == "__main__":
    main()
