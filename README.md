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
| `evds_checked` | `true` only when the TRY price was also compared with at least one TCMB EVDS series that day and was within the limit. `"TCMB EVDS"` then appears in `checked_against`. |
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
| check: TRY gold and silver | **TCMB EVDS**, the Electronic Data Delivery System of the Central Bank of the Republic of Türkiye. The series used are published there with Borsa İstanbul and the Ankara Chamber of Jewellers and Watchmakers as data sources. | EVDS terms: data may be used and published with the source cited; it is not passed on to users as a charge |

Source of the check: TCMB EVDS (evds3.tcmb.gov.tr), on the days where
`evds_checked` is `true`.

## How a day is checked

- The job reads the source's file for the current UTC day and computes gram price = price per troy ounce ÷ 31.1034768.
- The USD gold price must be within 2 % of gold-api.com, and the USD silver price within 4 %. If gold-api.com does not answer, gold is compared with NBP (3 %) and silver with the previous published file (5 %).
- For each currency, the exchange rate implied by the source must be within 2 % of open.er-api.com. Otherwise that currency is left out for the day.
- When a TCMB EVDS series has a value in the last 7 days, it must be within its limit of the TRY price. If EVDS is unreachable or has no value, the day is not stopped. The file then has `evds_checked: false`.
- If any check fails, nothing is written. The previous file stays and its `date` shows how old it is.
- IRR, SDG and SYP are never published, because the exchange-rate sources disagree on them by far more than 2 %.

## No guarantee

The prices are indicative and come from third-party sources. They are provided
"as is", without any warranty of accuracy, completeness or availability. The
file may be late, old, or missing. Do not use it for trading. For a zakat
decision, check the price with a local jeweller or bank.
