"""
ryefir_signal_engine.py — Ren signal-motor, INGEN Google Sheets-afhængighed
====================================================================
Udtrukket fra update.py (Stock System v9) til brug i webapp-fundamentet.
Ingen gspread, ingen Sheets-skrivning — kun de rene beregninger: hent
aktiedata via yfinance, beregn signal. Klar til at blive pakket ind i et
FastAPI-endpoint (GET /api/signal/{ticker}).

Alle tærskler og selve signal-logikken er identiske med den validerede,
backtestede udgave i update.py — ingen genimplementering, ren udtrækning.
"""

import time

import yfinance as yf
import pandas as pd
import numpy as np
import math

def clean(v):
    """Rydder NaN/inf-værdier til 0.0 — bruges internt af fetch_stock().
    (Denne blev glemt i første udtrækning — ægte fejl fundet ved Thomas'
    egen lokale test, ikke en antaget detalje.)"""
    try:
        f=float(v)
        if f!=f or f==float("inf") or f==float("-inf"): return 0.0
        return f
    except: return 0.0

def clean_nan(v, default=0):
    """Samme formål som clean(), men med valgfri default-værdi — bruges i
    fetch_benchmark() og get_signal(). Endnu en glemt afhængighed fra første
    udtrækning, fanget ved samme lokale test."""
    try:
        fv = float(v)
        return default if (fv != fv or fv in (float("inf"), float("-inf"))) else fv
    except: return default

# ══════════════════════════════════════════════════════════════════════════
# TÆRSKLER — identiske med update.py, se hypotese-loggen for evidensgrundlag
# ══════════════════════════════════════════════════════════════════════════
# THRESH_RS3M_SELL justeret til -10% — se nedenfor (backtest-evidens)
# update-70: THRESH_RS6M_SELL, THRESH_RS3M_DETERIORATING og THRESH_PNL_PROFIT fjernet —
# Operation Cynicism simplicity audit 19-20/8 2026. RS3M alene var bedst performende
# regel i backtest, uden dokumenteret gevinst fra RS6M/volumen-nuancer/den alternative
# take-profit-vej. THRESH_VOL_* står tilbage kun som informationsvisning (Dashboard),
# driver ikke længere signalet.
THRESH_RS3M_STRONG = 5
THRESH_SL_PROXIMITY = 10
THRESH_BIG_DROP     = -0.10  # Single-day fall triggering immediate Check Thesis signal
THRESH_BIG_DROP_BM  = -0.03  # If benchmark falls this much same day = macro, ignore
THRESH_RSI_OB          = 70
THRESH_TAKE_PROFIT_PCT = 30    # +30% over avg cost → Tag noget hjem?
THRESH_RS3M_PREWARNING = -5    # Pre-warning zone (-5% til -10%)
THRESH_RS3M_SELL       = -10   # Justeret fra -8% til -10% (backtest-evidens)
THRESH_VOL_HIGH    = 6
THRESH_VOL_ELEV    = 3
THRESH_VOL_RATIO   = 1.5


# ══════════════════════════════════════════════════════════════════════════
# TICKER-MAPPING — yfinance-formatering for tickers der afviger fra deres
# almindelige navn (fx nordiske børser, se External Prices-mønsteret)
# ══════════════════════════════════════════════════════════════════════════
TICKER_MAP = {
    "BOOZT":"BOOZT.ST","ETL":"ETL.PA","GOMX":"GOMX.ST","XUSE":"XUSE.AS",
    "NVO":"NOVO-B.CO","VWS":"VWS.CO","KING":"KING.OL","ORSTED":"ORSTED.CO",
    "IMEU":"IMEU.L","ASML":"ASML","NVDA":"NVDA","MSFT":"MSFT",
    "TEAM":"TEAM","AMBU-B":"AMBU-B.CO","AMBU":"AMBU-B.CO",
    "HFG":"HFG.DE","NELLY":"NELLY.ST","IXUA":"IXUA.DE",
    "NAS":"NAS.OL","ZAL":"ZAL.DE","NKE":"NKE","NOW":"NOW",
    "CRM":"CRM","SAP":"SAP","TSM":"TSM","CSU":"CSU.TO","VSURE":"VSURE.ST","NOVO-B":"NOVO-B.CO",
    "WEBN":"WEBN.DE",  # update-77: Amundi Prime All Country World UCITS ETF — Xetra
}
GF_MAP = {
    # Lag 1 — kerne momentum
    "ACN":"NYSE:ACN","BOOZT":"STO:BOOZT","ETL":"EPA:ETL","GOMX":"STO:GOMX",
    "XUSE":"AMS:XUSE","NVO":"CPH:NOVO-B","VWS":"CPH:VWS","KING":"OSL:KING",
    "TEAM":"NASDAQ:TEAM","AMBU":"CPH:AMBU-B","AMBU-B":"CPH:AMBU-B",
    "HFG":"ETR:HFG","NELLY":"STO:NELLY","IXUA":"ETR:IXUA","NAS":"OSL:NAS",
    "ASML":"NASDAQ:ASML","NVDA":"NASDAQ:NVDA","ZAL":"ETR:ZAL",
    "NKE":"NYSE:NKE","NOW":"NYSE:NOW","CRM":"NYSE:CRM","SAP":"NYSE:SAP","TSM":"NYSE:TSM",
    "MSFT":"NASDAQ:MSFT","GOOGL":"NASDAQ:GOOGL","META":"NASDAQ:META","AMZN":"NASDAQ:AMZN",
    "V":"NYSE:V","JPM":"NYSE:JPM","SIE":"ETR:SIE","COLO-B":"CPH:COLO-B",
    "CSU":"TSE:CSU","SE":"NYSE:SE","MC.PA":"EPA:MC","EUNN":"ETR:EUNN",
    # Lag 2 — europæisk kvalitet
    "RMS.PA":"EPA:RMS","OR.PA":"EPA:OR","SU.PA":"EPA:SU",
    "ATCO-A.ST":"STO:ATCO-A","AZN":"NASDAQ:AZN","ADYEN.AS":"AMS:ADYEN",
    "HEXA-B.ST":"STO:HEXA-B","ROG.SW":"SWX:ROG","SAP.DE":"ETR:SAP",
    "LLY":"NYSE:LLY","CAT":"NYSE:CAT","GE":"NYSE:GE",
    # Lag 3 — defensivt rotations-univers
    "NESN.SW":"SWX:NESN","UL":"NYSE:UL","PG":"NYSE:PG","KO":"NYSE:KO",
    "JNJ":"NYSE:JNJ","IBE.MC":"BME:IBE","DGE.L":"LON:DGE",
}
SCOUT_CCY = {
    "TEAM":"USD","AMBU":"DKK","AMBU-B":"DKK","HFG":"EUR","NELLY":"SEK",
    "IXUA":"EUR","NAS":"NOK","ASML":"USD","NVDA":"USD","ZAL":"EUR",
    "NKE":"USD","NOW":"USD","CRM":"USD","SAP":"USD","TSM":"USD","CSU":"CAD","VSURE":"EUR",
}
FX_FALLBACK = {"USD":6.43,"EUR":7.47,"SEK":0.70,"GBP":8.12,"NOK":0.65,"DKK":1.0,"CAD":4.85,"CHF":7.82,"JPY":0.044,"KRW":0.0047,"HKD":0.82}

def get_yf(t): return TICKER_MAP.get(t,t)


# ══════════════════════════════════════════════════════════════════════════
# FUNDAMENTALS-CACHE (sector/industry/navn/nøgletal)
# ══════════════════════════════════════════════════════════════════════════
# update-note (sector-collapse-fix): sector/industry/navn/roic/fcf_margin/
# fwd_pe/market_cap kom hidtil alle fra ét enkelt yf.Ticker(...).info-kald,
# genhentet PR HVERT ENKELT API-kald — dvs. hver gang en bruger genindlæser
# forsiden, sender frontend'en et .info-kald for hver eneste position igen.
# .info er tungt og rate-limit-følsomt hos Yahoo; nok samtidige/gentagne
# kald fik det jævnligt til at fejle (fanget af except-blokken nedenfor),
# hvilket viste sig som Kontroltårnets sektor-swimlanes der kollapsede til
# "Andet" for hele porteføljen — selv efter frontend'en begyndte at batche
# sine kald (se ryefir-frontend, fetchPortfolio). Sector/industry/navn og
# nøgletal ændrer sig reelt sjældent fra dag til dag, så de behøver ikke
# genhentes ved hvert opslag: en simpel in-memory-cache med lang TTL
# reducerer antallet af .info-kald drastisk (kun ét pr. ticker pr. døgn,
# uanset hvor mange brugere/sidevisninger), og ved fejl bruges sidste
# kendte gode værdi i stedet for at lade positionen falde til "ingen data"
# uden grund. Nulstilles ved hver Render-genstart/deploy — acceptabelt,
# det betyder blot at første opslag pr. ticker efter en genstart er
# uden cache, ligesom hidtil.
_FUNDAMENTALS_CACHE = {}
FUNDAMENTALS_CACHE_TTL_SEC = 24 * 60 * 60

def _fetch_fundamentals(ticker):
    cached = _FUNDAMENTALS_CACHE.get(ticker)
    now = time.time()
    if cached and now - cached["fetched_at"] < FUNDAMENTALS_CACHE_TTL_SEC:
        return cached["data"]

    fresh = {"roic": None, "fcf_margin": None, "fwd_pe": None, "market_cap": None,
             "sector": None, "industry": None, "name": None, "currency": None}
    try:
        info = yf.Ticker(get_yf(ticker)).info
        fwd_pe_raw = info.get("forwardPE") or info.get("trailingPE")
        if fwd_pe_raw and 0 < fwd_pe_raw < 500:
            fresh["fwd_pe"] = round(float(fwd_pe_raw), 1)
        fcf = info.get("freeCashflow"); rev = info.get("totalRevenue")
        if fcf and rev and rev > 0:
            fresh["fcf_margin"] = round(fcf / rev * 100, 1)
        ebit = info.get("ebit") or info.get("operatingIncome")
        tax = info.get("effectiveTaxRate", 0.25)
        equity = info.get("totalStockholderEquity") or info.get("bookValue", 0)
        debt = info.get("totalDebt", 0)
        if ebit and (equity or debt):
            invested = (float(equity) if equity else 0) + float(debt)
            if invested > 0:
                fresh["roic"] = round(float(ebit) * (1 - float(tax)) / invested * 100, 1)
        mc = info.get("marketCap")
        if mc:
            fresh["market_cap"] = float(mc)
        fresh["sector"] = info.get("sector")
        fresh["industry"] = info.get("industry")
        fresh["name"] = info.get("longName") or info.get("shortName")
        fresh["currency"] = info.get("currency")
        _FUNDAMENTALS_CACHE[ticker] = {"data": fresh, "fetched_at": now}
        return fresh
    except Exception:
        # .info fejlede (rate-limit/netværk) — brug sidste kendte gode
        # værdi frem for at lade positionen falde til "ingen sektor" uden
        # reel grund. Ingen tidligere cache findes (fx allerførste opslag
        # efter en genstart) → fresh (alt None), som hidtil.
        if cached:
            return cached["data"]
        return fresh


# ══════════════════════════════════════════════════════════════════════════
# DATAHENTNING
# ══════════════════════════════════════════════════════════════════════════
def fetch_stock(ticker, bm_close=None):
    try:
        hist=yf.Ticker(get_yf(ticker)).history(period="13mo").sort_index()
        if hist.empty or len(hist)<20: return None
        price=float(hist["Close"].iloc[-1])
        def perf(d):
            if len(hist)<d+1: return 0.0
            old=float(hist["Close"].iloc[-(d+1)])
            return round((price-old)/old*100,2) if old>0 else 0.0
        def perf_at(offset, window):
            """Performance over `window` days, as of `offset` trading days ago."""
            i_now=-(1+offset); i_old=-(1+offset+window)
            if len(hist)<abs(i_old): return perf(window)
            p_now=float(hist["Close"].iloc[i_now]); p_old=float(hist["Close"].iloc[i_old])
            return round((p_now-p_old)/p_old*100,2) if p_old>0 else 0.0
        # update-82: SMOOTHED+CONFIRMED tilstand — genskaber en fuld historisk
        # RS3M-vs-benchmark-serie (samme metode som backtest8-11 validerede: 5-dages
        # rullende gennemsnit + krav om 3 sammenhængende dage i ny tilstand før den
        # tæller). Reducerede aktions-signaler 64% uden at koste afkast i backtest.
        # Beregnes helt indenfor denne funktion — ingen ekstern cache nødvendig, da
        # både aktiens og benchmarkets fulde historik allerede er hentet her.
        confirmed_state = None; raw_direction = "→ Stable"
        if bm_close is not None and len(hist) >= 90:
            try:
                aligned_bm = bm_close.reindex(hist.index, method="ffill")
                ret_stock = hist["Close"].pct_change(63) * 100
                ret_bm = aligned_bm.pct_change(63) * 100
                rs3m_series = (ret_stock - ret_bm).dropna()
                if len(rs3m_series) >= 15:
                    smoothed = rs3m_series.rolling(5).mean().dropna()
                    delta5 = rs3m_series.diff(5)

                    def classify(rs3m, d):
                        if rs3m < THRESH_RS3M_SELL: return "Underperforming"
                        if rs3m < THRESH_RS3M_PREWARNING and d < -1.0: return "Monitor"
                        if rs3m > THRESH_RS3M_STRONG: return "Strong Hold"
                        return "Hold"

                    raw_states = [classify(smoothed.iloc[i], delta5.get(smoothed.index[i], 0) or 0)
                                  for i in range(len(smoothed))]
                    # 3-dages bekræftelseskrav
                    current = raw_states[0]; pending=None; pending_n=0
                    for s in raw_states:
                        if s == current: pending=None; pending_n=0
                        else:
                            pending_n = pending_n+1 if s==pending else 1
                            pending = s
                            if pending_n >= 3: current = s; pending=None; pending_n=0
                    confirmed_state = current

                    # Pil — rå 5-dages RS3M-retning, samme logik som Screener allerede
                    # bruger. Vises som sekundær mikro-indikator ved siden af det nu
                    # roligere hovedsignal (bekræftet i backtest11: bevæger sig 66% af dagene).
                    last_delta = delta5.iloc[-1] if len(delta5) and not pd.isna(delta5.iloc[-1]) else 0
                    if last_delta > 2: raw_direction = "↑ Accelerating"
                    elif last_delta < -2: raw_direction = "↓ Declining"
            except Exception:
                pass
        # update-74: tilbageregnet momentum-alder — hvor mange handelsdage tilbage har
        # aktiens EGEN 3M-rullende afkast (ikke benchmark-justeret, den fulde historiske
        # benchmark-serie er ikke tilgængelig her) ligget over en momentum-værdig tærskel.
        # Bruges til at seede Screener-cachen første gang en ticker ses, så "alder" ikke
        # kunstigt starter ved "Ny" for alt ved første kørsel. Loft på 90 dage.
        days_above_momentum = 0
        if len(hist) >= 64:
            closes = hist["Close"]
            for offset in range(0, min(90, len(closes)-63)):
                i_now = -(1+offset); i_old = i_now-63
                if abs(i_old) > len(closes): break
                p_now=float(closes.iloc[i_now]); p_old=float(closes.iloc[i_old])
                if p_old<=0: break
                if (p_now-p_old)/p_old*100 > 8:
                    days_above_momentum += 1
                else:
                    break
        delta=hist["Close"].diff()
        gain=delta.clip(lower=0).rolling(14).mean()
        loss=(-delta.clip(upper=0)).rolling(14).mean()
        rs=gain/loss.replace(0,np.nan)
        rsi_val=100-(100/(1+rs.iloc[-1]))
        rsi=round(float(rsi_val),1) if rsi_val==rsi_val else 50.0
        h52=round(float(hist.tail(252)["Close"].max()),4)
        l52=round(float(hist.tail(252)["Close"].min()),4)
        last10=hist.tail(10); vol20=float(hist["Volume"].tail(20).mean())
        hsd=sum(1 for _,r in last10.iterrows()
                if r["Close"]<r["Open"] and (r["Volume"]/vol20 if vol20>0 else 0)>=THRESH_VOL_RATIO)
        vol=("High sell volume" if hsd>=THRESH_VOL_HIGH else
             "Elevated sell vol" if hsd>=THRESH_VOL_ELEV else "Normal volume")
        # Fundamental data — cachet (se _fetch_fundamentals ovenfor), best-effort,
        # falder tilbage til sidste kendte gode værdi hvis .info fejler.
        fundamentals=_fetch_fundamentals(ticker)
        roic=fundamentals["roic"]; fcf_margin=fundamentals["fcf_margin"]
        fwd_pe=fundamentals["fwd_pe"]; market_cap=fundamentals["market_cap"]
        sector_gics=fundamentals["sector"]; industry_gics=fundamentals["industry"]
        company_name=fundamentals["name"]; currency=fundamentals["currency"]
        ipo_flag = len(hist) < 90  # less than ~4 months = no reliable RS3M
        result={"price":clean(price),"perf_1w":clean(perf(5)),"perf_1m":clean(perf(21)),
                "perf_1d":clean(perf(1)),"perf_3m":clean(perf(63)),"perf_6m":clean(perf(126)),"perf_12m":clean(perf(252)),
                "perf_3m_5d_ago":clean(perf_at(5,63)),
                "ipo_flag":ipo_flag,
                "rsi":clean(rsi),"high_52w":clean(h52),"low_52w":clean(l52),
                "vol_signal":vol,"vol_days":hsd,"avg_volume_20d":clean(vol20),
                "days_above_momentum":days_above_momentum,
                "confirmed_state":confirmed_state,"raw_direction":raw_direction,
                "roic":roic,"fcf_margin":fcf_margin,"fwd_pe":fwd_pe,
                "market_cap":market_cap,"sector":sector_gics,"industry":industry_gics,"name":company_name,"currency":currency}
        if result["price"]==0.0: return None
        return result
    except Exception as e:
        print(f"  {ticker} error: {e}"); return None


# Valutakurser til DKK — samme logik som fetch_fx_rates() i update.py (versionen
# i files-3/), udvidet med CAD som update.py ikke havde. Fallback-værdier bruges
# kun hvis Yahoo fejler for et enkelt par; CAD-fallbacken er et groft skøn (ny).
FX_PAIRS = {"USD":"USDDKK=X","EUR":"EURDKK=X","SEK":"SEKDKK=X",
            "GBP":"GBPDKK=X","NOK":"NOKDKK=X","CAD":"CADDKK=X"}
FX_FALLBACK = {"USD":6.43,"EUR":7.47,"SEK":0.70,"GBP":8.12,"NOK":0.65,"CAD":4.70,"DKK":1.0}

def fetch_fx_rates():
    """Returnerer (rates, fallbacks_used): rates = valuta -> kurs i DKK."""
    rates = {"DKK": 1.0}; fallbacks_used = []
    for ccy, pair in FX_PAIRS.items():
        try:
            hist = yf.Ticker(pair).history(period="2d")
            if hist.empty: raise ValueError("tom historik")
            rates[ccy] = round(float(hist["Close"].iloc[-1]), 4)
        except Exception:
            rates[ccy] = FX_FALLBACK[ccy]; fallbacks_used.append(ccy)
    return rates, fallbacks_used

BENCHMARK_TICKERS = ["URTH","ACWI","VT","IWDA.L","^GSPC"]
BENCHMARK_FALLBACK_VALS = {"m1":2.2,"m3":3.9,"m6":6.0,"m12":17.9,"m5d":-0.7,"m30d":2.2,"m90d":3.9,"m3_5d_ago":3.5,"d1":0.0}

# ══════════════════════════════════════════════════════════════════════════════
# ÅBEN SCANNING (update-65) — to-trins hentning af BROAD_UNIVERSE (715 tickers)
# Trin 1: hurtig batch-hentning af kun kursdata (RS3M/RSI) via yf.download
# Trin 2: dyre nøgletal (FCF/P-E) hentes KUN for dem der klarer trin 1-tærsklen
# Formål: undgå 715 individuelle .info-kald dagligt (langsomt + rate-limit-risiko)
# ══════════════════════════════════════════════════════════════════════════════
BROAD_MIN_RS3M = 8          # tærskel for at gå videre til trin 2 (fundamentals)
BROAD_SHORTLIST_CAP = 200   # maks antal der får fuld fundamental-hentning per køre

def fetch_broad_technical_batch(tickers, idx_perf, batch_size=60):
    """Trin 1: henter kun prishistorik for en stor tickerliste via yf.download
    (batched — langt færre HTTP-kald end fetch_stock() per ticker).
    Returnerer dict ticker -> {price, perf_1w, perf_1m, perf_3m, rsi, high_52w, low_52w, rs3m_vs_bm}.
    """
    out = {}
    bm3 = clean_nan(idx_perf.get("m3", 0), 0)
    yf_map = {get_yf(t): t for t in tickers}
    yf_symbols = list(yf_map.keys())
    for i in range(0, len(yf_symbols), batch_size):
        batch = yf_symbols[i:i+batch_size]
        try:
            data = yf.download(batch, period="13mo", group_by="ticker",
                                progress=False, threads=True, auto_adjust=True)
        except Exception as e:
            print(f"   Broad batch {i}-{i+batch_size} fejl: {e}")
            continue
        for sym in batch:
            orig_ticker = yf_map[sym]
            try:
                hist = data[sym] if len(batch) > 1 else data
                hist = hist.dropna(subset=["Close"])
                if hist.empty or len(hist) < 20:
                    continue
                price = float(hist["Close"].iloc[-1])
                def perf(d):
                    if len(hist) < d+1: return 0.0
                    old = float(hist["Close"].iloc[-(d+1)])
                    return round((price-old)/old*100, 2) if old > 0 else 0.0
                delta = hist["Close"].diff()
                gain = delta.clip(lower=0).rolling(14).mean()
                loss = (-delta.clip(upper=0)).rolling(14).mean()
                rs = gain/loss.replace(0, np.nan)
                rsi_val = 100-(100/(1+rs.iloc[-1]))
                rsi = round(float(rsi_val), 1) if rsi_val == rsi_val else 50.0
                h52 = round(float(hist.tail(252)["Close"].max()), 4)
                l52 = round(float(hist.tail(252)["Close"].min()), 4)
                rs3m = perf(63) - bm3
                out[orig_ticker] = {"price": clean(price), "perf_1w": clean(perf(5)),
                    "perf_1m": clean(perf(21)), "perf_3m": clean(perf(63)),
                    "rsi": clean(rsi), "high_52w": clean(h52), "low_52w": clean(l52),
                    "rs3m_vs_bm": round(rs3m, 2)}
            except Exception:
                continue
        print(f"   Broad scan batch {i//batch_size+1}: {len(out)} tickers OK indtil videre")
    return out

def broad_universe_shortlist(technical_data):
    """Filtrerer trin 1-resultater til en kortliste der klarer momentum-tærsklen —
    kun disse går videre til dyr fundamental-hentning (trin 2)."""
    hits = [(t, d["rs3m_vs_bm"]) for t, d in technical_data.items()
            if d.get("rs3m_vs_bm", -999) > BROAD_MIN_RS3M and d.get("rsi", 100) < 65]
    hits.sort(key=lambda x: -x[1])
    return [t for t, _ in hits[:BROAD_SHORTLIST_CAP]]

def fetch_benchmark():
    import math
    for ticker in BENCHMARK_TICKERS:
        try:
            hist=yf.Ticker(ticker).history(period="13mo").sort_index()
            if hist.empty or len(hist)<10: continue
            p=float(hist["Close"].iloc[-1])
            if math.isnan(p) or p<=0: continue
            def perf(d):
                if len(hist)<d+1: return 0.0
                old=float(hist["Close"].iloc[-(d+1)])
                if math.isnan(old) or old<=0: return 0.0
                v=round((p-old)/old*100,2)
                return 0.0 if math.isnan(v) else v
            def perf_at(offset,window):
                i_now=-(1+offset); i_old=-(1+offset+window)
                if len(hist)<abs(i_old): return perf(window)
                p_now=float(hist["Close"].iloc[i_now]); p_old=float(hist["Close"].iloc[i_old])
                if math.isnan(p_now) or math.isnan(p_old) or p_old<=0: return 0.0
                v=round((p_now-p_old)/p_old*100,2)
                return 0.0 if math.isnan(v) else v
            result={"m1":perf(21),"m3":perf(63),"m6":perf(126),"m12":perf(252),
                    "m5d":perf(5),"m30d":perf(21),"m90d":perf(63),"m3_5d_ago":perf_at(5,63),"d1":perf(1)}
            if any(abs(v)>0.01 for k,v in result.items() if k!="d1"):
                print(f"  Benchmark: {ticker} OK (3M: {result['m3']:+.1f}%)")
                # update-82: returnér også benchmarkets Close-serie — bruges til at
                # genskabe en fuld historisk RS3M-tidsserie per aktie (samme metode
                # som backtest-scripts allerede validerede), til det udglattede +
                # bekræftede signal. Ingen ekstern cache nødvendig.
                return result, hist["Close"]
        except: continue
    print("  Benchmark: alle tickers fejlede — bruger fallback")
    return BENCHMARK_FALLBACK_VALS, None

def fetch_macro_data(tickers):
    import math
    results={}
    for ticker in tickers:
        try:
            hist=yf.Ticker(ticker).history(period="13mo").sort_index()
            if hist.empty or len(hist)<20: continue
            price=float(hist["Close"].iloc[-1])
            if math.isnan(price) or price<=0: continue  # update-68: NaN-guard, samme rodårsag som Dashboard/Macro
            def perf(d):
                if len(hist)<d+1: return 0.0
                old=float(hist["Close"].iloc[-(d+1)])
                if math.isnan(old) or old<=0: return 0.0
                v=round((price-old)/old*100,2)
                return 0.0 if math.isnan(v) else v
            # update-78: Flow (volumen-baseret køb/salg-dage) fjernet helt — allerede
            # dokumenteret i macro_signal() at den ikke havde prædiktiv værdi og var
            # taget ud af selve signalet, men de fire kolonner blev aldrig fjernet fra
            # visningen. Ren efterladenskab, samme mønster som tidligere ryddet op.
            results[ticker]={
                "price":price,"perf_1w":perf(5),"perf_1m":perf(21),
                "perf_3m":perf(63),"perf_6m":perf(126),"perf_12m":perf(252),
            }
        except Exception as e:
            print(f"  {ticker} macro error: {e}")
    return results

def macro_signal(d, idx):
    rs3=d["perf_3m"]-idx["m3"]
    # Flow removed as signal component (Part 6: no predictive value in ETF flow)
    # Signal now based purely on RS3M vs benchmark
    if rs3>10: return "Strong momentum"
    if rs3>5:  return "Momentum"
    if rs3<-5: return "Weak"
    if rs3<-10: return "Avoid"
    return "Neutral"


# ══════════════════════════════════════════════════════════════════════════
# SIGNAL-LOGIK — kernen. Bekræftet Tier 1 (Smoothed+Confirmed), se
# ryefir_architecture_narrative.md for fuld evidens-gennemgang.
# ══════════════════════════════════════════════════════════════════════════
def get_signal(data, idx, avg_cost=None, stop_loss=None):
    # update-70: ét samlet signalflow — ingen pos_type-forgrening (MOMENTUM/VALUE/
    # CONVICTION fjernet), ingen volumen-tiers, RS6M fjernet. Se Operation Cynicism
    # simplicity audit 19-20/8 2026: samme aktie skal ALTID give samme signal.
    # IPO flag — less than 90 days of history, no reliable RS3M signal
    if data.get("ipo_flag", False):
        price=data["price"]
        if stop_loss and stop_loss>0 and price>0:
            if price<=stop_loss: return "Stop Loss!"
            sl_dist=(price-stop_loss)/price*100
            if sl_dist<=THRESH_SL_PROXIMITY: return "Near Stop Loss"
        return "New — No Signal History"
    idx_m3 = clean_nan(idx.get("m3"), 0)
    perf_3m = clean_nan(data.get("perf_3m"), 0)
    rs3=perf_3m-idx_m3
    rsi=data["rsi"]; price=data["price"]
    daily_ret = clean_nan(data.get("perf_1d", 0), 0)  # today's single-day return
    bm_daily  = clean_nan(idx.get("d1", 0), 0)         # benchmark single-day return
    # Stop-loss: always highest priority
    if stop_loss and stop_loss>0 and price>0:
        if price<=stop_loss: return "Stop Loss!"
        sl_dist=(price-stop_loss)/price*100
        if sl_dist<=THRESH_SL_PROXIMITY: return "Near Stop Loss"
    # Big single-day drop — immediate signal, macro-adjusted
    # Exception: if benchmark also fell hard, it's macro-driven (ignore)
    if (daily_ret <= THRESH_BIG_DROP and
        bm_daily > THRESH_BIG_DROP_BM):
        return "Check Thesis — Big Drop"
    # Underperformance/Monitor/Strong Hold — update-82: bruger nu det stabiliserede
    # signal (5-dages udglatning + 3-dages bekræftelseskrav) hvis det kunne beregnes,
    # ellers falder tilbage til den rå tærskel. Backtest 8-11 bekræftede: 64% færre
    # aktions-signaler, uændret eller marginalt bedre afkast, ingen tab af reel
    # forudsigelseskraft (Underperforming vs. Hold-gabet holdt sig konsistent).
    confirmed = data.get("confirmed_state")
    if confirmed == "Underperforming":
        return "Underperforming — Consider Rotating"
    if confirmed == "Monitor":
        return "Monitor"
    if confirmed is None:
        # Fallback til rå tærskel — kun hvis den stabiliserede beregning ikke lykkedes
        # (fx for kort historik, eller benchmark-serien ikke var tilgængelig)
        if rs3 < THRESH_RS3M_SELL:
            return "Underperforming — Consider Rotating"
        rs3_prev = data.get("perf_3m_5d_ago", rs3) - idx.get("m3_5d_ago", idx["m3"])
        delta_rs3 = rs3 - rs3_prev
        if rs3 < THRESH_RS3M_PREWARNING and delta_rs3 < -1.0:
            return "Monitor"
    # Positiv side
    # update-71: gevinst-lås — hvis stop-loss er hævet over indgangsprisen, har
    # brugeren allerede handlet på gevinsten (garanteret plus ved worst case).
    # Take Profit skal ikke blive ved at nagge når det reelt er "løst".
    profit_locked = stop_loss and avg_cost and stop_loss > avg_cost
    if avg_cost and not profit_locked and (price-avg_cost)/avg_cost*100 >= THRESH_TAKE_PROFIT_PCT:
        return "Take Profit?"  # +30% over indgangspris
    if confirmed == "Strong Hold" or (confirmed is None and rs3>THRESH_RS3M_STRONG and rsi<THRESH_RSI_OB):
        return "Strong Hold"   # outperformer, ikke overkøbt
    return "Hold"



def signal_strength(sig, pnl_p=0, sl_dist_pct=None, tab_dkk=0):
    """Kontekst-baseret signal-styrke niveau 0-4."""
    # ── Niveau 4: kritisk ────────────────────────────────────
    if sig in ("Stop Loss!", "Check Thesis — Big Drop"):
        return 4

    # ── Near Stop Loss: kontekst-baseret ─────────────────────
    if sig == "Near Stop Loss":
        if sl_dist_pct is not None:
            if sl_dist_pct < 3:    return 4  # < 3% fra SL = nødsignal
            if sl_dist_pct < 8:    return 3  # tæt på
        return 3   # default

    # ── Underperformance: niveau 3 ────────────────────────────
    if sig == "Underperforming — Consider Rotating":
        return 3

    # ── Monitor / pre-warning: niveau 1 ──────────────────────
    if sig in ("Monitor", "New — No Signal History"):
        return 1

    # ── Take Profit: kontekst-baseret, men loftet er sænket (update-71) ──────
    # En gevinst-mulighed skal ALDRIG kunne overtrumfe et reelt Stop Loss/
    # underperformance-signal om opmærksomhedspladsen i "Vær opmærksom på".
    if sig == "Take Profit?":
        if pnl_p >= 60:   return 2  # +60%+ — værd at bemærke, men aldrig kritisk
        return 1                    # +30-60% = mild opmærksomhed

    # ── Positiv side ─────────────────────────────────────────
    if sig == "Strong Hold":
        if pnl_p >= 20:   return 2  # outperformer markant
        return 1                    # positiv opmærksomhed

    # ── Neutral ───────────────────────────────────────────────
    return 0   # Hold og øvrige


def strength_label(niveau, sig=""):
    """Return visual strength label — differentiated positive/negative.
    update-75: engelsk (Stock System v9 er engelsk, kun Signal Test er dansk)."""
    positive = sig in ("Take Profit?", "Strong Hold")
    if positive:
        return {
            0: "·  OK",
            1: "↑○○○  Note",
            2: "↑↑○○  Consider",
            3: "↑↑↑○  Act",
            4: "↑↑↑↑  SECURE GAIN",
        }.get(niveau, "·")
    return {
        0: "·  OK",
        1: "●○○○  Note",
        2: "●●○○  Signal",
        3: "●●●○  Strong signal",
        4: "●●●●  ACT NOW",
    }.get(niveau, "·")

# ══════════════════════════════════════════════════════════════════════════════
# Read from Sheets
# ══════════════════════════════════════════════════════════════════════════════


if __name__ == "__main__":
    # Simpel selvtest — kør: python ryefir_signal_engine.py
    idx_perf, bm_close = fetch_benchmark()
    for test_ticker in ["MSFT", "NVO", "AAPL"]:
        data = fetch_stock(test_ticker, bm_close=bm_close)
        if data:
            sig = get_signal(data, idx_perf)
            print(f"{test_ticker}: pris={data['price']}, signal={sig}")
        else:
            print(f"{test_ticker}: kunne ikke hente data")
