# Ryefir Webapp (Backend) — arbejdsseddel

Denne fil er projektets korte hukommelse på tværs af sessioner, for
backend-repoet specifikt. Hver session starter uden hukommelse om
tidligere samtaler — denne fil er broen. Læs den ved start af en
session, og opdater den når noget er lavet, så næste session kan
fortsætte uden en lang forklaring.

**Søsterrepo:** `ryefir-frontend` (Next.js/TypeScript) har sin egen
tilsvarende `TASKS.md`. De to filer dækker hver deres repo — spørg
brugeren om status på tværs, i stedet for at antage.

---

## Cross-repo & cross-session koordinering (læs dette hvis noget "mangler")

Ryefir spænder over mindst to repos: **`ryefir-webapp`** (dette repo,
Python/FastAPI signal-API + chat-referencekode) og **`ryefir-frontend`**
(Next.js/TypeScript webapp). De har hver deres egen historik, egen
TASKS.md, og bliver typisk arbejdet på af *forskellige* Claude-sessioner.

**En session kender kun sit eget repo.** Den ved intet om arbejde i det
andet repo, medmindre det bliver sagt eksplicit — den kan ikke "se" en
branch eller commit i et repo den ikke selv har åbent. Fandt (2026-09-13):
en session her konkluderede fejlagtigt at frontend-arbejde "aldrig var
landet", fordi den ledte efter en `ryefir-frontend`-branch inde i dette
repo — forkert repo, ikke forkert arbejde.

**Derfor, når du rapporterer status på tværs af sessioner/repos:**
- Navngiv altid **hvilket repo** arbejdet er lavet i — aldrig bare "det er
  gjort", altid "gjort i `<repo>`".
- Giv **branch + commit-hash** når det er muligt (`git log --oneline -1`,
  `git rev-parse HEAD`), så modtageren kan verificere selv i stedet for at
  stole på en påstand fra en samtale den ikke selv var med i.
- Antag ALDRIG at et andet repos filer/branches skal findes i dit eget —
  tjek i stedet om opgaven overhovedet hører hjemme i det repo du sidder i.
- Er du usikker på om noget hører til i `ryefir-frontend` eller
  `ryefir-webapp` — spørg, i stedet for at konkludere ud fra hvad du selv
  kan se.

## Status (kort, udbyg selv videre)

- FastAPI-signal-API (`main.py`, `ryefir_signal_engine.py`), kørende på
  en gratis Render-instans — kan gå i dvale, første kald efter en pause
  kan tage 30-60 sekunder.
- `ryefir_signal_engine.py` beregner signaler/RSI/momentum for enkelt-
  tickers samt en bred screener (715 tickers, to-trins hentning via
  yfinance).
- `ryefir chat v8.html` er en standalone reference-prototype — **den
  rigtige chat er nu bygget ind i `ryefir-frontend`** (roadmap-punkt 3.3,
  `/api/chat`-route der proxy'er til Anthropic server-side). Denne fil
  er historisk reference, ikke noget der længere serveres direkte.
- 2026-09-13: tilføjede `sector`/`industry` til `/api/signal/{ticker}`-
  svaret (hentet via yfinance's `.info`, allerede internt beregnet i
  `fetch_stock()` til screeneren, men aldrig eksponeret udadtil før).
  Koordineret med en `ryefir-frontend`-session der bruger felterne til
  Kontroltårnets sektor-grupperede swimlanes.
- 2026-09-14: `/api/signal/{ticker}` udvidet med `high_52w`, `low_52w`,
  `roic`, `fcf_margin`, `fwd_pe`, `rs3m_vs_bm` — alle allerede beregnet
  internt i `fetch_stock()`/`get_signal()`, bare ikke eksponeret før.
- 2026-09-14: ny `/api/screener`-endpoint + screener cron-job. Se afsnit
  "Screener cron-job" nedenfor for fuld arkitektur og Render-opsætning —
  **Render Cron Job-servicen skal oprettes manuelt i dashboardet, det
  kunne ikke gøres herfra.**
- 2026-09-21/22: **sector/industry/navn/nøgletal kollapsede gentagne
  gange til "Andet"/tomt** på `ryefir-frontend`s Kontroltårn, selv efter
  frontend'en begyndte at batche sine samtidige kald. Rodårsag fundet:
  al den data kom fra ét `yf.Ticker(...).info`-kald, genhentet FRA BUNDEN
  ved hvert eneste API-kald (uanset at data som sector/navn stort set
  aldrig ændrer sig), og en tavs `except: pass` uden fallback — én fejlet
  `.info`-fetch (rate-limit/netværk hos Yahoo) gav "ingen sektor" for
  hele opslaget. Rettet (`425a32f`): `_fetch_fundamentals()` cacher nu i
  hukommelsen (24 t TTL) og falder tilbage til sidste kendte gode værdi
  ved fejl, i stedet for at returnere `None`. Reducerer samtidig det
  samlede antal `.info`-kald til Yahoo drastisk. **Ikke selv verificeret
  live** (ingen netværksadgang til Render/Yahoo fra denne sandbox) —
  brugeren bekræfter i `ryefir-frontend`s samtale/TASKS.md om det virker
  efter deploy.

- 2026-09-21: `/api/signal/{ticker}` har nu `currency` (via det cachede
  `_fetch_fundamentals()`). Nyt `GET /api/fx-rates` (kurser til DKK for USD,
  EUR, SEK, GBP, NOK, CAD; 6 t cache). Logikken er `fetch_fx_rates()` fra
  `update.py` (kun i Downloads/files-3, findes ikke i dette repo), udvidet
  med CAD. CAD-fallbacken (4.70) er et groft skøn; brugte fallbacks
  listes i svaret under `fallbacks_used`.

## Screener cron-job (åben scanning, 715 tickers)

**Princip:** den åbne screener scanner IKKE live ved hvert besøg på
`/api/screener` — det ville tage minutter (715 tickers, to-trins
yfinance-hentning) og er for langsomt til et webkald. I stedet kører
scanningen som en separat, daglig **Render Cron Job**-service
(`screener_job.py`), adskilt fra web-servicen (`main.py`).

**Filer:**
- [`broad_universe.py`](broad_universe.py) — `BROAD_UNIVERSE`-dict, 715
  tickers (486 USA S&P 500 + 229 Europa), genbrugt fra det oprindelige
  Stock System v9. Kun statisk metadata (navn/industri/region) — ingen
  live-data.
- [`screener_job.py`](screener_job.py) — selve batch-jobbet. Genbruger
  `fetch_broad_technical_batch()` (trin 1, billig teknisk batch-hentning
  for hele universet) og `broad_universe_shortlist()` (filtrerer til
  kandidater over RS3M-tærsklen, maks `BROAD_SHORTLIST_CAP=200`), begge
  allerede i `ryefir_signal_engine.py`. Trin 2 (dyre nøgletal for
  kandidaterne) genbruger `fetch_stock()` — samme funktion som
  `/api/signal/{ticker}` allerede bruger, ingen duplikeret logik.

**Deling af resultatet mellem de to Render-services:** Render deler
IKKE lokalt filsystem mellem to separate services på gratis-niveau, og
persistente diske (a) kræver en betalt plan og (b) understøtter
alligevel ikke deling mellem to services (verificeret via websøgning
2026-09-14 — Render-diske er eksklusive til én service). Løsningen:
jobbet committer resultat-JSON'en til en **dedikeret git-branch**,
`data/screener-cache` (**ikke** `main` — en daglig commit på `main`
ville trigge en unødvendig re-deploy af hele webappen hver dag).
Web-servicen henter JSON'en via GitHub's raw-content-URL og cacher den
i hukommelsen i 10 minutter (`SCREENER_CACHE_TTL_SEC` i `main.py`).

**Render-opsætning (manuel, skal gøres i dashboardet):**
1. New → Cron Job, samme GitHub-repo (`tkanstrup/ryefir-webapp`),
   branch `main`.
2. Build command: `pip install -r requirements.txt`
3. Command: `python3 screener_job.py`
4. Schedule: fx `0 6 * * *` (06:00 UTC dagligt, før europæisk
   markedsåbning).
5. Environment variable `GITHUB_TOKEN` — et **fine-grained GitHub
   Personal Access Token**, scoped kun til `ryefir-webapp`-repoet, med
   `Contents: Read and write`-rettighed. Oprettes under GitHub →
   Settings → Developer settings → Fine-grained tokens. **Kun brugeren
   selv kan oprette dette token** (kræver login på GitHub-kontoen).
   Uden `GITHUB_TOKEN` skriver jobbet kun lokalt og skipper push (ses i
   logs som "GITHUB_TOKEN ikke sat").

**Lokal test uden at pushe:**
```
python3 screener_job.py --no-push
```

**Verificeret 2026-09-14:** `data/screener-cache`-branchen er seedet
med et smoke-test-resultat (6 kandidater fra en 16-ticker delmængde,
IKKE en fuld scanning) for at bekræfte at hele kæden virker end-to-end
— `/api/screener` blev testet og henter/cacher korrekt fra branchen.
Den rigtige daglige cron (når Render-servicen er oprettet, se ovenfor)
overskriver den med det fulde 715-ticker-scan ved første kørsel.

**Kendte tekniske begrænsninger, ikke stødt på endnu, hold øje med:**
- Render Cron Jobs er gratis (indgår i de 750 gratis instans-timer/md.)
  — en daglig kørsel på nogle minutter er langt under det. Ingen
  request-timeout-begrænsning her, da det er et baggrundsjob, ikke et
  webkald.
- Hukommelse: jobbet processerer tickers batch-vis/sekventielt (ikke
  alle 715 i hukommelsen samtidig), bør være fint inden for gratis-
  niveauets RAM. Ikke stress-testet på fuld 715-liste endnu — hvis
  Render-jobbet fejler på hukommelse/tid ved første rigtige kørsel, er
  oplagte nedskaleringer: sænk `BROAD_SHORTLIST_CAP`, eller reducér
  `batch_size` i `fetch_broad_technical_batch()`.

---
*Sidst opdateret: 2026-09-14.*
