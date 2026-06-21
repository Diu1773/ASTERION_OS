# ASTERION → Grafana 연동

ASTERION은 `/metrics`에서 현재 텔레메트리·상태를 **Prometheus 노출 형식**으로 내보낸다.
Grafana를 Prometheus 데이터소스로 붙이면 시계열 대시보드가 된다. (추가형 — 기존 인메모리
1Hz 텔레메트리와 DB 다운샘플은 그대로 두고 읽기만 한다.)

```
ASTERION /metrics  ──scrape──►  Prometheus  ──datasource──►  Grafana 대시보드
```

## 1. /metrics 확인

서버 기동 후 (sim 모드 기본):

```
curl http://127.0.0.1:8520/metrics
```

`asterion_up 1`, `asterion_safety_state{state="..."} 1`, `asterion_mount_alt ...`,
`asterion_sun_alt ...`, `asterion_capture_active ...`, `asterion_uptime_seconds ...` 등이 보이면 정상.

> 인증을 켰다면(REMOTE_ACCESS_PLAN Phase A) `/metrics`는 **scope=metrics 토큰**(Prometheus가
> Bearer로 전달) 또는 **로그인 사용자**만 접근 가능. 토큰 발급: `python -m asterion.access token`
> → 출력된 `hash`를 `config.local.json`의 `server.auth.tokens.prometheus`에 넣고, `token`은
> Prometheus 스크레이프 설정(아래)에 넣는다. 인증이 꺼져 있으면 누구나 스크레이프 가능.

## 2. Prometheus 스크레이프

[`prometheus.yml`](prometheus.yml)의 `asterion` job을 Prometheus 설정에 추가하고 재시작.
도커로 빠르게:

```
docker run -p 9090:9090 -v "$PWD/prometheus.yml:/etc/prometheus/prometheus.yml" prom/prometheus
```

`http://localhost:9090` → Status → Targets 에서 `asterion`이 UP이면 수집 중.

## 3. Grafana

```
docker run -p 3000:3000 grafana/grafana
```

1. `http://localhost:3000` 접속(admin/admin).
2. Connections → Data sources → **Prometheus** 추가, URL `http://host.docker.internal:9090`
   (같은 호스트면 `http://localhost:9090`).
3. Dashboards → New → **Import** → [`asterion-dashboard.json`](asterion-dashboard.json) 업로드
   → 데이터소스로 방금 만든 Prometheus 선택.

기본 패널: Up · Capture active · Uptime · Disk free · Safety state(타임라인) · Mount Alt/Az ·
Sun altitude · 전체 채널. 실제 채널 목록은 `/metrics` 응답에서 확인해 패널을 늘리면 된다
(`{__name__=~"asterion_.+"}`로 전부 한눈에).

## 메모

- **시뮬레이터 데이터**는 `data/sim/`에 격리되고 90일 보존 정리(`[sim_retention]`)로 자동
  정돈된다 — Grafana로 보는 sim 추세도 그 기간 안의 것이다.
- 더 긴 보존/장기 분석은 Prometheus 자체 보존기간(`--storage.tsdb.retention.time`)으로 잡는다.
