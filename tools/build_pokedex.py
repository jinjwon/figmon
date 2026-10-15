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
    growth_name = {r["id"]: r["identifier"] for r in rows("growth_rates", folder)}
    bst = defaultdict(int)   # 능력치 합계 (기본 폼: pokemon_id == species_id)
    for r in rows("pokemon_stats", folder):
        bst[int(r["pokemon_id"])] += int(r["base_stat"])

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
        ps = [p[:3] for p in paths(sid)]       # 진화는 최대 3단계
        st = lambda p: [[i, names.get(i, species[i]["identifier"])] for i in p]
        flags = {"legendary": r["is_legendary"] == "1", "mythical": r["is_mythical"] == "1"}
        if len(ps) == 1:
            lines[str(sid)] = {"gen": gen, "type": first_type.get(ps[0][-1], "normal"), "stages": st(ps[0]), **flags}
            continue
        # 진화가 갈라짐: 공통 앞부분 + 갈래들 하나의 계열로 (어느 갈래로 갈지는 키우는 방식으로 정해짐)
        pre = 0
        while all(len(p) > pre for p in ps) and len({p[pre] for p in ps}) == 1:
            pre += 1
        lines[str(sid)] = {"gen": gen, "type": first_type.get(sid, "normal"), "stages": st(ps[0][:pre]), **flags,
                           "fork": {"options": [{"stages": st(p[pre:]), "type": first_type.get(p[-1], "normal"),
                                                 "final": p[-1]} for p in ps]}}
        for p in ps:   # 예전 버전에서 갈래별로 고른 저장 데이터용 (선택 창에는 안 보임)
            lines[f"{sid}-{p[-1]}"] = {"gen": gen, "type": first_type.get(p[-1], "normal"), "stages": st(p),
                                       **flags, "hidden": True}
    # 메가진화·원시회귀: {종 id: [[모습 id, 이름, 첫 타입], ...]} (이미지가 실제로 있는 것만)
    mega = defaultdict(list)
    for r in rows("pokemon", folder):
        ident, pid, sid = r["identifier"], int(r["id"]), int(r["species_id"])
        tail = next((t for t in ("-mega-x", "-mega-y", "-mega", "-primal") if ident.endswith(t)), None)
        if not tail:
            continue
        art = f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/{pid}.png"
        try:
            urllib.request.urlopen(urllib.request.Request(art, method="HEAD"), timeout=20)
        except Exception:
            continue
        if any(m[1].endswith(tail[-2:]) and tail in ("-mega-x", "-mega-y") for m in mega[str(sid)]) or (tail == "-mega" and mega[str(sid)]):
            continue   # 같은 메가의 다른 무늬(마기아나·싸리용 등)는 하나만
        nm = names.get(sid, species[sid]["identifier"])
        label = {"-mega-x": f"메가 {nm} X", "-mega-y": f"메가 {nm} Y", "-mega": f"메가 {nm}", "-primal": f"원시 {nm}"}[tail]
        mega[str(sid)].append([pid, label, first_type.get(pid, "normal")])
    # 포켓몬별 [성장 그룹, 능력치 합계] — 1세대 포함 전부 (성장 속도·전설 해금 난이도에 씀)
    info = {sid: [growth_name.get(r["growth_rate_id"], "medium"), bst.get(sid, 0)] for sid, r in species.items()}
    OUT.write_text(json.dumps({"source": "PokeAPI (https://github.com/PokeAPI/pokeapi), names in Korean",
                               "lines": lines, "legendary": legendary, "mythical": mythical, "info": info,
                               "mega": dict(sorted(mega.items(), key=lambda kv: int(kv[0])))},
                              ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    by_gen = defaultdict(int)
    for l in lines.values():
        by_gen[l["gen"]] += 1
    print(f"{OUT.name}: 계열 {len(lines)}개 (세대별 {dict(sorted(by_gen.items()))}), 전설 {len(legendary)} · 환상 {len(mythical)}마리, 메가진화 {sum(map(len, mega.values()))}종")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
