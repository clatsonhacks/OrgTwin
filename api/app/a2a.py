"""Agent-to-agent handoff messages between department agents.

Shaped after the A2A protocol's task/message/artifact concepts: a sender agent creates a task for a
receiver agent with a text instruction and structured data parts. Kept in-process for the demo; each
department agent exposes an agent card (name, skills) via /api/agents.
"""
import uuid
from datetime import datetime, timezone


def agent_card(team: dict) -> dict:
    return {
        "name": f"{team['name']} Agent",
        "id": f"agent:{team['key']}",
        "description": f"Handles recurring work for the {team['name']} team.",
        "skills": [{"id": r["key"], "name": r["name"], "tags": r.get("tasks", [])[:3]}
                   for r in team.get("roles", [])],
    }


def handoff(from_team: str, to_team: str, instruction: str, data: dict) -> dict:
    return {
        "kind": "task",
        "id": f"task-{uuid.uuid4().hex[:12]}",
        "from": f"agent:{from_team}",
        "to": f"agent:{to_team}",
        "status": {"state": "submitted", "timestamp": datetime.now(timezone.utc).isoformat()},
        "message": {"role": "agent", "parts": [{"kind": "text", "text": instruction},
                                                {"kind": "data", "data": data}]},
    }
