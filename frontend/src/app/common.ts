export interface Person {
  uid: string;
  name: string;
  email: string;
  staff: boolean;
  superuser: boolean;
}
export interface Session {
  access: string;
  refresh: string;
  expiresAt: number;
  requirements?: { password_change_required?: boolean; mfa_enrolment_required?: boolean };
}
export interface Bootstrap {
  user: Person;
  permissions: string[];
  clientId: string;
  agentMessages: Record<string, Record<string, string>>;
  supervisorMessages: Record<string, Record<string, string>>;
}
export interface Resource {
  key: string;
  name: string;
  group: string;
  kind: string;
  canAdd: boolean;
  canChange: boolean;
  canDelete: boolean;
}
export interface Option {
  value: string;
  label: string;
}
export interface Field {
  name: string;
  key: string;
  id?: string;
  label: string;
  kind: string;
  value: any;
  required?: boolean;
  readonly?: boolean;
  hidden?: boolean;
  help?: string;
  errors: string[];
  options?: Option[];
  searchable?: boolean;
  split?: boolean;
  min?: number;
  max?: number;
  maxLength?: number;
  media?: string;
}
export interface InlineRow {
  fields: Field[];
  errors?: string[];
}
export interface Inline {
  name: string;
  prefix: string;
  rows: InlineRow[];
  empty: InlineRow;
  management: Record<string, any>;
  canAdd: boolean;
  errors: string[];
}
export interface Editor extends Resource {
  id: string | null;
  label: string;
  fields: Field[];
  inlines: Inline[];
  errors: string[];
  rulePreview?: {
    agents: { uid: string; name: string; number: string; skills: Record<string, number> }[];
    languages: string[];
    queues: { uid: string; name: string; agents: string[] }[];
  };
  deleteImpact?: { counts: Record<string, number>; blocked: boolean };
}
export interface RecordRow {
  id: string;
  label: string;
  values: string[];
  canChange: boolean;
  canDelete: boolean;
}
export interface Listing extends Resource {
  dateField?: string;
  columns: { name: string; label: string; sortable: boolean }[];
  rows: RecordRow[];
  page: number;
  pages: number;
  total: number;
  filters: { name: string; label: string; options: Option[] }[];
  actions: { name: string; label: string }[];
}
