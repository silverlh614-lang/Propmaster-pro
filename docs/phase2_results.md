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
- **BTC·나머지 12종목 제외.** ARB·OP·SUI 는 상장 역사가 짧아 12mo 재확인 시 재평가.

## 유니버스 축소 = 매매로직 강제 (탈락 종목 원천 차단)

탈락 알트 11종(BNB·DOGE·ADA·AVAX·LINK·LTC·DOT·ATOM·APT·UNI·INJ)을 `SYMBOL_SPECS`
에서 **완전히 제거**. `enabled_symbols()`·토글·auto-discovery 가 모두 이 dict 로
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
