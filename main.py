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

import time

import requests
from fastapi import FastAPI, HTTPException
from ryefir_signal_engine import fetch_stock, fetch_benchmark, get_signal

app = FastAPI(title="Ryefir Signal API", version="0.1")

# update-note (screener-cron): den åbne screener (715 tickers) scannes IKKE
# live her — det ville tage minutter og er for langsomt til et webkald.
# I stedet kører en separat daglig Render "Cron Job"-service (screener_job.py),
# som gemmer resultatet som JSON på en dedikeret git-branch (`data/screener-cache`,
# bevidst IKKE `main`, så det ikke trigger en re-deploy af hele webappen hver
# dag). Dette endpoint henter og cacher den JSON i hukommelsen — se TASKS.md,
# afsnit "Screener cron-job", for den fulde arkitektur og Render-opsætning.
SCREENER_CACHE_URL = (
    "https://raw.githubusercontent.com/tkanstrup/ryefir-webapp/"
    "data/screener-cache/data/screener_cache.json"
)
SCREENER_CACHE_TTL_SEC = 600  # 10 min — nyt nok, uden at hamre GitHub ved hvert besøg
_screener_cache = {"data": None, "fetched_at": 0}

# update-note: benchmark hentes ved opstart og genbruges — at hente det for
# hvert enkelt ticker-kald ville være unødigt langsomt og belaste yfinance
# mere end nødvendigt. Simpel udgave nu; kan gøres tidsbaseret (fx cache i
# 1 time) senere, hvis appen bruges af flere samtidige brugere.
_idx_perf, _bm_close = fetch_benchmark()


@app.get("/")
def root():
    return {"status": "Ryefir Signal API kører",
            "endpoints": ["/api/signal/{ticker}", "/api/screener"]}


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
        "sector": data.get("sector"),
        "industry": data.get("industry"),
        "name": data.get("name"),
        "high_52w": data.get("high_52w"),
        "low_52w": data.get("low_52w"),
        "roic": data.get("roic"),
        "fcf_margin": data.get("fcf_margin"),
        "fwd_pe": data.get("fwd_pe"),
        "rs3m_vs_bm": data.get("perf_3m") - _idx_perf.get("m3"),
    }


@app.get("/api/screener")
def get_screener():
    """
    Serverer det cachede resultat af den daglige åbne screening (715 tickers).
    Scanner IKKE live — se update-note ovenfor. Cacher i hukommelsen
    SCREENER_CACHE_TTL_SEC ad gangen; falder tilbage til sidste kendte gode
    kopi hvis GitHub-hentningen fejler (fx et forbigående netværksproblem).
    """
    now = time.time()
    if _screener_cache["data"] is not None and now - _screener_cache["fetched_at"] < SCREENER_CACHE_TTL_SEC:
        return _screener_cache["data"]

    try:
        resp = requests.get(SCREENER_CACHE_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        if _screener_cache["data"] is not None:
            return _screener_cache["data"]
        raise HTTPException(
            status_code=503,
            detail=f"Screener-cache kunne ikke hentes, og ingen tidligere kopi findes i hukommelsen: {e}",
        )

    _screener_cache["data"] = data
    _screener_cache["fetched_at"] = now
    return data
