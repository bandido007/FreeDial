# Adding a business app

The contract between an app and the platform: which files an app may ship, what
each one must contain, and what the starter does with it. `make app name=<name>`
writes the mandatory ones; the optional ones you add when the feature needs them.

## 1. Scaffold

```bash
make app name=invoices            # → apps/invoices/, wired into settings, modules and the lint rules
make migrations app=invoices && make migrate
make seed && make schema && make test
```

Options: `--model Bill` (name of the first model, default the singular of the app
name), `--verbose "Billing"`. The app gets the next free band of 100 status codes
at or above 10000, declared on its `AppConfig` as `status_code_range`.

The generator inserts one line at each of these markers (never edit them away):
`# <scaffold:project-apps>` in `config/settings/base.py`,
`# <scaffold:project-modules>` in `config/modules.py`,
`# <scaffold:project-layers>` and `# <scaffold:project-forbidden>` in `pyproject.toml`.

## 2. The mandatory files

| File | Contains | Rules |
|------|----------|-------|
| `apps.py` | `AppConfig` with explicit `name`, `label`, `verbose_name`, `status_code_range`; `ready()` connects receivers and registers file purposes | `label` is how models are referenced (`invoices.Invoice`) and how the catalogue attributes declarations |
| `models.py` | `BaseModel` subclasses | `uid` (UUIDv7), `created_at/updated_at`, `created_by/updated_by/deleted_by`, soft delete. `objects` hides deleted rows, `all_objects` shows them. Audited automatically. Use `db_table = "<label>_<model>"` |
| `enums.py` | `BaseEnum` choices + `graphene.Enum.from_enum` | Enum *values* are what the API shows; keep them stable |
| `selectors.py` | Reads: `get_<x>(uid)` raising `NotFoundError`, `<x>s_queryset(filters)` | Never mutate. `select_related` what the type will resolve |
| `services.py` | Writes + rules, keyword-only arguments | Check the rule, then write. Raise `ValidationError`, `PermissionDeniedError`, `NotFoundError`, `DuplicateError`, `ThrottledError` (all `ApplicationError`) with a `code`. Log at `info` with the app label in brackets |
| `types.py` | `DjangoObjectType`s with explicit `fields`; enum fields declared explicitly | Never expose `id`, `password`, secrets or FKs by integer; add `<thing>_uid` / `<thing>_name` resolvers instead |
| `inputs.py` | `InputObjectType`s: one `<Model>Input` (create + update, `uid` optional) and one `<Model>FilterInput` | |
| `queries.py` | `Query` with `Response(...)` / `Page(...)` fields and thin resolvers | Decorators innermost-first: `@has_permission` / `@authenticated`, then `@log_errors_and_warnings`. Put `permissions=['code']` in the field description — the API reference and a guard test read it |
| `mutations.py` | `graphene.Mutation` classes with `Output = Response.get_response_type(Type)` and a `Mutation` root | `@classmethod` outermost, then `@has_permission`, then `@log_errors_and_warnings`. Check required arguments with `Utils.empty_and_null_checker` → code `9005` |
| `permissions.py` | `GROUPS` (`Group` → `Perm(code, name, description)`), `ROLES` (`RoleSpec`) | Codes are `can_<verb>_<noun>`; the level (VIEW / CHANGE / DECIDE) is derived from the verb. Every code a resolver requires must be declared; every declared code must be used |
| `status_codes.json` | `[{"code", "status", "messageEn", "messageSw"}]` | Inside the app's band; unique; every language in `API_LANGUAGES` |
| `admin.py` | A `ModelAdmin` per model | For operators; the SPA is the product |
| `tests/` | pytest: rules through services, API through `gql()` | Use the root fixtures (`make_user`, `grant`, `admin`, `gql`, `api`, `mailbox`) |

## 3. The optional files

| File | When | Shape |
|------|------|-------|
| `notifications.py` | The app sends messages | `CATEGORIES = [Category(code, name, description, templates=[Template("EMAIL", subject, body)], mandatory=False, sensitive=False)]`. Send with `notification_services.queue(...)` (Celery, on commit) or `send_now(...)`. Deep links come from `notifications.routes` helpers, never hand-built |
| `settings_catalogue.py` | Administrators should tune a rule at runtime | `SETTINGS = [SettingSpec(key, group, name, description, type, default, minimum, maximum, choices, public)]`; read with `policy("KEY", default)` |
| `schedule.py` | Periodic work | `BEAT_SCHEDULE = {"<label>-<what>": {"task": "<label>.<task>", "schedule": crontab(...)}}`. Keys are global — prefix with the app label |
| `tasks.py` | Celery tasks | `@shared_task(name="<label>.<task>")`; call a service, return something loggable |
| `workflows.py` | A process needs configurable approval | `WORKFLOWS = [spec]`, `NOTES = {key: WorkflowNote(...)}`, `AREA_ORDER = [...]`. See §5 |
| `receivers.py` | React to signals (`workflow_*`, `user_*`, model signals) | `connect()` called from `AppConfig.ready()`; receivers never raise |
| `seed.py` | Reference data the app needs (lookup rows, a default record) | `ORDER = 50`, `def run(command)`; idempotent — `make seed` runs on every deploy |
| `views.py` + `urls.py` | Something needs real HTTP (streaming, webhooks) | DRF `APIView`; keep the default permission classes (first-party token) unless the endpoint is deliberately public — then add it to `PUBLIC_DRF_VIEWS` in `tests/test_security_guards.py` |
| `checks.py` | Configuration the app cannot run without | `@register` Django checks with ids `<label>.E001…` |

## 4. Using the platform from your app

```python
from apps.core.hooks import policy, resolve_permissions  # runtime settings, permission checks
from apps.core.context import get_current_user, acting_as  # who is acting (audit uses it)
from apps.core.exceptions import ValidationError, NotFoundError, PermissionDeniedError
from apps.core.ratelimit import throttle, throttle_scope  # cache-backed counters
from apps.core.exports import build_table  # PDF / Word / Excel / CSV
from apps.core.crypto import encrypt_secret, decrypt_secret  # a *_encrypted column (rotate_secrets finds it)
from apps.audit.services import audited_update  # bulk update that leaves a trail
from apps.attachments import policies  # policies.register("invoice-document", rule, bucket="invoices")
from apps.attachments.services import store, link_to  # files for your records
from apps.notifications import services as notification_services
from apps.workflows import engine  # engine.start / engine.act / engine.available_actions
```

Files: give the attachment a `purpose` your app registered and link it with
`entity_type="invoices.Invoice", entity_uid=…`; the policy decides who may open it.

Workflows: `engine.start(workflow_key=…, entity_uid=…, entity_type=…, user=…)`
opens an item; `engine.act(entity_uid=…, action="APPROVE", user=…, comment=…)`
moves it. Your `receivers.py` listens to `workflow_started` / `workflow_closed`
and stamps your record's status from `entity_status`. The engine never touches
your table.

## 5. Shipping a workflow design

1. Declare it in `workflows.py` — `key`, `name`, `description`, `steps` with
   `transitions` whose `roles` are role **names** and whose `to_step` is a step
   **name** (`None` ends the workflow; the action decides the final status).
2. Annotate it in `NOTES` with a `WorkflowNote(area, governs, starts_when,
   worked_by, on_missing, notes)` — plain words for the analyst who will run it.
3. Add the area to `AREA_ORDER` if it is new.
4. `make workflow-docs` — regenerates `docs/WORKFLOW_CATALOGUE.md`.
5. `make seed` — installs and publishes it (only if nothing exists under the key).
6. `make test` — the guard fails on a design without a note, a stale document,
   or an entity status your receivers do not handle.

## 6. Conventions that tests enforce

- Operation names are unique across apps (`tests/test_api_contract.py`).
- `docs/schema.graphql` matches the live schema (`make schema` after any API change;
  its diff in the merge request *is* the API review).
- Every permission required by a resolver is declared; every declared permission is
  used; `permissions=[…]` notes in descriptions name real codes.
- Status codes are unique, in band, translated.
- The import order holds (`make lint`) — your app may import foundation apps; the
  reverse is refused.
- Nothing in a foundation app names a particular project or customer: what is
  project-specific lives in the environment (`PROJECT_NAME`, brand settings) or in
  business apps.

## 7. Removing an app

Delete the folder and the four scaffold-inserted lines; drop its tables with a
migration in another app or by hand; `make schema`. The permission catalogue
retires its codes on the next `make seed`.
