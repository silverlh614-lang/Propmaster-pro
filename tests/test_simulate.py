"""Offline tests for the challenge pass-probability Monte Carlo.
Run:  python -m tests.test_simulate"""
from __future__ import annotations

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="sim-test-")
os.environ["DATA_DIR"] = _tmp

from app.prop.simulate import simulate_challenge, sweep_plans  # noqa: E402

PROFILE = dict(win_rate=0.45, avg_win_r=2.0, trades_per_day=4, n_sims=800)


def test_deterministic_and_consistent():
    a = simulate_challenge("1step_classic", 10_000, **PROFILE, seed=11)
    b = simulate_challenge("1step_classic", 10_000, **PROFILE, seed=11)
    assert a == b                                     # same seed, same answer
    probs = (a["p_pass"], a["p_breach_daily"], a["p_breach_dd"], a["p_timeout"])
    assert all(0.0 <= p <= 1.0 for p in probs)
    assert abs(sum(probs) - 1.0) < 1e-6
    if a["p_pass"] > 0:
        assert abs(a["fee_per_funded_usd"] - a["fee_usd"] / a["p_pass"]) < 0.01
    print("ok  deterministic seed + probability accounting")


def test_edge_moves_pass_probability():
    """A better trade profile must pass more often (the edge sweep idea)."""
    weak = simulate_challenge("1step_classic", 10_000, win_rate=0.30,
                              avg_win_r=1.5, trades_per_day=4, n_sims=800, seed=3)
    strong = simulate_challenge("1step_classic", 10_000, win_rate=0.50,
                                avg_win_r=2.5, trades_per_day=4, n_sims=800, seed=3)
    assert strong["p_pass"] > weak["p_pass"]
    assert strong["p_pass"] > 0.5                    # clear edge passes mostly
    print("ok  pass probability rises with edge")


def test_budget_sizing_cannot_breach_but_fixed_can():
    """Engine budget sizing structurally cannot touch a floor with atomic
    flat trades; classic fixed sizing can and does."""
    budget = simulate_challenge("1step_turbo", 10_000, win_rate=0.35,
                                avg_win_r=2.0, trades_per_day=6,
                                sizing="budget", n_sims=600, seed=5)
    assert budget["p_breach_daily"] == 0.0 and budget["p_breach_dd"] == 0.0
    fixed = simulate_challenge("1step_turbo", 10_000, win_rate=0.35,
                               avg_win_r=2.0, trades_per_day=6,
                               sizing="fixed", risk_cap_pct=1.0,
                               n_sims=600, seed=5)
    assert fixed["p_breach_dd"] + fixed["p_breach_daily"] > 0.1
    print("ok  budget sizing kills breach risk; fixed sizing keeps it")


def test_tighter_dd_lowers_pass_rate():
    """Turbo (3% DD) must be harder than Classic (6% DD) under fixed sizing
    with the same profile."""
    kw = dict(win_rate=0.40, avg_win_r=2.0, trades_per_day=4,
              sizing="fixed", risk_cap_pct=1.0, n_sims=800, seed=9)
    classic = simulate_challenge("1step_classic", 10_000, **kw)
    turbo = simulate_challenge("1step_turbo", 10_000, **kw)
    assert turbo["p_pass"] <= classic["p_pass"]
    print("ok  tighter DD budget lowers pass rate (turbo <= classic)")


def test_sweep_respects_size_caps():
    out = sweep_plans(200_000, win_rate=0.45, avg_win_r=2.0,
                      trades_per_day=4, n_sims=300, seed=2)
    plans = {r["plan"] for r in out["results"]}
    assert plans == {"1step_pro", "1step_turbo"}      # 200K: Elite tier only
    assert out["best"] in plans
    out2 = sweep_plans(10_000, win_rate=0.45, avg_win_r=2.0,
                       trades_per_day=4, n_sims=300, seed=2)
    assert len(out2["results"]) == 4
    print("ok  plan sweep respects per-plan size caps")


if __name__ == "__main__":
    test_deterministic_and_consistent()
    test_edge_moves_pass_probability()
    test_budget_sizing_cannot_breach_but_fixed_can()
    test_tighter_dd_lowers_pass_rate()
    test_sweep_respects_size_caps()
    print("\nall simulate tests passed ✅")
