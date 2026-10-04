"""
Facit-test for signalmotoren: konstruerede kursforløb med kendt forventet signal.
Testtilfældene ligger i signal_facit.json (ren data, kan genbruges i v9-repoet).
Ingen netværk: Yahoo mockes. Kør:  python3 -m pytest tests -v
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import ryefir_signal_engine as eng  # noqa: E402

FACIT = json.loads((pathlib.Path(__file__).parent / "signal_facit.json").read_text())
DEFAULT_DAYS = 300
END_DATE = "2026-10-02"

NO_FUNDAMENTALS = {"roic": None, "fcf_margin": None, "fwd_pe": None, "market_cap": None,
                   "sector": None, "industry": None, "name": None, "currency": None}


def build_closes(path, days):
    kind = path["kind"]
    if kind == "flat":
        closes = np.full(days, float(path["price"]))
        closes[-1] = closes[-1] * (1 + path.get("last_day_pct", 0) / 100)
    elif kind == "steady":
        r = path["rel_63d_pct"] / 100
        closes = 100 * (1 + r) ** (np.arange(days) / 63)
    else:
        raise ValueError(f"ukendt kursforløb: {kind}")
    return closes


def to_hist(closes):
    idx = pd.bdate_range(end=END_DATE, periods=len(closes))
    close = pd.Series(closes, index=idx)
    return pd.DataFrame({"Open": close, "High": close, "Low": close,
                         "Close": close, "Volume": 1_000_000.0}, index=idx)


def build_benchmark(spec, stock_closes, days):
    if spec == "follows_stock":
        closes = np.array(stock_closes, dtype=float)
    else:
        closes = np.full(days, 100.0)
        if isinstance(spec, dict):
            closes[-1] = closes[-1] * (1 + spec.get("last_day_pct", 0) / 100)
    return closes


def benchmark_idx(closes):
    """Benchmark-nøgletal samme metode som fetch_benchmark(): procent, N handelsdage tilbage."""
    def perf(d, offset=0):
        if len(closes) < d + offset + 1:
            return 0.0
        now, old = closes[-(1 + offset)], closes[-(1 + offset + d)]
        return round((now - old) / old * 100, 2)
    return {"m3": perf(63), "d1": perf(1), "m3_5d_ago": perf(63, 5)}


@pytest.fixture
def mock_yahoo(monkeypatch):
    holder = {}

    class FakeTicker:
        def __init__(self, symbol):
            pass

        def history(self, period=None, **kwargs):
            return holder["hist"].copy()

    monkeypatch.setattr(eng.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(eng, "_fetch_fundamentals", lambda ticker: dict(NO_FUNDAMENTALS))
    return holder


def run_path_case(case, holder):
    days = case["path"].get("days", DEFAULT_DAYS)
    closes = build_closes(case["path"], days)
    holder["hist"] = to_hist(closes)
    bm_closes = build_benchmark(case.get("benchmark"), closes, days)
    bm_series = pd.Series(bm_closes, index=holder["hist"].index)
    data = eng.fetch_stock("TEST", bm_close=bm_series)
    assert data is not None, "fetch_stock gav None på et gyldigt kursforløb"
    return eng.get_signal(data, benchmark_idx(bm_closes),
                          avg_cost=case.get("avg_cost"), stop_loss=case.get("stop_loss"))


@pytest.mark.parametrize("case", FACIT["path_cases"], ids=lambda c: c["id"])
def test_kursforloeb(case, mock_yahoo):
    assert run_path_case(case, mock_yahoo) == case["expected"], case["beskrivelse"]


BASE_DATA = {"price": 100.0, "perf_1d": 0.0, "perf_3m": 0.0, "perf_3m_5d_ago": 0.0,
             "rsi": 50.0, "confirmed_state": "Hold", "ipo_flag": False}
BASE_IDX = {"m3": 0.0, "d1": 0.0, "m3_5d_ago": 0.0}


@pytest.mark.parametrize("case", FACIT["signal_cases"], ids=lambda c: c["id"])
def test_signal_direkte(case):
    data = {**BASE_DATA, **case["data"]}
    idx = {**BASE_IDX, **case.get("idx", {})}
    signal = eng.get_signal(data, idx, avg_cost=case.get("avg_cost"), stop_loss=case.get("stop_loss"))
    assert signal == case["expected"], case["beskrivelse"]


@pytest.mark.parametrize("case", FACIT["stop_cases"], ids=lambda c: c["id"])
def test_stop_loss_kilde(case):
    data = {**BASE_DATA, **case["data"]}
    r = eng.evaluate_signal(data, dict(BASE_IDX), avg_cost=case.get("avg_cost"), stop_loss=case.get("stop_loss"))
    assert r["signal"] == case["expected_signal"], case["beskrivelse"]
    assert r["market_signal"] == case["expected_market_signal"], case["beskrivelse"]
    assert r["stop_loss_source"] == case["expected_source"], case["beskrivelse"]
    assert r["signal_complete"] is case["expected_complete"], case["beskrivelse"]
    assert r["har_position"] is case["expected_har_position"], case["beskrivelse"]
    assert r["stop_loss"] == case["expected_stop_loss"], case["beskrivelse"]


def test_standard_stop_er_en_navngiven_konstant():
    assert eng.DEFAULT_STOP_LOSS_PCT == 15
    r = eng.evaluate_signal(dict(BASE_DATA), dict(BASE_IDX), avg_cost=200)
    assert r["stop_loss"] == 200 * (100 - eng.DEFAULT_STOP_LOSS_PCT) / 100
    assert r["stop_loss_default_pct"] == eng.DEFAULT_STOP_LOSS_PCT


def test_standard_stop_bruger_konstanten(monkeypatch):
    monkeypatch.setattr(eng, "DEFAULT_STOP_LOSS_PCT", 20)
    assert eng.evaluate_signal(dict(BASE_DATA), dict(BASE_IDX), avg_cost=100)["stop_loss"] == 80


def test_advarsel_kun_ved_standard_stop():
    ev = lambda **kw: eng.evaluate_signal(dict(BASE_DATA), dict(BASE_IDX), **kw)
    assert ev(avg_cost=100, stop_loss=90)["warning"] is None
    assert "standardværdien" in ev(avg_cost=100)["warning"]
    assert ev(stop_loss=90)["warning"] is None
    assert ev()["warning"] is None  # watchlist: ingen advarsel


def test_standard_stop_gennem_kursforloeb(mock_yahoo):
    """Hele vejen: kursforløb -> fetch_stock -> evaluate_signal, kun avg_cost sendt."""
    closes = build_closes({"kind": "flat", "price": 84.9}, DEFAULT_DAYS)
    mock_yahoo["hist"] = to_hist(closes)
    data = eng.fetch_stock("TEST", bm_close=pd.Series(closes, index=mock_yahoo["hist"].index))
    r = eng.evaluate_signal(data, benchmark_idx(closes), avg_cost=100)
    assert (r["signal"], r["stop_loss_source"]) == ("Stop Loss!", "standard")


def test_hvert_signal_i_motoren_er_daekket():
    """Hvert signal motoren kan returnere skal have mindst ét facit-tilfælde."""
    expected = {c["expected"] for k in ("path_cases", "signal_cases") for c in FACIT[k]}
    engine_signals = {"Stop Loss!", "Near Stop Loss", "Check Thesis — Big Drop",
                      "Underperforming — Consider Rotating", "Monitor", "Take Profit?",
                      "Strong Hold", "Hold", "New — No Signal History"}
    assert engine_signals <= expected, f"mangler facit for: {engine_signals - expected}"


def test_nan_bar_paa_sidste_dag_giver_stadig_signal(mock_yahoo):
    """Yahoo leverer indimellem en sidste bar med Close=NaN (europæiske tickers, okt. 2026)."""
    closes = build_closes({"kind": "flat", "price": 100}, DEFAULT_DAYS)
    hist = to_hist(closes)
    hist.iloc[-1, hist.columns.get_loc("Close")] = np.nan
    mock_yahoo["hist"] = hist
    data = eng.fetch_stock("TEST", bm_close=None)
    assert data is not None and data["price"] == 100.0


@pytest.mark.parametrize("last_day_pct", [-2.7, -0.1, 3.4])
def test_perf_returnerer_procent_ikke_broek(mock_yahoo, last_day_pct):
    """perf() returnerer procent: -2,7 betyder -2,7 %, ikke -0,027. Big Drop-enhedsfejlen
    (grænser som brøk, afkast som procent) opstod præcis her."""
    closes = build_closes({"kind": "flat", "price": 100, "last_day_pct": last_day_pct}, DEFAULT_DAYS)
    mock_yahoo["hist"] = to_hist(closes)
    data = eng.fetch_stock("TEST", bm_close=None)
    assert data["perf_1d"] == pytest.approx(last_day_pct, abs=0.01)
    assert data["perf_3m"] == pytest.approx(last_day_pct, abs=0.01)
    assert abs(data["perf_1d"]) > 0.05 or last_day_pct == -0.1  # ikke en brøk (-0,027)


def test_motorversion_er_sat():
    assert isinstance(eng.ENGINE_VERSION, str) and eng.ENGINE_VERSION


def test_signal_complete_false_kun_for_positioner_uden_eget_stop():
    ev = lambda **kw: eng.evaluate_signal(dict(BASE_DATA), dict(BASE_IDX), **kw)
    assert ev()["signal_complete"] is True and ev()["har_position"] is False          # watchlist
    assert ev(stop_loss=90)["signal_complete"] is True                                 # kun stop
    assert ev(avg_cost=100, stop_loss=90)["signal_complete"] is True                   # position + eget stop
    assert ev(avg_cost=100)["signal_complete"] is False and ev(avg_cost=100)["har_position"] is True


def test_watchlist_signal_er_identisk_med_markedssignalet():
    """Uden position må evaluate_signal ikke ændre signalet i forhold til get_signal."""
    for confirmed in ("Hold", "Strong Hold", "Monitor", "Underperforming"):
        data = {**BASE_DATA, "confirmed_state": confirmed}
        assert eng.evaluate_signal(data, dict(BASE_IDX))["signal"] == eng.get_signal(data, dict(BASE_IDX))
