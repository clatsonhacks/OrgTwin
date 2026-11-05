export type Role = {
  key: string; name: string; headcount: number; tasks: string[];
  automatable_hours: number; suggested_workflows: string[];
};
export type Team = {
  key: string; name: string; headcount: number; automatable_hours: number;
  roles: Role[]; added_for?: string;
};
export type Org = {
  id: number; name: string; source: "vultr" | "mock"; company: string; summary: string;
  ceo: { title: string }; teams: Team[];
};
export type Step = {
  key?: string; team?: string; to?: string; title: string; tool: string;
  action?: string; params?: Record<string, any>; per_row?: boolean;
  description?: string; needs_approval?: boolean; manual_min?: number;
  from_skill?: { id: number; name: string };
};
export type GraphNode = {
  id: string; type: "org" | "team" | "role" | "person" | "skill" | "workflow" | "tool"; label: string;
  team?: string; role?: string; person?: string; kind?: string; workflow_id?: number; steps?: number;
  tasks?: string[]; headcount?: number; executable?: boolean;
};
export type GraphEdge = { source: string; target: string; type: "has_team" | "has_role" | "seat" | "owns" | "uses_tool" | "reuses" };
export type OrgGraph = { nodes: GraphNode[]; edges: GraphEdge[] };
export type SkillMatch = {
  id: number; name: string; team_key: string; role_key: string; person: string; person_name: string;
  score: number; why: string; matched: string[];
};
export type Workflow = {
  id: number; team_key: string; role_key: string; name: string; goal: string;
  kind: "hero" | "draft" | "skill" | "flow"; steps: Step[]; mocked?: boolean;
  meta?: Record<string, any>; executable?: boolean;
  /** departments involved, in order (cross-team workflows have several) */
  teams?: string[];
};
export type SkillAnalysis = {
  name: string; steps: Step[]; executable: boolean;
  meta: { how: string; vision_model: string | null; planner: string; frames_received: number; frames_used: number;
          observations: string[]; transcript: string; note: string | null; source: string };
};
export type LiveEvent = { id: number; run_id: number | null; type: string; payload: any; ts: string };
export type Approval = {
  id: number; run_id: number; team_key: string; title: string; payload: any; status: string;
};
export type Status = { modes: Record<string, string>; demo_mode: boolean; auth_required: boolean };
export type Metrics = {
  runs_total: number; runs_completed: number; steps_executed: number; approvals: number;
  hours_saved: number; manual_minutes_per_run: number; automatable_hours_per_week: number;
};
export type Outbox = {
  emails: { to: string; intended_to: string; subject: string; body: string; mode: string }[];
  slack: { text: string; mode: string }[];
  messages?: { tool: string; action: string; mode: string; summary: string; detail: any; ts: number }[];
};
