# Tools

Zes read-only tools plus één resource. Ze praten met de FundingRadar-API (`api/v1`), dus de
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

**Twee standaardfilters van de applicatie** verklaren waarom dit minder rijen kan geven dan een
ruwe telling in de database: calls die de pijplijn als niet-passend voor HAS classificeerde, en
calls met een deadline vóór 2020, zijn verborgen. `include_ineligible=True` toont de eerste
groep; een expliciete `deadline_after` vervangt de 2020-grens.

---

## `search_calls`

| Parameter | Type | Default | Betekenis |
|---|---|---|---|
| `query` | string | — | Vrije tekst in titel en beschrijving |
| `source` | string | — | Bron op short name óf volledige naam, bv. `ZonMw`, `RVO`, `EU Portal` (onbekend → fout) |
| `research_group` | string | — | Lectoraat-slug of naam; alleen calls die de pijplijn daaraan matchte |
| `focus_area` | string | — | Eén van `onderzoek`, `onderwijs`, `zakelijke_dienstverlening`; andere waarden worden geweigerd |
| `status` | string | — | `open`, `upcoming`, `closed` of `intake_closed` |
| `funding_type` | string | — | Exact; in de praktijk bijna altijd `grant` |
| `language` | string | — | `nl` of `en` |
| `deadline_after` / `deadline_before` | date | — | Deadline op of na / op of vóór deze datum |
| `sort_by` | string | `deadline_asc` | `deadline_asc`, `deadline_strict`, `deadline_desc`, `created_at_desc`, `created_at_asc`, `title_asc`, `title_desc`, `budget_desc`, `budget_asc`, `status_priority` |
| `include_ineligible` | bool | `false` | Ook calls tonen die als niet-passend voor HAS zijn geclassificeerd |
| `limit` / `offset` | int | 20 / 0 | Paginering |

Sortering standaard: dichtstbijzijnde deadline eerst, zonder deadline achteraan. Zonder `status`
of `deadline_after` krijg je ook de gesloten calls (in productie het grootste deel).

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
