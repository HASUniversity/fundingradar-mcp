# Beheer: tokens, veiligheid en testen

## 1. Hoe toegang nu werkt

De server heeft **geen database-credentials**. Hij leest de FundingRadar-API met een
**persoonlijk API-token**:

```
MCP-client → server.py → HTTPS → api/v1 (Bearer-token) → FundingRadar-database
```

Dat is de kern van het ontwerp: één credential per persoon, in te trekken in de webapplicatie,
met de API als grens in plaats van de database. Een gelekt token geeft leestoegang tot de
funding-endpoints van dat ene account — niet tot `users.password_hash`, `php_sessions` of wat
een database-rol verder nog mag.

| Onderdeel | Waar het staat | Wie kan het intrekken |
|---|---|---|
| Token | clientconfig van de gebruiker | de gebruiker zelf (Settings → API access) |
| Token-hash | tabel `api_tokens` in de FundingRadar-database | idem (rij wordt op `revoked_at` gezet) |
| API-eindpunten | `api/v1/*.php` in de FundingRadar-repo | de beheerder (code) |

## 2. Tokens in de praktijk

- **Aanmaken**: Settings → API access. Naam, geldigheid, en je wachtwoord opnieuw. Maximaal 10
  actieve tokens per account (instelling `api_token_max_active`).
- **Bewaren**: het token verschijnt één keer. De server bewaart alleen `sha256(secret)`; er is
  geen functie die een token teruggeeft.
- **Intrekken**: de knop in de lijst, of `revoke_api_token` in `api/auth.php`. Direct effect,
  de volgende API-aanroep krijgt 401.
- **Vervallen**: een token met `expires_at` in het verleden is onbruikbaar, ook al is het niet
  ingetrokken. In de lijst staat het als "Revoked or expired".
- **Account uit**: een gedeactiveerd of niet-goedgekeurd account maakt al zijn tokens
  onbruikbaar (`findUsableByHash` joint op `users.active` en `users.approved`).
- **Quota**: 600 verzoeken per uur per token (instellingen `api_rate_limit_attempts` en
  `api_rate_limit_window`; 0 schakelt de limiet uit). Overschrijding geeft 429 met
  `Retry-After`.

## 3. Wat "read-only" hier betekent

1. **Alleen GET-oppervlak.** `api/v1/*.php` bevat uitsluitend leesacties; er is geen endpoint
   dat iets wijzigt.
2. **Scope in de database.** Elk token heeft `scopes = {read}` en elke v1-endpoint eist die
   scope expliciet (`ApiAuth::requireToken('read')`). Een token met een andere scope krijgt 403.
3. **Veldwhitelist.** `App\ApiSerializer` bepaalt wat er naar buiten gaat. `raw_content` (de
   volledige scrape-pagina), `external_id` en interne boekhoudkolommen komen er niet in, en
   promptvelden (`llm_custom_instructions`) ook niet.
4. **Geen CORS.** Er worden geen CORS-headers gestuurd, dus een willekeurige webpagina kan de
   API niet met een token uitlezen.
5. **Parametervalidatie.** Onbekende waarden worden geweigerd (422) in plaats van stil
   genegeerd: een verkeerde `focus_area`, `group_by` of `sort_by` levert een foutmelding met de
   toegestane waarden op.

Het token reist in **beide** headervormen: deze server stuurt `Authorization: Bearer` én
`X-API-Token`, zodat een proxy of front-end die één van de twee aanpast geen blokkade wordt.

Wat wel een harde grens is: op de productiehost wordt een `fr_`-waarde in de tokenheader naar
`api/v1/*.php` door nginx weggefilterd (eigen 404, nog vóór PHP), terwijl andere prefixen en
queryparameters doorkomen. Daarom zijn tokens 32 tekens met prefix `fdr_` in plaats van `fr_`;
zie `docs/api-v1.md` in de FundingRadar-repo voor de metingen.

Wat het **niet** doet: autorisatie per lectoraat. Elk token leest dezelfde data als de
webapplicatie; er is nog geen scoping op `user_research_group`. Dat is de logische volgende stap
als daar behoefte aan is.

## 4. Testen

Unit tests (geen netwerk, geen token nodig):

```bash
python -m unittest discover -s tests -t . -v      # 30 tests
```

End-to-end over het MCP-protocol, met een echt token in de omgeving of in `.env`:

```bash
python tests/stdio_smoke_test.py
```

Payloads bekijken zoals een client ze krijgt:

```bash
python tests/print_samples.py
```

| Meting 2026-10-09 | Resultaat |
|---|---|
| Unit tests (MCP) | 32 uitgevoerd, alle geslaagd |
| Protocolchecks tegen een echte installatie | 16 uitgevoerd, alle geslaagd |
| API end-to-end (`tests/test_api_v1_e2e.py` in de FundingRadar-repo) | 42 checks: inloggen, token aanmaken via de instellingenpagina, alle acht endpoints, beide headervormen, twaalf geweigerde invoerwaarden, tokenspec-pariteit, intrekken en daarna 401 |
| Token-unitchecks (`tests/test_api_tokens.php`) | 42 checks op formaat, hashing, headerresolutie en headerparsing |

## 5. Problemen oplossen

| Symptoom | Oorzaak en oplossing |
|---|---|
| `missing required environment variable FUNDINGRADAR_API_TOKEN` | Het token staat niet in de clientconfig. Let op: Hermes geeft stdio-servers alleen een veilige basisomgeving; alles wat de server nodig heeft moet expliciet in `env` staan. |
| `HTTP 401: Invalid or expired API token` | Token ingetrokken, verlopen, verkeerd overgetypt, of het account is gedeactiveerd. Maak een nieuw token aan. |
| `HTTP 404` met een HTML-body (`Server: nginx`) | De host laat een tokenheader met een waarde van 43 tekens of meer niet door naar `api/v1/*.php` — nog vóór PHP. Maak het token niet langer dan 42 tekens (deze installatie geeft er een van 31). |
| `FUNDINGRADAR_API_URL does not look like an http(s) URL` | De URL mist het schema (`https://`). |
| `must point at the application root, not at the API directory` | De URL eindigt op `/api/v1`; haal dat stuk eraf. |
| `the FundingRadar API answered with something that is not JSON` | De URL wijst niet naar de applicatie (verkeerd subpad, of een loginpagina). Controleer of `/api/v1/me.php` op die host bestaat. |
| `HTTP 404: Unknown research group` | Slug of naam klopt niet; haal de juiste op met `list_research_groups`. |
| `HTTP 422: ...` | Een parameter is geweigerd; de melding noemt de parameter en de toegestane waarden. |
| `HTTP 429: Rate limit exceeded` | Quota van dit token bereikt; `Retry-After` zegt hoelang. |
| Client toont de server als "failed" of zonder tools | Bijna altijd een pad- of pythonprobleem. Start `server.py` handmatig met dezelfde `command`/`args`: een goed gestarte server print niets en wacht op stdin. |

De server schrijft naar stderr; stdout is gereserveerd voor het protocol. Bij de rooktest wordt
stderr weggeschreven naar `smoke_test_stderr.log` in de repo-root (gitignored).

## 6. Wat er bewust niet in zit

- **Geen HTTP/SSE-transport.** De SDK kan het, maar een endpoint zonder authenticatie is een
  open deur. De API zelf is wél HTTP en vraagt een token.
- **Geen schrijfacties.** Feedback, favorieten en groepsbeheer blijven in de webapplicatie; de
  API kent alleen `read`.
- **Geen caching.** De dagelijkse scrape verandert de cijfers; cache zou vooral verwarring geven
  (de API cachet server-side waar dat veilig is).
- **Geen client-specifieke features.** Alleen standaard MCP over stdio.
- **Geen autorisatie per lectoraat.** Zie §3.

## 7. Verwante plekken

| Wat | Waar |
|---|---|
| API-documentatie (Swagger) | `https://<installatie>/api/v1/docs.php` |
| Machineleesbare spec (OpenAPI 3.1) | `https://<installatie>/api/v1/openapi.json` |
| Eindpunten | `showcases/fundingradar/api/v1/*.php` |
| Tokenbeheer in de app | `api/auth.php` (acties `list_api_tokens`, `create_api_token`, `revoke_api_token`) + `src/ApiToken.php`, `src/ApiAuth.php`, `src/Repository/ApiTokenRepository.php` |
| Migratie | `db/migrations/2026-10-09_api_tokens.sql` |
