"""Skill Recorder: turn a demonstration (screen keyframes + narration) into an executable workflow.

1. Observe: keyframes go to a vision model on Vultr Serverless Inference in small batches; it describes
   what the person does in each frame (app, sheet, columns, values, buttons).
2. Plan: an LLM turns the observations + narration into steps from the executable step library.
3. Fallbacks: no vision model -> narration only; no inference key -> deterministic planner. The result
   always says which path ran.
Frames are processed in memory only and never stored; only the text observations are kept.
"""
import json

from . import llm, workflows

MAX_FRAMES = 16
BATCH = 4

OBSERVE_PROMPT = """These are consecutive screenshots (frames {start}-{end}) of a person doing a task at work.
For EACH frame, write one line: "Frame N: <app/site> — <what the person is doing>". Be concrete: name the
spreadsheet, the column headers you can read, filters or values, the email recipient/subject, buttons
clicked. If a frame shows nothing new, say "no change". Do not guess beyond what is visible."""

PLAN_SYSTEM = """You turn a recorded demonstration of a work task into an automation for an AI agent.
You get what was seen on screen (frame by frame) and what the person said. Reproduce the SAME task in a
general way so it works on every matching row next time, not just the rows in the video.
""" + workflows.SCHEMA_PROMPT


async def observe(frames: list[str]) -> tuple[list[str], str | None]:
    """Describe frames with the vision model. Returns (observations, model or None if unavailable)."""
    frames = _sample(frames, MAX_FRAMES)
    if not frames:
        return [], None
    observations: list[str] = []
    model = None
    for i in range(0, len(frames), BATCH):
        batch = frames[i:i + BATCH]
        res = await llm.describe_images(OBSERVE_PROMPT.format(start=i + 1, end=i + len(batch)), batch)
        if res is None:
            return [], None
        model = res.model
        lines = [ln.strip(" -•\t") for ln in res.text.splitlines() if ln.strip()]
        observations += [ln for ln in lines if "no change" not in ln.lower()]
    return observations[:60], model


def _sample(frames: list[str], n: int) -> list[str]:
    frames = [f for f in frames if isinstance(f, str) and f.startswith("data:image/")]
    if len(frames) <= n:
        return frames
    step = (len(frames) - 1) / (n - 1)
    return [frames[round(i * step)] for i in range(n)]


async def analyze(team: dict, role: dict, transcript: str, frames: list[str], columns: list[str],
                  title: str = "") -> dict:
    observations, vision = await observe(frames)
    narration = (transcript or "").strip()
    evidence = ""
    if observations:
        evidence += "What was seen on screen:\n" + "\n".join(observations) + "\n\n"
    if narration:
        evidence += f"What the person said while doing it:\n{narration}\n"
    if not evidence:
        evidence = "(no narration and no vision model available)"

    fallback_text = narration + " " + " ".join(observations)
    res = await llm.complete(
        PLAN_SYSTEM.replace("COLUMNS", ", ".join(columns)),
        f"Team: {team['name']}\nRole: {role['name']}\n{evidence}\n"
        'Also include a short name for this skill as the "title" of the first step if obvious.',
        fallback=lambda: json.dumps(workflows.plan_from_text(fallback_text, role.get("tasks"))),
        temperature=0.2)
    mocked = res.mocked
    try:
        steps = workflows.normalize(llm.extract_json(res.text))
    except Exception:
        steps, mocked = workflows.plan_from_text(fallback_text, role.get("tasks")), True
    if not steps:
        steps, mocked = workflows.plan_from_text(fallback_text, role.get("tasks")), True

    name = title.strip() or _name_from(steps, narration)
    how = ("vision + narration" if vision and narration else "vision only" if vision
           else "narration only" if narration else "nothing to learn from")
    return {
        "name": name[:120],
        "steps": steps,
        "executable": workflows.is_executable(steps),
        "meta": {
            "source": "recording",
            "how": how,
            "vision_model": vision,
            "planner": "demo planner (no inference key)" if mocked else res.model,
            "frames_received": len(frames),
            "frames_used": min(len(frames), MAX_FRAMES) if vision else 0,
            "observations": observations,
            "transcript": narration[:4000],
            "note": None if vision else ("No vision model available on Vultr Serverless Inference for this key — "
                                         "the workflow was learned from the narration only."
                                         if not mocked else "Demo mode — set VULTR_INFERENCE_API_KEY to use Vultr models."),
        },
    }


def _name_from(steps: list[dict], narration: str) -> str:
    read = next((s for s in steps if s["action"] == "read_rows"), None)
    send = next((s for s in steps if s["tool"] == "email"), None)
    mark = next((s for s in steps if s["action"] == "update_row"), None)
    if read and send:
        status = next(iter(read["params"].get("where", {}).values()), "")
        name = f"Email {status + ' ' if status else ''}rows".strip()
        instr = send["params"].get("instruction", "").lower()
        if "payment" in instr or "reminder" in instr:
            name = f"Remind {status + ' ' if status else ''}rows to pay"
        elif "follow" in instr:
            name = f"Follow up {status + ' ' if status else 'each '}row".replace("row", "leads" if "lead" in narration.lower() else "rows")
        if mark:
            name += f", mark {mark['params'].get('value', 'done')}"
        return name[:60]
    if narration:
        first = narration.split(".")[0].strip()
        if 8 <= len(first) <= 45:
            return first[0].upper() + first[1:]
    return "Recorded skill"
