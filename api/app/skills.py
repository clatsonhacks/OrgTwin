"""Skill Recorder: turn a demonstration (screen keyframes + narration) into an executable workflow.

1. Observe: keyframes go to a vision model on Vultr Serverless Inference in small batches; it describes
   what the person does in each frame (app, sheet, columns, values, buttons).
2. Plan: an LLM turns the observations + narration into steps from the executable step library.
3. Fallbacks: no vision model -> narration only; no inference key -> deterministic planner. The result
   always says which path ran.
Frames are processed in memory only and never stored; only the text observations are kept.
"""


from . import llm, workflows

MAX_FRAMES = 16
BATCH = 4

OBSERVE_PROMPT = """These are consecutive screenshots (frames {start}-{end}) of a person doing a task at work.
For EACH frame, write one line: "Frame N: <app/site> — <what the person is doing>". Be concrete: name the
spreadsheet, the column headers you can read, filters or values, the email recipient/subject, what they type
into a search box, the website they open, buttons clicked. If a frame shows nothing new, say "no change".
If a frame only shows the recording tool itself (OrgTwin, a screen-share or microphone permission prompt),
write "Frame N: recorder" and nothing else — never repeat its example or placeholder text.
Do not guess beyond what is visible."""

PLAN_SYSTEM = """You turn a recorded demonstration of a work task into an automation for an AI agent.
You get what was seen on screen (frame by frame) and what the person said. Reproduce the SAME task the person
actually did, in a general way so it works next time, and nothing else. Base it on the evidence: do not add
spreadsheet, email or status steps unless the person did them. Ignore the recording tool itself (OrgTwin,
permission prompts) and any example text shown in it.
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
        observations += [ln for ln in lines if not _noise(ln)]
    return observations[:60], model


def _noise(line: str) -> bool:
    """Frames that add nothing: unchanged screens and the recorder's own UI (its hint text is not the task)."""
    low = line.lower()
    return ("no change" in low or low.rstrip(". ").endswith("recorder") or "orgtwin" in low
            or "localhost:3000" in low or "recording a skill" in low)


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
    data, res, problem = await llm.complete_json(
        PLAN_SYSTEM.replace("COLUMNS", ", ".join(columns)),
        f"Team: {team['name']}\nRole: {role['name']}\n{evidence}",
        fallback=lambda: None)
    steps = workflows.normalize(data) if not problem else []
    if not problem and not steps:
        problem = "the AI returned no usable steps"
    if problem:
        steps = workflows.plan_from_text(fallback_text, role.get("tasks"))
    mocked = bool(problem)
    ai_name = data.get("name") if isinstance(data, dict) and isinstance(data.get("name"), str) else ""

    name = title.strip() or ai_name.strip() or _name_from(steps, narration)
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
            "planner": f"rule-based fallback ({problem})" if mocked else res.model,
            "frames_received": len(frames),
            "frames_used": min(len(frames), MAX_FRAMES) if vision else 0,
            "observations": observations,
            "transcript": narration[:4000],
            "note": ("Demo mode — set VULTR_INFERENCE_API_KEY to use Vultr models." if problem == "no inference key"
                     else f"The AI planner failed ({problem}), so a simple rule-based planner wrote these steps — "
                          "check them before saving." if mocked
                     else None if vision
                     else "No vision model available on Vultr Serverless Inference for this key — "
                          "the workflow was learned from the narration only."),
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
