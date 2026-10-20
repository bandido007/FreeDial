import { Resource } from "../common";

/** Presentation of existing, permission-filtered AlgoDial pages. */
export interface WorkspaceArea {
  key: string;
  name: string;
  description: string;
  icon: string;
  rows: Resource[];
}
const areas = [
  {
    key: "people",
    name: "People & teams",
    description: "Organise the people who answer your calls.",
    icon: "users",
    apps: ["agents", "teams"],
    first: ["agents.agent", "teams.team", "agents.skill"],
  },
  {
    key: "routing",
    name: "Routing & phones",
    description: "Connect each caller to the right destination.",
    icon: "route",
    apps: ["queues", "telephony"],
    first: ["queues.queue", "queues.skillruleset", "queues.entrypoint", "telephony.trunk"],
  },
  {
    key: "ai",
    name: "AI agents",
    description: "Configure the AI agents that greet your callers.",
    icon: "wave",
    apps: ["ai"],
    first: [],
  },
  {
    key: "calls",
    name: "Calls & reports",
    description: "Review conversations and centre performance.",
    icon: "report",
    apps: ["calls", "reports"],
    first: ["calls.call", "calls.callbacktask", "reports.report"],
  },
  {
    key: "access",
    name: "Access & security",
    description: "Manage accounts, roles and sign-in policies.",
    icon: "shield",
    apps: ["users", "rbac", "authentication"],
    first: ["users.user", "rbac.role", "rbac.permission"],
  },
  {
    key: "system",
    name: "Centre settings",
    description: "Set up your centre and keep its records in order.",
    icon: "settings",
    apps: ["configuration", "setup", "notifications", "attachments", "audit"],
    first: ["setup.setupconfirmation", "configuration.systemsetting"],
  },
];
export function workspaceAreas(resources: Resource[], query = ""): WorkspaceArea[] {
  const term = query.trim().toLowerCase();
  return areas
    .map((area) => {
      const rows = resources.filter((resource) => {
        const app = resource.key.split(".")[0];
        const owner = areas.find((candidate) => candidate.apps.includes(app))?.key || "system";
        return (
          owner === area.key &&
          (!term || `${resource.name} ${resource.group} ${area.name}`.toLowerCase().includes(term))
        );
      });
      const rank = (resource: Resource) => {
        const index = area.first.indexOf(resource.key);
        return index < 0 ? area.first.length : index;
      };
      rows.sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
      return { key: area.key, name: area.name, description: area.description, icon: area.icon, rows };
    })
    .filter((area) => area.rows.length);
}
export function resourceLink(resource: Resource): string {
  return resource.kind === "settings"
    ? "/manage/settings"
    : resource.kind === "reports"
      ? "/reports"
      : resource.kind === "setup"
        ? "/setup"
        : "/manage/" + resource.key;
}
export function resourceIcon(resource: Resource): string {
  return workspaceAreas([resource])[0]?.icon || "settings";
}
