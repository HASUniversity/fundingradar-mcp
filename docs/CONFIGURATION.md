# Configureren in andere apps

De server spreekt MCP over **stdio**: elke client start `server.py` zelf als subproces en
communiceert via stdin/stdout. Dat is de meest portable vorm — elke MCP-client ondersteunt
hem — en er is geen poort, proxy of netwerktoegang nodig.

Twee dingen zijn in élke client hetzelfde en gaan meestal mis:

1. **Absolute paden.** Clients starten het subproces zonder voorspelbare working directory.
   Gebruik dus `/pad/naar/.venv/bin/python` en `/pad/naar/server.py`, nooit `./server.py`.
   Op Windows: forward slashes of dubbele backslashes in JSON (`"D:/GIT/fundingradar-mcp/server.py"`).
   Gebruik de **python van de venv**, niet de systeem-python: alleen die heeft `mcp`.
2. **Het token.** Dat gaat via het `env`-blok van de client, niet via een `.env` in de repo
   (dat bestand staat in `.gitignore` en is dus niet meegeleverd bij een clone).

---

## 1. Eerst een token aanmaken

In FundingRadar: **Settings → API access → Create token**.

- Naam: iets waaraan je hem later herkent (`Claude Desktop`, `Cursor`, `laptop Koen`).
- Geldigheid: 90 dagen, 1 jaar of nooit.
- Je **wachtwoord** wordt gevraagd: een token werkt door nadat je uitlogt, dus een gestolen
  sessie alleen mag er geen kunnen aanmaken.
- Het token (`fr_…`) wordt **één keer** getoond. Daarna staat er alleen nog een prefix in de
  lijst — de server bewaart uitsluitend een SHA-256-hash.
- Intrekken kan altijd, met de knop naast het token. Wat het token gebruikte, houdt er direct
  mee op.

## 2. Omgevingsvariabelen

| Variabele | Verplicht | Default | Toelichting |
|---|---|---|---|
| `FUNDINGRADAR_API_TOKEN` | ja | — | `fr_…`, uit Settings → API access |
| `FUNDINGRADAR_API_URL` | nee | `https://dilab.has.nl/showcases/fundingradar` | De **applicatieroot**, niet `/api/v1`. Een pad dat op `/api/v1` eindigt wordt geweigerd met uitleg |
| `FUNDINGRADAR_API_TIMEOUT` | nee | `30` | Seconden, 1–300 |

Een token dat niet met `fr_` begint wordt geweigerd; een ontbrekend token geeft een foutmelding
die naar de instellingenpagina verwijst. De server start altijd — pas een toolaanroep faalt.

---

## 3. Generiek / portable `.mcp.json`

Werkt in Claude Code en in VS Code (portable vorm), en is het formaat dat de meeste clients
begrijpen. Zet dit als `.mcp.json` in de root van je project:

```json
{
  "mcpServers": {
    "fundingradar": {
      "command": "/absoluut/pad/.venv/bin/python",
      "args": ["/absoluut/pad/server.py"],
      "env": {
        "FUNDINGRADAR_API_TOKEN": "fr_PLAK-HIER-JE-TOKEN"
      }
    }
  }
}
```

Windows-variant van de twee paden:

```json
      "command": "D:/GIT/fundingradar-mcp/.venv/Scripts/python.exe",
      "args": ["D:/GIT/fundingradar-mcp/server.py"],
```

Zet een bestand met een token erin **niet** in versiebeheer. Deel je configuratie liever met een
leesbare placeholder en laat iedereen zijn eigen token invullen — dat is ook het punt van
persoonlijke tokens.

## 4. Claude Desktop

Bestand (Windows): `%APPDATA%\Claude\claude_desktop_config.json`
Sleutel: `mcpServers` — hetzelfde formaat als hierboven.

```json
{
  "mcpServers": {
    "fundingradar": {
      "command": "D:/GIT/fundingradar-mcp/.venv/Scripts/python.exe",
      "args": ["D:/GIT/fundingradar-mcp/server.py"],
      "env": {
        "FUNDINGRADAR_API_TOKEN": "fr_..."
      }
    }
  }
}
```

Claude Desktop opnieuw starten (het leest de config alleen bij het opstarten).

## 5. Claude Code

**a. Commando** — schrijft niets met de hand weg:

```bash
claude mcp add fundingradar --scope project \
  --env FUNDINGRADAR_API_TOKEN=fr_... \
  -- /pad/naar/.venv/bin/python /pad/naar/server.py
```

**b. Bestand** — `.mcp.json` in de projectroot met `mcpServers` (zie §3). Claude Code kent
`${VAR}`-uitbreiding in de config; zonder default laadt een ontbrekende variabele de server met
de letterlijke `${VAR}`-tekst, dus gebruik `${VAR:-default}` als je dat risico wilt afdekken.

## 6. VS Code / GitHub Copilot

**Workspace, VS Code-vorm** — `.vscode/mcp.json` met een top-level `servers` (let op: níet
`mcpServers`) en expliciet `type`:

```json
{
  "servers": {
    "fundingradar": {
      "type": "stdio",
      "command": "D:/GIT/fundingradar-mcp/.venv/Scripts/python.exe",
      "args": ["D:/GIT/fundingradar-mcp/server.py"],
      "env": {
        "FUNDINGRADAR_API_TOKEN": "fr_..."
      }
    }
  }
}
```

**Workspace, portable vorm** — `.mcp.json` met `mcpServers` (identiek aan §3); die vorm leest de
Agent Host native en werkt ook in andere Copilot-tools. Wil je het token niet in een bestand,
gebruik dan de `inputs`-vorm van VS Code: een top-level `inputs`-array met type `promptString`
(`"password": true`) en in `env` de verwijzing `${input:<id>}`. VS Code vraagt de waarde dan bij
de eerste start en bewaart hem in zijn eigen opslag.

Server toevoegen via de UI: `MCP: Add Server` in het Command Palette, of de MCP-gallery
(`@mcp` in de Extensions-view).

## 7. Cursor

Bestand: `.cursor/mcp.json` (alleen dit project) of `~/.cursor/mcp.json` (al je projecten).
Sleutel: `mcpServers`. Bij een gelijke servernaam wint de projectversie.

```json
{
  "mcpServers": {
    "fundingradar": {
      "command": "D:/GIT/fundingradar-mcp/.venv/Scripts/python.exe",
      "args": ["D:/GIT/fundingradar-mcp/server.py"],
      "env": {
        "FUNDINGRADAR_API_TOKEN": "fr_..."
      }
    }
  }
}
```

Cursor opnieuw starten; controleer onder *MCP & Integrations* of de server een groen bolletje
heeft.

## 8. Hermes Agent

```yaml
# ~/.hermes/config.yaml
mcp_servers:
  fundingradar:
    command: "D:/GIT/fundingradar-mcp/.venv/Scripts/python.exe"
    args: ["D:/GIT/fundingradar-mcp/server.py"]
    env:
      FUNDINGRADAR_API_TOKEN: "fr_..."
    timeout: 60
```

Of via de CLI:

```bash
hermes mcp add fundingradar \
  --command "D:/GIT/fundingradar-mcp/.venv/Scripts/python.exe" \
  --env FUNDINGRADAR_API_TOKEN=fr_... \
  --args "D:/GIT/fundingradar-mcp/server.py"
```

Twee Hermes-eigenaardigheden:

- Hermes geeft een stdio-server **niet** je hele shellomgeving mee, alleen een veilige basis
  (`PATH`, `HOME`, …). Zet het token dus expliciet in `env`.
- De tools heten in Hermes `mcp_fundingradar_<tool>`, bijvoorbeeld
  `mcp_fundingradar_search_calls`.

## 9. Paseo

Paseo is een orchestrator die zelf **geen** externe MCP-servers inleest: het serveert zijn eigen
tools aan de agents die het start. Agents krijgen externe servers via de provider-CLI. Draait je
Paseo-agent op de Hermes-provider (`command: ["hermes", "acp"]`), dan neemt Hermes zijn eigen
`mcp_servers` mee de sessie in — de configuratie van §8 is dus voldoende. Gebruik je Claude Code
of Codex als provider, configureer de server dan in de config van díe CLI (`.mcp.json`
respectievelijk `~/.codex/config.toml`).

---

## 10. Controleren of het werkt

Zonder client, één commando (start de server, doet de handshake, roept de tools aan en drukt af
wat er werkelijk terugkomt):

```bash
.venv/Scripts/python.exe tests/stdio_smoke_test.py
```

Met een client: vraag iets concreets, bijvoorbeeld *"Welke funding calls zijn er voor lectoraat
green-health?"* of *"Gebruik fundingradar: zoek open calls van ZonMw"*. Zie je de tools niet, kijk
dan in de log van de client naar de opstartfout van het subproces — dat is bijna altijd een
verkeerd pad of een verkeerde python.

Veelvoorkomende fouten en wat ze betekenen staan in
[`OPERATIONS.md`](OPERATIONS.md#5-problemen-oplossen).

---

## Bron en verificatie van deze formats

De formats komen niet uit mijn hoofd; ze zijn op 2026-10-09 opgehaald bij de leveranciers.

| Client | Bron | Status |
|---|---|---|
| Claude Code | `docs.claude.com/en/docs/claude-code/mcp` (markdown) | ✅ gelezen en gevolgd |
| VS Code / Copilot | `code.visualstudio.com/docs/copilot/customization/mcp-servers` + `.../reference/mcp-configuration` | ✅ gelezen en gevolgd (`servers` vs `mcpServers`, `inputs`/`promptString`, portable vormen) |
| Cursor | `cursor.com/help/customization/mcp` + docs-citaties | ✅ gecontroleerd (`.cursor/mcp.json`, `mcpServers`); de cursordocs-pagina zelf is JS-only en niet volledig uit te lezen — de vorm is uit meerdere bronnen consistent |
| Hermes | eigen `hermes mcp --help` op deze machine + `native-mcp`-referentie | ✅ lokaal geverifieerd |
| Paseo | eigen skill + `paseo.sh/docs/mcp` | ✅ gelezen |
| Claude Desktop | MCP-docs (`%APPDATA%\Claude\claude_desktop_config.json`) | ✅ Windows-pad geverifieerd; macOS-pad (`~/Library/Application Support/Claude/`) is de gangbare variant, niet door mij getest |
