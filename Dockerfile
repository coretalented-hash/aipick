# AIPICK — 랜딩페이지 + 진단 API 컨테이너
# Render / Railway / Fly.io / Cloud Run 등 어디서나 동일하게 동작합니다.
FROM python:3.12-slim

WORKDIR /app
COPY . /app

# 핵심(진단 엔진 + 서버)은 Python 표준 라이브러리만 사용 → pip install 불필요.
# (선택) 2층 "3개 AI 브랜드 인식"(--llm)을 켜려면 아래 주석을 해제하세요:
# RUN pip install --no-cache-dir anthropic

# PaaS가 $PORT 를 주입합니다. 로컬/기본은 8000.
ENV PORT=8000
EXPOSE 8000

# 헬스체크 (플랫폼이 자체 체크를 하면 무시됩니다)
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/healthz',timeout=4)" || exit 1

CMD ["python", "server.py"]
