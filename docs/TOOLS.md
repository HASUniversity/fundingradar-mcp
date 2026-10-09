# Tools

Zeven read-only tools plus één resource. Ze praten met de FundingRadar-API (`api/v1`), dus de
uitvoer is precies wat de API teruggeeft: dezelfde velden als de webapplicatie gebruikt. De
volledige veldbeschrijving staat in de OpenAPI-spec van de installatie
(`/api/v1/openapi.json`, interactief op `/api/v1/docs.php`).

Afspraken die voor alle tools gelden: bedragen in euro's, datums als `YYYY-MM-DD`-strings,
`jsonb`-velden als gewone JSON-arrays/objecten, UUID's als strings. Pagineren gaat met `limit`
(1–100) en `offset`; elke lijst-tool geeft `total_matched` (alles wat matcht), `returned` (deze
pagina) en `has_more`.

Fouten komen terug als een tool-resultaat met `isError: true` en de melding van de API, zodat de
aanroepende agent kan bijsturen:

```
Error executing tool search_calls: HTTP 422: deadline_after must be an ISO date (YYYY-MM-DD) (the request parameters were rejected)
Error executing tool calls_for_research_group: HTTP 404: Unknown research group: does-not-exist (nothing matches that identifier; check the slug or UUID)
Error executing tool get_call: HTTP 401: Invalid or expired API token (the API token is missing, revoked or expired — create a new one in FundingRadar under Settings → API access)
```

**Standaardfilters van de applicatie** verklaren waarom dit minder rijen kan geven dan een
ruwe telling in de database. Drie dingen staan standaard uit:

1. **Calls waar niets meer mee kan** — gesloten rondes (`closed`, `intake_closed`) en calls
   waarvan de deadline is gepasseerd. Dit is de belangrijkste: een agent die "is hier een call
   voor?" beantwoordt, hoort geen call uit 2022 te noemen. `include_closed=True` opent het
   archief; een expliciete `status` zet de filter ook uit.
2. Calls die de pijplijn als niet-passend voor HAS classificeerde → `include_ineligible=True`.
3. Calls met een deadline vóór 2020 → een expliciete `deadline_after` vervangt die grens.

Eén definitie geldt overal: `CallRepository::ACTIONABLE_SQL` in de FundingRadar-repo, dezelfde
regel die de lectoraattellingen (`matched_open_calls`) gebruiken.

**URL's zitten altijd in de uitvoer**, zodat een agent nooit een call noemt zonder hem te kunnen
openen: `url` (de pagina van de fondsverstrekker), `apply_url`, `call_document_url` en
`tracked_url` — dezelfde bestemming via de klikteller van de applicatie. Zie *URL's en kliks*
onderaan voor het verschil en wanneer je welke doorgeeft.

---

## `search_calls`

| Parameter | Type | Default | Betekenis |
|---|---|---|---|
| `query` | string | — | **Het onderwerp, geen vraag.** De woorden worden samen met hun NL/EN-tegenhanger en de URL's van de calls gezocht (zie `expand`) |
| `expand` | bool | `true` | Zoek ook de synoniemen (bodem ↔ soil, glastuinbouw ↔ greenhouse horticulture) en laat de call-URL als treffer tellen. `false` stuurt `literal=1` naar de API — die opt-out, want uitbreiden is de default |
| `source` | string | — | Bron op short name óf volledige naam, bv. `ZonMw`, `RVO`, `EU Portal` (onbekend → fout) |
| `research_group` | string | — | Lectoraat-slug of naam; alleen calls die de pijplijn daaraan matchte |
| `focus_area` | string | — | Eén van `onderzoek`, `onderwijs`, `zakelijke_dienstverlening`; andere waarden worden geweigerd |
| `status` | string | — | `open`, `upcoming`, `closed` of `intake_closed` |
| `funding_type` | string | — | Exact; in de praktijk bijna altijd `grant` |
| `language` | string | — | `nl` of `en` |
| `deadline_after` / `deadline_before` | date | — | Deadline op of na / op of vóór deze datum |
| `sort_by` | string | `deadline_asc` | `deadline_asc`, `deadline_strict`, `deadline_desc`, `created_at_desc`, `created_at_asc`, `title_asc`, `title_desc`, `budget_desc`, `budget_asc`, `status_priority` |
| `include_closed` | bool | `false` | Ook calls tonen waar niets meer mee kan: gesloten rondes en deadlines die zijn gepasseerd |
| `include_ineligible` | bool | `false` | Ook calls tonen die als niet-passend voor HAS zijn geclassificeerd |
| `brief` | bool | `false` | Alleen de velden om te beoordelen en te openen (id, titel, bron, status, deadline, `url`, `tracked_url`) in plaats van het hele record van ~40 velden. Voor scannen en lange lijsten; `get_call` haalt het volledige record op |
| `limit` / `offset` | int | 20 / 0 | Paginering |

Sortering standaard: dichtstbijzijnde deadline eerst, zonder deadline achteraan. Standaard krijg
je alleen calls waar nog iets mee kan (niet gesloten, deadline niet gepasseerd); met
`include_closed=True`, een expliciete `status` of een `deadline_before` in het verleden krijg je
het archief erbij.

**Zoeken is letterlijk — help het.** Zonder uitbreiding moet *elk* woord van `query` matchen, dus
een hele alinea of een term in de verkeerde taal vindt niets (gemeten: een volledige zin gaf 0
calls). Met `expand` aan (default) wordt de vraag omgezet in termen en ge-OR'd over titel,
beschrijving, fonds, programma **en de URL's van de call** (`/bijdrageregeling-water-en-bodem`).
Diezelfde zin gaf 210 calls. Het antwoord bevat `searched_terms`: wat er werkelijk gezocht is —
lees dat en stuur bij als je onderwerp er niet in zit. Verder dan vier woorden en drie verwanten
per woord gaat het niet, en stopwoorden of te algemene woorden (health, management, kwaliteit)
worden overgeslagen.

De zoekopdracht gaat als **POST-body** naar de API: dezelfde velden, maar buiten de URL — dus
niet in access logs, en niet beperkt door URL-lengte. Een gewone GET met `query=…` werkt ook;
filteren zonder zoekterm is altijd een GET.

Sweep liever meerdere assen dan één term: onderwerp (bodem, water, ai), methode (monitoring,
sensor, remote sensing), sector (glastuinbouw, veehouderij, voeding, verpakking). En combineer
dit met `calls_for_research_group`: dat is de semantische route, want de pijplijn matchte elke
call zelf aan een lectoraat met een reden (`match_reason`). In de praktijk komt de beste treffer
vaak uit die matches en niet uit de trefwoorden.

Elke call in het resultaat bevat de dashboardvelden plus `research_group_count`, `tags`,
`research_groups` (id + naam) en `is_favorited`. `raw_content` (de volledige scrape-pagina) wordt
nooit meegestuurd.

---

## `get_call`

Volledig record van één call plus de relaties. `public_id` accepteert de publieke UUID **of** het
numerieke id.

Uitvoer: `call` (dashboardvelden, inclusief `description`, alle budget- en
cofinancieringsvelden, `eligibility_*`, `themes`, `disciplines`, `focus_areas`, `contact_details`,
`urgency`), `matched_research_groups` (per match: `slug`, `name`, `contact_email`, `matched_by`,
`match_reason`, `email_sent`, `notified_at`), `matched_research_themes` en `tags`.

Onbekende of misvormde id's geven een fout (`HTTP 404` respectievelijk `HTTP 422`), geen lege
uitvoer.

Elke call bevat de URL's: `url` (de pagina van de fondsverstrekker zelf), `apply_url` (indien apart
aanvraagformulier), `call_document_url` en `tracked_url` — dezelfde bestemming via de klikteller van
de applicatie, zodat een klik in **Click Analytics** terechtkomt (zie *URL's en kliks* onderaan).

---

## `read_call_page`

Haalt de pagina van de fondsverstrekker op en geeft de **tekst** terug, zodat het model de
actuele status kan beoordelen ("is deze ronde nog open, is de deadline verschoven?"). De status en
deadline in de database komen uit de dagelijkse pijplijn en kunnen achterlopen; deze tool leest de
bron zelf.

| Parameter | Type | Default | Betekenis |
|---|---|---|---|
| `public_id` | string | *verplicht* | Publieke UUID of numeriek id van de call |
| `max_chars` | int | 6000 | Hoeveel tekst je terugkrijgt (1000–20000) |

Uitvoer: `call` (`public_id`, `title`, `source`, `status`, `deadline`, `url`, `apply_url`,
`tracked_url`), `page` (`url`, `final_url`, `http_status`, `content_type`, `bytes`, `title`, `text`,
`truncated`) en een `note` die zegt dat je de status uit de paginatekst moet halen.

Lukt het lezen niet, dan zegt de tool dat — met de reden en de URL. Dat is expres: een agent hoort
"Ik kon de pagina niet lezen" te melden in plaats van een status te verzinnen. Drie gevallen:

| Geval | Wat je terugkrijgt |
|---|---|
| De site weigert scripts (401/403/405/406/429) | *"the site refused the request (HTTP 403) … Open <url> in a browser"* |
| De pagina bouwt zich met JavaScript (geen leesbare tekst) | *"returned no readable text … Open it in a browser"* |
| Geen HTML (PDF), time-out of onbereikbare host | De reden, met de URL of de tijdlimiet |

De fetcher is bewust bescheiden: één GET, geen cookies, geen JavaScript, een herkenbare
User-Agent, maximaal 400 kB en standaard 20 seconden. Pagina's achter een browser-muur vallen dus
af — dat is de grens van deze tool, niet een fout die je moet omzeilen.

Interne adressen worden geweigerd (loopback, private en link-local, dus ook
`169.254.169.254`), en redirects worden hop voor hop gevolgd met dezelfde controle. Reden: de URL
komt uit een databaserecord en `call_document_url` wordt door het model uit paginacontent gehaald,
dus dat is geen invoer die wij bepalen — zonder die grens zou deze tool een weg naar binnen zijn.

---

## `calls_for_research_group`

De calls die de pijplijn aan één lectoraat matchte, met de reden en de mailstatus — de tool voor
"wat is er voor ons relevant".

| Parameter | Type | Default | Betekenis |
|---|---|---|---|
| `research_group` | string | *verplicht* | Slug of naam, bv. `green-health` |
| `include_closed` | bool | `false` | Sluit gesloten calls standaard uit |
| `limit` / `offset` | int | 20 / 0 | Paginering |

Onbekende slug → fout, niet "nul resultaten": dat verschil is expres, anders kun je een typefout
niet onderscheiden van een leeg lectoraat. Elke match bevat het `public_id` van de call (voor
`get_call` of een deep link), `match_reason`, `email_sent` en `notified_at`.

---

## `list_sources`

De gescrapete fondsen met aantallen. `active_only` staat standaard aan. Per bron: `id`, `name`,
`short_name`, `website_url`, `funder_type`, `active`, `link_status`, `last_checked_at`,
`scrape_priority` en `call_count`. `total_calls` telt de calls van de opgehaalde bronnen.

---

## `list_research_groups`

Alle lectoraten met aantallen; gebruik dit om slugs te vinden voor de andere tools. Per groep:
`id`, `slug`, `name`, `short_description`, `homepage_url`, `contact_email`, `is_internal`,
`is_professorship` (true voor de lectoraten), `member_count`, `theme_count`, `call_count` (alle
matches, inclusief gesloten) en `matched_open_calls` (alleen niet-gesloten). `contact_email` is
een persoonsgegeven; gebruik het alleen waar dat de bedoeling is.

---

## `funding_stats`

| Parameter | Type | Default | Betekenis |
|---|---|---|---|
| `group_by` | string | `source` | `source`, `month`, `status`, `language`, `funding_type`, `focus_area` of `research_group` |
| `months` | int | 12 | 1–120 |

Onbekende `group_by` geeft een fout met de toegestane lijst. Twee kanttekeningen: bij
`research_group` telt een call die aan meerdere lectoraten hangt net zo vaak mee (de buckets zijn
geen optelling van unieke calls), en `focus_area` is afgekapt op 200 buckets omdat die kolom ook
vrije tekst bevat. `counted_calls` is het aantal getelde rijen, niet het aantal unieke calls.

---

## URL's en kliks

Elke call en elke match komt met de URL's erbij:

| Veld | Wat het is | Waarvoor |
|---|---|---|
| `url` | De pagina van de fondsverstrekker | Lezen (`read_call_page`) of doorgeven aan een mens |
| `apply_url` | Het aanvraagformulier, als dat apart staat | Aanvragen |
| `call_document_url` | Het document of de flyer bij de call | Details |
| `tracked_url` | Dezelfde bestemming via `/api/click.php` van de applicatie | **Doorgeven aan mensen** |

`tracked_url` telt de klik en stuurt daarna door naar `url`. Zulke kliks komen in
`email_click_log` en verschijnen in de **Click Analytics** van de beheerpagina, met
`utm_source=mcp`, `utm_medium=agent`, `utm_content=call_link` (of `apply_link`) en het `user_id`
van het account waarvan het API-token is gebruikt. Zo zie je naast mail- en dashboardkliks ook
wat er via agents wordt doorgegeven — en door wie. Bij een call met een aparte aanvraagpagina
hoort `tracked_apply_url` bij `apply_url`.

Geef bij een mens dus `tracked_url` en bij een machine die de pagina gaat lezen `url`: de
klikteller is een omweg, geen inhoud.

---

## Prompts

Twee prompts, zodat een client ze als commando kan aanbieden in plaats van dat elke agent de
werkwijze opnieuw moet raden:

| Prompt | Argument | Wat hij doet |
|---|---|---|
| `vind_funding_voor_tekst` | `tekst` | De werkwijze voor "welke funding past bij dit plan": onderwerp uit de tekst halen, over assen sweepen, de lectoraatsmatches erbij, de bronpagina lezen, rapporteren in de vaste vorm |
| `rapporteer_calls` | `onderwerp` | De rapportvorm van dit huis: tabel per call, altijd de URL, drie blokken (nog te grijpen / gesloten / bewust niet meegenomen), en benoemen wat niet te verifieren was |

## Hoe deze server zich aan de spec houdt

- **Annotaties kloppen.** Alle tools zijn `readOnlyHint` en niet-destructief; de databasetools zijn
  `idempotentHint` met `openWorldHint: false` (een database die wij beheren), en `read_call_page`
  juist `openWorldHint: true` met `idempotentHint: false` - die haalt een levende pagina op.
- **Een kanaal per antwoord.** Tools geven een JSON-object terug dat de SDK als tekstblok doorgeeft;
  er gaat geen afwijkende `structuredContent` naast, want clients die beide kanalen krijgen laten
  het tekstblok stil vallen (bekende voetangel). Geen `outputSchema`: de rijen spiegelen de API met
  ~40 velden, en een schema zou dat dupliceren zonder dat een client er iets aan heeft.
- **Zeven tools**, binnen de 5-15 die de praktijk aanhoudt, met specifieke namen in plaats van
  generieke.
- **Context is schaars:** `brief` om te scannen, `limit`/`offset` met `has_more`, en
  `searched_terms` zodat een agent ziet wat er werkelijk gezocht is.
- **Fouten komen als `isError` met een actionable melding** (bv. "maak een nieuw token aan onder
  Settings - API access"), zodat de agent kan bijsturen.
- **Niets naar stdout behalve het protocol** (gecontroleerd), geen credentials in logs, en de
  server draait over stdio.

## Resource `fundingradar://call/{public_id}`

Zelfde inhoud als `get_call`, als JSON, voor clients die resources lezen. Bij een onbekende UUID
geeft de resource een generieke protocolfout; gebruik dan de tool `get_call` voor een duidelijke
melding.

---

## Kanttekeningen in de data (gemeten op 2026-10-09)

Dit is wat de productiedatabase op dat moment bevatte. Geen conclusies over kwaliteit, wel dingen
die je moet weten voordat je een getal als antwoord geeft. Let op: de aantallen hieronder komen
uit directe SQL-tellingen; via de API liggen ze iets lager door de twee standaardfilters
hierboven.

| Observatie | Aantal |
|---|---|
| Totaal calls | 4.452 |
| Waarvan `closed` / `open` / `upcoming` / `intake_closed` / zonder status | 3.770 / 346 / 313 / 16 / 7 |
| Calls met een deadline in de toekomst | 590 |
| Calls zonder enkele lectoraat-match | 3.107 |
| Matches totaal (`call_research_group`) | 5.745 |
| Bronnen (actief / totaal) | 31 / 32 |
| Lectoraten | 12 |
| Actieve thema's | 21 |

- `focus_areas` bevat de drie canonieke waarden (`onderzoek` 1.778, `zakelijke_dienstverlening`
  669, `onderwijs` 139) plus veel vrije tekst. Filteren op iets anders dan die drie is daarom
  geblokkeerd in plaats van stil verkeerd.
- `funding_type` is vrije tekst (3.227× `grant`, 1.036× leeg, daarna een lange staart met onder
  meer `subsidie` naast `subsidy`).
- `language` is bij 1.119 calls leeg.
- `urgency` hangt volledig samen met de deadline — EXPIRED = 2.264 calls met een verstreken
  deadline (exact), HOT (109) en WATCH (84) zijn calls met een nabije deadline, PLAN (1.995) is de
  rest. Het is een label van de applicatie, geen eigenschap van de call.
- Alle calls zijn aangemaakt tussen 2026-04 en 2026-10; een `months`-venster van 24 maanden dekt
  in de praktijk dus alles.
- De cijfers lopen mee met de dagelijkse scrape; dit is een momentopname.
