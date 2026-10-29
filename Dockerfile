# syntax=docker/dockerfile:1
# check=skip=SecretsUsedInArgOrEnv
# (위 경고 억제: FCM_KEY_PAIR 는 이름만 KEY 인 웹 푸시 *공개* VAPID 키다. 비공개 키였다면 vite.config.ts 가 빌드를 거부한다)

# ---- 1) 프런트엔드 빌드 (web/dist) ----------------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# 웹 푸시 VAPID *공개* 키만 번들에 들어간다(vite.config.ts 가 형식을 검증). 비공개 키·다른 비밀은 빌드 인자로 받지 않는다.
ARG FCM_KEY_PAIR=""
RUN FCM_KEY_PAIR="$FCM_KEY_PAIR" npm run build

# ---- 2) 런타임 ----------------------------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Seoul HOST=0.0.0.0
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY stockwatch ./stockwatch
COPY config.yaml ./
COPY --from=web /src/web/dist ./web/dist
USER app
EXPOSE 5000
# 비밀(.env 의 POSTGRES_*, TOSS_*, FCM_SERVICE_ACCOUNT 등)은 이미지에 없다. 컨테이너 환경 변수로 넣고, 서비스 계정 키는 볼륨으로 마운트한다.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:5000/api/status', timeout=4)"
# 수집 스케줄러·주문 동기화가 프로세스 안 스레드로 돌아 워커를 늘리면 중복 실행된다 → 반드시 단일 프로세스
CMD ["python", "-m", "stockwatch.web"]
