"""
Test af fundamentals-cachen (sector/industry/navn/valuta/nøgletal). Ingen netværk.
Rammer fejlen fra 3. okt. 2026: Yahoo svarede uden fejl men med tom .info, og de tomme
felter blev cachet som succes i 24 t -> null for alle tickers.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import ryefir_signal_engine as eng  # noqa: E402

GOOD_INFO = {"sector": "Technology", "industry": "Software", "longName": "Test Inc.",
             "currency": "USD", "forwardPE": 20.0, "marketCap": 1e9}
FIELDS = ("sector", "industry", "name", "currency")


@pytest.fixture
def yahoo(monkeypatch):
    state = {"info": GOOD_INFO, "now": 1_000_000.0, "calls": 0}

    class FakeTicker:
        def __init__(self, symbol):
            pass

        @property
        def info(self):
            state["calls"] += 1
            if isinstance(state["info"], Exception):
                raise state["info"]
            return state["info"]

    monkeypatch.setattr(eng.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(eng.time, "time", lambda: state["now"])
    eng._FUNDAMENTALS_CACHE.clear()
    eng._FUNDAMENTALS_FAILED_AT.clear()
    yield state
    eng._FUNDAMENTALS_CACHE.clear()
    eng._FUNDAMENTALS_FAILED_AT.clear()


def fields(r):
    return {k: r[k] for k in FIELDS}


def test_normal_hentning(yahoo):
    r = eng._fetch_fundamentals("T")
    assert fields(r) == {"sector": "Technology", "industry": "Software", "name": "Test Inc.", "currency": "USD"}
    assert r["stale"] is False and r["as_of"] == yahoo["now"]


def test_tom_info_caches_ikke_som_succes(yahoo):
    yahoo["info"] = {"trailingPegRatio": None}
    r = eng._fetch_fundamentals("T")
    assert all(v is None for v in fields(r).values())
    assert "T" not in eng._FUNDAMENTALS_CACHE
    yahoo["info"] = GOOD_INFO
    yahoo["now"] += eng.FUNDAMENTALS_RETRY_COOLDOWN_SEC + 1
    assert fields(eng._fetch_fundamentals("T"))["sector"] == "Technology"


def test_pause_efter_fejl_hamrer_ikke_yahoo(yahoo):
    yahoo["info"] = ConnectionError("429")
    eng._fetch_fundamentals("T")
    calls = yahoo["calls"]
    yahoo["now"] += 60
    eng._fetch_fundamentals("T")
    assert yahoo["calls"] == calls


def test_sidst_kendte_vaerdi_som_stale_ved_fejl(yahoo):
    eng._fetch_fundamentals("T")
    yahoo["info"] = ConnectionError("429")
    yahoo["now"] += eng.FUNDAMENTALS_CACHE_TTL_SEC + 1
    r = eng._fetch_fundamentals("T")
    assert fields(r)["sector"] == "Technology"
    assert r["stale"] is True


def test_tom_info_overskriver_ikke_gode_vaerdier(yahoo):
    eng._fetch_fundamentals("T")
    yahoo["info"] = {"trailingPegRatio": None}
    yahoo["now"] += eng.FUNDAMENTALS_CACHE_TTL_SEC + 1
    r = eng._fetch_fundamentals("T")
    assert fields(r)["currency"] == "USD" and r["stale"] is True


def test_delvis_respons_beholder_manglende_felter(yahoo):
    eng._fetch_fundamentals("T")
    yahoo["info"] = {"longName": "Test Inc.", "currency": "USD"}
    yahoo["now"] += eng.FUNDAMENTALS_CACHE_TTL_SEC + 1
    r = eng._fetch_fundamentals("T")
    assert r["sector"] == "Technology" and r["stale"] is False


def test_udloeber_efter_7_dage(yahoo):
    eng._fetch_fundamentals("T")
    yahoo["info"] = ConnectionError("429")
    yahoo["now"] += eng.FUNDAMENTALS_STALE_MAX_AGE_SEC + 1
    r = eng._fetch_fundamentals("T")
    assert all(v is None for v in fields(r).values())


def test_fejl_logges_med_ticker_og_aarsag(yahoo, capsys):
    yahoo["info"] = ConnectionError("429 Too Many Requests")
    eng._fetch_fundamentals("T")
    out = capsys.readouterr().out
    assert "FUNDAMENTALS_FAIL" in out and "ticker=T" in out and "ConnectionError" in out


def test_valuta_og_navn_fra_kursopslag_naar_info_er_nede(monkeypatch):
    """fetch_stock skal ikke vise ukendt valuta bare fordi .info fejler."""
    import numpy as np
    import pandas as pd
    idx = pd.bdate_range(end="2026-10-02", periods=300)
    close = pd.Series(np.full(300, 100.0), index=idx)
    hist = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1e6}, index=idx)

    class FakeTicker:
        def __init__(self, symbol):
            pass
        history_metadata = {"currency": "EUR", "longName": "SAP SE"}
        def history(self, period=None, **kw):
            return hist.copy()

    monkeypatch.setattr(eng.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(eng, "_fetch_fundamentals", lambda t: {**{k: None for k in eng._FUNDAMENTAL_FIELDS},
                                                                "stale": False, "as_of": None})
    d = eng.fetch_stock("SAP.DE", bm_close=None)
    assert d["currency"] == "EUR" and d["name"] == "SAP SE"
