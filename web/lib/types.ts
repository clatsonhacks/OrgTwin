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
};
export type Workflow = {
  id: number; team_key: string; role_key: string; name: string; goal: string;
  kind: "hero" | "draft" | "skill"; steps: Step[]; mocked?: boolean;
  meta?: Record<string, any>; executable?: boolean;
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
};
