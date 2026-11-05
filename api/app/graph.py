"""Org knowledge graph: who does what, and which skills exist where.

Nodes  org · team · role · person (a desk, `<role>#<seat>`) · skill (recorded) · workflow (designed / hero) · tool
Edges  has_team (org→team) · has_role (team→role) · seat (role→person) · owns (person→skill|workflow)
       uses_tool (skill|workflow→tool) · reuses (workflow→skill it was built from)

The same graph powers workflow design: `relevant_skills` ranks existing skills for a new goal by
text overlap and by how close the skill sits to the person/role asking for it.
"""
import re

_STOP = set("""a an the and or of to for in on at by with from into as is are be it its this that these those each every
any all my our your their them they we i you me us do does done then than so if when while per via up out about over
please just also new""".split())


def tokens(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(w) < 3 or w in _STOP:
            continue
        # crude stemming so "invoices"/"invoice", "chasing"/"chase" meet
        for suf in ("ing", "ed", "s"):
            if len(w) > 4 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        if len(w) > 4 and w.endswith("e"):
            w = w[:-1]
        out.add(w)
    return out


def owner_of(wf: dict) -> str:
    p = (wf.get("meta") or {}).get("person")
    role = wf.get("role_key") or ""
    return p if isinstance(p, str) and p.startswith(f"{role}#") else f"{role}#0"


def skill_text(wf: dict) -> str:
    parts = [wf.get("name", ""), wf.get("goal", "")]
    for s in wf.get("steps") or []:
        parts += [s.get("title", ""), s.get("description", "")]
    return " ".join(parts)


def build(org: dict | None, workflows: list[dict]) -> dict:
    if not org:
        return {"nodes": [], "edges": []}
    nodes: list[dict] = [{"id": "org", "type": "org", "label": org.get("company") or org.get("name") or "Org"}]
    edges: list[dict] = []
    seats: set[str] = set()
    for t in org.get("teams", []):
        tid = f"team:{t['key']}"
        nodes.append({"id": tid, "type": "team", "label": t["name"], "team": t["key"], "headcount": t.get("headcount", 0)})
        edges.append({"source": "org", "target": tid, "type": "has_team"})
        for r in t.get("roles", []):
            rid = f"role:{t['key']}/{r['key']}"
            nodes.append({"id": rid, "type": "role", "label": r["name"], "team": t["key"], "role": r["key"],
                          "tasks": r.get("tasks", []), "headcount": r.get("headcount", 1)})
            edges.append({"source": tid, "target": rid, "type": "has_role"})
            for seat in range(max(1, int(r.get("headcount") or 1))):
                pid = f"person:{t['key']}/{r['key']}#{seat}"
                seats.add(pid)
                nodes.append({"id": pid, "type": "person", "label": f"{r['name']} #{seat + 1}", "team": t["key"],
                              "role": r["key"], "person": f"{r['key']}#{seat}"})
                edges.append({"source": rid, "target": pid, "type": "seat"})

    tools: set[str] = set()
    for w in workflows:
        wid = f"wf:{w['id']}"
        meta = w.get("meta") or {}
        nodes.append({"id": wid, "type": "skill" if w["kind"] == "skill" else "workflow", "label": w["name"],
                      "kind": w["kind"], "team": w["team_key"], "role": w["role_key"], "workflow_id": w["id"],
                      "person": owner_of(w), "steps": len(w.get("steps") or []), "executable": w.get("executable")})
        pid = f"person:{w['team_key']}/{owner_of(w)}"
        if pid in seats:
            edges.append({"source": pid, "target": wid, "type": "owns"})
        for s in w.get("steps") or []:
            tid = f"tool:{s.get('tool')}"
            if s.get("tool") and tid not in tools:
                tools.add(tid)
                nodes.append({"id": tid, "type": "tool", "label": s["tool"]})
        for tool in {s.get("tool") for s in w.get("steps") or [] if s.get("tool")}:
            edges.append({"source": wid, "target": f"tool:{tool}", "type": "uses_tool"})
        for sid in meta.get("uses_skills") or []:
            if any(x["id"] == sid for x in workflows):
                edges.append({"source": wid, "target": f"wf:{sid}", "type": "reuses"})
    return {"nodes": nodes, "edges": edges}


def relevant_skills(goal: str, team_key: str, role_key: str, person: str | None, skills: list[dict],
                    limit: int = 3, min_score: float = 0.34) -> list[dict]:
    """Rank recorded skills for a goal. Score = share of the skill's key words found in the goal,
    weighted by graph distance (same person 1.0 · same role 0.9 · same team 0.8 · elsewhere 0.65)."""
    g = tokens(goal)
    if not g:
        return []
    ranked = []
    for s in skills:
        name_t = tokens(s.get("name", "")) | tokens(s.get("goal", ""))
        all_t = name_t | tokens(skill_text(s))
        if not all_t:
            continue
        hit_name = len(g & name_t) / max(1, min(len(name_t), len(g)))
        hit_all = len(g & all_t) / max(1, len(g))
        text = 0.7 * hit_name + 0.3 * hit_all
        if text <= 0:
            continue
        if s["team_key"] == team_key and s["role_key"] == role_key and person and owner_of(s) == person:
            near, why = 1.0, "same person"
        elif s["team_key"] == team_key and s["role_key"] == role_key:
            near, why = 0.9, "same role"
        elif s["team_key"] == team_key:
            near, why = 0.8, "same team"
        else:
            near, why = 0.65, "another team"
        score = round(text * near, 3)
        if score >= min_score:
            ranked.append({"id": s["id"], "name": s["name"], "team_key": s["team_key"], "role_key": s["role_key"],
                           "person": owner_of(s), "person_name": (s.get("meta") or {}).get("person_name") or "",
                           "score": score, "why": why, "matched": sorted(g & all_t)})
    ranked.sort(key=lambda x: -x["score"])
    return ranked[:limit]
