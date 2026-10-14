"""
pokedex.json 만들기: PokeAPI 공개 데이터(CSV)로 2~9세대 진화 계열과 한글 이름을 생성한다.
1세대는 server.py에 직접 적어 둔 데이터를 쓰고(기존 저장 데이터 호환), 여기서는 2세대부터 만든다.

사용: python3 tools/build_pokedex.py            (CSV를 내려받음)
      python3 tools/build_pokedex.py <CSV 폴더>  (이미 받은 CSV 사용)
"""
import csv
import io
import json
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

BASE = "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/"
KO = "3"   # languages.csv: 3 = ko
OUT = Path(__file__).resolve().parent.parent / "pokedex.json"


def rows(name, folder=None):
    if folder:
        text = (Path(folder) / f"{name}.csv").read_text(encoding="utf-8")
    else:
        text = urllib.request.urlopen(BASE + name + ".csv", timeout=60).read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def main(folder=None):
    species = {int(r["id"]): r for r in rows("pokemon_species", folder)}
    names = {int(r["pokemon_species_id"]): r["name"] for r in rows("pokemon_species_names", folder)
             if r["local_language_id"] == KO}
    type_name = {r["id"]: r["identifier"] for r in rows("types", folder)}
    first_type = {int(r["pokemon_id"]): type_name[r["type_id"]] for r in rows("pokemon_types", folder) if r["slot"] == "1"}

    children = defaultdict(list)
    for sid, r in species.items():
        if r["evolves_from_species_id"]:
            children[int(r["evolves_from_species_id"])].append(sid)

    def paths(sid):
        """sid부터 끝까지 진화 경로들 (갈라지면 여러 개)"""
        kids = sorted(children.get(sid, []))
        if not kids:
            return [[sid]]
        return [[sid] + p for k in kids for p in paths(k)]

    lines, legendary, mythical = {}, [], []
    for sid, r in sorted(species.items()):
        if r["is_legendary"] == "1":
            legendary.append(sid)
        if r["is_mythical"] == "1":
            mythical.append(sid)
        if r["evolves_from_species_id"]:      # 진화 전 단계(가장 처음 모습)만 계열의 시작
            continue
        gen = int(r["generation_id"])
        if gen < 2:                            # 1세대는 server.py에 있음
            continue
        ps = paths(sid)
        for p in ps:
            p = p[:3]                          # 진화는 최대 3단계
            key = str(sid) if len(ps) == 1 else f"{sid}-{p[-1]}"
            lines[key] = {
                "gen": gen,
                "type": first_type.get(p[-1], "normal"),
                "stages": [[i, names.get(i, species[i]["identifier"])] for i in p],
                "legendary": r["is_legendary"] == "1",
                "mythical": r["is_mythical"] == "1",
            }
    OUT.write_text(json.dumps({"source": "PokeAPI (https://github.com/PokeAPI/pokeapi), names in Korean",
                               "lines": lines, "legendary": legendary, "mythical": mythical},
                              ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    by_gen = defaultdict(int)
    for l in lines.values():
        by_gen[l["gen"]] += 1
    print(f"{OUT.name}: 계열 {len(lines)}개 (세대별 {dict(sorted(by_gen.items()))}), 전설 {len(legendary)} · 환상 {len(mythical)}마리")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
