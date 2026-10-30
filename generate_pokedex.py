#!/usr/bin/env python3
"""
Pokedex data builder (Gen 1, Pokemon Blue) for the ESP32 Pokedex.

Pulls everything from PokeAPI (https://pokeapi.co) and writes:
  out/001.json ... out/151.json   one file per Pokemon (National Dex number)
  out/index.json                  [["001","Bulbasaur","Grass","Poison"], ...]

Only the Python standard library is needed.

  python3 generate_pokedex.py                 # all 151
  python3 generate_pokedex.py --only 1-10     # a range, for a quick test
  python3 generate_pokedex.py --only 25       # a single one

Everything fetched is cached in ./cache so a re-run (or a run that was
interrupted) does not hit the API again.
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request

DEFAULT_BASE = "https://pokeapi.co/api/v2"
VERSION = "blue"
VERSION_GROUP = "red-blue"
GEN1_MAX = 151
GEN_NUM = {
    "generation-i": 1, "generation-ii": 2, "generation-iii": 3,
    "generation-iv": 4, "generation-v": 5, "generation-vi": 6,
    "generation-vii": 7, "generation-viii": 8, "generation-ix": 9,
}
NAME_FIX = {29: "Nidoran F", 32: "Nidoran M"}   # the female/male symbols are not in the TFT font
MAX_AREA_ROWS = 14


# ----------------------------------------------------------------- API ---
class Api:
    def __init__(self, base, cache_dir, delay):
        self.base = base.rstrip("/")
        self.cache_dir = cache_dir
        self.delay = delay
        os.makedirs(cache_dir, exist_ok=True)

    def get(self, path):
        path = path.strip("/")
        cp = os.path.join(self.cache_dir, path.replace("/", "__") + ".json")
        if os.path.exists(cp):
            with open(cp, "r", encoding="utf-8") as f:
                return json.load(f)
        url = self.base + "/" + path
        last = None
        for attempt in range(5):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "pokedex-builder/1.0"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode("utf-8"))
                tmp = cp + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                os.replace(tmp, cp)
                time.sleep(self.delay)
                return data
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise
                last = e
            except Exception as e:          # network hiccup, retry
                last = e
            time.sleep(1.5 * (attempt + 1))
        raise RuntimeError("giving up on %s: %s" % (url, last))


def path_of(url):
    """'https://pokeapi.co/api/v2/move/33/' -> 'move/33'"""
    return url.split("/api/v2/", 1)[1].strip("/")


def id_of(url):
    return int(url.rstrip("/").split("/")[-1])


# ---------------------------------------------------------------- text ---
def ascii_clean(s):
    if s is None:
        return ""
    s = s.replace("\u00ad\n", "").replace("\u00ad", "")
    s = re.sub(r"POK[\u00e9\u00c9]MON", "Pokemon", s)
    s = re.sub(r"Pok[\u00e9\u00c9]mon", "Pokemon", s)
    for a, b in (("\u2019", "'"), ("\u2018", "'"), ("\u201c", '"'), ("\u201d", '"'),
                 ("\u2013", "-"), ("\u2014", "-"), ("\u2026", "..."),
                 ("\u2642", " M"), ("\u2640", " F")):
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fix_caps(s):
    """Gen 1 flavour text shouts names (BULBASAUR); make them Bulbasaur."""
    return re.sub(r"\b[A-Z]{3,}\b", lambda m: m.group(0).capitalize(), s)


def title(slug):
    return " ".join(w.capitalize() for w in slug.split("-"))


def en_name(names, fallback):
    for n in names:
        if n["language"]["name"] == "en":
            return ascii_clean(n["name"])
    return title(fallback)


def fmt_height(dm):
    inches = int(round(dm * 3.937))
    return "%d'%02d\"" % (inches // 12, inches % 12)


def fmt_weight(hg):
    return "%.1f lbs" % (hg * 0.2204623)


# --------------------------------------------------------------- moves ---
_move_cache = {}


def vg_id(url):
    return id_of(url)


def gen1_value(mv, field):
    """Value of a move field as it was in Red/Blue (PokeAPI keeps later changes in past_values)."""
    past = sorted(mv.get("past_values", []), key=lambda e: vg_id(e["version_group"]["url"]))
    for e in past:
        v = e.get(field)
        if v is not None:
            return v["name"] if field == "type" else v
    return mv["type"]["name"] if field == "type" else mv.get(field)


def machine_label(api, mv):
    for e in mv.get("machines", []):
        if e["version_group"]["name"] == VERSION_GROUP:
            mach = api.get(path_of(e["machine"]["url"]))
            return mach["item"]["name"].upper()        # tm01 -> TM01
    return None


def move_info(api, slug):
    if slug in _move_cache:
        return _move_cache[slug]
    mv = api.get("move/" + slug)
    info = {
        "name": en_name(mv.get("names", []), slug),
        "type": title(gen1_value(mv, "type")),
        "power": gen1_value(mv, "power") or 0,
        "acc": gen1_value(mv, "accuracy") or 0,
        "pp": gen1_value(mv, "pp") or 0,
        "machine": machine_label(api, mv),
    }
    _move_cache[slug] = info
    return info


def build_moves(api, p):
    levelup, machines = [], []
    for m in p["moves"]:
        for d in m["version_group_details"]:
            if d["version_group"]["name"] != VERSION_GROUP:
                continue
            method = d["move_learn_method"]["name"]
            if method == "level-up":
                levelup.append((d["level_learned_at"], m["move"]["name"]))
            elif method == "machine":
                machines.append(m["move"]["name"])
    levelup.sort(key=lambda t: t[0])

    moves = []
    for lvl, slug in levelup:
        i = move_info(api, slug)
        moves.append([lvl, i["name"], i["type"], i["power"], i["acc"], i["pp"]])

    tms = []
    for slug in machines:
        i = move_info(api, slug)
        if i["machine"]:
            tms.append([i["machine"], i["name"], i["type"], i["power"], i["acc"], i["pp"]])
    tms.sort(key=lambda t: (t[0][:2] != "TM", int(re.sub(r"\D", "", t[0]) or 0)))
    return moves, tms


# --------------------------------------------------------------- types ---
def gen1_types(p):
    chosen = p["types"]
    best = None
    for pt in p.get("past_types", []):
        g = GEN_NUM.get(pt["generation"]["name"], 99)
        if best is None or g < best[0]:
            best = (g, pt["types"])
    if best:
        chosen = best[1]
    return [title(t["type"]["name"]) for t in sorted(chosen, key=lambda t: t["slot"])]


# ----------------------------------------------------------- evolution ---
_chain_cache = {}
_name_cache = {}


def species_name(api, n):
    if n not in _name_cache:
        if n in NAME_FIX:
            _name_cache[n] = NAME_FIX[n]
        else:
            sp = api.get("pokemon-species/%d" % n)
            _name_cache[n] = en_name(sp.get("names", []), sp.get("name", str(n)))
    return _name_cache[n]


def evo_how(details):
    if not details:
        return "?"
    d = details[0]
    trig = d["trigger"]["name"]
    if trig == "trade":
        return "Trade"
    if d.get("item"):
        return title(d["item"]["name"])
    if d.get("min_level"):
        return "Lv %d" % d["min_level"]
    return "?"


def chain_edges(api, chain_url):
    cid = id_of(chain_url)
    if cid in _chain_cache:
        return _chain_cache[cid]
    chain = api.get("evolution-chain/%d" % cid)["chain"]
    edges = []

    def walk(node, parent):
        sid = id_of(node["species"]["url"])
        if parent is not None and parent <= GEN1_MAX and sid <= GEN1_MAX:
            edges.append((parent, sid, evo_how(node.get("evolution_details", []))))
        for c in node.get("evolves_to", []):
            walk(c, sid)

    walk(chain, None)
    _chain_cache[cid] = edges
    return edges


def build_evo(api, n, chain_url):
    edges = chain_edges(api, chain_url)
    comp = {n}
    changed = True
    while changed:                              # connected component around n
        changed = False
        for a, b, _ in edges:
            if (a in comp) != (b in comp):
                comp.update((a, b))
                changed = True
    ce = [e for e in edges if e[0] in comp and e[1] in comp]
    depth = {x: 0 for x in comp}
    for _ in range(len(comp)):                  # longest path from a root
        for a, b, _ in ce:
            depth[b] = max(depth[b], depth[a] + 1)
    nodes = sorted(comp, key=lambda x: (depth[x], x))
    return {
        "nodes": [[x, species_name(api, x), depth[x]] for x in nodes],
        "edges": [[a, b, h] for a, b, h in ce],
    }


# --------------------------------------------------------------- areas ---
def clean_area(name):
    s = re.sub(r"^kanto-", "", name)
    m = re.match(r"route-(\d+)", s)
    if m:
        return "Route %s" % m.group(1)
    s = re.sub(r"-area$", "", s)
    out = []
    for w in s.split("-"):
        if re.fullmatch(r"b?\d+f", w):
            out.append(w.upper())
        elif w == "mt":
            out.append("Mt.")
        else:
            out.append(w.capitalize())
    return " ".join(out)


def build_area(api, n):
    try:
        enc = api.get("pokemon/%d/encounters" % n)
    except urllib.error.HTTPError:
        enc = []
    merged = {}                                  # (place, method) -> [lo, hi, chance]
    for e in enc:
        place = clean_area(e["location_area"]["name"])
        for vd in e["version_details"]:
            if vd["version"]["name"] != VERSION:
                continue
            per_area = {}                        # chances inside one area add up
            for ed in vd["encounter_details"]:
                key = (place, title(ed["method"]["name"]))
                cur = per_area.setdefault(key, [ed["min_level"], ed["max_level"], 0])
                cur[0] = min(cur[0], ed["min_level"])
                cur[1] = max(cur[1], ed["max_level"])
                cur[2] += ed["chance"]
            for key, v in per_area.items():      # same place in several areas: keep the best
                cur = merged.setdefault(key, [v[0], v[1], 0])
                cur[0] = min(cur[0], v[0])
                cur[1] = max(cur[1], v[1])
                cur[2] = max(cur[2], v[2])
    rows = []
    for (place, method), (lo, hi, ch) in merged.items():
        rows.append([place, method, str(lo) if lo == hi else "%d-%d" % (lo, hi), min(ch, 100)])
    rows.sort(key=lambda r: (-r[3], r[0]))
    return rows[:MAX_AREA_ROWS]


# --------------------------------------------------------------- build ---
def pick_flavor(sp):
    texts = {}
    for e in sp.get("flavor_text_entries", []):
        if e["language"]["name"] == "en":
            texts.setdefault(e["version"]["name"], e["flavor_text"])
    for v in (VERSION, "red", "yellow"):
        if v in texts:
            return fix_caps(ascii_clean(texts[v]))
    for t in texts.values():
        return fix_caps(ascii_clean(t))
    return ""


def build_one(api, n):
    p = api.get("pokemon/%d" % n)
    sp = api.get("pokemon-species/%d" % n)
    genus = ""
    for g in sp.get("genera", []):
        if g["language"]["name"] == "en":
            genus = ascii_clean(g["genus"])
    moves, tms = build_moves(api, p)
    rec = {
        "n": n,
        "name": species_name(api, n),
        "cat": genus,
        "types": gen1_types(p),
        "ht": fmt_height(p["height"]),
        "htm": "%.1f m" % (p["height"] / 10.0),
        "wt": fmt_weight(p["weight"]),
        "wtm": "%.1f kg" % (p["weight"] / 10.0),
        "dex": pick_flavor(sp),
        "moves": moves,
        "tms": tms,
        "evo": build_evo(api, n, sp["evolution_chain"]["url"]),
        "area": build_area(api, n),
    }
    return rec


def parse_range(s):
    if "-" in s:
        a, b = s.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(s)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="1-%d" % GEN1_MAX, help="e.g. 1-151, 1-10, 25")
    ap.add_argument("--out", default="out")
    ap.add_argument("--cache", default="cache")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--delay", type=float, default=0.05, help="pause after each real request")
    a = ap.parse_args()

    ids = [n for n in parse_range(a.only) if 1 <= n <= GEN1_MAX]
    api = Api(a.base, a.cache, a.delay)
    os.makedirs(a.out, exist_ok=True)

    failed = []
    for n in ids:
        try:
            rec = build_one(api, n)
            with open(os.path.join(a.out, "%03d.json" % n), "w", encoding="utf-8") as f:
                json.dump(rec, f, separators=(",", ":"), ensure_ascii=True)
            print("%03d %-12s %-14s moves:%-3d tms:%-3d areas:%d" % (
                n, rec["name"], "/".join(rec["types"]), len(rec["moves"]), len(rec["tms"]), len(rec["area"])))
        except Exception as e:
            failed.append(n)
            print("%03d FAILED: %s" % (n, e))

    # index.json is rebuilt from whatever is in the output folder
    index = []
    total = biggest = 0
    for n in range(1, GEN1_MAX + 1):
        fp = os.path.join(a.out, "%03d.json" % n)
        if not os.path.exists(fp):
            continue
        with open(fp, "r", encoding="utf-8") as f:
            raw = f.read()
        r = json.loads(raw)
        total += len(raw)
        biggest = max(biggest, len(raw))
        t = r["types"] + ["", ""]
        index.append(["%03d" % n, r["name"], t[0], t[1]])
    with open(os.path.join(a.out, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, separators=(",", ":"), ensure_ascii=True)

    print("\nentries: %d   biggest file: %d bytes   total: %d bytes" % (len(index), biggest, total))
    if failed:
        print("FAILED: %s  (just run the script again, finished work is cached)" % failed)
        sys.exit(1)


if __name__ == "__main__":
    main()