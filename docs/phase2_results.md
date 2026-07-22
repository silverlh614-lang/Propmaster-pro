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

## 탈락 종목 재검증 — mean_revert 전략 추가 (2026-07-18, 음성 결과 종결)

진단: 탈락 종목(성숙·레인지 알트)은 **우리 두 전략이 모두 브레이크아웃 계열**이라
걸러졌다 — "돌파하면 추세 지속?" 한 질문만 검증한 셈. 레인지 종목엔 가짜 돌파가
많아 브레이크아웃이 −1R 반복(SOL prop −0.089 가 전형). "브레이크아웃 무엣지" ≠
"매매 불가" → 반대 논리(평균회귀)로 재검증 필요.

- 추가: `mean_revert` 전략 (`strategies/mean_revert.py`, 레지스트리 등록). SMA±Nσ
  밴드 페이드 — 종가가 평균에서 mr_entry_sd σ 이상 벗어나면 회귀에 베팅. 강한 HTF
  추세와 반대로는 페이드 안 함(mr_trend_guard, 기본 off). ATR 스탑은 브레이크아웃과
  동일(FSM 1R 앵커) — 진입 기하만 다르다. 불변식 준수: 시그널만 방출, 레지스트리
  추가만, 엔진 본체 불변. 기본 미채택 — 게이트 통과로만 라이브 자격.
- 스캔 대상: 브레이크아웃 게이트 탈락 성숙·레인지 알트 — BNB·LINK·LTC·ADA·ATOM·
  UNI·APT·INJ·DOGE 등(이미 CANDIDATE_SPECS 에 존재, 스캔 전용·거래 차단). 기존 후보
  (TRX·BCH·ETC·FIL·AAVE·CRV·POL·HBAR 등)와 함께 레인지 성향 풀. (DOT·AVAX 등은
  브레이크아웃 게이트를 통과해 이미 SYMBOL_SPECS 승격 — 재검증 대상 아님.)
- 도구: `/backtest/scan?strat=mean_revert` 로 전략 오버라이드 — 한 URL로 탈락 풀 전체를
  평균회귀로 재백테스트. 캐시 키에 전략 포함.
- 파라미터(mr_mean_bars=20, mr_entry_sd=2.0)는 구조적 초기값 — hand-tune 아님. 게이트가
  expR>0·PF≥1.2·거래≥20 로 판정한다.

### 결과 (12mo, 기본 청산) — 8종 전부 게이트 탈락

| 종목 | expR | PF | 거래 | 비고 |
|---|---|---|---|---|
| UNI | +0.058 | 1.05 | 182 | 최선(그래도 문턱 미달) |
| LINK | +0.047 | 1.05 | 178 | |
| LTC | −0.081 | 0.87 | 166 | |
| BNB | −0.088 | 0.86 | 175 | |
| APT | −0.111 | 0.84 | 162 | 모멘텀 — 페이드 부적합 |
| INJ | −0.151 | 0.79 | 191 | 모멘텀 — 최악권 |
| DOGE | −0.159 | 0.78 | 162 | 밈·펌프 — 페이드 부적합 |
| ADA | −0.235 | 0.68 | 184 | |

### 청산 미스매치 가설 검증 → 반증

가설: 회귀는 평균 도달 시 고정 익절해야 하는데 FSM 이 2R 추세청산이라 엣지가 샌다.
검증: UNI 에 회귀청산(rr_target=1, trail_atr_mult=1) 적용 → expR +0.058 → **−0.18**,
PF 1.05 → 0.71, 승률 36.8→42.1%. **더 악화.** 승률↑·expR↓ 조합이 진단 — 1R 에서
자르니 자주 이기지만 작게 이기고, 손실은 −1R 풀사이즈로 비대칭이 역전.

**결론: baseline 의 미미한 양수는 회귀 엣지가 아니라 페이드 후 추세지속을 트레일이
2R 까지 태운 꼬리 잔여였다. 1R 로 자르니 정체 노출.** 청산을 고치니 악화 = 엔트리
자체에 엣지 없음. → **이 종목들은 브레이크아웃으로도 평균회귀로도 무엣지 = 우리
전략군이 잡을 구조 없음(사실상 효율적 가격).** mean_revert 종결(음성 결과).

- 코드 처리: `mean_revert` 는 레지스트리에 **유지**(vbo 처럼 default-off 옵션 전략).
  전략이 깨진 게 아니라 이 종목군에 엣지가 없는 것 — 진짜 레인지 종목이 나오면 재사용.
  현재 이 전략을 라이브 자격 부여하는 심볼은 **없음**. 스캔 후보 풀도 그대로 둔다
  (백테스트 조회 전용, 거래 차단 불변).

## 신선 12mo 재스캔 + chop 레짐 필터 A/B (2026-07-20)

배포 인스턴스에서 전 로스터(23종 × 2전략) 12mo 재스캔 (`/backtest/scan?months=12`,
46행) + chop 필터 A/B (`chop_gate_flips=8&chop_window=20`, 46행).

### (1) 매핑 정정 2건 — baseline 최적 전략 재확인

신선 12mo 에서 두 종목의 최적 전략이 현행 매핑과 어긋나 정정 (게이트 A/B 근거):

| 종목 | 현행 매핑 | baseline 12mo 최적 | 정정 |
|---|---|---|---|
| **ARB** (코어) | prop_breakout (expR 0.133·PF 1.20·109거래) | **vbo 0.325·PF 1.50·65거래** | → **vbo** |
| **OP** (유니버스) | vbo (0.128·PF 1.19 **탈락**) | **prop 0.159·PF 1.23 통과** | → **prop_breakout** |

- ARB: 코어 종목이 더 약한 Donchian 으로 가동 중이었다 — vbo 로 expR 2.4×·PF 경계선
  (1.20)→견고(1.50). 나머지 18종은 최적과 일치(매핑 무변경). AVAX(27거래)·BTC(<20)
  소표본 유지. → `DEFAULT_SYMBOL_STRATEGY` 두 값 변경.

### (2) 레짐 필터 A/B (chop · adx) → **둘 다 글로벌 기본 기각** (음성 결과)

가설: 레짐 필터로 박스권/약추세 휩쏘를 걸러 얇은 종목 PF 개선. `chop_gate_flips=8`
와 `adx_gate_min=20` 을 각각 전 로스터 A/B. **결과: 둘 다 비균일 — 개선·악화가
섞이고, 각각 코어 게이트 통과 종목을 탈락시킴.**

**chop 필터** (baseline expR → chop expR):

| 방향 | 종목 |
|---|---|
| 개선 | DOT 0.261→0.303 · POPCAT 0.215→**0.289** · STX 0.146→0.164 · OP 0.159→0.170 · RENDER·ALGO·LDO |
| 악화 | **SOL vbo 0.185→0.076 (PF 1.28→1.10 탈락)** · HYPE 0.416→0.314 · ARB 0.325→0.230 · PNUT·XRP·ZEC·S |

**adx 필터** (baseline expR → adx expR) — **더 파괴적: 최강 코어 ETH 를 탈락시킴**:

| 방향 | 종목 |
|---|---|
| 개선 | **LDO 0.137→0.370** · ZEC 0.241→0.290 · STX 0.146→0.184 · TRUMP 0.192→0.219 · XRP 0.203→0.244 · HYPE 0.416→0.831(22거래) |
| 악화(탈락) | **ETH prop 0.80→−0.087** ☠️ · SOL −0.025 · WLD 0.21→−0.045 · TAO 0.198→0.094 · ALGO 0.133→0.084 · PNUT 0.196→0.123 · JTO 0.134→0.129 |

- **판정: `chop_gate_flips`·`adx_gate_min` 글로벌 기본 채택 불가 (둘 다).** 균일 개선이
  아니고, 기존 게이트 통과 코어를 탈락시킨다 — chop 은 SOL, **adx 는 ETH(포트폴리오
  최강)** 까지. 청산 variant(ETH n=4)·mean_revert 와 동일한 실패 모드. 종목별 on/off 는
  cherry-pick → 불변식 #5 위반. **두 필터 모두 글로벌 off 유지.**
- 구조 신호는 실재: 두 필터 모두 추세지속형(DOT·POPCAT·STX·ZEC·LDO)엔 +, 빠른
  되돌림형(SOL·HYPE·ETH·PNUT)엔 −. 방향은 일관 — 얇은 성숙 추세종목엔 필터가 도움.
  → 후속 가설: "종목 추세성(예: Kaufman ER)으로 필터 적용 여부를 **게이트가 종목별로
  판정**"(단일 심볼 cherry-pick 이 아니라 규칙 기반). 지금은 채택 안 함.

## 리셋 전 청산(flatten_before_reset) A/B → 종결·OFF 유지 (2026-07-21)

가설: 00:30 UTC 일일 재앵커에 걸친 미실현 손실이 "부풀려진 balance 기준 새 플로어"에
즉시 브리치시킬 수 있다(리셋 함정). `flatten_before_reset_min`으로 리셋 N분 전 청산하면
방지된다. `/backtest/scan?months=12&flatten_before_reset_min=30` vs baseline A/B.

**결과: 두 스캔이 46행 전부 완전 동일 — 파라미터가 무시됨.** 원인: `flatten_before_reset`/
`_reset_guard_hits`는 **`symbol_bot.py`(라이브 결정 루프)에만** 있고 백테스트 엔진
(`app/trading/backtest/`)에는 구현이 없다. 백테스트는 PositionManager+RiskManager 를
직접 리플레이하므로 **SymbolBot 루프 레벨 룰(flatten·proximity 등)은 백테스트가 반영
못 한다** — 전략(strategy)·FSM 레벨만 A/B 가능 (chop/adx 가 먹힌 이유). Monte-Carlo
(`simulate.py`)도 atomic 트레이드라 리셋 함정을 못 보고, 확장해도 "포지션이 리셋에
걸치는 빈도"를 가정해야 해 결과가 0~14%로 요동(오버핏).

**판정: flatten OFF 유지 (기본값 0).** 근거:
1. **검증 불가** — 백테스트 미구현·Monte-Carlo 가정민감. 규율상 검증 안 된 룰은 안 켠다.
2. **구조적으로 잉여** — `max_total_open_risk_pct`(2%) < 일일예산(3%) < 최대DD(6%). 리셋에
   걸친 전 포지션 미실현 손실은 최대 2% < 일일 플로어까지 3% → **일일 리셋 함정은 이미
   오픈리스크 캡이 방지.** 잔여는 DD 플로어 근처 tail 뿐이고 예산 사이징이 완화.
3. **레버가 아님** — 챌린지 통과율(Monte-Carlo, `simulate.py`)은 예산 사이징+양엣지면
   브리치≈0(실패=타임아웃)이고, 승률 40%→P(pass) 95% vs 승률 7%→DD브리치 60%+. 통과의
   관건은 flatten 이 아니라 **엣지(승률) 유지**다.

> 후속(선택): 백테스트 엔진에 flatten 을 구현하면 실가격 경로로 확정 판정 가능하나,
> 구조 분석상 효과 미미가 예상돼 우선순위 낮음. 현재는 종결.

## 브레이크-리테스트 확인 진입 가설 → 기각 (2026-07-22)

**결과: 기각·OFF 유지.** 12mo 전종목 A/B (`scan?months=12` vs `&retest_confirm=1`,
overrides 에 `retest_confirm:true` 확인). vbo 는 리테스트 게이트가 없어 전 행 불변 —
prop_breakout 만 비교.

- **prop_breakout 게이트 통과 11개 → 4개** (붕괴). 23개 중 **21개 악화**, 2개만 개선.
- **획득(fail→pass) 2**: XRP(0.132→0.178) · NEAR(0.115→0.172) — 되돌림 잦은 종목.
- **상실(pass→fail) 9 ☠️**: SUI **0.310→0.138** · RENDER 0.211→0.081 · TAO 0.198→0.056 ·
  TRUMP 0.191→0.011 · OP 0.159→−0.039 · ARB 0.133→−0.054 · STX·JTO·ALGO. 코어(추세)
  종목이 −0.13~−0.20 expR 로 폭락. MDD 도 악화(OP −745→−2941).
- **구조 신호**: chop/adx 의 정확한 거울상. 리테스트는 **되돌림형(XRP·NEAR)엔 +,
  깨끗한 추세종목(SUI·RENDER·TAO)엔 −** — 돌파가 안 돌아오고 달아나면 진입을 놓친다.
  Donchian 돌파의 엣지는 "리테스트 없이 달아나는 강한 추세"에 있고, 리테스트 요구가
  바로 그걸 버린다.
- **판정 근거**: 균일 개선 아님 + 코어 파괴 = chop/adx·mean_revert·flatten 과 동일 실패
  모드. 종목별 on/off 는 cherry-pick → 불변식 #5 위반. **글로벌 OFF 유지.**
- 노브·구현·테스트는 남긴다(등록된 음성 결과). 후속(선택): 종목 추세성(Kaufman ER)으로
  게이트가 **규칙 기반**으로 필터 선택(추세→plain, 되돌림→retest)하는 메타 가설 — chop/adx
  때와 동일한 미채택 후보. 지금은 안 함.

<details><summary>등록 시 설계 메모 (2026-07-22)</summary>

리더보드 상위권 다수가 쓰는 "Break & Retest"(초기 돌파 대신 되돌림 후 재장악에서 진입)를
가짜돌파 필터로 검증. 전략(진입조건) 레벨이라 **백테스트 A/B 가능**(flatten 과 달리).

- 구현: `strategies/filters.py::retest_confirmed` (stateless, 최근 봉 윈도우 패턴).
  세 조건 AND — (1) 최근 `retest_window` 봉이 사전채널(최근봉 제외) 극단을 종가 돌파,
  (2) 같은 구간 저가가 레벨 ±`retest_band_atr`×ATR 존까지 되돌림, (3) 현재 봉 종가 재장악.
- 노브(전부 기본 OFF, hand-tune 금지): `retest_confirm=0` · `retest_window=5` ·
  `retest_band_atr=0.5`. off 면 기존 돌파 트리거 그대로(회귀 없음, 테스트로 고정).
- 가설: 초기 찌름을 걸러 **승률↑·PF↑**, 대신 거래수↓(진입 지연). 프롭 관점에선
  가짜돌파 후 즉시 손절 연쇄(일일 브리치 유발)를 줄이는 게 기대 효과.

**실행 (배포에서, 12mo 로스터 전종목 A/B):**
```
# baseline
/api/trading/backtest/scan?months=12
# retest on (기본 파라미터)
/api/trading/backtest/scan?months=12&retest_confirm=1
# (선택) 밴드/윈도우 민감도
/api/trading/backtest/scan?months=12&retest_confirm=1&retest_window=8&retest_band_atr=0.75
```
두 스캔 JSON 을 붙여넣으면 종목별 expR/PF/거래수 델타로 채택 판정. **채택 기준:
균일(또는 최소 비악화) 개선 — chop/adx 처럼 코어(ETH 등)를 탈락시키면 기각.**

</details>

## VWAP 되돌림 전략 백테스트 → 로스터 편입 기각 (2026-07-22)

신규 `vwap_pullback` 전략 12mo 전종목 스캔 (`scan?months=12&strat=vwap_pullback`).
가설: 동적 세션 VWAP 눌림은 정적 리테스트가 놓친 추세를 올라타 상위권 데이터(VWAP
바운스 65% vs 단일돌파 42%)처럼 승률↑.

**결과: 기각 (로스터 미편입).**
- **게이트 통과 2/23**: POPCAT(0.236·PF1.39) · WLD(0.145·PF1.21). 나머지 21종 탈락.
- **기존 최선을 이기고 통과하는 종목 = POPCAT 단 1종** (+0.021 vs vbo 0.215) — 노이즈
  수준 단일 종목이라 cherry-pick(불변식 #5). WLD 는 통과하나 제 vbo(0.210)보다 나쁨.
- **승률 28~42%** — 외부의 65% 는 재현 안 됨. 크립토 15m VWAP 바운스 ≠ 금·지수 VWAP.
- **깨끗한 추세종목 초토화**: RENDER −0.201 · S −0.146 · SUI 0.0 · NEAR −0.124.

**구조 신호 (3번째 동일 확인)**: VWAP 는 잡음/밈코인(POPCAT·WLD)에 상대적 최선,
깨끗한 추세종목(RENDER·SUI·S)에 최악 — **retest·mean_revert 와 정확히 같은 패턴**.
세 번의 독립 실험(retest·mean_revert·vwap)이 동일 결론으로 수렴:

> **우리 유니버스는 "추세성"으로 선별돼 단일 모멘텀 엣지에 최적화돼 있다. 눌림·되돌림·
> 페이드 계열 진입은 그 선별 기준이 제거한 특성을 노리므로 이 유니버스에선 전부 진다.**
> VWAP형 엣지를 살리려면 잡은 유니버스를 잡음종목까지 넓혀야 하는데, 이는 "유니버스
> 축소=로직 강제" 원칙과 상충. → 현 로스터엔 편입 안 함.

전략·노브·테스트는 존치(등록됨). 후속(선택): 유니버스를 밈/잡음종목으로 확장하는 별도
트랙을 열 때 재평가. 지금은 종결.
