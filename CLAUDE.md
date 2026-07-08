# Propmaster Pro — AI 실행 규칙 (CLAUDE.md)

> **본 문서는 최상위 실행 규칙 SSOT다. 얇게 유지한다** — 상세는 §5 라우터의 문서로.
> 패치 노트·작업 이력을 이 파일에 누적하지 않는다.

---

## 1. Project Identity

**Breakout 스타일 크립토 프롭 트레이딩 플랫폼 (시뮬레이션)**: 평가 챌린지 → 펀디드 계좌 →
온디맨드 페이아웃 수명주기를 equity 기준 룰 엔진이 강제하고, 그 아래에서 독자
레버리지-마진 페이퍼 트레이딩 엔진이 집행한다 (**Phase 1 = 페이퍼 전용**). FastAPI + Railway.

- `app/prop/` — 프롭 코어: 플랜 카탈로그(plans) · 챌린지 계좌 룰 엔진(account) ·
  데스크 수명주기(desk) · 영속화(store) · REST(api)
- `app/trading/` — 집행 엔진: kline 수집 → 전략 시그널 → 포지션 FSM → 리스크 관문
- `scripts/` — 정적 가드 (complexity·responsibility), pre-commit 배선
- `tests/` — 오프라인 테스트 (네트워크 금지)
- `docs/` — 설계 문서 (프롭 SSOT·런북)

모든 수치는 예측이 아닌 **구조화된 의견**이며 투자 조언이 아니다. 프롭 규칙·수수료는
Breakout Prop 공개 구조를 본뜬 시뮬레이션 파라미터일 뿐 실제 서비스가 아니다.

---

## 2. 핵심 불변식 (절대 규칙)

1. **Paper-First** — `live_enabled` 기본 `False`. Phase 3 게이트(백테스트 통과 + 명시적
   `TRADING_LIVE_ENABLED=1` + 자격증명) 전에는 실주문 경로를 작성하지 않는다.
   라이브 모드 거부 로직(`app/trading/bot.py`)을 우회·완화하는 패치 금지.
2. **리스크 관문 우회 금지** — 모든 신규 진입은 `RiskManager.allow_entry()`
   (`app/trading/risk.py`) 단일 허가점을 통과한다. 킬스위치·동시 포지션/오픈리스크
   캡을 우회하는 주문 생성 금지. 킬스위치 해제는 명시적 조작(`/api/trading/kill/reset`)만.
3. **전략-집행 분리** — Strategy 는 `TradeSignal` 방출만 한다. 주문·리스크·정산은 포지션
   FSM (`app/trading/execution/position.py`) 소유다 (`strategies/base.py` 프로토콜).
   새 전략은 `app/trading/strategies/` 레지스트리 추가로만 — 엔진 본체 수정 금지.
4. **시세 단일 통로** — 캔들·현재가는 `app/trading/collectors/kline.py`
   (Binance→OKX 폴백)만 경유하고, 장기 히스토리는 `backtest/history.py`
   (vision 아카이브)만 경유한다. 가격을 다른 경로로 들여오지 않는다.
5. **레버리지·리스크 규율 + hand-tune 금지** — 레버리지 ≤ 5x(심볼 클래스 캡 우선),
   ATR 기반 스탑을 유지한다. 사이징은 **프롭 예산 기반**(잔여 일일/DD 예산의 고정 분율,
   `risk_per_trade_pct` 는 상한 캡; 프롭 계좌 없으면 고정 비율 폴백)이다. 시그널·리스크
   임계값은 손으로 튜닝하지 않는다 — Phase 2 백테스트 게이트가 결정한다.

6. **프롭 룰 엔진 단일 판정** — 챌린지 계좌의 브리치(일일손실·최대DD)는
   `app/prop/account.py` `evaluate()` 한 곳에서만 판정한다. 브리치는 **equity(미실현 포함)**
   기준·**터미널**(FAILED 영구, 리셋 금지 — 새 시도 = 새 구매)이며, 단계 통과는
   **실현 balance + flat** 기준이다. 브리치 시 킬스위치 트립 + 전 포지션 청산을 우회하는
   패치 금지. PropDesk 는 `TradingManager`(단일 조립점)에서만 생성한다 — 원장·리스크 관문과
   같은 객체를 공유해야 하기 때문이다.

**provider 장애 ≠ 시장 신호** — 시세 API 실패는 폴백·캐시로 흡수하며, 어떤 경우에도
방향성 판단(Long/Short)의 근거가 되지 않는다.

---

## 3. 작성 규칙 (정적 가드로 강제)

| 규칙 | 내용 | 가드 |
|------|------|------|
| **@responsibility 태그** | 모든 `.py` 상단 20줄 내, 25단어 이내 책임 한 줄 (tests 면제) | `scripts/check_responsibility.py` |
| **500줄 한계** | 파일당 500줄 초과 금지. 기존 초과분은 BASELINE 래칫(cap 초과 즉시 실패, 목록은 감소만) | `scripts/check_complexity.py` |
| **가드 자체 테스트** | 가드 수정 시 `tests/test_guards.py` 경계값 테스트 동반 갱신 | `python -m tests.test_guards` |
| **pre-commit 필수** | `--no-verify` 우회 금지. clone 직후 `python scripts/install_git_hooks.py` 1회 실행 | `.git/hooks/pre-commit` |

- 새 가드 추가: `scripts/check_<이름>.py` 작성 → `validate_all.py` GUARDS 등록 → 경계값 테스트.
- 레거시에 새 규칙 도입 시: 기존 위반은 사유와 함께 BASELINE 동결, 신규만 차단 (래칫).

---

## 4. 검증 명령

```bash
python scripts/validate_all.py       # 정적 가드 전체 (pre-commit 이 실행하는 것)
python -m tests.test_prop            # 프롭 데스크·룰 엔진 오프라인 테스트
python -m tests.test_engine           # 트레이딩 엔진 오프라인 테스트
python -m tests.test_guards          # 가드 경계값 테스트
python scripts/install_git_hooks.py  # pre-commit 훅 설치 (clone 후 1회)
```

커밋 전 최소 요건: 가드 전체 통과 + 수정한 모듈의 관련 테스트 통과.

---

## 5. Reference Docs Router

**작업 도메인에 해당하는 문서 1개만 읽어라.**

| 트리거 키워드 | 참조 문서 |
|---------------|-----------|
| 도메인 개요 · API 목록 · 실행 주기 · 배포 | `README.md` |
| 프롭 규칙 상세 · 플랜 파라미터 · 수명주기 설계 | `docs/prop_system.md` |
| 트레이딩 봇 설계 · Phase 2 백테스트 · 배포 런북 | `docs/engine_phase2_runbook.md` |
| 에이전트 팀 · 스킬 오케스트레이션 | `.claude/agents/` · `.claude/skills/` |

---

## 6. Patch Scope Rule

- **diff-only** — 수정된 부분만 출력. 변경 없는 코드 재출력 금지 (신규 파일만 전체 허용).
- **최소 변경** — 요청 도메인 밖 파일을 건드리지 않는다. 무관 리팩토링 금지.
- **정직한 보고** — 변경 파일 목록 / 동작 영향 / 실행한 검증과 결과 / 남은 리스크.
  테스트 실패·스킵을 숨기지 않는다.
- 임계값·파라미터 변경은 근거(백테스트·데이터)를 커밋 메시지에 남긴다.

> **ONE-LINE PRINCIPLE:** 페이퍼가 증명하기 전에는 라이브는 없다 — 가드 통과, diff만, 최소로.
