# metal-prices

One JSON file with the price of one gram of gold and one gram of silver in 52
currencies. A scheduled job checks and rebuilds it once a day. A zakat calculator
reads it to suggest a nisab price. The user can always type their own local price.

```
https://gbytelabs.github.io/metal-prices/metal/latest.json
```

## What is in the file

```json
{
 "schema": 1,
 "date": "2026-11-20",
 "generated_at": "2026-11-20T07:17:41Z",
 "source": "fawazahmed0 currency-api",
 "checked_against": ["gold-api.com", "open.er-api.com", "TCMB EVDS"],
 "evds_checked": true,
 "unit": "price of 1 gram of fine gold / fine silver in the currency",
 "prices": {
  "TRY": {"gold": 5769.6999, "silver": 68.135517},
  "USD": {"gold": 114.14903, "silver": 1.3480082}
 },
 "not_published": {"IRR": "excluded: ...", "SDG": "excluded: ...", "SYP": "excluded: ..."},
 "checks": {"metal": {...}, "fx": {...}, "evds": {...}}
}
```

| field | meaning |
|---|---|
| `schema` | Contract version, an integer. Today it is `1`. A change that breaks readers gets a new number. |
| `date` | The UTC calendar day of the data (`YYYY-MM-DD`). This is the date of the source's daily file, not the time the job ran. A day that fails its checks is not written, so the file keeps an older `date`. Readers should show this date or compare it with today to judge how old the price is. |
| `generated_at` | When the job wrote the file (UTC, ISO 8601). |
| `source` | Where the published numbers come from. |
| `checked_against` | The independent sources the day was checked against. |
| `evds_checked` | `true` only when at least one TCMB EVDS series gave a value that day and the TRY price was within the limit of every series that gave one. `"TCMB EVDS"` then appears in `checked_against`. In every other case it is `false`: no series answered, or one of them was beyond its limit (see `checks.evds` below). |
| `unit` | Always the same: the price of 1 gram of fine (pure) metal. |
| `prices` | ISO 4217 code → `gold` and `silver`. Each value is a positive decimal in that currency per gram. A currency that is not in `prices` has no price that day. |
| `not_published` | Currencies left out, with the reason. Some are left out permanently (`excluded: ...`), others only for one day. |
| `checks` | The measured gaps behind the day's decision, as a percentage of the mean of the two values (`gap_pct`), with each limit (`limit_pct`). This is for transparency. Readers do not need it. |

Prices are rounded to 8 significant digits. Unknown fields may be added within
schema 1. Readers should ignore fields they do not know.

## Sources and licences

| role | source | licence / terms |
|---|---|---|
| published numbers | [fawazahmed0 currency-api](https://github.com/fawazahmed0/exchange-api) (daily `xau` and `xag` files) | CC0-1.0 |
| check: gold and silver in USD | [gold-api.com](https://gold-api.com) | its [terms](https://gold-api.com/terms) permit commercial use |
| check: gold in USD when gold-api.com does not answer | [NBP, Narodowy Bank Polski](https://api.nbp.pl/en.html) gold price and USD rate | public API of the central bank of Poland |
| check: exchange rate of each currency | [ExchangeRate-API open access](https://www.exchangerate-api.com/docs/free) (open.er-api.com) | used only as a check; its rates are not republished |
| check: TRY gold and silver | **TCMB EVDS**, the Electronic Data Delivery System of the Central Bank of the Republic of Türkiye. The series used are the Borsa İstanbul gold and silver closing prices (`TP.ALTINPIYASA.KAP02`, TL/kg, and `TP.GUMUSPIYASA.KAP05`, TL/g), published there with Borsa İstanbul as data source. | EVDS terms: data may be used and published with the source cited; it is not passed on to users as a charge |

Source of the check: TCMB EVDS (evds3.tcmb.gov.tr), on the days where
`evds_checked` is `true`.

## Licence and terms of use

The code and files of this repository are under the MIT License (see `LICENSE`).

The published numbers are computed from the daily files of the fawazahmed0
currency-api, which are under CC0-1.0. The other sources in the table above are
used only to check a day. Their data is not republished. Each source keeps its own
terms, as listed in that table.

Nothing in this repository gives permission to use the file or the code for a
purpose that is against the law, or against the terms of the sources named above.
Whoever uses it is responsible for the laws that apply to them.

## How a day is checked

- The job reads the source's file for the current UTC day and computes gram price = price per troy ounce ÷ 31.1034768.
- The USD gold price must be within 2 % of gold-api.com, and the USD silver price within 4 %. If gold-api.com does not answer, gold is compared with NBP (3 %) and silver with the previous published file (5 %).
- For each currency, the exchange rate implied by the source must be within 2 % of open.er-api.com. Otherwise that currency is left out for the day.
- The TRY price is compared with the latest value of each TCMB EVDS series in the last 7 days: gold within 3 %, silver within 5 %. If EVDS is unreachable or has no value, the day is not stopped. The file then has `evds_checked: false`. A gap beyond the EVDS limit does not stop the day either, for now (see below).
- If any other check fails, nothing is written. The previous file stays and its `date` shows how old it is.
- IRR, SDG and SYP are never published, because the exchange-rate sources disagree on them by far more than 2 %.

### The EVDS check (`checks.evds`)

The Borsa İstanbul prices are domestic prices. They may carry a premium over the
international price converted to TRY. The size of that premium has not been
measured yet, so the EVDS limits are not enforced: a day where a series is beyond
its limit is still published, with `evds_checked: false` and the status
`diverged_not_enforced`. The gaps are recorded every day. Once they have been
read for some days, the limit may be enforced (`evds.enforce` in the job's
configuration). From then on, such a day is refused like any other failed check,
and the file keeps its older `date`.

| `checks.evds.status` | meaning | `evds_checked` |
|---|---|---|
| `checked` | At least one series gave a value, and every series that gave one is within its limit. | `true` |
| `diverged_not_enforced` | At least one series gave a value beyond its limit. The day was published because the limit is not enforced. | `false` |
| `no_data` | EVDS answered, but no series had a value in the last 7 days. | `false` |
| `unavailable` | No series gave a value, and at least one call to EVDS failed. | `false` |
| `no_key` | The run had no EVDS key. | `false` |
| `not_configured` | No EVDS series is configured. | `false` |
| `try_not_published` | TRY did not pass the day's exchange-rate check, so there was nothing to compare. | `false` |

With `checked` and `diverged_not_enforced`, `series` lists every series that
gave a value: `code`, `metal`, `date` (the date of the EVDS value, which can
be earlier than the file's `date`), `try_per_gram` (the EVDS value per gram),
`gap_pct`, `limit_pct` and `within_limit`. A series that gave no value is not
listed.

## No guarantee

The prices are indicative and come from third-party sources. They are provided
"as is", without any warranty of accuracy, completeness or availability. The
file may be late, old, or missing. Do not use it for trading. For a zakat
decision, check the price with a local jeweller or bank.
