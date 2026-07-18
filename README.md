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

## 구성

| 파트 | 역할 | 실행 주기 |
|---|---|---|
| **트레이딩 엔진** (`app/trading/`) | 프롭 최적화 자동매매: kline 수집 → Donchian 돌파+HTF 추세 시그널(`prop_breakout` 기본) → ATR 스탑·2:1 R:R → 부분청산·트레일링. 사이징은 잔여 프롭 예산 기반(일일예산 25%·DD예산 10%), 당일 3패 시 정지, 애드업 OFF (Phase 1 = 페이퍼 전용, `TRADING_*`로 조정) | 온라인/상시 (봇 start 시, 저널은 `data/trades.csv`) |
| **프롭 데스크** (`app/prop/`) | 챌린지 수명주기·룰 엔진·행위 감시·스케일링·페이아웃 + 통과 확률 몬테카를로 | 매 마감봉 판정 |
| **관제탑** (`static/terminal.html`) | `/` — 프롭 패널·봇 상태·포지션·캔들·백테스트·트레이드 저널 | — |
| **텔레그램 알림** (`app/trading/notify.py`·`watch.py`) | 단방향 매매 알람: 진입(매수/매도)·목표가도달·청산 푸시(저널 sink) + 실시간 목표가·손절가 **근접** 알림(매 폴링 현재가 감시). 계정 연동·수신 명령 없음, 기본 OFF (`TELEGRAM_*` 설정 시 ON) | 이벤트·근접 시 |

## API

| 엔드포인트 | 설명 |
|---|---|
| `GET /api/prop/plans` | 프롭 플랜 카탈로그 + 수수료 테이블 |
| `POST /api/prop/challenge` | 챌린지 구매 `{plan, size}` (flat일 때만, 원장 리셋) |
| `GET /api/prop/account` | 활성 챌린지 계좌 상태 (플로어·여유·진행률) |
| `POST /api/prop/payout` | 온디맨드 페이아웃 요청 `{amount}` (펀디드 전용) |
| `GET /api/prop/payouts` | 페이아웃 이력 |
| `POST /api/prop/simulate` | 챌린지 통과 확률 몬테카를로 (플랜 스윕 / budget vs fixed 사이징) |
| `GET /` | 프롭 트레이딩 관제탑 (랜딩) |
| `GET /healthz` | 헬스체크 (Railway healthcheckPath) |
| `GET /terminal` | 트레이딩 관제탑 UI (`/`와 동일) |
| `GET /api/trading/status` | 봇 상태 (포지션 FSM·캔들·리스크·오늘 성과) |
| `POST /api/trading/start` | 봇 시작 `{mode: "paper", strategy: "prop_breakout"}` |
| `POST /api/trading/stop` | 봇 정지 (열린 페이퍼 포지션 청산) |
| `POST /api/trading/manual` | 수동 개입 `{action: long\|short\|close, symbol}` |
| `POST /api/trading/kill/reset` | 킬스위치 해제 |
| `GET /api/trading/notify` | 텔레그램 알림 설정 상태 (설정 여부·대상 이벤트, 값 비노출) |
| `POST /api/trading/notify/test` | 텔레그램 테스트 메시지 1건 발송 (배선 확인) |
| `GET /api/trading/candles` | 캔들 조회 (`?symbol=BTC&tf=entry\|htf&limit=120`) |
| `GET /api/trading/trades` | 트레이드 저널 + 승률/PnL 집계 (`?symbol=BTC`) |
| `GET /api/trading/trades.csv` | 저널 엑셀(CSV) 다운로드 (`?symbol=BTC`) |
| `GET /api/trading/signals` | 시그널 기록 + 전진검증 — 전략이 낸 모든 신호(체결 무관), 목표/손절 판정 결과와 라이브 win-rate·expectancy_r (`?symbol=&limit=`) |
| `GET /api/trading/signals.csv` | 시그널 기록 CSV 다운로드 (`?symbol=BTC`) |
| `GET /api/trading/config` | 트레이딩 설정 (환경변수 `TRADING_*`로 오버라이드) |
| `GET /api/trading/discovery` | 자동 종목 발굴 랭킹·상태 (`docs/auto_discovery.md`, 기본 OFF) |
| `POST /api/trading/discovery/scan` | 유니버스 수동 스캔 (로테이션은 `TRADING_AUTO_DISCOVERY=1`일 때만) |
| `POST /api/trading/backtest` | 리플레이 백테스트 (전략·파라미터 스윕 → 승률/기대값/PF/Z/MDD 표) |
| `GET /docs` | OpenAPI 문서 |

> ⚠️ Part 5는 현재 **Phase 1 — 페이퍼 트레이딩 전용**입니다. 어떤 주문도 실제 거래소로
> 전송되지 않으며, live 모드는 Phase 3(백테스트 게이트 통과 후)에서 활성화됩니다.
> 설계·운영 문서: `docs/engine_phase2_runbook.md`

## Railway 배포

1. 이 리포를 Railway 프로젝트에 연결하면 `railway.json`이 Dockerfile 빌드를 지정합니다.
2. 헬스체크는 `/healthz`, 포트는 Railway가 주입하는 `PORT`를 사용합니다.
3. 주요 환경변수:

   | 변수 | 기본값 | 의미 |
   |---|---|---|
   | `DATA_DIR` | `./data` | 프롭 계좌·봇 상태·저널·히스토리 캐시 저장 경로 (볼륨 마운트 시 지정) |
   | `TRADING_*` | — | 엔진 설정 오버라이드 (`docs/engine_phase2_runbook.md` 표 참조) |
   | `PROP_*` | — | 프롭 설정: `PROP_MIN_PAYOUT`·`PROP_FEE_MULT`·`PROP_SCALE_MAX`·`PROP_CONDUCT_ENFORCE` |
   | `TELEGRAM_BOT_TOKEN` | — | 텔레그램 봇 토큰 (BotFather 발급). 미설정 시 알림 OFF |
   | `TELEGRAM_CHAT_ID` | — | 알림 수신 챗 ID. 토큰과 함께 있어야 발송 활성화 |
   | `TELEGRAM_ALERT_EVENTS` | `OPEN,CLOSE,PARTIAL,SIGNAL` | 알림 대상. `SIGNAL`=전략 조건 충족 즉시 발송(체결과 무관 — 리스크 관문·예산 캡에 막혀도 알림). `OPEN`/`CLOSE`=실제 페이퍼 체결. `ADD`(애드업) 추가 가능 |
   | `TELEGRAM_NEAR_FRAC` | `0.15` | 실시간 근접 알림 밴드 — 현재가가 목표가·손절가까지 남은 거리 ≤ (진입→기준 거리 × 이 값)이면 1회 푸시. `0`=끄기 |

4. 상태를 재배포 간에 유지하려면 Railway Volume을 붙이고 `DATA_DIR`를 마운트 경로로 지정하세요 (필수).

### 텔레그램 알림 설정 (단방향 매매 알람)

진입(매수/매도)·청산 시그널을 텔레그램으로 받는 단순 알람입니다 (계정 연동·수신 명령 없음).
**계좌 브리치(챌린지 종료)** 는 `TELEGRAM_ALERT_EVENTS` 설정과 무관하게 항상 별도 고우선
알림으로 푸시됩니다 — 플랫 상태에서 브리치가 나도(청산 로그 없음) 놓치지 않도록.
**시그널 알림(`SIGNAL`)** 은 전략 조건이 충족되는 순간 바로 발송되며, 리스크 관문
(예산·동시포지션 캡)에 막혀 실제 진입을 못 해도 알림은 옵니다 — "🔔 시그널"로 표시되고
**기준가·목표가(rr_target)·손절가** 와, 진입이 막혔으면 **차단 사유(⛔)** 까지 함께 옵니다.

1. 텔레그램에서 **@BotFather** → `/newbot` → 봇 토큰 발급.
2. 방금 만든 봇에게 **아무 메시지나 1건** 전송 (그룹이면 봇을 추가하고 그룹에 글 작성).
3. `chat_id` 찾기 — 셋업 도구 실행 (프로젝트 의존성 없이 stdlib만 사용):
   ```bash
   TELEGRAM_BOT_TOKEN=<발급토큰> python scripts/telegram_chat_id.py
   # → 봇에게 말 건 chat_id 목록 출력
   ```
4. Railway 변수에 `TELEGRAM_BOT_TOKEN`·`TELEGRAM_CHAT_ID` 설정 (선택: `TELEGRAM_ALERT_EVENTS`).
5. 배선 확인 — 실제 메시지 1건 발송:
   ```bash
   TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python scripts/telegram_chat_id.py --test
   ```
   배포 후에는 `/api/trading/notify/test` 를 폰 브라우저 주소창에 열면(GET/POST 모두 허용)
   테스트 메시지가 발송되고, `GET /api/trading/notify` 로 설정 상태를 조회합니다.
   chat_id 를 폰만으로 찾으려면 봇에게 메시지를 보낸 뒤
   `https://api.telegram.org/bot<토큰>/getUpdates` 를 브라우저에 열어 `chat.id` 를 읽으면 됩니다.

> 미설정 시 알림은 조용히 OFF이며, 발송 실패는 트레이딩 루프에 영향을 주지 않습니다.

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
├── app/main.py         # FastAPI 조립 (프롭 + 트레이딩 라우터)
├── static/terminal.html   # 관제탑 UI (프롭 패널은 다음 단계)
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
