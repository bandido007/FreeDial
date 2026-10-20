"""JSON representation of the EXISTING management workspace.

The registered ModelAdmins remain the permission, validation and save policy. This
adapter exposes their forms and inline formsets as data to Angular; it neither
accepts arbitrary model names nor creates a second set of business rules. Calls
and agent live state still use their existing GraphQL commands and WebSockets.
"""

import json
import re
from dataclasses import asdict
from datetime import date, datetime
from functools import wraps

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.models import LogEntry
from django.contrib.admin.utils import flatten_fieldsets, label_for_field, lookup_field
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404, JsonResponse, QueryDict
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils.text import capfirst
from django.views.decorators.http import require_GET
from oauth2_provider.contrib.rest_framework import OAuth2Authentication, TokenHasReadWriteScope
from oauth2_provider.models import get_application_model
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.agentdesk.views import page_messages as agent_messages
from apps.agents.models import Agent, Skill
from apps.authentication.middleware import UnrestrictedSession
from apps.configuration import services as configuration
from apps.core.exceptions import ApplicationError
from apps.core.hooks import resolve_permissions
from apps.queues.models import Queue
from apps.rbac.models import Role
from apps.reports.selectors import build_report, period
from apps.reports.tables import tables
from apps.setup import checklist
from apps.setup import services as setup_services
from apps.setup.permissions import CONFIRM_SETUP_TESTS, VIEW_SETUP_GUIDE
from apps.supervisor.views import page_messages as supervisor_messages

# Only apps whose management pages already exist in AlgoDial. Third-party OAuth
# internals and future apps do not automatically become frontend features.
MANAGED_APPS = frozenset(
    {
        "agents",
        "ai",
        "attachments",
        "audit",
        "authentication",
        "calls",
        "configuration",
        "notifications",
        "queues",
        "rbac",
        "reports",
        "setup",
        "teams",
        "telephony",
        "users",
    }
)
SENSITIVE = re.compile(r"password|secret|token|code_hash|api_key|credential", re.I)


def endpoint(methods, *, staff=True):
    def decorate(fn):
        @api_view(methods)
        @authentication_classes([OAuth2Authentication])
        @permission_classes([IsAuthenticated, TokenHasReadWriteScope, UnrestrictedSession])
        @wraps(fn)
        def view(request, *args, **kwargs):
            if staff and not (request.user.is_active and request.user.is_staff):
                raise PermissionDenied
            try:
                response = fn(request, *args, **kwargs)
            except ApplicationError as error:
                response = Response({"message": error.message, "code": error.code}, status=400)
            except DjangoValidationError as error:
                response = Response({"message": str(error)}, status=400)
            response["Cache-Control"] = "private, no-store"
            return response

        return view

    return decorate


def model_admin(label):
    for model, manager in admin.site._registry.items():
        if model._meta.app_label in MANAGED_APPS and model._meta.label_lower == label:
            return manager
    raise Http404("This management page does not exist.")


def authorize(manager, request, obj=None, mode="view"):
    if not manager.has_module_permission(request):
        raise PermissionDenied
    permission = (
        manager.has_view_or_change_permission
        if mode == "view"
        else getattr(manager, f"has_{mode}_permission")
    )
    allowed = permission(request) if mode == "add" else permission(request, obj)
    if not allowed:
        raise PermissionDenied


def text(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return strip_tags(str(value))


def display(manager, obj, name):
    # Never serialize model objects or rendered HTML. Media URLs are extracted
    # separately and fetched with the caller's bearer token.
    if SENSITIVE.search(str(name)):
        return "Not displayed"
    try:
        _, _, value = lookup_field(name, obj, manager)
        if hasattr(value, "all"):
            return ", ".join(str(item) for item in value.all()[:100])
        return text(value)
    except (AttributeError, TypeError):
        return ""


def summary(manager, request):
    opts = manager.model._meta
    special = {
        "reports.report": "reports",
        "setup.setupconfirmation": "setup",
        "configuration.systemsetting": "settings",
    }
    return {
        "key": opts.label_lower,
        "name": str(capfirst(opts.verbose_name_plural)),
        "group": str(opts.app_config.verbose_name),
        "kind": special.get(opts.label_lower, "records"),
        "canAdd": manager.has_add_permission(request),
        "canChange": manager.has_change_permission(request),
        "canDelete": manager.has_delete_permission(request),
    }


@endpoint(["GET"], staff=False)
def bootstrap(request):
    user = request.user
    app = get_application_model().objects.filter(name=settings.OAUTH_APP_NAME).first()
    return Response(
        {
            "user": {
                "uid": str(user.uid),
                "name": user.get_full_name() or user.email,
                "email": user.email,
                "staff": user.is_staff,
                "superuser": user.is_superuser,
            },
            "permissions": sorted(resolve_permissions(user, request)),
            "clientId": app.client_id if app else "",
            "agentConfigured": Agent.objects.filter(user=user, active=True).exists(),
            "agentMessages": agent_messages(),
            "supervisorMessages": supervisor_messages(),
        }
    )


@require_GET
def public_config(request):
    app = get_application_model().objects.filter(name=settings.OAUTH_APP_NAME).first()
    response = JsonResponse({"clientId": app.client_id if app else ""})
    response["Cache-Control"] = "no-store"
    return response


@endpoint(["GET"])
def catalogue(request):
    rows = [
        summary(manager, request)
        for model, manager in admin.site._registry.items()
        if model._meta.app_label in MANAGED_APPS
        and manager.has_module_permission(request)
        and manager.has_view_or_change_permission(request)
    ]
    return Response(sorted(rows, key=lambda row: (row["group"], row["name"])))


def row_data(manager, request, obj, columns):
    return {
        "id": str(obj.pk),
        "label": str(obj),
        "values": [display(manager, obj, name) for name in columns],
        "canChange": manager.has_change_permission(request, obj),
        "canDelete": manager.has_delete_permission(request, obj),
    }


@endpoint(["GET"])
def records(request, label):
    manager = model_admin(label)
    authorize(manager, request)
    rows = manager.get_queryset(request)
    columns = list(manager.get_list_display(request))
    search = request.GET.get("q", "").strip()
    if search:
        rows, duplicates = manager.get_search_results(request, rows, search)
        if duplicates:
            rows = rows.distinct()
    allowed_filters = [f for f in manager.get_list_filter(request) if isinstance(f, str)]
    for name in allowed_filters:
        if name in request.GET and request.GET[name] != "":
            value = request.GET[name]
            if manager.model._meta.get_field(name).get_internal_type() == "BooleanField":
                value = value.lower() == "true"
            rows = rows.filter(**{name: value})
    if manager.date_hierarchy:
        field = manager.model._meta.get_field(manager.date_hierarchy)
        lookup = manager.date_hierarchy + ("__date" if field.get_internal_type() == "DateTimeField" else "")
        for parameter, operator in (("dateFrom", "gte"), ("dateTo", "lte")):
            if request.GET.get(parameter):
                try:
                    value = date.fromisoformat(request.GET[parameter])
                except ValueError as error:
                    raise DjangoValidationError("Enter a valid filter date.") from error
                rows = rows.filter(**{lookup + "__" + operator: value})
    # A sort name must be a concrete model field, never an arbitrary ORM path.
    sort = request.GET.get("sort", "")
    concrete = {f.name for f in manager.model._meta.concrete_fields} & set(columns)
    if sort.lstrip("-") in concrete:
        rows = rows.order_by(sort)
    elif not rows.ordered:
        rows = rows.order_by("pk")
    page = Paginator(rows, 25).get_page(request.GET.get("page", 1))
    filters = []
    for name in allowed_filters:
        try:
            field = manager.model._meta.get_field(name)
            if field.is_relation:
                choices = options(field.formfield(), request)
            elif field.choices:
                choices = [{"value": str(k), "label": str(v)} for k, v in field.flatchoices]
            elif field.get_internal_type() == "BooleanField":
                choices = [{"value": "true", "label": "Yes"}, {"value": "false", "label": "No"}]
            else:
                choices = [
                    {"value": str(v), "label": str(v)}
                    for v in rows.values_list(name, flat=True).distinct()[:100]
                    if v is not None
                ]
            if choices:
                filters.append({"name": name, "label": str(field.verbose_name), "options": choices})
        except Exception:  # A custom list filter continues to work on its native page.
            continue
    return Response(
        {
            **summary(manager, request),
            "columns": [
                {
                    "name": name,
                    "label": str(label_for_field(name, manager.model, manager)),
                    "sortable": name in concrete,
                }
                for name in columns
            ],
            "rows": [row_data(manager, request, obj, columns) for obj in page],
            "page": page.number,
            "pages": page.paginator.num_pages,
            "total": page.paginator.count,
            "filters": filters,
            "dateField": manager.date_hierarchy,
            "actions": [
                {"name": key, "label": str(value[2])} for key, value in manager.get_actions(request).items()
            ],
        }
    )


def options(field, request, selected=None, query=""):
    if isinstance(field, forms.ModelChoiceField):
        related_admin = admin.site._registry.get(field.queryset.model)
        if related_admin is not None and not related_admin.has_view_or_change_permission(request):
            return []
        rows = field.queryset
        if query and related_admin:
            rows, duplicate = related_admin.get_search_results(request, rows, query)
            if duplicate:
                rows = rows.distinct()
        selected_ids = selected if isinstance(selected, (list, tuple)) else [selected]
        chosen = rows.filter(pk__in=[value for value in selected_ids if value not in (None, "")])
        rows = list(chosen[:100]) + list(rows[:100])
        seen = set()
        result = []
        for obj in rows:
            if obj.pk in seen:
                continue
            seen.add(obj.pk)
            result.append({"value": str(obj.pk), "label": field.label_from_instance(obj)})
        return result
    if isinstance(field, forms.ChoiceField):
        result = []
        for value, label in field.choices:
            if isinstance(label, (list, tuple)):
                result.extend({"value": str(k), "label": str(v)} for k, v in label)
            else:
                result.append({"value": str(value), "label": str(label)})
        return result
    return []


def field_data(form, name, request, readonly=False):
    field, bound = form.fields[name], form[name]
    value = bound.value()
    if isinstance(field, forms.ModelMultipleChoiceField):
        value = [str(v) for v in value or []]
    elif isinstance(field, forms.ModelChoiceField) and value is not None:
        value = str(value)
    elif isinstance(value, (date, datetime)):
        value = value.isoformat()
    elif isinstance(value, (dict, list)) and not isinstance(field, forms.MultipleChoiceField):
        value = json.dumps(value, ensure_ascii=False, indent=2)
    kind = "text"
    if isinstance(field.widget, forms.PasswordInput) or SENSITIVE.search(name):
        kind, value = "password", ""
    elif isinstance(field, forms.MultipleChoiceField):
        kind = "multiselect"
    elif isinstance(field, forms.ChoiceField):
        kind = "select"
    elif isinstance(field, forms.BooleanField):
        kind = "checkbox"
    elif isinstance(field, forms.SplitDateTimeField):
        kind = "datetime"
        if isinstance(value, str):
            value = value.replace(" ", "T")[:19]
    elif isinstance(field, forms.DateTimeField):
        kind = "datetime"
    elif isinstance(field, forms.DateField):
        kind = "date"
    elif isinstance(field, (forms.IntegerField, forms.DecimalField, forms.FloatField)):
        kind = "number"
    elif isinstance(field, forms.FileField):
        kind, value = "file", ""
    elif isinstance(field.widget, forms.Textarea) or isinstance(field, forms.JSONField):
        kind = "textarea"
    elif isinstance(field, forms.EmailField):
        kind = "email"
    # Django's read-only password hash widget must never reach a browser JSON payload.
    if "ReadOnlyPassword" in type(field.widget).__name__:
        kind, value, readonly = "readonly", "Use password reset to change this user's password.", True
    return {
        "name": name,
        "key": bound.html_name,
        "id": bound.auto_id,
        "label": str(field.label or name),
        "kind": kind,
        "value": value,
        "required": field.required,
        "readonly": readonly or field.disabled,
        "hidden": field.widget.is_hidden,
        "help": text(field.help_text),
        "errors": [text(e) for e in bound.errors],
        "options": options(field, request, value) if kind in ("select", "multiselect") else [],
        "searchable": isinstance(field, forms.ModelChoiceField),
        "min": getattr(field, "min_value", None),
        "max": getattr(field, "max_value", None),
        "maxLength": getattr(field, "max_length", None),
        "split": isinstance(field, forms.SplitDateTimeField),
    }


def readonly_data(manager, obj, name):
    value = display(manager, obj, name) if obj else ""
    media = ""
    if obj and name in ("recording_player", "message"):
        _, _, raw = lookup_field(name, obj, manager)
        match = re.search(r'<audio[^>]+src="(/calls/[^\"]+)"', str(raw))
        media = match[1] if match else ""
    return {
        "name": name,
        "key": name,
        "label": str(label_for_field(name, manager.model, manager)),
        "kind": "readonly",
        "value": value,
        "readonly": True,
        "media": media,
        "errors": [],
    }


def form_context(manager, request, obj, data=None, files=None):
    adding = obj is None
    form_class = manager.get_form(request, obj, change=not adding)
    form = form_class(
        data=data,
        files=files,
        instance=obj,
        initial=manager.get_changeform_initial_data(request) if adding else None,
    )
    formsets = []
    for formset_class, inline in manager.get_formsets_with_inlines(request, obj):
        prefix = formset_class.get_default_prefix()
        formsets.append(
            (
                formset_class(
                    data=data,
                    files=files,
                    instance=form.instance,
                    prefix=prefix,
                    queryset=inline.get_queryset(request),
                ),
                inline,
            )
        )
    return form, formsets


def serialized_form(manager, request, obj, form, formsets):
    can_change = (
        manager.has_add_permission(request) if obj is None else manager.has_change_permission(request, obj)
    )
    names = list(flatten_fieldsets(manager.get_fieldsets(request, obj)))
    for name in form.fields:
        if name not in names:
            names.append(name)
    fields = [
        field_data(form, name, request, not can_change)
        if name in form.fields
        else readonly_data(manager, obj, name)
        for name in names
    ]
    groups = []
    for group, inline in formsets:
        editable = can_change and (
            inline.has_change_permission(request, obj) or inline.has_add_permission(request, obj)
        )
        rows = []
        for child in group.forms:
            child_fields = [field_data(child, name, request, not editable) for name in child.fields]
            for name in inline.get_readonly_fields(request, obj):
                child_fields.append(
                    readonly_data(inline, child.instance if child.instance.pk else None, name)
                )
            rows.append({"fields": child_fields, "errors": [text(e) for e in child.non_field_errors()]})
        groups.append(
            {
                "name": str(inline.opts.verbose_name_plural),
                "prefix": group.prefix,
                "rows": rows,
                "empty": {
                    "fields": [
                        field_data(group.empty_form, name, request, not editable)
                        for name in group.empty_form.fields
                    ]
                },
                "management": {b.html_name: b.value() for b in group.management_form},
                "canAdd": can_change and inline.has_add_permission(request, obj),
                "errors": [text(e) for e in group.non_form_errors()],
            }
        )
    return {
        **summary(manager, request),
        "id": str(obj.pk) if obj else None,
        "label": str(obj) if obj else "New record",
        "fields": fields,
        "inlines": groups,
        "errors": [text(e) for e in form.non_field_errors()],
        "canChange": can_change,
        "canDelete": bool(obj and manager.has_delete_permission(request, obj)),
    }


def payload(request):
    if request.content_type == "application/json":
        values = request.data.get("values", {})
        if not isinstance(values, dict):
            raise DjangoValidationError("Form values must be an object.")
        result = QueryDict("", mutable=True)
        for key, value in values.items():
            if isinstance(value, list):
                result.setlist(key, [str(v) for v in value])
            elif value is not None:
                result[key] = str(value) if not isinstance(value, bool) else str(value).lower()
        return result
    return request.POST


@endpoint(["GET", "POST", "DELETE"])
def record(request, label, pk):
    manager = model_admin(label)
    obj = None if pk == "new" else manager.get_object(request, pk)
    if pk != "new" and obj is None:
        raise Http404("Record not found.")
    mode = (
        "view"
        if request.method == "GET"
        else "delete"
        if request.method == "DELETE"
        else "add"
        if obj is None
        else "change"
    )
    authorize(manager, request, obj, mode)
    if request.method == "DELETE":
        if request.data.get("confirm") is not True:
            return Response({"message": "Confirm deletion first."}, status=400)
        deleted, counts, lacking, protected = manager.get_deleted_objects([obj], request)
        if lacking or protected:
            return Response(
                {"message": "Related records prevent deletion.", "protected": [text(v) for v in protected]},
                status=400,
            )
        with transaction.atomic():
            manager.log_deletions(request, [obj])
            manager.delete_model(request, obj)
        return Response({"message": "Deleted."})
    form, formsets = form_context(
        manager, request, obj, payload(request) if request.method == "POST" else None, request.FILES or None
    )
    if request.method == "POST":
        valid = form.is_valid()
        for formset, _inline in formsets:
            valid = formset.is_valid() and valid
        if valid:
            with transaction.atomic():
                saved = manager.save_form(request, form, change=obj is not None)
                manager.save_model(request, saved, form, change=obj is not None)
                manager.save_related(request, form, [group for group, _ in formsets], change=obj is not None)
                if obj is None:
                    manager.log_addition(request, saved, "Created in Angular workspace")
                else:
                    manager.log_change(request, saved, "Updated in Angular workspace")
            return Response(
                {
                    "id": str(saved.pk),
                    "message": "Saved.",
                    "messages": [str(item) for item in messages.get_messages(request._request)],
                }
            )
        return Response(serialized_form(manager, request, obj, form, formsets), status=422)
    result = serialized_form(manager, request, obj, form, formsets)
    if label == "queues.skillruleset" and (
        request.user.is_superuser or "can_view_agents" in resolve_permissions(request.user, request)
    ):
        result["rulePreview"] = {
            "agents": [
                {
                    "uid": str(agent.uid),
                    "name": agent.name,
                    "number": agent.number,
                    "skills": {row.skill.name: row.level for row in agent.skills.all()},
                }
                for agent in Agent.objects.filter(active=True).prefetch_related("skills__skill")
            ],
            "languages": list(
                Skill.objects.filter(kind="language", active=True).values_list("name", flat=True)
            ),
            "queues": [
                {
                    "uid": str(queue.uid),
                    "name": queue.name,
                    "agents": [str(agent.uid) for agent in queue.agents.all()],
                }
                for queue in Queue.objects.filter(skill_rule_set=obj, active=True).prefetch_related("agents")
            ]
            if obj
            else [],
        }
    if obj and result["canDelete"]:
        deleted, counts, lacking, protected = manager.get_deleted_objects([obj], request)
        result["deleteImpact"] = {
            "counts": {str(key): value for key, value in counts.items()},
            "blocked": bool(lacking or protected),
        }
    return Response(result)


@endpoint(["GET"])
def choices(request, label):
    manager = model_admin(label)
    pk = request.GET.get("record", "new")
    obj = None if pk == "new" else manager.get_object(request, pk)
    authorize(manager, request, obj, "add" if obj is None else "view")
    form, formsets = form_context(manager, request, obj)
    prefix = request.GET.get("inline")
    if prefix:
        group = next((group for group, _inline in formsets if group.prefix == prefix), None)
        if group is None:
            raise Http404
        form = group.empty_form
    field = form.fields.get(request.GET.get("field", ""))
    if field is None:
        raise Http404
    return Response(options(field, request, query=request.GET.get("q", "")))


@endpoint(["POST"])
def action(request, label):
    manager = model_admin(label)
    authorize(manager, request)
    name = request.data.get("action")
    selected = request.data.get("ids", [])
    if not isinstance(selected, list) or not selected or len(selected) > 100:
        raise DjangoValidationError("Select between 1 and 100 records.")
    registered = manager.get_actions(request).get(name)
    if registered is None:
        raise PermissionDenied
    rows = list(manager.get_queryset(request).filter(pk__in=selected))
    if len(rows) != len(set(selected)):
        raise PermissionDenied
    if request.data.get("confirm") is not True:
        raise DjangoValidationError("Confirm this action first.")
    with transaction.atomic():
        if name == "delete_selected":
            for obj in rows:
                authorize(manager, request, obj, "delete")
                _deleted, _counts, lacking, protected = manager.get_deleted_objects([obj], request)
                if lacking or protected:
                    raise DjangoValidationError("Related records prevent deletion.")
            manager.log_deletions(request, rows)
            manager.delete_queryset(request, manager.get_queryset(request).filter(pk__in=selected))
        else:
            if not all(manager.has_change_permission(request, obj) for obj in rows):
                raise PermissionDenied
            registered[0](manager, request, manager.get_queryset(request).filter(pk__in=selected))
    return Response({"message": "Action completed."})


@endpoint(["GET"])
def reports(request):
    manager = model_admin("reports.report")
    authorize(manager, request)
    try:
        start = date.fromisoformat(request.GET["since"]) if request.GET.get("since") else None
        end = date.fromisoformat(request.GET["until"]) if request.GET.get("until") else None
        since, until = period(request.GET.get("period", "today"), start, end)
    except ValueError as error:
        raise DjangoValidationError("Choose a valid report period and date range.") from error
    report = build_report(since=since, until=until)
    return Response(
        {
            "since": since,
            "until": report.until,
            "tables": [
                {"key": key, "title": table.title, "columns": table.columns, "rows": table.rows}
                for key, table in tables(report).items()
            ],
        }
    )


@endpoint(["GET", "POST"])
def setup(request):
    codes = resolve_permissions(request.user, request)
    if not (request.user.is_superuser or VIEW_SETUP_GUIDE in codes):
        raise PermissionDenied
    if request.method == "POST":
        if not (request.user.is_superuser or CONFIRM_SETUP_TESTS in codes):
            raise PermissionDenied
        operation = request.data.get("action")
        if operation not in ("confirm", "clear"):
            raise DjangoValidationError("Choose confirm or clear.")
        if operation == "confirm":
            setup_services.confirm(
                actor=request.user, check=request.data.get("check", ""), note=request.data.get("note", "")
            )
        else:
            setup_services.clear(actor=request.user, check=request.data.get("check", ""))
    sections = checklist.build()
    done, counted, attention = checklist.progress(sections)
    roles = [
        (role, sorted(link.permission.name for link in role.role_permissions.all()))
        for role in Role.objects.filter(active=True)
        .prefetch_related("role_permissions__permission")
        .order_by("name")
    ]
    html = render_to_string(
        "setup/guide.html",
        {**admin.site.each_context(request), "roles": roles, "sections": [], "title": "Setup guide"},
        request=request._request,
    )
    manual = html.split('<section id="manual"', 1)[1].split("</section>", 1)[0]
    manual = '<section id="manual"' + manual + "</section>"

    def frontend_link(match):
        app, model, pk, add = match.groups()
        key = app + "." + model
        path = {
            "reports.report": "/reports",
            "setup.setupconfirmation": "/setup",
            "configuration.systemsetting": "/manage/settings",
        }.get(key, "/manage/" + key)
        return 'href="' + path + ("?record=" + pk if pk else "?record=new" if add else "") + '"'

    manual = re.sub(r'href="/admin/([^/]+)/([^/]+)/(?:([0-9]+)/change/|(add)/)?"', frontend_link, manual)
    return Response(
        {
            "sections": [asdict(section) for section in sections],
            "manual": manual,
            "done": done,
            "counted": counted,
            "attention": attention,
            "canConfirm": request.user.is_superuser or CONFIRM_SETUP_TESTS in codes,
        }
    )


def frontend(request, path=""):
    """Serve the built Angular app and its assets on the existing Django origin.

    API/media endpoints keep their existing authorization. The entry HTML is
    public, just like /agent/; route guards and authenticated APIs protect data.
    """
    from pathlib import Path
    from secrets import token_urlsafe

    from django.http import FileResponse, HttpResponse

    from apps.agentdesk.views import page_headers

    root = (Path(settings.BASE_DIR) / "frontend/dist/algodial/browser").resolve()
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root):
        raise Http404
    if path and candidate.is_file():
        response = FileResponse(candidate.open("rb"))
        response["Cache-Control"] = (
            "public, max-age=31536000, immutable"
            if re.search(r"-[A-Za-z0-9_]{8,}\.", candidate.name)
            else "no-cache"
        )
        return response
    if Path(path).suffix and not path.startswith("manage/"):
        raise Http404("Frontend asset not found.")
    index = root / "index.html"
    if not index.is_file():
        return HttpResponse(
            "Build the Angular frontend with npm run build --prefix frontend.",
            status=503,
            content_type="text/plain",
        )
    nonce = token_urlsafe(24)
    html = index.read_text().replace("<app-root", f'<app-root ngCspNonce="{nonce}"', 1)
    response = page_headers(request, HttpResponse(html))
    response["Content-Security-Policy"] = response["Content-Security-Policy"].replace(
        "style-src 'self'", f"style-src 'self' 'nonce-{nonce}'"
    )
    response["Content-Security-Policy"] = response["Content-Security-Policy"].replace(
        "base-uri 'none'", "base-uri 'self'"
    )
    response["Cache-Control"] = "no-store"
    return response


@endpoint(["GET", "POST"])
def settings_view(request):
    manager = model_admin("configuration.systemsetting")
    authorize(manager, request, mode="change" if request.method == "POST" else "view")
    if request.method == "POST":
        if request.data.get("action") == "reset":
            return Response(configuration.reset_value(key=request.data.get("key", ""), actor=request.user))
        return Response(
            configuration.set_value(
                key=request.data.get("key", ""), value=request.data.get("value"), actor=request.user
            )
        )
    return Response(
        {"rows": configuration.all_settings(), "canChange": manager.has_change_permission(request)}
    )


@endpoint(["POST"])
def user_password(request, pk):
    manager = model_admin("users.user")
    obj = manager.get_object(request, pk)
    if obj is None:
        raise Http404
    authorize(manager, request, obj, "change")
    form = manager.change_password_form(obj, request.data)
    if not form.is_valid():
        return Response(
            {
                "message": "Check the password fields.",
                "errors": {name: [str(error) for error in errors] for name, errors in form.errors.items()},
            },
            status=422,
        )
    with transaction.atomic():
        saved = form.save()
        manager.log_change(request, saved, "Password changed in Angular workspace")
    return Response(
        {"message": "Password changed." if saved.has_usable_password() else "Password sign-in disabled."}
    )


@endpoint(["GET"])
def history(request, label, pk):
    manager = model_admin(label)
    obj = manager.get_object(request, pk)
    if obj is None:
        raise Http404
    authorize(manager, request, obj)
    entries = (
        LogEntry.objects.filter(
            content_type=ContentType.objects.get_for_model(manager.model), object_id=str(obj.pk)
        )
        .select_related("user")
        .order_by("-action_time")[:100]
    )
    return Response(
        [
            {"at": entry.action_time, "user": str(entry.user), "action": entry.get_change_message()}
            for entry in entries
        ]
    )
