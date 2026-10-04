"""
build_fundamentals_static.py — bygger data/fundamentals_static.json (navn, sektor, branche, valuta)
====================================================================
Køres af GitHub Actions (.github/workflows/fundamentals-static.yml), IKKE af Render: Yahoo
blokerer/rate-begrænser .info fra Renders delte IP, men svarer fra GitHubs runnere. Filen
committes til den dedikerede branch `data/fundamentals-static` (ikke `main`, så Render ikke
redeployer) og læses af API'et som SIDSTE fallback når live .info og cachen fejler.

Univers: BROAD_UNIVERSE + TICKER_MAP-nøgler + env EXTRA_TICKERS (kommasepareret; sættes som
GitHub Actions-variabel, så brugerens/testernes tickers aldrig står i koden).
Et fejlet opslag sletter aldrig en tidligere gemt værdi (--previous bruges som udgangspunkt).

  python3 tools/build_fundamentals_static.py --probe              # test .info for ACN, print udfald
  python3 tools/build_fundamentals_static.py --out data/fundamentals_static.json [--previous f.json]
  ... --publish    # commit til data-branchen (kræver GITHUB_TOKEN + GITHUB_REPOSITORY)
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yfinance as yf  # noqa: E402
from broad_universe import BROAD_UNIVERSE  # noqa: E402
from ryefir_signal_engine import TICKER_MAP, get_yf  # noqa: E402

DATA_BRANCH = "data/fundamentals-static"
OUTPUT_PATH = "data/fundamentals_static.json"
IDENTITY_KEYS = ("sector", "industry", "longName", "shortName", "currency")
PAUSE_SEC = 0.5


def universe():
    extra = [t.strip().upper() for t in os.environ.get("EXTRA_TICKERS", "").replace("\n", ",").split(",") if t.strip()]
    return sorted(set(BROAD_UNIVERSE) | set(TICKER_MAP) | set(extra))


def fetch_identity(ticker, retries=2):
    """(entry, fejltekst). entry = None hvis Yahoo ikke gav brugbar .info."""
    last = "ukendt"
    for attempt in range(retries + 1):
        try:
            info = yf.Ticker(get_yf(ticker)).info
            if isinstance(info, dict) and any(info.get(k) for k in IDENTITY_KEYS):
                return {"name": info.get("longName") or info.get("shortName"),
                        "sector": info.get("sector"), "industry": info.get("industry"),
                        "currency": info.get("currency"), "country": info.get("country"),
                        "quoteType": info.get("quoteType")}, None
            last = f"tom/ufuldstændig .info (nøgler={len(info) if isinstance(info, dict) else type(info).__name__})"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(2 * (attempt + 1))
    return None, last


def probe():
    print(f"yfinance {getattr(yf, '__version__', '?')}")
    for t in ("ACN", "SAP.DE"):
        entry, err = fetch_identity(t, retries=0)
        print(f"PROBE {t}: " + (f"OK {entry}" if entry else f"FEJL {err}"))


def build(out_path, previous_path):
    previous = {}
    if previous_path and os.path.exists(previous_path):
        previous = json.load(open(previous_path)).get("tickers", {})
    tickers = universe()
    now = datetime.now(timezone.utc).isoformat()
    result, ok, failed = dict(previous), 0, []
    print(f"Univers: {len(tickers)} tickers (tidligere gemt: {len(previous)})", flush=True)
    for i, t in enumerate(tickers, 1):
        entry, err = fetch_identity(t)
        if entry:
            merged = {**{k: v for k, v in previous.get(t, {}).items() if k != "as_of"},
                      **{k: v for k, v in entry.items() if v is not None}}
            result[t] = {**merged, "as_of": now}
            ok += 1
        else:
            failed.append((t, err))
        if i % 50 == 0:
            print(f"  {i}/{len(tickers)} — ok={ok} fejlet={len(failed)}", flush=True)
        time.sleep(PAUSE_SEC)
    print(f"Færdig: ok={ok} fejlet={len(failed)}")
    for t, err in failed[:15]:
        print(f"  FEJL {t}: {err}")
    if ok == 0:
        sys.exit("Ingen tickers kunne hentes — skriver/publicerer ikke (Yahoo blokerer også her?)")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({"generated_at": now, "count": len(result), "tickers": dict(sorted(result.items()))},
                  f, indent=1, ensure_ascii=False)
    print(f"Skrev {out_path} ({len(result)} tickers)")


def publish(out_path):
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        sys.exit("GITHUB_TOKEN/GITHUB_REPOSITORY ikke sat — kan ikke publicere")
    remote = f"https://x-access-token:{token}@github.com/{repo}.git"
    with tempfile.TemporaryDirectory() as tmp:
        def run(*args):
            return subprocess.run(args, cwd=tmp, check=True, capture_output=True, text=True)
        existing = True
        try:
            run("git", "clone", "--depth", "1", "--branch", DATA_BRANCH, remote, tmp)
        except subprocess.CalledProcessError:
            existing = False
            run("git", "clone", "--depth", "1", "--branch", "main", remote, tmp)
            run("git", "checkout", "-b", DATA_BRANCH)
        run("git", "config", "user.email", "fundamentals-static@ryefir.local")
        run("git", "config", "user.name", "Ryefir Fundamentals Job")
        os.makedirs(os.path.join(tmp, os.path.dirname(OUTPUT_PATH)), exist_ok=True)
        with open(out_path) as src, open(os.path.join(tmp, OUTPUT_PATH), "w") as dst:
            dst.write(src.read())
        run("git", "add", OUTPUT_PATH)
        if not run("git", "status", "--porcelain").stdout.strip():
            print("Ingen ændring siden sidst — pusher ikke."); return
        run("git", "commit", "-m", f"Statisk fundamentals-fil {datetime.now(timezone.utc).date()}")
        run("git", "push", "origin", f"HEAD:{DATA_BRANCH}")
        print(f"Pushet til {DATA_BRANCH} ({'opdateret' if existing else 'ny branch'})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--out", default=OUTPUT_PATH)
    ap.add_argument("--previous")
    ap.add_argument("--publish", action="store_true")
    a = ap.parse_args()
    if a.probe:
        probe()
    else:
        build(a.out, a.previous)
        if a.publish:
            publish(a.out)
