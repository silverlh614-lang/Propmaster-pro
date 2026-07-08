# Propmaster Pro — 프롭 시스템 설계 SSOT

Breakout Prop(breakoutprop.com, 2025-09 Kraken 인수)의 공개 구조를 본뜬
**크립토 프롭 트레이딩 시뮬레이션**. 트레이더가 평가 챌린지를 통과하면
(시뮬레이션상의) 회사 자본 계좌로 전환되고, 수익 인출 시 분할(기본 80%)을 받는다.

> 시뮬레이션 전용. 실제 자금·실제 서비스가 아니며 투자 조언이 아니다.
> 실서비스 규칙은 변경될 수 있다 — 최신 값은 공식 사이트 확인.

## 1. 수명주기

```
/api/prop/challenge (구매, 수수료 기록, 원장=계좌 크기로 리셋)
        │
        ▼
 EVALUATION (phase 1..N) ──목표 달성(실현 balance, flat)──▶ 다음 phase / FUNDED
        │                                                      │
        │ equity가 일일손실/최대DD 플로어 터치                     │ 수익 발생 시
        ▼                                                      ▼
     FAILED (터미널 — 킬스위치 트립 + 전 포지션 청산,       /api/prop/payout
             재도전 = 새 챌린지 구매)                      (온디맨드, min $50,
                                                          분할 80% 적용)
```

- 시간 제한 없음 · 최소 거래일 없음 · 일관성 규칙 없음 · 수익 상한 없음.
- 단계 통과/펀디드 전환 시 잔고는 계좌 크기로 리셋된다 (새 단계 = 새 스테이크).
- 엔진 제약: **활성 계좌는 동시에 1개** (트레이딩 엔진 원장이 하나이므로).

## 2. 룰 판정 메커니즘 (`app/prop/account.py`)

공식 문서("Mastering Drawdown") 기준:

| 항목 | 기준 |
|---|---|
| 한도 **계산** | **BALANCE**(실현 손익) 기준 — 일일 앵커·트레일링 최고점 모두 balance 추적 |
| 브리치 **판정** | **EQUITY**(미실현 포함) 기준 — 포지션 보유 중 인트라바 브리치 가능 |
| 일일손실 | 매일 **00:30 UTC** 시점 balance에서 재앵커: floor = anchor × (1 − X%) |
| Static DD | floor = 시작 잔고 × (1 − X%). 수익이 나도 **절대 안 올라감** |
| Trailing DD | floor = 최고 balance − (시작 잔고의 X%) — 예산 고정, 플로어만 상승 |
| 수익 목표 | **실현 balance ≥ size×(1+목표%)** 이고 **flat일 때만** 통과 |
| 브리치 결과 | 계좌 FAILED(영구) + 킬스위치 + 전 포지션 강제 청산 |

판정 단일 지점: `ChallengeAccount.evaluate()` — 매 마감봉마다
`TradingManager.prop_tick()`이 (equity_mark, balance, flat)을 공급한다.

## 3. 플랜 카탈로그 (`app/prop/plans.py`, 2026 리서치 보정)

| 플랜 | 목표 | 최대 DD | DD 방식 | 일일손실 | 최대 크기 |
|---|---|---|---|---|---|
| 1-Step Classic | 10% | 6% | Static | 4% | $100K |
| 1-Step Pro | 12% | 5% | Static | 3% | $200K |
| 1-Step Turbo | 9% | 3% | Static | 3% | $200K |
| 2-Step Classic | 5% → 10% | 8% | **Trailing** | 5% | $100K |

- 계좌 크기: $5K / $10K / $25K / $50K / $100K / $200K (플랜별 상한 있음).
- 수수료: 공표 가격 테이블(예: Classic $10K = $110, Turbo $200K = $1,199),
  미확인 구간은 `fee_rate × size` 폴백. `PROP_FEE_MULT`로 전역 스케일.
- 수익 분할: 기본 80%. **90% 업그레이드** = 구매 시 애드온(+20% 수수료, 계좌 영구).
- 페이아웃: 펀디드 전용, 온디맨드, 최소 `PROP_MIN_PAYOUT`(기본 $50),
  인출 가능액 = balance − size. **첫 페이아웃에 평가 수수료 전액 환불** (계좌당 1회).
  USDC 지급은 시뮬레이션 표기만.
- 레버리지: **심볼 클래스 캡** — 메이저(BTC/ETH) 5x, 알트(SOL 등) 2x
  (`SymbolSpec.leverage_cap`, 전역 `leverage_max`와 둘 중 타이트한 쪽이 바인딩).

### 리서치에서 확인된 상충 (보류 항목)

1. 1-Step Classic 일일손실 3% vs **4%** — 공식 FAQ 예시(105,000−4%)를 채택.
2. Pro 목표 12% 고정 vs 크기별 12→24% 스케일 — 12% 고정 채택 (소수 소스만 스케일 주장).
3. 페이아웃 최소 "없음" vs "분할 후 $100" — env `PROP_MIN_PAYOUT`로 조정 가능하게 설계.
4. 실수수료 일부 구간($10K/$50K Pro·Turbo) 미공표 — fee_rate 폴백 사용.

## 4. 배선 (엔진과의 결합점)

| 결합점 | 위치 | 내용 |
|---|---|---|
| 조립 | `TradingManager.__init__` | PropDesk 생성 유일 지점 — 원장·리스크 관문 공유 |
| 진입 차단 | `RiskManager.allow_entry` | `prop.entries_allowed()` — FAILED 계좌는 전 진입 거부 |
| 마크 판정 | `SymbolBot._step` → `TradingManager.prop_tick` | 매 마감봉, 신규 진입 시도 **전에** 판정 |
| 브리치 처리 | `TradingManager._on_prop_breach` | 킬스위치 트립 + 전 심볼 페이퍼 청산 + 영속화 |
| 원장 리셋 | `PropDesk._reset_stake` | 구매/단계 통과/펀디드 전환 시 잔고 = 계좌 크기 |

## 5. API

| 엔드포인트 | 설명 |
|---|---|
| `GET /api/prop/plans` | 플랜 카탈로그 + 수수료 테이블 |
| `POST /api/prop/challenge` | 챌린지 구매 `{plan, size}` (flat일 때만) |
| `GET /api/prop/account` | 활성 계좌 상태 (플로어·여유·진행률, 마크 반영) |
| `POST /api/prop/payout` | 페이아웃 요청 `{amount}` (펀디드 전용) |
| `GET /api/prop/payouts` | 페이아웃 이력 |

`GET /api/trading/status`의 `prop` 필드에도 동일 상태가 실린다 (관제탑 폴링용).

## 6. 다음 단계

- ~~관제탑 프롭 패널~~ ✅ / ~~심볼별 레버리지 차등~~ ✅ / ~~90% 분할·수수료 환불~~ ✅
- 금지 행위 시뮬레이션(마틴게일 감지 등)과 스케일링 플랜($200K→$2M).
- 페이아웃 이력 전용 패널·다계좌(챌린지 병행) 지원.
