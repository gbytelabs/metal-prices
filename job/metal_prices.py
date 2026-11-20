#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Daily gold and silver price per gram per currency, one JSON file.

Published source: fawazahmed0 currency-api (CC0). Before anything is written
the day is checked: the USD metal price against gold-api.com (fallbacks: NBP
for gold, the last published file for silver), each currency's FX against
open.er-api.com, and the TRY side against TCMB EVDS when a key and series codes
are there. A day that fails a check writes nothing: the last good file stays
and the run exits 1. EVDS missing, down or without data never stops the day;
an EVDS gap beyond its limit stops it only while `evds.enforce` is true.
Standard library only.

    python3 metal_prices.py publish --site <published repo root>
    python3 metal_prices.py diagnose-evds     # EVDS gold/silver series, latest values

The EVDS key comes only from the environment variable EVDS_API_KEY. It is sent
as the `key` request header (never in a URL) and is redacted from every line
this script prints.
"""

import argparse
import datetime as dt
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
OUTPUT = Path("metal") / "latest.json"
SCHEMA = 1
GRAMS_PER_OUNCE = 31.1034768
USER_AGENT = "metal-prices-job/1"
KEY_ENV = "EVDS_API_KEY"
SOURCE = "fawazahmed0 currency-api"
EVDS_NAME = "TCMB EVDS"
DIAGNOSE_MATCH = r"alt[ıi]n|g[üu]m[üu][şs]|k[ıi]ymetli|gold|silver|precious"
SECRET_FIELD = re.compile(r"key|token|secret|pass", re.IGNORECASE)

_secrets: list[str] = []


def register_secret(value):
    if value:
        _secrets.append(value)


def redact(text):
    for s in _secrets:
        text = text.replace(s, "***")
    return text


def log(message=""):
    print(redact(str(message)), flush=True)


class FetchError(Exception):
    """A call that brought no body; `not_found` is HTTP 404."""

    def __init__(self, message, not_found=False):
        super().__init__(message)
        self.not_found = not_found


class Refused(Exception):
    """A check failed or could not be made: the day publishes nothing new."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_get(url, headers=None, follow_redirects=True, timeout=25):
    """GET `url`; the error text names the status or reason, never headers."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    handlers = [] if follow_redirects else [_NoRedirect]
    try:
        with urllib.request.build_opener(*handlers).open(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}", not_found=e.code == 404) from None
    except OSError as e:
        raise FetchError(f"{type(e).__name__}: {getattr(e, 'reason', e)}") from None


def _positive(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0


def gap(a, b):
    """|a - b| over the mean of the two."""
    return abs(a - b) / ((a + b) / 2)


def _json(body, what):
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise ValueError(f"{what}: not JSON") from None


def _round(v):
    return float(f"{v:.8g}")


def _pct(v):
    return round(v * 100, 2)


def load_config(path=CONFIG_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── published source ─────────────────────────────────────────────────────────


def parse_fawaz(body, metal):
    """`(date, {CUR: units per ounce})` from one fawazahmed0 file."""
    doc = _json(body, metal)
    rates = doc.get(metal) if isinstance(doc, dict) else None
    if not isinstance(rates, dict) or not isinstance(doc.get("date"), str):
        raise ValueError(f"{metal}: no '{metal}' table or no date")
    if not _positive(rates.get("usd")):
        raise ValueError(f"{metal}: no positive USD value")
    return doc["date"], {k.upper(): v for k, v in rates.items() if _positive(v)}


def load_fawaz(fetch, cfg, day, metal):
    """Today's table for `metal` (xau | xag), or None while no mirror has the
    day yet. A broken file, with no good mirror beside it, refuses the day."""
    ver = f"{day.year}.{day.month}.{day.day}"
    broken = []
    for tpl in cfg["urls"]["fawaz"]:
        url = tpl.format(ver=ver, date=day.isoformat(), metal=metal)
        try:
            date, rates = parse_fawaz(fetch(url), metal)
        except FetchError as e:
            log(f"  not available: {url} ({e})")
            continue
        except ValueError as e:
            log(f"  broken answer: {url} ({e})")
            broken.append(str(e))
            continue
        if date != day.isoformat():
            log(f"  not available: {url} (file dated {date})")
            continue
        return rates
    if broken:
        raise Refused(f"fawazahmed0 answered with a broken file: {'; '.join(broken)}")
    return None


# ── metal referees ───────────────────────────────────────────────────────────


def gold_api(fetch, cfg, symbol):
    """USD per ounce from gold-api.com, or None when it gives no usable answer."""
    url = cfg["urls"]["gold_api"].format(symbol=symbol)
    try:
        doc = _json(fetch(url), url)
        if not isinstance(doc, dict) or not _positive(doc.get("price")):
            raise ValueError(f"{url}: no positive price")
        if doc.get("symbol", symbol) != symbol or doc.get("currency", "USD") != "USD":
            raise ValueError(f"{url}: not a USD {symbol} price")
        return doc["price"]
    except (FetchError, ValueError) as e:
        log(f"  referee gold-api.com {symbol} unavailable: {e}")
        return None


def nbp_gold(fetch, cfg, day):
    """`(USD per ounce, date)` from NBP's gold (PLN/g) and USD rate, or None."""
    urls = cfg["urls"]
    try:
        gold = _json(fetch(urls["nbp_gold"]), "NBP gold")[0]
        rate = _json(fetch(urls["nbp_usd"]), "NBP USD")["rates"][0]
        pln_per_gram, pln_per_usd = gold["cena"], rate["mid"]
        if not (_positive(pln_per_gram) and _positive(pln_per_usd)):
            raise ValueError("no positive values")
        oldest = day - dt.timedelta(days=cfg["nbp_max_age_days"])
        dates = [dt.date.fromisoformat(gold["data"]), dt.date.fromisoformat(rate["effectiveDate"])]
        if not all(oldest <= d <= day for d in dates):
            raise ValueError(f"values dated {dates[0]} / {dates[1]}, outside {oldest}..{day}")
        return pln_per_gram / pln_per_usd * GRAMS_PER_OUNCE, dates[0].isoformat()
    except (FetchError, ValueError, KeyError, IndexError, TypeError) as e:
        log(f"  fallback referee NBP unavailable: {e}")
        return None


def _compare(metal, ours, ref, referee, limit):
    g = gap(ours, ref)
    log(f"  {metal}: {ours:.4f} vs {ref:.4f} USD/oz ({referee}): gap {g:.2%}, limit {limit:.0%}")
    if g > limit:
        raise Refused(f"{metal} differs from {referee} by {g:.2%} (limit {limit:.0%})")
    return {"referee": referee, "gap_pct": _pct(g), "limit_pct": _pct(limit)}


def check_metals(fetch, cfg, day, gold_usd, silver_usd, previous):
    """The metal check record; Refused when a metal is off or has no referee."""
    limits = cfg["max_gap"]
    ref = gold_api(fetch, cfg, "XAU")
    if ref is not None:
        gold = _compare("gold", gold_usd, ref, "gold-api.com", limits["gold"])
    else:
        nbp = nbp_gold(fetch, cfg, day)
        if nbp is None:
            raise Refused("gold: no referee answered (gold-api.com, NBP)")
        gold = _compare("gold", gold_usd, nbp[0], "NBP", limits["gold_fallback_nbp"])
        gold["referee_date"] = nbp[1]
    ref = gold_api(fetch, cfg, "XAG")
    if ref is not None:
        silver = _compare("silver", silver_usd, ref, "gold-api.com", limits["silver"])
    else:
        if previous is None:
            raise Refused("silver: gold-api.com unavailable and no previous file to compare with")
        prev = previous["prices"]["USD"]["silver"] * GRAMS_PER_OUNCE
        silver = _compare(
            "silver", silver_usd, prev, f"previous file {previous['date']}",
            limits["silver_fallback_previous"],
        )
    return {"gold": gold, "silver": silver}


# ── FX referee and the price table ───────────────────────────────────────────


def load_fx(fetch, cfg):
    url = cfg["urls"]["fx"]
    try:
        doc = _json(fetch(url), url)
        if not isinstance(doc, dict) or doc.get("result") != "success" or doc.get("base_code") != "USD":
            raise ValueError(f"{url}: not a successful USD table")
        if not isinstance(doc.get("rates"), dict):
            raise ValueError(f"{url}: no rates")
        return doc["rates"]
    except (FetchError, ValueError) as e:
        raise Refused(f"FX referee open.er-api.com unavailable: {e}") from None


def build_prices(cfg, xau, xag, fx):
    """`(prices, dropped, worst FX gap)`. A currency whose FX, implied by the
    published metal tables, differs from the FX referee is left out today."""
    limit = cfg["max_gap"]["fx"]
    prices, dropped, worst = {}, {}, 0.0
    for cur in cfg["currencies"]:
        if cur in cfg["excluded"]:
            continue
        au, ag, ref = xau.get(cur), xag.get(cur), fx.get(cur)
        if not (_positive(au) and _positive(ag)):
            dropped[cur] = "missing in the published source today"
            continue
        if not _positive(ref):
            dropped[cur] = "missing at the FX referee today"
            continue
        g = max(gap(au / xau["USD"], ref), gap(ag / xag["USD"], ref))
        if g > limit:
            dropped[cur] = f"FX differs from open.er-api.com by {g:.2%} today"
            continue
        worst = max(worst, g)
        prices[cur] = {"gold": _round(au / GRAMS_PER_OUNCE), "silver": _round(ag / GRAMS_PER_OUNCE)}
    for cur, why in dropped.items():
        log(f"  {cur} left out: {why}")
    if "USD" not in prices:
        raise Refused("USD did not pass")
    return prices, dropped, worst


# ── TCMB EVDS (TRY side, optional) ───────────────────────────────────────────


def evds_series_url(base, code, start, end):
    return f"{base}series={code}&startDate={start:%d-%m-%Y}&endDate={end:%d-%m-%Y}&type=json"


def fetch_evds(fetch, ecfg, code, day, key):
    """The raw answer of one series over the lookback window up to `day`."""
    start = day - dt.timedelta(days=ecfg["lookback_days"])
    return fetch(evds_series_url(ecfg["base_url"], code, start, day), headers={"key": key}, follow_redirects=False)


def evds_items(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
        raise ValueError("no item list")
    return doc["items"]


def latest_evds_value(items, code, day):
    """`(date, value)` of the newest non-empty value up to `day`, or None."""
    field = code.replace(".", "_")
    best = None
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = item.get(field, item.get(code))
        try:
            value = float(raw)
            date = dt.datetime.strptime(item["Tarih"], "%d-%m-%Y").date()
        except (TypeError, ValueError, KeyError):
            continue
        if _positive(value) and date <= day and (best is None or date > best[0]):
            best = (date, value)
    return best


def compare_evds(s, date, value, ours, label=""):
    """The per-series record of one EVDS value against our TRY price per gram."""
    ref = value / s["grams_per_unit"]
    g = gap(ours, ref)
    inside = g <= s["max_gap"]
    log(
        f"  TRY {s['metal']}{label}: {ours:.2f} vs {ref:.2f} TRY/g ({EVDS_NAME} {s['code']}, {date}): "
        f"gap {g:.2%}, limit {s['max_gap']:.0%}{'' if inside else ', BEYOND the limit'}"
    )
    return {
        "code": s["code"], "metal": s["metal"], "date": date.isoformat(), "try_per_gram": _round(ref),
        "gap_pct": _pct(g), "limit_pct": _pct(s["max_gap"]), "within_limit": inside,
    }


def check_evds(fetch, cfg, day, try_prices, key):
    """The EVDS record. Raises Refused only when `evds.enforce` is true and a
    value it did read is beyond its limit; anything else is a status."""
    ecfg = cfg["evds"]
    series = [s for s in ecfg["series"] if s.get("code")]
    if not series:
        return {"status": "not_configured"}
    if not key:
        return {"status": "no_key"}
    if try_prices is None:
        return {"status": "try_not_published"}
    compared, failed = [], 0
    for s in series:
        try:
            items = evds_items(_json(fetch_evds(fetch, ecfg, s["code"], day, key), s["code"]))
        except Exception as e:  # an optional referee: any failure is "unavailable"
            log(f"  {EVDS_NAME} {s['code']} unavailable: {type(e).__name__}: {e}")
            failed += 1
            continue
        found = latest_evds_value(items, s["code"], day)
        if found is None:
            log(f"  {EVDS_NAME} {s['code']}: no value in the last {ecfg['lookback_days']} days")
            continue
        compared.append(compare_evds(s, *found, try_prices[s["metal"]]))
    if not compared:
        return {"status": "unavailable" if failed else "no_data"}
    beyond = [c for c in compared if not c["within_limit"]]
    if not beyond:
        return {"status": "checked", "series": compared}
    if ecfg.get("enforce", False):
        c = beyond[0]
        raise Refused(f"TRY {c['metal']} differs from {EVDS_NAME} {c['code']} ({c['date']}) by {c['gap_pct']:.2f}%")
    log(f"  {EVDS_NAME} diverged beyond its limit; not enforced (evds.enforce is false): evds_checked is false")
    return {"status": "diverged_not_enforced", "series": compared}


# ── the run ──────────────────────────────────────────────────────────────────


def read_previous(path):
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        dt.date.fromisoformat(doc["date"])
        if not _positive(doc["prices"]["USD"]["silver"]):
            raise ValueError("no USD silver")
        return doc
    except FileNotFoundError:
        return None
    except (ValueError, KeyError, TypeError) as e:
        log(f"  previous file unreadable, ignored: {e}")
        return None


def publish(site, cfg, fetch, now, key):
    """0 = written, already current or not yet available; 1 = refused."""
    register_secret(key)
    day = now.date()
    path = Path(site) / OUTPUT
    previous = read_previous(path)
    if previous and previous["date"] >= day.isoformat():
        log(f"{OUTPUT.as_posix()} already holds {previous['date']}: nothing to do")
        return 0
    log(f"building {day} ({now:%H:%M} UTC)")
    try:
        xau = load_fawaz(fetch, cfg, day, "xau")
        xag = load_fawaz(fetch, cfg, day, "xag") if xau is not None else None
        if xau is None or xag is None:
            if now.hour >= cfg["final_hour_utc"]:
                raise Refused(f"no fawazahmed0 file for {day} by {cfg['final_hour_utc']}:00 UTC")
            log(f"fawazahmed0 has not published {day} yet; a later run tries again")
            return 0
        metal = check_metals(fetch, cfg, day, xau["USD"], xag["USD"], previous)
        fx = load_fx(fetch, cfg)
        prices, dropped, fx_worst = build_prices(cfg, xau, xag, fx)
        evds = check_evds(fetch, cfg, day, prices.get("TRY"), key)
    except Refused as e:
        kept = previous["date"] if previous else "no file"
        log(f"REFUSED: {e}. {OUTPUT.as_posix()} is left as it was ({kept}).")
        return 1
    checked_against = sorted({m["referee"] for m in metal.values() if not m["referee"].startswith("previous")})
    checked_against.append("open.er-api.com")
    if evds["status"] == "checked":
        checked_against.append(EVDS_NAME)
    doc = {
        "schema": SCHEMA,
        "date": day.isoformat(),
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": SOURCE,
        "checked_against": checked_against,
        "evds_checked": evds["status"] == "checked",
        "unit": "price of 1 gram of fine gold / fine silver in the currency",
        "prices": prices,
        "not_published": {**{c: f"excluded: {why}" for c, why in cfg["excluded"].items()}, **dropped},
        "checks": {
            "metal": metal,
            "fx": {"referee": "open.er-api.com", "max_gap_pct": _pct(fx_worst),
                   "limit_pct": _pct(cfg["max_gap"]["fx"])},
            "evds": evds,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    log(f"wrote {OUTPUT.as_posix()}: {day}, {len(prices)} currencies, EVDS {evds['status']}")
    return 0


def _describe(value, field=""):
    """Type of `value`; for a scalar not named like a secret, its start too."""
    kind = type(value).__name__
    if isinstance(value, (dict, list)):
        return f"{kind}[{len(value)}]"
    if SECRET_FIELD.search(field):
        return f"{kind} (value not shown)"
    return f"{kind} {redact(str(value))[:40]!r}"


def answer_shape(doc):
    """Lines describing an answer: its keys and types, and the first item only."""
    lines = [f"answer: {_describe(doc)}"]
    first = None
    if isinstance(doc, dict):
        lines += [f"  {k}: {_describe(v, str(k))}" for k, v in doc.items()]
        first = next((v[0] for v in doc.values() if isinstance(v, list) and v), None)
    elif isinstance(doc, list) and doc:
        first = doc[0]
    if isinstance(first, dict):
        lines.append("first item:")
        lines += [f"  {k}: {_describe(v, str(k))}" for k, v in first.items()]
    elif first is not None:
        lines.append(f"first item: {_describe(first)}")
    return lines


def diagnose_values(fetch, cfg, key, now, pause):
    """Print each configured series' latest value and, when the source has
    today's file, its gap against that TRY price. Nothing is written. True
    when every configured series gave a value."""
    ecfg, day = cfg["evds"], now.date()
    series = [s for s in ecfg["series"] if s.get("code")]
    if not series:
        log("\nno series code in config.json: no values to read")
        return True
    log(f"\nlatest values, {day - dt.timedelta(days=ecfg['lookback_days'])}..{day}:")
    ours = None
    try:
        xau = load_fawaz(fetch, cfg, day, "xau")
        xag = load_fawaz(fetch, cfg, day, "xag") if xau is not None else None
        if xau and xag and _positive(xau.get("TRY")) and _positive(xag.get("TRY")):
            ours = {"gold": xau["TRY"] / GRAMS_PER_OUNCE, "silver": xag["TRY"] / GRAMS_PER_OUNCE}
    except Refused as e:
        log(f"  {e}")
    if ours is None:
        log(f"  no TRY price from {SOURCE} for {day} in this run: no gap computed")
    ok = True
    for s in series:
        code = s["code"]
        try:
            body = fetch_evds(fetch, ecfg, code, day, key)
        except Exception as e:
            log(f"  {code}: unavailable: {type(e).__name__}: {e}")
            ok = False
            continue
        finally:
            time.sleep(pause)
        try:
            doc = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            text = redact(body.decode("utf-8", "replace"))
            log(f"  {code}: answer is not JSON, {len(body)} bytes, starts {text[:120]!r}")
            ok = False
            continue
        try:
            found = latest_evds_value(evds_items(doc), code, day)
        except ValueError:
            found = None
        if found is None:
            log(f"  {code}: no value read; the answer's shape, for fixing the parser:")
            for line in answer_shape(doc):
                log(f"    {line}")
            ok = False
            continue
        date, value = found
        log(f"  {code}: {value:.2f} per {s['grams_per_unit']} g on {date} = {value / s['grams_per_unit']:.2f} TRY/g")
        if ours is not None:
            compare_evds(s, date, value, ours[s["metal"]], label=f" ({SOURCE} {day}, unchecked)")
    return ok


def diagnose_evds(fetch, cfg, key, pattern=DIAGNOSE_MATCH, pause=0.5, now=None):
    """Print EVDS data groups whose name matches `pattern` and their series,
    then the configured series' latest values. Never writes a file."""
    register_secret(key)
    if not key:
        log(f"{KEY_ENV} is not set: nothing to diagnose")
        return 2
    groups = list_evds_groups(fetch, cfg, key, pattern, pause)
    values_ok = diagnose_values(fetch, cfg, key, now or dt.datetime.now(dt.timezone.utc), pause)
    return 0 if groups and values_ok else 1


def list_evds_groups(fetch, cfg, key, pattern, pause):
    """The matching data groups, each printed with its series."""
    base, rx = cfg["evds"]["base_url"], re.compile(pattern, re.IGNORECASE)

    def get(path):
        doc = _json(fetch(base + path, headers={"key": key}, follow_redirects=False), path)
        time.sleep(pause)
        if not isinstance(doc, list):
            raise ValueError(f"{path}: not a list")
        return doc

    try:
        categories = get("categories/type=json")
    except Exception as e:
        log(f"categories unavailable: {type(e).__name__}: {e}")
        return []
    groups = []
    for c in categories:
        try:
            cid = int(c["CATEGORY_ID"])
            found = get(f"datagroups/mode=2&code={cid}&type=json")
        except Exception as e:
            log(f"category {c}: {type(e).__name__}: {e}")
            continue
        for g in found:
            name = f"{g.get('DATAGROUP_NAME', '')} {g.get('DATAGROUP_NAME_ENG', '')}"
            if rx.search(name):
                groups.append(g)
    for g in groups:
        code = g.get("DATAGROUP_CODE")
        log(f"\n[{code}] {g.get('DATAGROUP_NAME')} | source: {g.get('DATASOURCE')} | {g.get('FREQUENCY_STR')}")
        try:
            series = get(f"serieList/type=json&code={code}")
        except Exception as e:
            log(f"  series list unavailable: {type(e).__name__}: {e}")
            continue
        for s in series:
            log(
                f"  {s.get('SERIE_CODE')} | {s.get('SERIE_NAME')} | {s.get('FREQUENCY_STR')} | "
                f"{s.get('START_DATE')}..{s.get('END_DATE')}"
            )
    log(f"\n{len(groups)} data group(s) matched /{pattern}/")
    return groups


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", default=str(CONFIG_PATH))
    sub = p.add_subparsers(dest="mode", required=True)
    pub = sub.add_parser("publish", help="build and write metal/latest.json")
    pub.add_argument("--site", default=".", help="root of the published repository")
    pub.add_argument("--now", help="UTC time to act as, ISO 8601 (default: the clock)")
    diag = sub.add_parser("diagnose-evds", help="list EVDS gold/silver series and read the configured ones (needs the key)")
    diag.add_argument("--match", default=DIAGNOSE_MATCH)
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    key = os.environ.get(KEY_ENV, "").strip()
    if args.mode == "diagnose-evds":
        return diagnose_evds(http_get, cfg, key, pattern=args.match)
    now = dt.datetime.now(dt.timezone.utc)
    if args.now:
        now = dt.datetime.fromisoformat(args.now)
        now = now.replace(tzinfo=dt.timezone.utc) if now.tzinfo is None else now.astimezone(dt.timezone.utc)
    return publish(args.site, cfg, http_get, now, key)


if __name__ == "__main__":
    sys.exit(main())
