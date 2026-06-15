# AIPICK · 코탤(COTAL)

AI가 추천하는 전문가로 만들어드립니다 — ChatGPT·Gemini·Claude 시대의 AI 검색 최적화(GEO/AEO) 랜딩페이지 + 실시간 진단 도구.

```
Landings/
├─ index.html              # 랜딩페이지 (단일 파일, 인라인 CSS/JS)
├─ assets/                 # COTAL 로고 (blue/white)
├─ server.py               # 앱 서버: 정적 파일 + /api/diagnose API
├─ tools/aipick-diagnose/
│  └─ diagnose.py          # AI 검색 최적화 진단 엔진 (표준 라이브러리만)
├─ robots.txt              # AI 크롤러 개방
├─ Dockerfile             ─┐
├─ render.yaml            ─┤ 배포 설정 (컨테이너 PaaS)
├─ Procfile              ─┘
└─ requirements.txt        # 핵심은 의존성 0 (선택 모듈만 anthropic)
```

핵심 진단 엔진과 서버는 **Python 표준 라이브러리만** 사용합니다 — 설치할 패키지가 없습니다.

---

## 로컬 실행

```bash
python server.py          # http://localhost:4321
```

- 랜딩페이지: <http://localhost:4321/>
- 진단 API:   <http://localhost:4321/api/diagnose?url=python.org>
- 헬스체크:   <http://localhost:4321/healthz>

CLI 단독 진단:
```bash
python tools/aipick-diagnose/diagnose.py https://example.com         # 리포트
python tools/aipick-diagnose/diagnose.py https://example.com --json  # JSON
```

---

## 배포 (컨테이너 PaaS — 페이지 + API 한 곳에)

페이지와 진단 API가 같은 서버(같은 origin)에서 동작하므로 CORS 설정이 필요 없습니다.
아래 중 **한 곳**만 고르면 됩니다. 둘 다 Dockerfile을 자동 인식합니다.

### 0) GitHub에 올리기 (공통 준비)
PaaS는 GitHub 저장소를 연결해 배포합니다. 로컬 git 저장소는 이미 초기화되어 있습니다.

```bash
# 1) GitHub에서 빈 저장소를 하나 만든 뒤(예: aipick), 원격 연결:
git remote add origin https://github.com/<계정>/aipick.git
git branch -M main
git push -u origin main
```

### A) Render  ← 추천 (임시 URL `*.onrender.com` 즉시 발급)
1. <https://render.com> 가입 → **New ▸ Blueprint** → 위 GitHub 저장소 선택
   - 저장소의 `render.yaml`을 자동 인식해 서비스가 구성됩니다.
   - (Blueprint 대신) **New ▸ Web Service**로도 가능: Runtime을 **Docker**로 두고 나머지 기본값.
2. **Health Check Path** = `/healthz` (render.yaml에 이미 지정됨)
3. **Create** → 빌드 후 `https://aipick-xxxx.onrender.com` 형태의 URL이 발급됩니다.
4. 무료 플랜은 일정 시간 미사용 시 콜드 스타트(첫 요청이 느림)가 있습니다. 운영 시 Starter 이상 권장.

### B) Railway  (대안)
1. <https://railway.app> → **New Project ▸ Deploy from GitHub repo** → 저장소 선택
2. Railway가 Dockerfile을 자동 인식해 빌드합니다. (`$PORT` 자동 주입)
3. **Settings ▸ Networking ▸ Generate Domain** → `*.up.railway.app` URL 발급

> Fly.io / Google Cloud Run 등 다른 컨테이너 호스트도 같은 Dockerfile로 동일하게 동작합니다.

---

## 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `PORT` | 4321(로컬)/8000(Docker) | 바인딩 포트. **PaaS가 자동 주입**하므로 직접 설정 불필요. |
| `AIPICK_MAX_CONCURRENT` | `4` | 동시 진단 수 (외부 fetch 폭주 방지) |
| `AIPICK_CACHE_TTL` | `600` | 진단 결과 캐시 TTL(초). 같은 URL 재요청 시 즉시 응답. |
| `ANTHROPIC_API_KEY` | (없음) | **[나중에]** 2층 "3개 AI 브랜드 인식" 모듈용. 지금은 비워둬도 전체가 정상 동작합니다. |

> 2층 모듈을 켤 때: `Dockerfile`의 `pip install anthropic` 주석을 해제하고, PaaS 대시보드에 `ANTHROPIC_API_KEY`를 입력하세요. (OpenAI·Gemini·Perplexity 어댑터는 추후 추가)

---

## 도메인 연결 (나중에)

임시 URL로 먼저 운영하다가 도메인(예: `aipick.cotal.kr`)이 준비되면:

1. **PaaS 대시보드 ▸ Custom Domain**에 `aipick.cotal.kr` 추가
2. 안내되는 값으로 DNS의 **CNAME** 레코드 설정
   (예: `aipick` → `aipick-xxxx.onrender.com`)
3. HTTPS 인증서는 Render/Railway가 자동 발급합니다.
4. 도메인 확정 후 마무리:
   - `robots.txt`의 `Sitemap:` 줄 활성화(절대 URL)
   - `index.html` JSON-LD의 `logo`/`url`을 절대경로로 교체

---

## 동작 확인 체크리스트

배포 후 발급된 URL로:
- [ ] `/` 페이지가 정상 표시 (로고·히어로·섹션)
- [ ] `/healthz` 가 `{"status":"ok"}` 반환
- [ ] CTA 폼에 `python.org` 입력 → AIPICK Score 80점대 표시
- [ ] `nytimes.com` 입력 → 56점, AI 크롤러 대거 차단 표시
