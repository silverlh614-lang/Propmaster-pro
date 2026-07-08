# Propmaster Pro — Breakout 스타일 크립토 프롭 트레이딩 (시뮬레이션)

**평가 챌린지 → 펀디드 계좌 → 온디맨드 페이아웃** 수명주기를 equity 기반 룰 엔진이
강제하는 프롭 트레이딩 플랫폼 시뮬레이션입니다. Breakout Prop(2025-09 Kraken 인수)의
공개 규칙 구조를 본떴으며, 그 아래에서 독자 레버리지-마진 **페이퍼** 트레이딩 엔진이
집행합니다. 프롭 설계 SSOT: [`docs/prop_system.md`](docs/prop_system.md)

> ⚠️ 시뮬레이션 전용 — 실제 자금/서비스가 아니며 투자 조언이 아닙니다.

## 프롭 시스템 (`app/prop/`)

| 플랜 | 목표 | 최대 DD | DD 방식 | 일일손실 | 최대 크기 |
|---|---|---|---|---|---|
| 1-Step Classic | 10% | 6% | Static | 4% | $100K |
| 1-Step Pro | 12% | 5% | Static | 3% | $200K |
| 1-Step Turbo | 9% | 3% | Static | 3% | $200K |
| 2-Step Classic | 5% → 10% | 8% | Trailing | 5% | $100K |

- 한도는 **balance로 계산**, 브리치는 **equity(미실현 포함)로 판정**. 일일손실은 매일
  00:30 UTC 잔고에서 재앵커. 브리치 = 계좌 종료 (킬스위치 트립 + 전 포지션 청산, 영구).
- 시간 제한·최소 거래일·일관성 규칙 없음. 단계 통과 시 잔고가 계좌 크기로 리셋되고,
  펀디드 계좌는 수익 목표 없이 온디맨드 페이아웃(최소 $50, 기본 분할 80%)만 남습니다.
- 진입 차단은 리스크 단일 관문(`RiskManager.allow_entry`)에 통합, 룰 판정은
  매 마감봉마다 `ChallengeAccount.evaluate()` 단일 지점에서 수행됩니다.

## 구성 (집행 엔진 + 분석 사이드카)

| 파트 | 역할 | 실행 주기 |
|---|---|---|
| **Part 1 — 앙상블** (`app/ensemble.py`) | 6개 렌즈 삼각분포의 mixture-of-experts 몬테카를로 → 바닥 가격 분포 | 오프라인/주간 배치 (스냅샷 갱신 시 재계산) |
| **Part 2 — FSM** (`app/fsm.py`) | WATCH → CAP_WATCH → ACCUMULATE → CONFIRM → TREND 상태기계 → 일별 deploy fraction | 온라인/일별 (상태는 `data/fsm_state.json`에 영속) |
| **Part 3 — 풀사이클 체인** (`app/chain.py`) | 바닥 앙상블 → 회복 배수 → 2028 반감기 가격 → ROI regime 혼합(랠리 소멸 30%) → 2029 고점 가격·시점 | 오프라인 (파라미터 = 명시적 판단) |
| **트레이딩 엔진** (`app/trading/`) | 레버리지-마진 추세돌파 자동매매: kline 수집 → 상위추세+돌파 시그널 → ATR 스탑·2:1 R:R → 부분청산·트레일링 (Phase 1 = 페이퍼 전용. 레버리지 ≤ 5x, 고정 비율 리스크 — `TRADING_*`로 조정) | 온라인/상시 (봇 start 시, 저널은 `data/trades.csv`) |
| **대시보드** (`static/index.html`) | `/model` — 스냅샷 앵커·분포 차트·P(바닥<레벨)·FSM 조작 UI | — |
| **관제탑** (`static/terminal.html`) | `/` — 레버리지 봇 상태·포지션·캔들, 수동 개입(L/S/X), 트레이드 저널 | — |
| **지식 베이스** (`knowledge/`) | 방법론·데이터·원칙 원문 (`/api/knowledge`로 서빙) | living document |

## API

| 엔드포인트 | 설명 |
|---|---|
| `GET /api/prop/plans` | 프롭 플랜 카탈로그 + 수수료 테이블 |
| `POST /api/prop/challenge` | 챌린지 구매 `{plan, size}` (flat일 때만, 원장 리셋) |
| `GET /api/prop/account` | 활성 챌린지 계좌 상태 (플로어·여유·진행률) |
| `POST /api/prop/payout` | 온디맨드 페이아웃 요청 `{amount}` (펀디드 전용) |
| `GET /api/prop/payouts` | 페이아웃 이력 |
| `GET /` | 프롭 트레이딩 관제탑 (랜딩) |
| `GET /model` | BTC 사이클 바닥 모델 대시보드 |
| `GET /healthz` | 헬스체크 (Railway healthcheckPath) |
| `GET /api/snapshot` | 온체인 스냅샷 앵커 |
| `GET /api/price` | **최신 현물가 자동 갱신** — CoinGecko → Coinbase → Binance 폴백, TTL 캐시, 전부 실패 시 스냅샷 값 (`live: false`) |
| `GET /api/simulate` | 기본 300k 시뮬레이션 요약 + 히스토그램 (캐시) |
| `POST /api/simulate` | 커스텀 렌즈/가중치로 민감도 실험 |
| `GET /api/distribution.png` | 분포 차트 (matplotlib 렌더) |
| `GET /api/chain` | 풀사이클 체인 기본 300k 시뮬레이션 (바닥/반감기/고점 percentile, 확률표, 고점 시점) |
| `POST /api/chain` | 커스텀 파라미터 (회복 배수, 랠리 소멸 확률, ROI 삼각분포, 렌즈) 실험 |
| `GET /api/chain.png` | 반감기·고점 분포 2패널 차트 |
| `GET /api/fsm/state` | FSM 현재 상태 |
| `POST /api/fsm/update` | 일별 온체인 입력 주입 → phase + deploy fraction |
| `POST /api/fsm/reset` | FSM 초기화 (`?ladder_tranches=5`) |
| `GET /api/fsm/demo` | 합성 하락→회복 경로 데모 (상태 비파괴) |
| `GET /api/knowledge` | 지식 베이스 마크다운 원문 |
| `GET /terminal` | 트레이딩 관제탑 UI (`/`와 동일) |
| `GET /api/trading/status` | 봇 상태 (포지션 FSM·캔들·리스크·오늘 성과) |
| `POST /api/trading/start` | 봇 시작 `{mode: "paper", strategy: "trend_breakout"}` |
| `POST /api/trading/stop` | 봇 정지 (열린 페이퍼 포지션 청산) |
| `POST /api/trading/manual` | 수동 개입 `{action: long\|short\|close, symbol}` |
| `POST /api/trading/kill/reset` | 킬스위치 해제 |
| `GET /api/trading/candles` | 캔들 조회 (`?symbol=BTC&tf=entry\|htf&limit=120`) |
| `GET /api/trading/trades` | 트레이드 저널 + 승률/PnL 집계 (`?symbol=BTC`) |
| `GET /api/trading/trades.csv` | 저널 엑셀(CSV) 다운로드 (`?symbol=BTC`) |
| `GET /api/trading/config` | 트레이딩 설정 (환경변수 `TRADING_*`로 오버라이드) |
| `POST /api/trading/backtest` | 리플레이 백테스트 (전략·파라미터 스윕 → 승률/기대값/PF/Z/MDD 표) |
| `GET /docs` | OpenAPI 문서 |

> ⚠️ Part 5는 현재 **Phase 1 — 페이퍼 트레이딩 전용**입니다. 어떤 주문도 실제 거래소로
> 전송되지 않으며, live 모드는 Phase 3(백테스트 게이트 통과 후)에서 활성화됩니다.
> 설계·운영 문서: `docs/engine_phase2_runbook.md`

## Railway 배포

1. 이 리포를 Railway 프로젝트에 연결하면 `railway.json`이 Dockerfile 빌드를 지정합니다.
2. 헬스체크는 `/healthz`, 포트는 Railway가 주입하는 `PORT`를 사용합니다.
3. **[SNAPSHOT] 값 갱신은 코드 수정 없이 환경변수로**:

   | 변수 | 기본값 | 의미 |
   |---|---|---|
   | `REALIZED_PRICE` | 53600 | 실현가격 (결정적 선) |
   | `MA_200W` | 61800 | 200주 이동평균 |
   | `SPOT` | 61500 | 현물가 |
   | `ATH` | 126198 | 사상 최고가 |
   | `SNAPSHOT_DATE` | 2026-07-03 | 스냅샷 기준일 |
   | `DATA_DIR` | `./data` | FSM 상태/차트/트레이딩 데이터 저장 경로 (볼륨 마운트 시 지정). 봇 가동 중 봇 상태·kline 캐시·저널(`trades.csv`)이 여기 쌓임 — Phase 2 백테스트 재료 |
   | `PRICE_TTL_SECONDS` | 60 | 실시간 현물가 캐시 수명 |
   | `PRICE_FEED_DISABLED` | (없음) | `1`이면 실시간 피드 끄고 항상 스냅샷 값 사용 |

   현물가는 `/api/price`가 자동 갱신하므로 `SPOT`은 실시간 피드 불가 시의 폴백입니다.
   실현가격·200주선은 무료 공개 API가 없는 온체인 집계값이라 env 기반을 유지합니다
   (`/refresh-snapshot` 파이프라인으로 재검증).

4. FSM 상태를 재배포 간에 유지하려면 Railway Volume을 붙이고 `DATA_DIR`를 마운트 경로로 지정하세요.

### 로컬 실행

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
# http://localhost:8000
```

## 디렉토리

```
├── app/prop/           # 프롭 코어: plans / account(룰 엔진) / desk / store / api
├── app/trading/  # 집행 엔진: 수집·전략·포지션 FSM·리스크 관문·백테스트
├── app/                # 분석 사이드카 (ensemble / fsm / chain / snapshot / main)
├── static/terminal.html   # 관제탑 UI (프롭 패널은 다음 단계)
├── knowledge/          # 분석 지식 베이스 원문
├── docs/               # prop_system.md (프롭 SSOT) · engine_phase2_runbook.md
├── scripts/            # 정적 가드 + pre-commit 배선
├── tests/              # 오프라인 테스트 (test_prop / test_engine / ...)
├── Dockerfile          # Railway 빌드
└── railway.json        # Railway 배포 설정
```

## 검증

```bash
python scripts/validate_all.py   # 정적 가드 (500줄 한계 · @responsibility)
python -m tests.test_prop        # 프롭 데스크·룰 엔진
python -m tests.test_engine       # 트레이딩 엔진
```

## 라이선스 / 면책

프롭 트레이딩 시뮬레이션 프레임워크 예제일 뿐이며 투자 조언이 아닙니다.
실제 Breakout Prop 규칙·수수료는 변경될 수 있으니 공식 사이트를 확인하세요.
