# Phase 2 백테스트 게이트 — 검증 결과 로그

> 목적: 실주문(Phase 3) 이전에 전략이 +EV인지 확인하는 게이트의 **근거 기록**
> (불변식 §6·§8 — 임계값·채택 판단은 근거를 남긴다). 하드튜닝이 아니라,
> 기존 디폴트가 out-of-sample 로 재현되는지 확인한 결과다.

- 데이터: Binance USDⓈ-M vision 월별 아카이브, **12개월** (`snapshots ≈ 8,703` 진입봉)
- 실행: `/api/trading/backtest/quick` · `/backtest/sweep/preset` (Railway, months=12)
- 통과 기준: `expectancy_r > 0` AND `PF ≥ 1.2` AND `trades ≥ 20` AND MDD 감내 AND **재현성**
- MDD 는 백테스트 standalone 고정비율($200) 기준. 실계좌는 예산 사이징이
  손실 시 베팅을 축소 → 몬테카를로상 **브리치 0%**(원금 보존, 실패는 timeout).

## 심볼 × 전략 결과 (디폴트 donchian 55 / atr_stop_mult 2.5)

| 심볼 · 전략 | 거래 | 승률 | expR | PF | MDD($) | z | 판정 |
|---|---|---|---|---|---|---|---|
| **ETH · prop_breakout** | 35 | 37% | 0.80 | 2.10 | -14.6 | 1.32 | ✅ 강한 통과 |
| **SOL · vbo** | 76 | 41% | 0.185 | 1.28 | -15.4 | 0.97 | ✅ 통과 (견고) |
| **XRP · vbo** | 73 | 42% | 0.203 | 1.33 | -20.0 | 1.11 | ✅ 통과 (민감) |
| XRP · prop_breakout | 121 | 38% | 0.132 | 1.17 | -39.1 | 0.78 | ❌ PF<1.2 |
| SOL · prop_breakout | 131 | 31% | -0.089 | 0.89 | -34.2 | -0.61 | ❌ 손실 |
| BTC · vbo | 15 | 20% | 0.394 | 1.58 | -14.8 | 0.41 | ❌ 표본<20 |
| BTC · prop_breakout | 17 | 12% | 1.09* | 2.29 | -24.7 | 0.59 | ❌ 표본<20, 아웃라이어 |

\* BTC prop_breakout 의 높은 expR 은 승률 11.8%에 단일 대박(≈17.5R) 하나가 캐리 —
신뢰 불가.

## 재현성 검증

- **atr_stop_mult 2.5 ≫ 1.5** — 코드 기존 2026-07 스윕 + BTC 16조합 + ETH 16조합
  **3중 확인**. ETH 스윕에서 atr 1.5 계열은 PF 1.0~1.24 로 붕괴, atr 2.5 계열
  4개 설정이 모두 하드기준 통과. → 디폴트(55/2.5)는 하드튜닝 없이 확증됨.
- **알트 = vbo (A/B 확증)** — SOL 은 Donchian 이 **손실**(-0.089), vbo 는 수익(+0.185).
  XRP 는 vbo 가 전지표 우위(PF 1.17→1.33, MDD -19.5→-10%). 메이저(ETH)는 Donchian
  이 최강(PF 2.10). "메이저 Donchian · 알트 vbo" 설계가 out-of-sample 로 재현.
- **vbo_k 견고성 (0.5→0.7)** — SOL: expR 0.185→0.29·PF 1.28→1.39 (견고/개선).
  XRP: expR 0.203→0.123·PF 1.33→1.17 (민감, k=0.7서 임계 이탈). → SOL 단단, XRP 얇음.

## 채택 (검증된 포트폴리오)

| 심볼 | 채택 전략 | 신뢰 | evaluation P(pass)·브리치 (1step $10K, 예산 사이징) |
|---|---|---|---|
| ETH | prop_breakout | 높음 (풀 스윕) | 100% · 0% |
| SOL | vbo | 높음 (vbo_k 견고) | 96% · 0% |
| XRP | vbo | 보통 (파라미터 민감) | 98% · 0% |

- **BTC 제외** — 두 전략 모두 표본 부족(15~17건)·z<0.6.

## 유니버스 스캔 (전 19종목 × 두 전략, 6개월) — `/api/trading/backtest/scan`

두 전략을 심볼마다 돌려 게이트로 랭킹. **핵심: 엣지는 소수 집중 — 7/19만 통과.**

| 통과 | 최선 전략 | expR | PF | 창 | 미달(참고) |
|---|---|---|---|---|---|
| ETH | prop_breakout | 0.80 | 2.10 | 12mo | UNI 0.088/1.15, INJ 0.067/1.09, |
| ARB | prop_breakout | 0.36 | 1.65 | 6mo | BNB 0.05/1.06, DOT 0.041/1.05, |
| SUI | prop_breakout | 0.35 | 1.60 | 6mo | ATOM 0.024/1.02, APT 0.022/1.02, |
| OP | vbo | 0.333 | 1.58 | 6mo | LINK 0.018/1.01 (전부 본전) |
| XRP | vbo | 0.203 | 1.33 | 12mo | ADA -0.111, AVAX -0.111, |
| SOL | vbo | 0.185 | 1.28 | 12mo | DOGE -0.183, LTC -0.211 (손실) |
| NEAR | prop_breakout | 0.163 | 1.26 | 6mo | BTC: 표본<20 |

- 전략은 **심볼별로 다름**(단순 메이저/알트 아님): ETH·ARB·SUI·NEAR→Donchian, SOL·XRP·OP→vbo.
- 나머지 12종목은 본전(PF~1.0) 또는 손실 → **로스터에 넣으면 수수료만 희석.**

## 최종 로스터 확정

| 티어 | 심볼 | 근거 | 코드 반영 |
|---|---|---|---|
| **코어 (12mo 확정)** | ETH·SOL·XRP | 12개월 out-of-sample 통과 | `DEFAULT_SYMBOLS` + `DEFAULT_SYMBOL_STRATEGY` 기본값 |
| **보강 (6mo 강세, 12mo 확인 대기)** | ARB·SUI·OP·NEAR | 6개월 PF 1.26~1.65 | env(`TRADING_SYMBOLS`)로만 추가 |

- **코드 기본값 변경** (근거: 본 문서): `DEFAULT_SYMBOLS="ETH,SOL,XRP"`,
  `DEFAULT_SYMBOL_STRATEGY={ETH:prop_breakout, SOL:vbo, XRP:vbo}`. env 미설정 시에도
  SOL/XRP 가 손실 전략(Donchian)으로 돌지 않도록 baking.
- **권장 env** (보강 포함, 운영자 선택):
  ```
  TRADING_SYMBOLS = ETH,SOL,XRP,ARB,SUI,OP,NEAR
  TRADING_SYMBOL_STRATEGY = ETH:prop_breakout,ARB:prop_breakout,SUI:prop_breakout,NEAR:prop_breakout,SOL:vbo,XRP:vbo,OP:vbo
  TRADING_MAX_TOTAL_OPEN_RISK_PCT = 3
  ```
- **BTC·나머지 12종목 제외.**

## 12개월 재확인 (2026-07-17) — 보강 4종목 티어 판정

6mo 강세였던 4종목을 12mo 로 재검증 (레짐 2개 포함). 기준: PF ≥ 1.2.

| 심볼 · 전략 | 12mo 거래 | 승률 | expR | PF | z | MDD($) | 판정 |
|---|---|---|---|---|---|---|---|
| **SUI · prop_breakout** | 109 | 45% | 0.31 | **1.52** | **1.99** | -23.0 | ✅ **승격** — 전 포트폴리오 최고 z |
| **ARB · prop_breakout** | 109 | 39% | 0.133 | **1.20** | 0.88 | -21.0 | ✅ 턱걸이 유지 (경계선) |
| OP · vbo | 67 | 39% | 0.128 | 1.19 | 0.63 | -32.2 | ❌ 강등 — 기준 0.01 미달 + **15연패** |
| NEAR · prop_breakout | 123 | 37% | 0.116 | 1.16 | 0.75 | -25.8 | ❌ 강등 — 6mo(1.26)→12mo(1.16) 붕괴 |

- 해석: OP·NEAR 의 6mo 성적은 최근 레짐 편향이었다 — 12mo 창에서 임계 밑으로.
  OP 의 최대 15연패는 일일 3패 정지 규칙과 결합하면 evaluation 진행을 심하게 저해.
- OP·NEAR 는 유니버스에 남김(열린 포지션 관리·수동 토글용) — 기본 거래 제외.
- evaluation P(pass) (12mo 프로필): SUI 99.9%·ARB 87.3% (브리치 0%).

## 후보 풀 스캔 (2026-07-17) — 신규 18종 × 두 전략, 12mo

CANDIDATE_SPECS(스캔 전용 풀) 18종을 12mo 게이트로 스캔. **7종 통과 → 승격.**

| 승격 | 전략 | 거래 | 승률 | expR | PF | MDD($) | P(pass) |
|---|---|---|---|---|---|---|---|
| **FET** | prop_breakout | 101 | 41% | 0.228 | 1.36 | -21.3 | 97% (vbo 도 1.25 통과 — 이중 확인) |
| **TAO** | prop_breakout | 104 | 39% | 0.198 | 1.33 | -14.9 | 95% |
| **WLD** | vbo | 74 | 43% | 0.210 | 1.33 | -29.8 | 98% |
| **ENA** | prop_breakout | 121 | 39% | 0.150 | 1.22 | -21.2 | 87% (vbo 1.21 준통과) |
| **STX** | prop_breakout | 115 | 39% | 0.146 | 1.22 | -19.4 | 86% |
| **LDO** | vbo | 78 | 38% | 0.137 | 1.20 | -14.6 | 83% (턱걸이) |
| **ICP** | vbo | 67 | 40% | 0.187 | 1.27 | -26.6 | — |

- 탈락 11종: TRX·BCH·ETC·FIL·AAVE·CRV·POL·HBAR (구형 — 전멸), TIA·SEI·JUP (신형이나
  미달). **패턴 재확인: 엣지는 신형 내러티브(AI·L2·신 L1)에 집중, 성숙 알트는 무엣지.**
- FET 는 두 전략 모두 통과한 유일 종목 — 종목 자체 추세성의 강한 신호.

## 후보 풀 스캔 라운드2 (2026-07-17) — Breakout 정합 21종 × 두 전략, 12mo

Breakout Prop 실거래 목록 정합으로 CANDIDATE_SPECS 에 추가한 21종을 12mo 게이트로
스캔(`scripts/gate_scan.py` / `GET /api/trading/backtest/scan`). **7종 통과 → 승격.**

| 승격 | 전략 | 거래 | 승률 | expR | PF | 수익% | MDD($) |
|---|---|---|---|---|---|---|---|
| **AVAX** ⚠️ | prop_breakout | 27 | 37% | 0.793 | 2.26 | +17.6 | -8.6 |
| **DOT** | vbo | 67 | 43% | 0.261 | 1.39 | +18.0 | -26.8 |
| **ZEC** | prop_breakout | 118 | 39% | 0.241 | 1.38 | +30.7 | -16.9 |
| **RENDER** | prop_breakout | 112 | 43% | 0.211 | 1.34 | +25.0 | -11.6 |
| **TRUMP** | prop_breakout | 96 | 38% | 0.192 | 1.27 | +18.5 | -20.2 |
| **JTO** | prop_breakout | 114 | 39% | 0.134 | 1.21 | +14.9 | -29.1 |
| **ALGO** | prop_breakout | 113 | 41% | 0.133 | 1.21 | +14.8 | -20.4 |

- ⚠️ **AVAX**: PF 2.26·expR 0.79로 수치는 최상이나 **27거래 소표본**(게이트 최소 20 턱걸이).
  BTC(표본<20 제외)와 같은 맥락의 잠정 승격 — 다음 라운드 재현성·z-score 확인 대상.
- **관찰(watch) — GRASS**: prop_breakout PF 1.17(+12.7%)·vbo PF 1.18(+7.4%), 둘 다 +기대값
  이나 PF 1.2 문턱 미달. 후보풀 잔류 — 엑싯 A/B(2.5R/0.5/2.5×ATR)로 재도전 가치.
- **탈락 13종**: ADA·LINK·UNI(PF~1.0 본전) · LTC·APT·DOGE·BNB(음) · KAITO·ONDO·ATOM·INJ
  (본전~음) · VIRTUAL·WIF(+기대값이나 PF 1.1x).
- **전략 편향**: 통과 7종 중 **6종이 prop_breakout**(DOT만 vbo) — 이 후보군에선 Donchian
  돌파가 변동성 돌파(vbo)보다 우세. 승격 매핑도 그대로 반영(`DEFAULT_SYMBOL_STRATEGY`).
- **엣지는 전략·창(window) 의존**: 예전 "무엣지"로 제거했던 DOT·AVAX 가 12mo 재평가에서
  통과 — DOT 는 vbo 로, AVAX 는 prop_breakout 로. 반면 ADA·LINK·LTC·BNB 는 이번에도 재탈락.

## 미수록 11종 분석 (2026-07-17, 웹조사)

앱 지원 목록에는 있으나 후보풀 미편입이던 11종(HYPE·PUMP·PENGU·MOODENG·POPCAT·
PNUT·S·AIXBT·FARTCOIN·XPL·ASTER)을 상장·네이밍·이력 기준으로 조사(Binance/OKX
공지·집계 사이트).

- **1000x 네이밍 위험은 전무** — 11종 모두 Binance USDⓈ-M **직명** 퍼프. 단
  **S(Sonic)=`SUSDT`** (≠ `SONICUSDT`=Sonic SVM, 별개 프로젝트) — 매핑 주의.
- **편입 8종**(직명 + OKX 스왑 + 12mo+): HYPE·PENGU·MOODENG·POPCAT·PNUT·S·
  AIXBT·FARTCOIN → CANDIDATE_SPECS 추가, 라운드3 게이트 스캔 대상.
- **보류 3종**(12mo 미만): PUMP(TGE 2025-07, ~경계) · XPL(2025-09, ~11mo) ·
  ASTER(2025-09, ~10mo). 이력이 차는 2026-09~10 재검토.

## 후보 게이트 라운드3 (2026-07-17) — 미수록 편입 8종 × 두 전략, 12mo

미수록 11종 분석에서 편입한 8종을 게이트 스캔. **4종 통과 → 승격.**

| 승격 | 전략 | 거래 | 승률 | expR | PF | 수익% | MDD($) |
|---|---|---|---|---|---|---|---|
| **HYPE** | vbo | 42 | 45% | 0.416 | 1.74 | +18.3 | -15.3 |
| **S**(Sonic) | prop_breakout | 118 | 40% | 0.192 | 1.32 | +23.6 | -31.9 |
| **POPCAT** | vbo | 62 | 44% | 0.215 | 1.36 | +13.5 | -9.9 |
| **PNUT** | vbo | 73 | 42% | 0.196 | 1.29 | +14.5 | -35.2 |

- **S(Sonic, =SUSDT)**: **양 전략 통과**(vbo expR 0.199 / prop 0.192) — 종목 엣지 강함.
  견고성(118거래·PF 1.32·+23.6%)으로 **prop_breakout** 매핑. (vbo 와 거의 동률.)
- **탈락 4종**: PENGU·MOODENG(+기대값이나 PF 1.1x 문턱 미달, 관찰) · AIXBT·FARTCOIN(음).
- **전략-종목 매칭 인사이트(라운드2+3 종합)**: **밈/고변동(HYPE·POPCAT·PNUT·DOT) →
  vbo**, **성숙 추세/L1(ZEC·RENDER·TRUMP·ALGO·AVAX·S) → prop_breakout**. 라운드2는
  prop 편향, 라운드3(밈 중심)은 vbo 편향으로 구조가 데이터로 선명.

## 최종 로스터 v2 (2026-07-17)

**게이트 통과 12종:** ETH·SOL·XRP·ARB·SUI (1차) + FET·TAO·WLD·ENA·STX·LDO·ICP (2차 승격).
전략 분포: Donchian 7(ETH·ARB·SUI·FET·TAO·ENA·STX) · vbo 5(SOL·XRP·WLD·LDO·ICP).

> **로스터 정정 (Breakout 실거래 정합):** Breakout Prop 앱 종목 목록(스크린샷)
> 대조 결과 **FET·ENA·ICP 는 실거래 미지원**이라 기본 로스터에서 제외했다 —
> 게이트는 통과했으나 실제로 매매할 수 없는 종목은 시뮬레이션에서도 거래하지
> 않는다(실거래 목록 하위집합 원칙).
>
> **라운드2 반영(위 표):** ZEC·RENDER·JTO·TRUMP·ALGO·AVAX·DOT 7종 추가 승격 →
> 현재 `DEFAULT_SYMBOLS` = **16종**(ETH·SOL·XRP·ARB·SUI·TAO·WLD·STX·LDO +
> ZEC·RENDER·JTO·TRUMP·ALGO·AVAX·DOT). 유니버스 19종(+BTC·OP·NEAR 관리용).
동시 포지션 5·총 오픈리스크 2% 전역 캡은 그대로 — 종목 증가 = 기회 밀도 증가, 리스크 불변.

## 유니버스 축소 = 매매로직 강제 (탈락 종목 원천 차단)

탈락 알트 11종(BNB·DOGE·ADA·AVAX·LINK·LTC·DOT·ATOM·APT·UNI·INJ)을 `SYMBOL_SPECS`
에서 **완전히 제거**. (단 **AVAX·DOT 는 2026-07-17 라운드2 게이트를 통과해 재승격** —
엣지는 전략·창 의존이라 재평가에서 뒤집힐 수 있음을 보여준 사례.) `enabled_symbols()`·토글·auto-discovery 가 모두 이 dict 로
필터하므로:
- 기존 `TRADING_SYMBOLS` env 에 탈락 종목이 남아 있어도 **재배포 시 자동 제외**
  (env 18종 → 검증 7종만 가동, 검증됨).
- `+종목` UI 로 켰던 탈락 종목(수동 핀)도 매니저 생성자에서 필터 → 자동 정리.
- BTC 는 게이트 미달이나 메이저 기준 심볼이라 유니버스엔 남김(기본 로스터 제외,
  명시적으로 켤 때만 거래). 유니버스 = {BTC, ETH, SOL, XRP, NEAR, ARB, OP, SUI}.

## 한계 (정직한 기록)

- 표본 12개월·저빈도(연 35~76건)·z ≈ 1.0(ETH 1.32) — 엣지는 양수이나 **얇음**.
  evaluation P(pass) 96~100%는 "측정 엣지가 미래에도 유지된다면"의 값이며, z<2 는
  통계적 확신이 강하지 않음을 뜻한다.
- XRP 는 vbo_k 에 민감 → 실거래 시 디폴트(k=0.5) 고정, 또는 관찰 우선.
- Phase 3 진입 시에도 페이퍼-퍼스트 불변식 유지: live 경로는 `live_enabled` 게이트
  뒤, 데모/테스트넷 검증 → `TRADING_LIVE_ENABLED=1` + 소액 순.

## 청산 관리 A/B — variant(3R/33%/3×ATR) 기각 (2026-07-17)

가설: 트레일 확대(2→3×ATR) + 부분익절 축소(0.5→0.33)로 크립토 모멘텀 테일을 더
포착한다. `/backtest/scan?rr_target=3&partial_tp_frac=0.33&trail_atr_mult=3` 으로 전
로스터 재백테스트, baseline 과 심볼별 best 전략 비교.

**결과: 청산 품질 개선이 아니라 저빈도 추세추종 레짐으로의 전환.** 트레일이 넓어져
포지션이 오래 물리며 왕복 횟수가 급감 — ETH prop_breakout 35→**4거래**가 결정적 증거.

- 개선(생존): ARB vbo 0.325→0.508, FET prop 0.228→0.441, TAO prop 0.198→0.274,
  NEAR vbo 신규통과(0.306). 테일 포착 효과 자체는 부분 확인.
- **대가(탈락): ETH·SUI·XRP·OP·ENA 5종이 게이트 탈락** — 엣지 소멸이 아니라 표본(n)
  붕괴로 재현성(trades≥20) 미달(ETH n=4 신뢰 불가). 프롭 관점에서도 ETH 연 4거래는
  평가 챌린지 최소 거래일·시간 제약 위반.
- **판정: 현행 청산(2R/50%/2×ATR) 유지.** variant 는 로스터 최강 종목을 재현성
  실패로 날리고 구성을 갈아엎는다 — exit 튜닝이 아닌 다른 시스템. 불변식 #5(종목별
  cherry-pick 금지)상 채택 불가. 테일 효과는 실재하나 3×ATR 은 과도 — 더 완만한
  중간값(예: 2.5R/0.5/2.5×ATR)만 게이트가 판정할 후속 가설로 남긴다.
- 도구: `/backtest/scan` 이 여분 쿼리 파라미터를 config 오버라이드로 받도록 확장
  (`_apply_query_overrides`, quick·scan 공유). 매매 경로·리스크 관문 불변.
