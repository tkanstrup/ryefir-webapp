"""
main.py — Ryefir webapp-backend, første version
====================================================================
Pakker ryefir_signal_engine.py ind som en altid-kørende webserver.
Modsat update.py (planlagt batch-job via GitHub Actions) er DETTE en
webserver der skal køre kontinuerligt, og svare på forespørgsler i
realtid — passer til Railway/Render, ikke GitHub Actions.

Lokal test:  uvicorn main:app --reload
Herefter:    åbn http://127.0.0.1:8000/api/signal/MSFT i browseren
"""

from fastapi import FastAPI, HTTPException
from ryefir_signal_engine import fetch_stock, fetch_benchmark, get_signal

app = FastAPI(title="Ryefir Signal API", version="0.1")

# update-note: benchmark hentes ved opstart og genbruges — at hente det for
# hvert enkelt ticker-kald ville være unødigt langsomt og belaste yfinance
# mere end nødvendigt. Simpel udgave nu; kan gøres tidsbaseret (fx cache i
# 1 time) senere, hvis appen bruges af flere samtidige brugere.
_idx_perf, _bm_close = fetch_benchmark()


@app.get("/")
def root():
    return {"status": "Ryefir Signal API kører", "endpoints": ["/api/signal/{ticker}"]}


@app.get("/api/signal/{ticker}")
def get_ticker_signal(ticker: str, avg_cost: float = None, stop_loss: float = None):
    """
    Henter live signal for én ticker.
    avg_cost/stop_loss er valgfrie query-parametre (?avg_cost=430&stop_loss=365.5)
    — uden dem beregnes kun de regime-uafhængige signaler (Underperforming/
    Monitor/Strong Hold/Hold), IKKE Stop Loss/Take Profit, som kræver
    testerens egen indgangspris (samme skel som Kategori E vs. P i
    arkitektur-dokumentet).
    """
    ticker = ticker.upper().strip()
    data = fetch_stock(ticker, bm_close=_bm_close)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Kunne ikke hente data for '{ticker}' — tjek stavning/ticker-format")

    signal = get_signal(data, _idx_perf, avg_cost=avg_cost, stop_loss=stop_loss)

    return {
        "ticker": ticker,
        "price": data["price"],
        "signal": signal,
        "rsi": data.get("rsi"),
        "perf_3m": data.get("perf_3m"),
        "direction": data.get("raw_direction"),
        "confirmed_state": data.get("confirmed_state"),
    }
