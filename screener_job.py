"""
screener_job.py — dagligt batch-job for den åbne screener (BROAD_UNIVERSE)
====================================================================
Køres IKKE af webserveren selv, men som en separat Render "Cron Job"-
service (adskilt fra main.py's web-service), én gang dagligt. Formål:
undgå at /api/screener skal live-scanne 715 tickers ved hvert besøg —
i stedet scanner dette job, gemmer resultatet, og /api/screener læser
bare det cachede resultat (millisekunder, ikke minutter).

Deling af resultatet mellem de to Render-services (cron-jobbet og
web-servicen) sker via GitHub, ikke en delt disk — Render deler ikke
lokalt filsystem mellem to separate services på gratis-niveau, og
persistente diske kræver en betalt plan og understøtter alligevel ikke
deling mellem to services. Løsningen her koster intet ekstra: jobbet
committer resultat-JSON'en til en DEDIKERET branch (`data/screener-cache`,
IKKE `main`, så det ikke trigger en re-deploy af hele webappen hver dag),
og main.py henter den branch's JSON via GitHub's raw-content-URL.

Kør lokalt til test:  python3 screener_job.py --no-push
Render Cron Job-opsætning: se TASKS.md, afsnit "Screener cron-job".
"""
import os
import sys
import json
import subprocess
import tempfile
from datetime import datetime, timezone

from ryefir_signal_engine import (
    fetch_benchmark, fetch_broad_technical_batch, broad_universe_shortlist,
    fetch_stock, get_signal,
)
from broad_universe import BROAD_UNIVERSE

REPO = "tkanstrup/ryefir-webapp"
DATA_BRANCH = "data/screener-cache"
OUTPUT_PATH = "data/screener_cache.json"


def compute_screener_data():
    """Kører hele to-trins scanningen og bygger den JSON /api/screener skal servere."""
    print(f"Screener-job start — {len(BROAD_UNIVERSE)} tickers i universet")
    idx_perf, bm_close = fetch_benchmark()

    print("Trin 1: teknisk batch-hentning (kurs/RSI/RS3M) for hele universet...")
    technical = fetch_broad_technical_batch(list(BROAD_UNIVERSE.keys()), idx_perf)
    print(f"  {len(technical)}/{len(BROAD_UNIVERSE)} tickers gav gyldig kursdata")

    shortlist = broad_universe_shortlist(technical)
    print(f"Trin 2: {len(shortlist)} kandidater kvalificerede til fuld fundamental-hentning")

    candidates = []
    for ticker in shortlist:
        data = fetch_stock(ticker, bm_close=bm_close)
        if data is None:
            continue
        meta = BROAD_UNIVERSE.get(ticker, {})
        signal = get_signal(data, idx_perf)
        idx_m3 = idx_perf.get("m3", 0) or 0
        candidates.append({
            "ticker": ticker,
            "name": data.get("name") or meta.get("name"),
            "region": meta.get("region"),
            "sector": data.get("sector"),
            "industry": data.get("industry") or meta.get("industry"),
            "price": data.get("price"),
            "signal": signal,
            "rsi": data.get("rsi"),
            "perf_3m": data.get("perf_3m"),
            "rs3m_vs_bm": round((data.get("perf_3m") or 0) - idx_m3, 2),
            "direction": data.get("raw_direction"),
            "confirmed_state": data.get("confirmed_state"),
            "high_52w": data.get("high_52w"),
            "low_52w": data.get("low_52w"),
            "roic": data.get("roic"),
            "fcf_margin": data.get("fcf_margin"),
            "fwd_pe": data.get("fwd_pe"),
            "market_cap": data.get("market_cap"),
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe_size": len(BROAD_UNIVERSE),
        "scanned": len(technical),
        "shortlisted": len(shortlist),
        "candidates": candidates,
    }


def write_local(result):
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"Skrev {OUTPUT_PATH} lokalt ({len(result['candidates'])} kandidater)")


def push_to_github(result):
    """Committer OUTPUT_PATH til DATA_BRANCH via en midlertidig, isoleret klon —
    rører ikke det checkout jobbets egen build/run kører fra."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN ikke sat — skipper push, filen er kun skrevet lokalt.")
        return
    remote = f"https://x-access-token:{token}@github.com/{REPO}.git"

    with tempfile.TemporaryDirectory() as tmp:
        def run(*args, **kwargs):
            return subprocess.run(args, cwd=tmp, check=True,
                                   capture_output=True, text=True, **kwargs)

        cloned_existing_branch = True
        try:
            run("git", "clone", "--depth", "1", "--branch", DATA_BRANCH, remote, tmp)
        except subprocess.CalledProcessError:
            cloned_existing_branch = False
            run("git", "clone", "--depth", "1", "--branch", "main", remote, tmp)
            run("git", "checkout", "-b", DATA_BRANCH)

        run("git", "config", "user.email", "screener-job@ryefir.local")
        run("git", "config", "user.name", "Ryefir Screener Job")

        dest = os.path.join(tmp, OUTPUT_PATH)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        run("git", "add", OUTPUT_PATH)
        status = run("git", "status", "--porcelain")
        if not status.stdout.strip():
            print("Ingen ændring i data siden sidste kørsel — pusher ikke.")
            return

        run("git", "commit", "-m",
            f"Screener-cache {result['generated_at']} "
            f"({result['shortlisted']} kandidater)")
        push_args = ["git", "push", "origin", f"HEAD:{DATA_BRANCH}"]
        if not cloned_existing_branch:
            push_args.insert(2, "-u")
        run(*push_args)
        print(f"Pushet til {DATA_BRANCH} på {REPO}")


if __name__ == "__main__":
    result = compute_screener_data()
    write_local(result)
    if "--no-push" not in sys.argv:
        push_to_github(result)
