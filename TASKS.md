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

---
*Sidst opdateret: 2026-09-13.*
