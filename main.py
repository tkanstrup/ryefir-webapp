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

import os
import time
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from ryefir_signal_engine import fetch_stock, fetch_benchmark, get_signal, fetch_fx_rates, ENGINE_VERSION, probe_info, _FUNDAMENTALS_LAST_FAIL


# Starlettes JSONResponse sætter "application/json" uden charset som
# standard — JSON er UTF-8 pr. spec (RFC 8259), så det virker fint for
# klienter der parser med en rigtig JSON-parser (fx fetch().json() i
# frontend'en), men en browser der åbner URL'en direkte kan falde tilbage
# til Latin-1/Windows-1252, hvilket viser "kører" som "kÃ¸rer" og "↑" som
# "â†‘". Eksplicit charset i Content-Type-headeren fjerner den tvetydighed.
class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


app = FastAPI(title="Ryefir Signal API", version="0.1", default_response_class=UTF8JSONResponse)

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

FX_CACHE_TTL_SEC = 6 * 60 * 60  # 6 t — kurser til værdiansættelse, ikke handel i realtid
FX_FALLBACK_CACHE_TTL_SEC = 3 * 60  # kort, så faste kurser ikke hænger efter Yahoo er tilbage
_fx_cache = {"data": None, "fetched_at": 0}

# update-note: benchmark hentes ved opstart og genbruges — at hente det for
# hvert enkelt ticker-kald ville være unødigt langsomt og belaste yfinance
# mere end nødvendigt. Simpel udgave nu; kan gøres tidsbaseret (fx cache i
# 1 time) senere, hvis appen bruges af flere samtidige brugere.
_idx_perf, _bm_close = fetch_benchmark()


@app.get("/")
def root():
    return {"status": "Ryefir Signal API kører",
            "engine_version": ENGINE_VERSION,
            # Render sætter RENDER_GIT_COMMIT automatisk — viser hvilken commit der kører (null lokalt)
            "commit": os.environ.get("RENDER_GIT_COMMIT"),
            "endpoints": ["/api/signal/{ticker}", "/api/screener", "/api/fx-rates"]}


# Seneste VELLYKKEDE fetch_stock()-resultat pr. ticker. Bruges kun hvis en ny
# hentning fejler: så returneres det gemte resultat markeret "stale": true i
# stedet for en fejl. Udløber efter 24 t, så intet gammelt vises uden at sige det.
# Ligger i hukommelsen — tabes ved genstart/dvale af Render-instansen.
STALE_MAX_AGE_SEC = 24 * 60 * 60
_last_good = {}  # ticker -> {"data": ..., "fetched_at": epoch-sekunder}


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _build_signal_response(ticker, data, avg_cost, stop_loss, fetched_at, stale):
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
        "currency": data.get("currency"),
        "high_52w": data.get("high_52w"),
        "low_52w": data.get("low_52w"),
        "roic": data.get("roic"),
        "fcf_margin": data.get("fcf_margin"),
        "fwd_pe": data.get("fwd_pe"),
        "rs3m_vs_bm": data.get("perf_3m") - _idx_perf.get("m3"),
        "engine_version": ENGINE_VERSION,
        "stale": stale,
        "as_of": _iso(fetched_at),
        # Sektor/branche/navn/valuta/nøgletal kan være en sidst kendt værdi (op til 7 dage gammel)
        # hvis Yahoo ikke svarede; fundamentals_as_of = hvornår de blev hentet (null = ingen data).
        "fundamentals_stale": data.get("fundamentals_stale", False),
        # "live" = frisk .info, "cache" = sidst kendte værdi, "static" = GitHub-genereret fil, null = ingen data
        "fundamentals_source": data.get("fundamentals_source"),
        "fundamentals_as_of": _iso(data["fundamentals_as_of"]) if data.get("fundamentals_as_of") else None,
    }


@app.get("/api/signal/{ticker}")
def get_ticker_signal(ticker: str, avg_cost: float = None, stop_loss: float = None):
    """
    Henter live signal for én ticker.
    avg_cost/stop_loss er valgfrie query-parametre (?avg_cost=430&stop_loss=365.5)
    — uden dem beregnes kun de regime-uafhængige signaler (Underperforming/
    Monitor/Strong Hold/Hold), IKKE Stop Loss/Take Profit, som kræver
    testerens egen indgangspris (samme skel som Kategori E vs. P i
    arkitektur-dokumentet).

    Svaret har altid "stale" og "as_of". Fejler hentningen, men der findes et
    vellykket resultat yngre end 24 t, returneres det med "stale": true og
    "as_of" = tidspunktet det blev hentet. Signalet genberegnes med de aktuelle
    avg_cost/stop_loss. Først uden brugbar cache gives 404.
    """
    ticker = ticker.upper().strip()
    now = time.time()
    data = fetch_stock(ticker, bm_close=_bm_close)
    if data is not None:
        _last_good[ticker] = {"data": data, "fetched_at": now}
        return _build_signal_response(ticker, data, avg_cost, stop_loss, now, stale=False)

    cached = _last_good.get(ticker)
    if cached is not None and now - cached["fetched_at"] < STALE_MAX_AGE_SEC:
        age_min = int((now - cached["fetched_at"]) / 60)
        print(f"STALE_SERVED ticker={ticker} alder={age_min} min", flush=True)
        return _build_signal_response(ticker, cached["data"], avg_cost, stop_loss,
                                      cached["fetched_at"], stale=True)
    raise HTTPException(status_code=404, detail=f"Kunne ikke hente data for '{ticker}' — tjek stavning/ticker-format")


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


@app.get("/api/fx-rates")
def get_fx_rates():
    """
    Valutakurser til DKK (1 enhed valuta = X DKK). Cachet i hukommelsen
    FX_CACHE_TTL_SEC ad gangen. Falder tilbage til sidste kendte kopi hvis
    en ny hentning fejler totalt; enkelte fejlede par bruger faste fallback-
    værdier og listes i "fallbacks_used", så det aldrig sker tavst.
    """
    now = time.time()
    if _fx_cache["data"] is not None:
        ttl = FX_FALLBACK_CACHE_TTL_SEC if _fx_cache["data"]["fallbacks_used"] else FX_CACHE_TTL_SEC
        if now - _fx_cache["fetched_at"] < ttl:
            return _fx_cache["data"]
    try:
        rates, fallbacks_used = fetch_fx_rates()
    except Exception as e:
        if _fx_cache["data"] is not None:
            return _fx_cache["data"]
        raise HTTPException(status_code=503, detail=f"Valutakurser kunne ikke hentes: {e}")
    data = {"base": "DKK",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "rates": rates, "fallbacks_used": fallbacks_used}
    _fx_cache["data"] = data; _fx_cache["fetched_at"] = now
    return data


# MIDLERTIDIG diagnose (fjernes når årsagen til tomme fundamentals er fundet): kører .info
# direkte på Render og viser det rå udfald. Højst ét live-opslag pr. 30 sek. i alt, så
# endpointet ikke kan bruges til at hamre Yahoo.
_probe_state = {"last": 0.0}


@app.get("/api/debug/fundamentals/{ticker}")
def debug_fundamentals(ticker: str):
    ticker = ticker.upper().strip()
    now = time.time()
    if now - _probe_state["last"] < 30:
        return {"ticker": ticker, "throttled": True, "retry_in_sec": int(30 - (now - _probe_state["last"])) + 1,
                "last_fundamentals_fail": _FUNDAMENTALS_LAST_FAIL.get(ticker)}
    _probe_state["last"] = now
    return probe_info(ticker)
