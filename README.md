# FundingRadar MCP

Een **read-only MCP-server** die de FundingRadar-API ontsluit voor elke MCP-client: Claude
Desktop, Claude Code, VS Code/Copilot, Cursor, Hermes, Paseo en alles wat MCP over stdio
spreekt.

De server houdt **geen database-credentials**. Je meldt je aan met een **persoonlijk API-token**
dat je in FundingRadar zelf aanmaakt onder *Settings → API access*. Wat een collega kan lezen,
is wat zijn eigen account kan lezen; het token intrekken werkt per direct. De server kan
alleen lezen — dat is een eigenschap van de API, niet een afspraak.

## Status

Versie 0.2.0 (2026-10-09). Getest: 36 unit tests en 16 protocolchecks over stdio tegen een
echte installatie. De API-kant is getest met 44 end-to-end checks (inloggen → token maken →
API gebruiken → token intrekken) plus 52 PHP-unitchecks; die tests staan in de
FundingRadar-repository (`tests/test_api_v1_e2e.py`, `tests/test_api_tokens.php`). Daarnaast is
deze server end-to-end tegen **productie** gedraaid met een tijdelijk token: handshake, tools/list
en vier toolaanroepen met echte data (zie `docs/OPERATIONS.md`).

| Document | Waarvoor |
|---|---|
| [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) | Token aanmaken en de server aanmelden in je MCP-client |
| [`docs/TOOLS.md`](docs/TOOLS.md) | De zes tools met parameters, uitvoer en kanttekeningen in de data |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Tokenbeheer, veiligheid, testen, problemen oplossen |

De API zelf heeft interactieve documentatie op `/api/v1/docs.php` van de installatie
(Swagger) en een machineleesbare spec op `/api/v1/openapi.json`.

## Snel starten

```bash
git clone <repo-url> fundingradar-mcp
cd fundingradar-mcp

python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# .venv/bin/python -m pip install -r requirements.txt          # macOS/Linux

cp .env.example .env
# 1. Maak een token aan in FundingRadar: Settings → API access → Create token
# 2. Plak het in .env achter FUNDINGRADAR_API_TOKEN
```

Controleren zonder client (dit is ook de test):

```bash
.venv/Scripts/python.exe tests/stdio_smoke_test.py             # start de server en stelt hem vragen
.venv/Scripts/python.exe -m unittest discover -s tests -t .    # unit tests, geen netwerk nodig
```

Daarna aanmelden bij je client — één blok JSON of YAML, zie
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md). Het algemene (portable) blok:

```json
{
  "mcpServers": {
    "fundingradar": {
      "command": "/pad/naar/fundingradar-mcp/.venv/bin/python",
      "args": ["/pad/naar/fundingradar-mcp/server.py"],
      "env": {
        "FUNDINGRADAR_API_URL": "https://dilab.has.nl/showcases/fundingradar",
        "FUNDINGRADAR_API_TOKEN": "fdr_..."
      }
    }
  }
}
```

`FUNDINGRADAR_API_URL` mag weg: die valt terug op de productie-installatie. Zonder token start
de server wél (tools zijn zichtbaar), maar faalt elke aanroep met een uitleg — een client die de
server wegzet als "failed" helpt niemand.

## Wat de server kan

Zes read-only tools plus één resource:

| Tool | Waarvoor |
|---|---|
| `search_calls` | Zoeken op tekst, bron, lectoraat, focusgebied, status, type, taal, deadline en sortering |
| `get_call` | Volledig record van één call, met de gematchte lectoraten, thema's en tags |
| `calls_for_research_group` | Calls die de pijplijn aan één lectoraat matchte, met matchreden en mailstatus |
| `list_sources` | De gescrapete fondsen met aantallen calls |
| `list_research_groups` | Alle lectoraten met thema-, match- en documentaantallen |
| `funding_stats` | Aantallen per bron, maand, status, taal, type, focusgebied of lectoraat |

Resource `fundingradar://call/{public_id}` geeft hetzelfde als `get_call`, voor clients die
resources lezen in plaats van tools aanroepen.

## Structuur

```
server.py                     startpunt voor clients (stdio)
fundingradar_mcp/
  app.py                      tooldefinities, instructies voor de client, stdio-entrypoint
  service.py                  één methode per tool: API-aanroepen en payloads
  client.py                   HTTP naar api/v1 + foutmapping naar leesbare meldingen
  config.py                   omgevingsvariabelen -> API-instellingen
tests/
  test_config.py              URL/token-validatie, maskering, HTTP-foutmapping
  test_service.py             eindpunt-mapping per tool
  stdio_smoke_test.py         end-to-end over het MCP-protocol
docs/                         configuratie, tools, beheer
```

Enige afhankelijkheid: `mcp` (zie `requirements.txt`). De HTTP-client gebruikt de
standard library.

## Aannames en grenzen

- **Alleen lezen.** Geen schrijftool. De API kent alleen GET-eindpunten met scope `read`.
- **Eén installatie per proces**, ingesteld via `FUNDINGRADAR_API_URL`; voor prod en nonprod
  draai je twee entries in je clientconfig.
- **Alleen stdio.** HTTP/SSE zou kunnen, maar vraagt authenticatie op de server zelf.
- **Het token staat in de clientconfig.** Dat is een credential op schijf, maar wel een
  persoonlijke, read-only en per direct intrekbare — en de blast radius is de API, niet de
  database (die bevat ook `users.password_hash` en sessies).
- **Geen CORS.** De API is bedoeld voor scripts, servers en agents; een browser kan er niet
  bij.
