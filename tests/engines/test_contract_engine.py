from __future__ import annotations

from decimal import Decimal

from app.engines.contract_engine import (
    compute_contract_score,
    compute_iv_rank,
    delta_score,
    dte_score,
    evaluate_and_rank_contracts,
    evaluate_contract_gates,
    select_atm_contract,
    spread_score,
    volume_oi_score,
)
from app.models.enums import OptionType
from tests.engines.factories_options import make_contract, make_quote

# ---------------------------------------------------------------------------
# Hard Gates
# ---------------------------------------------------------------------------


def test_gates_pass_at_exact_thresholds():
    quote = make_quote(bid=1.90, ask=2.00, volume=100, open_interest=100, delta=0.40)  # spread = 5.0%
    contract = make_contract(dte=7)
    gates = evaluate_contract_gates(quote=quote, contract=contract)
    assert gates.passes_all_hard_gates is True


def test_gates_reject_spread_above_5_percent():
    quote = make_quote(bid=1.80, ask=2.00)  # spread = 10%
    contract = make_contract()
    gates = evaluate_contract_gates(quote=quote, contract=contract)
    assert gates.spread_ok is False
    assert gates.passes_all_hard_gates is False


def test_gates_reject_low_oi_or_volume():
    contract = make_contract()
    low_oi = evaluate_contract_gates(quote=make_quote(open_interest=99), contract=contract)
    assert low_oi.open_interest_ok is False

    low_vol = evaluate_contract_gates(quote=make_quote(volume=99), contract=contract)
    assert low_vol.volume_ok is False


def test_gates_reject_delta_outside_range():
    contract = make_contract()
    too_low = evaluate_contract_gates(quote=make_quote(delta=0.39), contract=contract)
    assert too_low.delta_ok is False

    too_high = evaluate_contract_gates(quote=make_quote(delta=0.71), contract=contract)
    assert too_high.delta_ok is False

    missing = evaluate_contract_gates(quote=make_quote(delta=None), contract=contract)
    assert missing.delta_ok is False


def test_gates_reject_dte_outside_range_unless_0dte_allowed():
    quote = make_quote()
    zero_dte_contract = make_contract(dte=0)

    rejected = evaluate_contract_gates(quote=quote, contract=zero_dte_contract, allow_0dte=False)
    assert rejected.dte_ok is False

    allowed = evaluate_contract_gates(quote=quote, contract=zero_dte_contract, allow_0dte=True)
    assert allowed.dte_ok is True

    too_far_out = evaluate_contract_gates(quote=quote, contract=make_contract(dte=46))
    assert too_far_out.dte_ok is False


# ---------------------------------------------------------------------------
# Score normalization
# ---------------------------------------------------------------------------


def test_spread_score_endpoints():
    assert spread_score(Decimal(0)) == Decimal(100)
    assert spread_score(Decimal(5)) == Decimal(0)
    assert spread_score(Decimal(3)) == Decimal(40)


def test_delta_score_peaks_on_ideal_band():
    assert delta_score(Decimal("0.55")) == Decimal(100)
    assert delta_score(Decimal("0.50")) == Decimal(100)
    assert delta_score(Decimal("0.65")) == Decimal(100)
    assert delta_score(Decimal("0.40")) == Decimal(70)
    assert delta_score(Decimal("0.70")) == Decimal(70)


def test_dte_score_peaks_on_ideal_band():
    assert dte_score(14) == Decimal(100)
    assert dte_score(21) == Decimal(100)
    assert dte_score(7) == Decimal(70)
    assert dte_score(45) == Decimal(70)


def test_volume_oi_score_monotonic_and_bounded():
    low = volume_oi_score(open_interest=1, volume=100)
    mid = volume_oi_score(open_interest=100, volume=100)
    high = volume_oi_score(open_interest=5000, volume=100)
    assert low < mid < high
    assert high == Decimal(100)
    assert low >= Decimal(0)


def test_iv_rank_none_without_history():
    assert compute_iv_rank(Decimal("0.30"), []) is None


def test_iv_rank_percentile_calculation():
    history = [Decimal("0.10"), Decimal("0.20"), Decimal("0.30"), Decimal("0.40")]
    # current 0.30 is <= itself and the two below it -> 3/4 = 75%
    assert compute_iv_rank(Decimal("0.30"), history) == Decimal(75)


def test_contract_score_requires_delta():
    contract = make_contract()
    quote = make_quote(delta=None)
    try:
        compute_contract_score(quote=quote, contract=contract, iv_rank=None)
        raised = False
    except ValueError:
        raised = True
    assert raised is True


# ---------------------------------------------------------------------------
# Ranking (the "Contract A/B/C" example, Methodology §15)
# ---------------------------------------------------------------------------


def test_ranking_orders_best_contract_first():
    # Contract A: tight spread, ideal delta, ideal DTE, huge OI -> best.
    contract_a = make_contract(contract_id="A", dte=18)
    quote_a = make_quote(contract_id="A", bid=2.08, ask=2.10, delta=0.58, open_interest=8000, volume=2000)

    # Contract B: passes every gate but noticeably worse across the board.
    contract_b = make_contract(contract_id="B", dte=40)
    quote_b = make_quote(contract_id="B", bid=1.86, ask=1.95, delta=0.42, open_interest=150, volume=120)

    # Contract C: passes gates but poor liquidity and off-center delta.
    contract_c = make_contract(contract_id="C", dte=44)
    quote_c = make_quote(contract_id="C", bid=1.90, ask=1.98, delta=0.69, open_interest=100, volume=100)

    ranked = evaluate_and_rank_contracts(
        [(contract_a, quote_a), (contract_b, quote_b), (contract_c, quote_c)]
    )

    assert [e.contract.contract_id for e in ranked] == ["A", "B", "C"]
    assert ranked[0].score > ranked[1].score > ranked[2].score


def test_ranking_excludes_contracts_failing_hard_gates():
    passing = make_contract(contract_id="PASS")
    passing_quote = make_quote(contract_id="PASS")

    failing = make_contract(contract_id="FAIL")
    failing_quote = make_quote(contract_id="FAIL", bid=1.5, ask=2.0)  # spread = 25%, fails gate

    ranked = evaluate_and_rank_contracts([(passing, passing_quote), (failing, failing_quote)])
    assert [e.contract.contract_id for e in ranked] == ["PASS"]


# ---------------------------------------------------------------------------
# ATM selection
# ---------------------------------------------------------------------------


def test_select_atm_contract_picks_nearest_strike():
    contracts = [
        make_contract(contract_id="low", strike=90, option_type=OptionType.CALL, dte=21),
        make_contract(contract_id="atm", strike=100, option_type=OptionType.CALL, dte=21),
        make_contract(contract_id="high", strike=110, option_type=OptionType.CALL, dte=21),
    ]
    atm = select_atm_contract(contracts, Decimal(101), option_type=OptionType.CALL)
    assert atm is not None
    assert atm.contract_id == "atm"


def test_select_atm_contract_filters_by_type_and_min_dte():
    contracts = [
        make_contract(contract_id="too_short", strike=100, option_type=OptionType.CALL, dte=2),
        make_contract(contract_id="put", strike=100, option_type=OptionType.PUT, dte=21),
        make_contract(contract_id="ok", strike=100, option_type=OptionType.CALL, dte=21),
    ]
    atm = select_atm_contract(contracts, Decimal(100), option_type=OptionType.CALL, min_dte=7)
    assert atm is not None
    assert atm.contract_id == "ok"


def test_select_atm_contract_none_when_no_eligible_contracts():
    assert select_atm_contract([], Decimal(100)) is None
