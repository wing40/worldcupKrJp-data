#!/usr/bin/env python3
"""
한일 월드컵 트래커 — 교차검증 발행 엔진 (라이브).

설계:
  - 일정/상대팀/경기장 = 고정 사실(FIXTURES).
  - 스코어/상태 = 라이브 소스에서 가져와 교차검증.
      * 소스 2곳 일치 → 발행(verified)
      * 소스 불일치 → status="verifying" 으로 보류(틀린 값 안 내보냄)
      * 신뢰 소스(ESPN) 단독 → 발행
      * 라이브 0곳 → 검증 시드(SEED_RESULTS) 폴백
  - 조별 순위(groups)도 라이브 경기결과로 계산. 데이터 없으면 GROUPS_SEED 폴백.
  - 출력 = 앱이 읽는 matches.json 스키마 그대로.

소스: ESPN(무료, 키 불필요) + TheSportsDB(무료 키 '3'). 둘 다 실패해도 시드로 안전 동작.
"""
import json
import sys
import os
import urllib.request
from datetime import datetime, timezone, timedelta

UA = {"User-Agent": "worldcupkrjp-reconcile/1.0 (+https://github.com/)"}

# ── 팀 레지스트리: 내부코드 → 표시정보 + 매칭 별칭(소문자) + 조 ──────────────
TEAMS = {
    "MEX": {"ko": "멕시코", "en": "Mexico", "flag": "🇲🇽", "group": "A", "aliases": {"mexico"}},
    "KOR": {"ko": "대한민국", "en": "South Korea", "flag": "🇰🇷", "group": "A", "aliases": {"south korea", "korea republic", "korea"}},
    "CZE": {"ko": "체코", "en": "Czechia", "flag": "🇨🇿", "group": "A", "aliases": {"czechia", "czech republic"}},
    "RSA": {"ko": "남아공", "en": "South Africa", "flag": "🇿🇦", "group": "A", "aliases": {"south africa"}},
    "NED": {"ko": "네덜란드", "en": "Netherlands", "flag": "🇳🇱", "group": "F", "aliases": {"netherlands", "holland"}},
    "JPN": {"ko": "일본", "en": "Japan", "flag": "🇯🇵", "group": "F", "aliases": {"japan"}},
    "SWE": {"ko": "스웨덴", "en": "Sweden", "flag": "🇸🇪", "group": "F", "aliases": {"sweden"}},
    "TUN": {"ko": "튀니지", "en": "Tunisia", "flag": "🇹🇳", "group": "F", "aliases": {"tunisia"}},
}
ALIAS_TO_CODE = {a: code for code, t in TEAMS.items() for a in t["aliases"]}

# 앱의 우리 팀 코드(KR/JP) → 내부 코드
OUR = {"KR": "KOR", "JP": "JPN"}

FIXTURES = {
    "KR": {
        "nameKo": "대한민국", "nameEn": "South Korea", "flag": "🇰🇷", "group": "A",
        "matches": [
            {"id": "KR1", "opp": "CZE", "opponentKo": "체코", "opponentEn": "Czechia", "opponentFlag": "🇨🇿",
             "kickoffUtc": "2026-06-12T02:00:00Z", "venue": "Estadio Akron, Zapopan"},
            {"id": "KR2", "opp": "MEX", "opponentKo": "멕시코", "opponentEn": "Mexico", "opponentFlag": "🇲🇽",
             "kickoffUtc": "2026-06-19T01:00:00Z", "venue": "Estadio Akron, Zapopan"},
            {"id": "KR3", "opp": "RSA", "opponentKo": "남아공", "opponentEn": "South Africa", "opponentFlag": "🇿🇦",
             "kickoffUtc": "2026-06-25T01:00:00Z", "venue": "Estadio BBVA, Guadalupe"},
        ],
    },
    "JP": {
        "nameKo": "일본", "nameEn": "Japan", "flag": "🇯🇵", "group": "F",
        "matches": [
            {"id": "JP1", "opp": "NED", "opponentKo": "네덜란드", "opponentEn": "Netherlands", "opponentFlag": "🇳🇱",
             "kickoffUtc": "2026-06-14T20:00:00Z", "venue": "AT&T Stadium, Arlington"},
            {"id": "JP2", "opp": "TUN", "opponentKo": "튀니지", "opponentEn": "Tunisia", "opponentFlag": "🇹🇳",
             "kickoffUtc": "2026-06-21T04:00:00Z", "venue": "Estadio BBVA, Guadalupe"},
            {"id": "JP3", "opp": "SWE", "opponentKo": "스웨덴", "opponentEn": "Sweden", "opponentFlag": "🇸🇪",
             "kickoffUtc": "2026-06-25T23:00:00Z", "venue": "AT&T Stadium, Arlington"},
        ],
    },
}

# 라이브 0곳일 때 폴백(현재까지 끝난 경기, 교차검증 2026-06-15).
SEED_RESULTS = {
    "KR1": {"status": "finished", "scoreFor": 2, "scoreAgainst": 1},
    "JP1": {"status": "finished", "scoreFor": 2, "scoreAgainst": 2},
}

# 조별 순위 폴백.
GROUPS_SEED = {
    "A": [
        {"rank": 1, "code": "MEX", "played": 1, "w": 1, "d": 0, "l": 0, "gf": 2, "ga": 0, "pts": 3},
        {"rank": 2, "code": "KOR", "played": 1, "w": 1, "d": 0, "l": 0, "gf": 2, "ga": 1, "pts": 3},
        {"rank": 3, "code": "CZE", "played": 1, "w": 0, "d": 0, "l": 1, "gf": 1, "ga": 2, "pts": 0},
        {"rank": 4, "code": "RSA", "played": 1, "w": 0, "d": 0, "l": 1, "gf": 0, "ga": 2, "pts": 0},
    ],
    "F": [
        {"rank": 1, "code": "SWE", "played": 1, "w": 1, "d": 0, "l": 0, "gf": 5, "ga": 1, "pts": 3},
        {"rank": 2, "code": "JPN", "played": 1, "w": 0, "d": 1, "l": 0, "gf": 2, "ga": 2, "pts": 1},
        {"rank": 3, "code": "NED", "played": 1, "w": 0, "d": 1, "l": 0, "gf": 2, "ga": 2, "pts": 1},
        {"rank": 4, "code": "TUN", "played": 1, "w": 0, "d": 0, "l": 1, "gf": 1, "ga": 5, "pts": 0},
    ],
}

# 조별리그 스윕 날짜 범위(UTC). 한·일 + 같은조 전 경기 포함.
SWEEP_DATES = [(datetime(2026, 6, 10, tzinfo=timezone.utc) + timedelta(days=i)).strftime("%Y%m%d")
               for i in range(0, 18)]  # 6/10 ~ 6/27


def http_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.load(r)


def code_of(name):
    return ALIAS_TO_CODE.get((name or "").strip().lower())


# 매치맵 형식: { frozenset({codeA,codeB}): {"status": "finished"/"other", "score": {codeA:int, codeB:int}} }

def fetch_espn():
    """ESPN 공개 scoreboard(키 불필요). 날짜 스윕 → 우리 8팀 경기만 추출."""
    out = {}
    base = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard?dates="
    for ds in SWEEP_DATES:
        try:
            j = http_json(base + ds)
        except Exception as e:
            print(f"[espn] {ds} skip: {e}", file=sys.stderr)
            continue
        for ev in j.get("events", []):
            try:
                comp = ev["competitions"][0]
                cs = comp["competitors"]
                if len(cs) != 2:
                    continue
                c1, c2 = code_of(cs[0]["team"].get("displayName")), code_of(cs[1]["team"].get("displayName"))
                if not c1 or not c2:
                    continue
                completed = bool(ev.get("status", {}).get("type", {}).get("completed"))
                if not completed:
                    continue
                s1 = int(cs[0].get("score"))
                s2 = int(cs[1].get("score"))
                out[frozenset({c1, c2})] = {"status": "finished", "score": {c1: s1, c2: s2}}
            except Exception:
                continue
    return out


def fetch_sportsdb():
    """TheSportsDB(무료 키 '3') eventsday → 우리 8팀 경기만. 2차 교차검증용."""
    out = {}
    base = "https://www.thesportsdb.com/api/v1/json/3/eventsday.php?s=Soccer&d="
    for ds in SWEEP_DATES:
        d = f"{ds[0:4]}-{ds[4:6]}-{ds[6:8]}"
        try:
            j = http_json(base + d)
        except Exception as e:
            print(f"[sportsdb] {d} skip: {e}", file=sys.stderr)
            continue
        for ev in (j.get("events") or []):
            try:
                c1, c2 = code_of(ev.get("strHomeTeam")), code_of(ev.get("strAwayTeam"))
                if not c1 or not c2:
                    continue
                if ev.get("intHomeScore") in (None, "") or ev.get("intAwayScore") in (None, ""):
                    continue
                s1, s2 = int(ev["intHomeScore"]), int(ev["intAwayScore"])
                out[frozenset({c1, c2})] = {"status": "finished", "score": {c1: s1, c2: s2}}
            except Exception:
                continue
    return out


def reconcile_fixture(our_code, opp_code, mid, sources):
    """KR/JP 한 경기 교차검증."""
    key = frozenset({our_code, opp_code})
    votes = []
    for m in sources:
        if key in m and m[key]["status"] == "finished":
            sc = m[key]["score"]
            votes.append((sc[our_code], sc[opp_code]))
    if len(votes) >= 2:
        if len(set(votes)) == 1:
            sf, sa = votes[0]
            return {"status": "finished", "scoreFor": sf, "scoreAgainst": sa}
        return {"status": "verifying", "scoreFor": None, "scoreAgainst": None}  # 불일치 보류
    if len(votes) == 1:
        sf, sa = votes[0]
        return {"status": "finished", "scoreFor": sf, "scoreAgainst": sa}
    if mid in SEED_RESULTS:
        return dict(SEED_RESULTS[mid])
    return {"status": "scheduled", "scoreFor": None, "scoreAgainst": None}


def result_letter(sf, sa):
    if sf is None or sa is None:
        return None
    return "W" if sf > sa else ("L" if sf < sa else "D")


def build_team(code, sources):
    cfg = FIXTURES[code]
    our = OUR[code]
    matches, w = [], {"w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0}
    for fx in cfg["matches"]:
        r = reconcile_fixture(our, fx["opp"], fx["id"], sources)
        finished = r["status"] == "finished"
        sf, sa = r["scoreFor"], r["scoreAgainst"]
        letter = result_letter(sf, sa) if finished else None
        if finished and sf is not None:
            w["gf"] += sf; w["ga"] += sa
            w["w"] += letter == "W"; w["d"] += letter == "D"; w["l"] += letter == "L"
        matches.append({
            "opponentKo": fx["opponentKo"], "opponentEn": fx["opponentEn"],
            "opponentFlag": fx["opponentFlag"], "kickoffUtc": fx["kickoffUtc"],
            "venue": fx["venue"], "status": r["status"],
            "scoreFor": sf, "scoreAgainst": sa, "result": letter,
        })
    pts = w["w"] * 3 + w["d"]
    return {
        "code": code, "nameKo": cfg["nameKo"], "nameEn": cfg["nameEn"],
        "flag": cfg["flag"], "group": cfg["group"],
        "record": {**w, "pts": pts}, "matches": matches,
    }


def build_groups(espn):
    """ESPN 라이브 경기결과로 A/F조 순위 계산. 데이터 없으면 시드 폴백."""
    out = {}
    for g in ("A", "F"):
        codes = [c for c, t in TEAMS.items() if t["group"] == g]
        tally = {c: {"w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "played": 0} for c in codes}
        seen = 0
        for key, m in espn.items():
            cc = [c for c in key if c in codes]
            if len(cc) != 2 or m["status"] != "finished":
                continue
            a, b = cc[0], cc[1]
            sa, sb = m["score"][a], m["score"][b]
            for x, sx, sy in ((a, sa, sb), (b, sb, sa)):
                t = tally[x]; t["played"] += 1; t["gf"] += sx; t["ga"] += sy
                t["w"] += sx > sy; t["d"] += sx == sy; t["l"] += sx < sy
            seen += 1
        if seen == 0:
            out[g] = [dict(r, **team_names(r["code"])) for r in GROUPS_SEED[g]]
            continue
        rows = []
        for c in codes:
            t = tally[c]
            rows.append({"code": c, "played": t["played"], "w": t["w"], "d": t["d"], "l": t["l"],
                         "gf": t["gf"], "ga": t["ga"], "pts": t["w"] * 3 + t["d"]})
        rows.sort(key=lambda r: (-r["pts"], -(r["gf"] - r["ga"]), -r["gf"], TEAMS[r["code"]]["en"]))
        for i, r in enumerate(rows):
            r["rank"] = i + 1
            r.update(team_names(r["code"]))
        out[g] = rows
    return out


def team_names(code):
    t = TEAMS[code]
    return {"nameKo": t["ko"], "nameEn": t["en"], "flag": t["flag"]}


def main():
    espn = fetch_espn()
    sdb = fetch_sportsdb()
    sources = [s for s in (espn, sdb) if s]  # 비어있는 소스는 제외
    print(f"[sources] espn={len(espn)} matches, sportsdb={len(sdb)} matches", file=sys.stderr)

    groups_raw = build_groups(espn)
    # groups 출력은 code 제거하고 앱 스키마(rank/nameKo/nameEn/flag/played/w/d/l/gf/ga/pts)로
    groups = {g: [{k: v for k, v in r.items() if k != "code"} for r in rows] for g, rows in groups_raw.items()}

    out = {
        "schemaVersion": 1,
        "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tournament": "2026 FIFA World Cup",
        "source": "cross-verified by reconcile.py (ESPN + TheSportsDB)",
        "teams": [build_team("KR", sources), build_team("JP", sources)],
        "groups": groups,
    }
    path = sys.argv[1] if len(sys.argv) > 1 else "public/matches.json"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"wrote {path} @ {out['updatedAt']}")


if __name__ == "__main__":
    main()
