# UI 패널 빌드 — 백엔드 기능 대시보드 노출 (계약서)

세션 감사 결과: campaign·nightrunner·forecast·weather/sources 백엔드+REST는 완성됐으나 **대시보드 UI가
전무**(app.js 호출 0건). 챗으로만 조작 가능. 이 계약은 그 UI를 추가형으로 끼운다.

## 결정 (사용자 선택: "새 운영 탭 분리")
- **새 운영(ops/OPERATE) 탭** ← NightRunner 무인운영 제어 (향후 Alert이력·실행모니터 자리)
- **캠페인** → PLAN 최상위 (캠페인=여러밤 → 스케줄=오늘밤, 위계 자연스러움)
- **예보** → 기상(env) 탭, viz 그래프
- **기상 소스** → 기존 weather 패널에 접이식 섹션으로 병합 (새 패널 X, env 5 유지)
- 결과 탭: 관제·장비·기상(+예보)·계획(+캠페인)·운영(NightRunner)·분석·시스템 (7탭)

## 패널 끼우는 정형 (1개당)
1. index.html: `<div class="muuri-item wN" data-panel="X"><section class="card">…</section></div>`
2. app.js `PANEL_DEF["X"]` (klass/fills/min·max·def)
3. app.js `PROTO_GS_LAYOUT["X"]` ({x,y,w,h} — 해당 탭 블록)
4. app.js `PROTO_GS_H["X"]` (폴백 h)
5. JS 모듈 loadX()+renderX()+wireX() + init 등록 + WS/폴링 + `?v=` 범프

## 단계 (각 단계 라이브 검증 후 커밋)
- [ ] **U0** preview 서버 재기동 (현 서버 stale — 신규 백엔드 라우트 없음). 이후 라이브 검증 가능.
- [ ] **U1** 운영 탭 스캐폴드: 탭버튼 + `data-pane=ops`/`grid-ops` + TABS 배열에 "ops". 빈 그리드가
  다른 탭 안 깨고 렌더되는지(탭 전환·그리드 생성). ✅검증: 7탭 전환, grid-ops 생성, 콘솔에러 0.
- [ ] **U2** NightRunner 패널(운영 탭): /api/nightrunner status·start·stop. 상태뱃지·슬롯진행·시작/중지.
  폴링 3s. ✅검증: status 렌더, start→운영중, stop→대기.
- [ ] **U3** 캠페인 패널(PLAN 최상위, 기존 패널 y 하향): /api/campaigns list·create·plan-night.
  진행바·예상밤·plan-night 버튼. ✅검증: 목록·진행률 렌더, plan-night→draft.
- [ ] **U4** 예보 패널(기상): /api/forecast. 24h 구름/강수 막대(틸/앰버/레드 임계). ✅검증: 막대 렌더.
- [ ] **U5** 기상 소스(weather 패널 병합): /api/weather/sources. 소스별 최신·age 뱃지. ✅검증: 소스행.
- [ ] **U6** 풀리뷰(워크플로 팬아웃) + 회귀(node --check, 기존 탭/패널 무손상, ?v 일치).

## 가드레일
- **순수 프론트 + 기존 REST 호출만.** 백엔드/안전/스케줄러 0줄 수정.
- 기존 패널·레이아웃·localStorage 키 보존(레이아웃 버전 v5 유지 — 새 패널은 PROTO에만 추가).
- config.local.json 미접근. SIM 데이터로 검증.
- 각 단계 node --check 통과 + preview 라이브 + ?v 범프.
