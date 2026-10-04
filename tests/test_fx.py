"""Test af valutakurserne (fetch_fx_rates). Ingen netværk: Yahoo mockes."""
import pathlib
import re
import sys

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ryefir_signal_engine as eng  # noqa: E402


def fake_yahoo(monkeypatch, close_by_pair=None, fail=()):
    class FakeTicker:
        def __init__(self, pair):
            self.pair = pair

        def history(self, period=None, **kw):
            if self.pair in fail:
                raise ConnectionError("nede")
            c = (close_by_pair or {}).get(self.pair, 7.0)
            return pd.DataFrame({"Close": [c, c]})
    monkeypatch.setattr(eng.yf, "Ticker", FakeTicker)


def test_valutaer_der_skal_vaere_med():
    assert {"USD", "EUR", "SEK", "GBP", "NOK", "CAD", "CHF", "JPY", "HKD"} <= set(eng.FX_PAIRS)
    assert "KRW" not in eng.FX_PAIRS  # Yahoo har ikke KRWDKK=X


def test_alle_par_har_en_fallback_og_dkk_er_1():
    assert set(eng.FX_PAIRS) <= set(eng.FX_FALLBACK)
    assert eng.FX_FALLBACK["DKK"] == 1.0
    assert eng.FX_FALLBACK["CAD"] == 4.65


def test_fx_fallback_er_kun_defineret_en_gang():
    """En dublet i filen overskrev tidligere tavst den rigtige dict (CAD 4,85 vs 4,65)."""
    src = (ROOT / "ryefir_signal_engine.py").read_text()
    assert len(re.findall(r"^FX_FALLBACK\s*=", src, flags=re.M)) == 1


def test_friske_kurser_ingen_fallbacks(monkeypatch):
    fake_yahoo(monkeypatch, {"JPYDKK=X": 0.04205123456, "USDDKK=X": 6.642137})
    rates, fb = eng.fetch_fx_rates()
    assert fb == [] and rates["DKK"] == 1.0
    assert set(rates) == set(eng.FX_PAIRS) | {"DKK"}
    assert rates["JPY"] == 0.042051            # 6 decimaler, ikke 0,042
    assert rates["USD"] == 6.642137


def test_et_par_fejler_kun_det_faar_fallback(monkeypatch):
    fake_yahoo(monkeypatch, fail={"CHFDKK=X"})
    rates, fb = eng.fetch_fx_rates()
    assert fb == ["CHF"] and rates["CHF"] == eng.FX_FALLBACK["CHF"] and rates["USD"] == 7.0


def test_alt_fejler_giver_alle_fallbacks(monkeypatch):
    fake_yahoo(monkeypatch, fail={p for p in eng.FX_PAIRS.values()})
    rates, fb = eng.fetch_fx_rates()
    assert set(fb) == set(eng.FX_PAIRS)
    assert rates == {**{c: eng.FX_FALLBACK[c] for c in eng.FX_PAIRS}, "DKK": 1.0}
