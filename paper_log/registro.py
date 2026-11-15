#!/usr/bin/env python3
"""Paper-only forecast log: Claude's probability vs the market's, then the outcome.

Append-only JSONL (registro.jsonl). Each line carries the SHA-256 of the previous
line, so a later edit or deletion of an old line breaks the chain (`verify`).
No trades are placed anywhere: "paper bets" are arithmetic on logged numbers.

Commands:
  add --file new.json      append forecasts (a JSON list of objects)
  resolve --id A-0001 --outcome 1 [--ret 1.25] --source "..."   (outcome: 1, 0 or void)
  due [--date YYYY-MM-DD]  open forecasts whose event date has passed
  open                     all open forecasts
  report                   write stato.md and print the summary
  verify                   check the hash chain
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "registro.jsonl")
REPORT = os.path.join(HERE, "stato.md")

# Rules fixed before the first forecast (see REGOLE.md). Do not tune after the fact.
EDGE_A = 0.10        # track A: paper bet when |p_claude - p_market| >= 10 points
COST_A = 0.02        # track A: 2 points of spread/fees per contract
EDGE_B = 0.10        # track B: paper position when |p_claude - 0.5| >= 10 points
COST_B = 0.15        # track B: 0.15% round-trip cost on the underlying, no leverage
MIN_CASES = 50       # no verdict on a track before 50 resolved, independent-ish cases
END_DATE = "2027-02-15"
CATEGORIES = {"banche_centrali", "macro", "trimestrali", "regolatorio", "altro"}
REQUIRED = ["track", "titolo", "question", "category", "cluster", "event_date",
            "p_claude", "blind", "p_market", "market_source", "rationale"]


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_lines(path=None):
    path = path or LOG
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln.rstrip("\n") for ln in f if ln.strip()]


def chain_hash(line):
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def append(record, path=None):
    path = path or LOG
    lines = read_lines(path)
    record["prev"] = chain_hash(lines[-1]) if lines else "genesis"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def load(path=None):
    """Return (forecasts by id, resolutions by id)."""
    fc, res = {}, {}
    for ln in read_lines(path):
        r = json.loads(ln)
        if r["type"] == "forecast":
            fc[r["id"]] = r
        elif r["type"] == "resolution":
            res[r["id"]] = r
    return fc, res


def verify(path=None):
    prev = "genesis"
    for i, ln in enumerate(read_lines(path), 1):
        if json.loads(ln).get("prev") != prev:
            return False, i
        prev = chain_hash(ln)
    return True, None


def validate(item, today):
    for k in REQUIRED:
        if k not in item:
            raise ValueError(f"missing field {k!r} in {item.get('titolo', item)}")
    if item["track"] not in ("A", "B"):
        raise ValueError("track must be A or B")
    if item["category"] not in CATEGORIES:
        raise ValueError(f"category must be one of {sorted(CATEGORIES)}")
    for k in ("p_claude", "p_market"):
        if not (0.01 <= float(item[k]) <= 0.99):
            raise ValueError(f"{k} must be between 0.01 and 0.99")
    ev = dt.date.fromisoformat(item["event_date"])
    if ev <= today:
        raise ValueError(f"event_date {ev} is not in the future: forecasts are logged before the outcome")
    if item["track"] == "B":
        if abs(float(item["p_market"]) - 0.5) > 1e-9:
            raise ValueError("track B uses the coin-flip benchmark: p_market must be 0.5")
        if not item.get("asset"):
            raise ValueError("track B needs 'asset'")
    if not isinstance(item["blind"], bool):
        raise ValueError("blind must be true or false")


def cmd_add(args):
    with open(args.file, encoding="utf-8") as f:
        items = json.load(f)
    today = dt.datetime.now(dt.timezone.utc).date()
    for it in items:
        validate(it, today)
    fc, _ = load()
    open_keys = {(r["track"], r["cluster"]) for r in fc.values()}
    counters = {"A": 0, "B": 0}
    for r in fc.values():
        counters[r["track"]] = max(counters[r["track"]], int(r["id"].split("-")[1]))
    for it in items:
        key = (it["track"], it["cluster"])
        if key in open_keys:
            raise ValueError(f"cluster {key} already has a forecast: one question per event")
        open_keys.add(key)
    for it in items:
        counters[it["track"]] += 1
        rec = dict(it)
        rec.update(type="forecast", id=f"{it['track']}-{counters[it['track']]:04d}", ts=now(),
                   p_claude=round(float(it["p_claude"]), 4), p_market=round(float(it["p_market"]), 4))
        append(rec)
        print("added", rec["id"], rec["titolo"])


def cmd_resolve(args):
    fc, res = load()
    if args.id not in fc:
        sys.exit(f"unknown id {args.id}")
    if args.id in res:
        sys.exit(f"{args.id} is already resolved")
    if args.outcome not in ("1", "0", "void"):
        sys.exit("outcome must be 1, 0 or void")
    rec = {"type": "resolution", "id": args.id, "ts": now(), "source": args.source,
           "outcome": args.outcome if args.outcome == "void" else int(args.outcome)}
    if fc[args.id]["track"] == "B" and args.outcome != "void":
        if args.ret is None:
            sys.exit("track B needs --ret (percent move of the asset, close to close)")
        if (args.ret > 0) != (rec["outcome"] == 1):
            sys.exit("--ret sign and outcome disagree")
        rec["ret_pct"] = args.ret
    append(rec)
    print("resolved", args.id, rec["outcome"])


def cmd_due(args):
    day = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(dt.timezone.utc).date()
    fc, res = load()
    for i, r in sorted(fc.items()):
        if i not in res and dt.date.fromisoformat(r["event_date"]) <= day:
            print(json.dumps({k: r.get(k) for k in ("id", "titolo", "question", "event_date",
                                                     "market_source", "asset", "reference")},
                             ensure_ascii=False))


def cmd_open(args):
    fc, res = load()
    for i, r in sorted(fc.items()):
        if i not in res:
            print(i, r["event_date"], r["cluster"], "|", r["titolo"])


# ---------- scoring ----------

def brier(p, o):
    return (p - o) ** 2


def paper_bet_a(p_c, p_m, o):
    """Track A: buy YES or NO at the market price plus cost when the gap is wide. P&L per contract."""
    if p_c - p_m >= EDGE_A:
        return o - (p_m + COST_A)
    if p_m - p_c >= EDGE_A:
        return (1 - o) - ((1 - p_m) + COST_A)
    return None


def paper_bet_b(p_c, ret_pct):
    """Track B: long or short the underlying, unlevered, when Claude is at least 60/40. P&L in %."""
    if p_c - 0.5 >= EDGE_B:
        return ret_pct - COST_B
    if 0.5 - p_c >= EDGE_B:
        return -ret_pct - COST_B
    return None


def cluster_bootstrap(rows, n=5000, seed=7):
    """90% interval for mean(Brier market - Brier Claude), resampling whole clusters."""
    by = {}
    for r in rows:
        by.setdefault(r["cluster"], []).append(r["bm"] - r["bc"])
    keys = sorted(by)
    if len(keys) < 2:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        vals = [v for k in rng.choices(keys, k=len(keys)) for v in by[k]]
        means.append(sum(vals) / len(vals))
    means.sort()
    return means[int(0.05 * n)], means[int(0.95 * n) - 1]


def stats(track, fc, res):
    rows, n_open, n_void, n_seen = [], 0, 0, 0
    for i, f in sorted(fc.items()):
        if f["track"] != track:
            continue
        r = res.get(i)
        if r is None:
            n_open += 1
            continue
        if r["outcome"] == "void":
            n_void += 1
            continue
        if not f["blind"]:
            n_seen += 1   # made after seeing the market price: listed, never scored
            continue
        o = r["outcome"]
        row = {"id": i, "cluster": f["cluster"], "o": o, "pc": f["p_claude"], "pm": f["p_market"],
               "bc": brier(f["p_claude"], o), "bm": brier(f["p_market"], o), "blind": f["blind"],
               "ret": r.get("ret_pct"), "titolo": f["titolo"]}
        row["bet"] = (paper_bet_a(row["pc"], row["pm"], o) if track == "A"
                      else paper_bet_b(row["pc"], row["ret"]))
        rows.append(row)
    s = {"track": track, "n": len(rows), "open": n_open, "void": n_void, "seen": n_seen, "rows": rows}
    if rows:
        s["brier_claude"] = sum(r["bc"] for r in rows) / len(rows)
        s["brier_market"] = sum(r["bm"] for r in rows) / len(rows)
        s["ci"] = cluster_bootstrap(rows)
        bets = [r["bet"] for r in rows if r["bet"] is not None]
        s["bets"] = len(bets)
        s["bets_won"] = sum(1 for b in bets if b > 0)
        s["pnl"] = sum(bets)
        if track == "B":
            s["hit"] = sum(1 for r in rows if (r["pc"] > 0.5) == (r["o"] == 1) and r["pc"] != 0.5)
            s["always_long"] = sum(r["ret"] - COST_B for r in rows)
    s["verdict"] = verdict(s)
    return s


def verdict(s):
    if s["n"] < MIN_CASES:
        return f"troppo presto: {s['n']} casi chiusi su {MIN_CASES} necessari"
    better = s["brier_claude"] < s["brier_market"]
    sure = s["ci"] is not None and s["ci"][0] > 0
    paid = s["bets"] > 0 and s["pnl"] > 0
    if better and sure and paid:
        return "vantaggio credibile: meglio del mercato, differenza non spiegabile dal caso, scommesse su carta in utile"
    if better and paid:
        return "indizio debole: meglio del mercato ma la differenza può essere caso"
    return "nessun vantaggio dimostrato"


def fmt(x, d=3):
    return "n/d" if x is None else f"{x:.{d}f}"


def cmd_report(args):
    ok, bad = verify()
    fc, res = load()
    out = ["# Registro di prova: previsioni di Claude contro il mercato", "",
           f"Aggiornato: {now()} (UTC). Solo su carta, nessuna operazione reale o demo.",
           f"Catena di controllo del registro: {'integra' if ok else f'ROTTA alla riga {bad}'}.", ""]
    names = {"A": "Pista A: eventi con probabilità di mercato (mercati di previsione)",
             "B": "Pista B: direzione del prezzo dopo l'evento (contro il 50%)"}
    summary = []
    for t in ("A", "B"):
        s = stats(t, fc, res)
        out += [f"## {names[t]}", ""]
        out.append(f"- Casi chiusi che contano: {s['n']}. Aperti: {s['open']}. Annullati: {s['void']}. "
                   f"Chiusi ma esclusi dal punteggio (prezzo già visto): {s['seen']}.")
        if s["n"]:
            out.append(f"- Errore medio (Brier, più basso è meglio): Claude {fmt(s['brier_claude'])}, "
                       f"mercato {fmt(s['brier_market'])}.")
            if s["ci"]:
                out.append(f"- Differenza a favore di Claude, intervallo al 90%: da {fmt(s['ci'][0])} a {fmt(s['ci'][1])} "
                           "(se comprende lo zero, può essere caso).")
            unit = "contratti da 1" if t == "A" else "punti percentuali, senza leva"
            out.append(f"- Scommesse su carta (solo quando lo scarto è di almeno 10 punti): {s['bets']}, "
                       f"vinte {s['bets_won']}, risultato netto {fmt(s['pnl'], 2)} ({unit}).")
            if t == "B":
                out.append(f"- Direzione indovinata: {s['hit']} su {s['n']}. "
                           f"Confronto: comprare sempre avrebbe reso {fmt(s['always_long'], 2)} punti percentuali.")
        out += [f"- Giudizio: {s['verdict']}.", ""]
        summary.append(f"{t}: chiusi {s['n']}, aperti {s['open']}, " +
                       (f"Brier Claude {fmt(s['brier_claude'])} vs mercato {fmt(s['brier_market'])}, "
                        f"scommesse {s['bets']} per {fmt(s['pnl'], 2)}; " if s["n"] else "") + s["verdict"])
        out += ["| Id | Evento | Data | Claude | Mercato | Esito | Scommessa |", "|---|---|---|---|---|---|---|"]
        done = {r["id"]: r for r in s["rows"]}
        for i, f in sorted(fc.items()):
            if f["track"] != t:
                continue
            r = done.get(i)
            if i not in res:
                esito = "aperto"
            elif res[i]["outcome"] == "void":
                esito = "annullato"
            else:
                esito = "sì" if res[i]["outcome"] else "no"
            if t == "B" and res.get(i, {}).get("ret_pct") is not None:
                esito += f" ({res[i]['ret_pct']:+.2f}%)"
            bet = "" if r is None or r["bet"] is None else f"{r['bet']:+.2f}"
            flag = "" if f["blind"] else " *"
            out.append(f"| {i} | {f['titolo']}{flag} | {f['event_date']} | {f['p_claude']:.0%} | "
                       f"{f['p_market']:.0%} | {esito} | {bet} |")
        out.append("")
    out += ["\\* previsione fatta dopo aver già visto il prezzo di mercato: resta in elenco ma non entra nel punteggio.", "",
            f"Fine della prova: {END_DATE}, oppure prima se entrambe le piste arrivano a {MIN_CASES} casi chiusi.", ""]
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("\n".join(summary))
    print("chain:", "ok" if ok else f"BROKEN at line {bad}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("--file", required=True); a.set_defaults(fn=cmd_add)
    r = sub.add_parser("resolve"); r.add_argument("--id", required=True)
    r.add_argument("--outcome", required=True); r.add_argument("--ret", type=float)
    r.add_argument("--source", required=True); r.set_defaults(fn=cmd_resolve)
    d = sub.add_parser("due"); d.add_argument("--date"); d.set_defaults(fn=cmd_due)
    sub.add_parser("open").set_defaults(fn=cmd_open)
    sub.add_parser("report").set_defaults(fn=cmd_report)
    sub.add_parser("verify").set_defaults(fn=lambda a: print(verify()))
    args = ap.parse_args()
    try:
        args.fn(args)
    except ValueError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
