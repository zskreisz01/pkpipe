"""FastAPI application: JSON API (``/api``), HTMX UI (``/``), app MCP server (``/mcp``) (spec 014).

Every request passes :class:`pkpipe.api.security.GuardMiddleware` (Host allowlist, token or session,
CSRF/Origin for cookie requests, security headers). The routes are thin: the rules are in
:mod:`pkpipe.api.services`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from pkpipe.adapters.app_store_sqlite import RevisionConflict
from pkpipe.api import forms
from pkpipe.api.security import (
    SESSION_COOKIE,
    GuardMiddleware,
    LoginThrottle,
    Sessions,
    Tokens,
    allowed_hosts,
)
from pkpipe.api.services import AppServices, NotFound, Refused
from pkpipe.api.workspace import Conflict, WorkspaceError
from pkpipe.domain.triage import ItemState
from pkpipe.engine.registry import ConfigError
from pkpipe.mcp.server import build_app_mcp

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))


class FileEdit(BaseModel):
    path: str
    content: str | None = None  # None deletes the file from the overlay
    base_hash: str | None = None
    rev: int | None = None


class PublishRequest(BaseModel):
    summary: str = ""
    override_reason: str | None = None


class CategoryRequest(BaseModel):
    id: str
    label: str
    subflow: str
    para_path: str
    triage_hint: str
    rev: int | None = None


class RunRequest(BaseModel):
    flow: str
    inputs: dict[str, Any] | None = None


class RestoreRequest(BaseModel):
    apply: bool = False
    force: bool = False


class DecisionRequest(BaseModel):
    category: str | None = None


class CommentRequest(BaseModel):
    comment: str = ""


class RetryRequest(BaseModel):
    """The bulk retry of failed items needs the explicit ``confirm`` (spec 027)."""

    confirm: bool = False
    item_ids: list[str] | None = None


class BulkConfirmRequest(BaseModel):
    state: str | None = None
    category: str | None = None
    needs_owner: bool | None = None


def create_app(
    services: AppServices,
    tokens: Tokens,
    *,
    bind_host: str = "127.0.0.1",
    extra_hosts: tuple[str, ...] = (),
    mount_mcp: bool = True,
    public_url: str | None = None,
    on_shutdown: Callable[[], None] | None = None,
) -> FastAPI:
    sessions, throttle = Sessions(), LoginThrottle()
    mcp_app = None
    if mount_mcp:
        mcp_app = build_app_mcp(services, tokens).http_app(
            path="/mcp", allowed_hosts=list(extra_hosts) or None
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if mcp_app is not None:
            async with mcp_app.lifespan(app):
                yield
        else:
            yield
        if on_shutdown:
            on_shutdown()

    app = FastAPI(
        title="pkpipe",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        routes=list(mcp_app.routes) if mcp_app is not None else None,
    )
    app.state.services, app.state.sessions = services, sessions
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    app.add_middleware(
        GuardMiddleware, tokens=tokens, sessions=sessions, hosts=allowed_hosts(bind_host, None, extra_hosts)
    )

    # -- error mapping --------------------------------------------------------------------------------------
    def problem(status: int, exc: Exception) -> JSONResponse:
        return JSONResponse({"error": str(exc.args[0]) if exc.args else str(exc)}, status_code=status)

    app.add_exception_handler(NotFound, lambda r, e: problem(404, e))
    app.add_exception_handler(Refused, lambda r, e: problem(409, e))
    app.add_exception_handler(Conflict, lambda r, e: problem(409, e))
    app.add_exception_handler(RevisionConflict, lambda r, e: problem(409, e))
    app.add_exception_handler(WorkspaceError, lambda r, e: problem(400, e))
    app.add_exception_handler(ConfigError, lambda r, e: problem(422, e))
    app.add_exception_handler(ValueError, lambda r, e: problem(422, e))

    def render(request: Request, name: str, status_code: int = 200, **ctx: Any) -> HTMLResponse:
        sid = request.state.sid
        ctx.update(
            csrf=sessions.csrf(sid) if sid else "",
            owner=request.state.principal == "owner",
            editable=services.workspace.editable,
        )
        return templates.TemplateResponse(request, name, ctx, status_code=status_code)

    # -- login ----------------------------------------------------------------------------------------------
    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return render(request, "login.html", error=None)

    @app.post("/login")
    def login(request: Request, token: str = Form("")):
        if throttle.blocked():
            return render(request, "login.html", 429, error="Too many attempts. Wait a minute.")
        if tokens.scope_of(token.strip()) != "owner":
            throttle.failed()
            return render(request, "login.html", 401, error="Invalid token.")
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(
            SESSION_COOKIE,
            sessions.create(),
            httponly=True,
            samesite="strict",
            path="/",
            max_age=sessions.ttl_s,
            # behind `tailscale serve` the app sees http, but the owner's browser is on https
            secure=request.url.scheme == "https" or (public_url or "").startswith("https://"),
        )
        return resp

    @app.post("/logout")
    def logout(request: Request):
        sessions.drop(request.state.sid)
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    @app.get("/api/health")
    def health():
        return {"ok": True}

    # -- JSON API: runs and triage --------------------------------------------------------------------------
    @app.get("/api/flows")
    def api_flows():
        return {"flows": services.flows()}

    @app.get("/api/runs")
    def api_runs(status: str | None = None, flow: str | None = None, limit: int = 50):
        return {"runs": services.list_runs(status=status, flow_id=flow, limit=min(limit, 200))}

    @app.get("/api/runs/{run_id}")
    def api_run(run_id: str):
        return services.get_run(run_id)

    @app.post("/api/runs", status_code=202)
    def api_start_run(body: RunRequest):
        return services.start_run(body.flow, trigger="ui", actor="ui", inputs=body.inputs)

    @app.get("/api/snapshots")
    def api_snapshots():
        return {"snapshots": services.list_snapshots()}

    @app.post("/api/runs/{run_id}/restore")
    def api_restore(run_id: str, body: RestoreRequest):
        """Owner only (agents reach /mcp and /api/health only): the plan, applied with ``apply``."""
        return services.restore_run(run_id, apply=body.apply, force=body.force, actor="ui")

    @app.get("/api/triage")
    def api_triage(
        state: str | None = None,
        category: str | None = None,
        needs_owner: bool | None = None,
        outcome: str | None = None,
    ):
        return services.list_triage(state, category=category, needs_owner=needs_owner, outcome=outcome)

    @app.post("/api/triage/bulk-confirm")
    def api_bulk_confirm(body: BulkConfirmRequest):
        return services.bulk_confirm(
            actor="ui", state=body.state, category=body.category, needs_owner=body.needs_owner
        )

    @app.get("/api/triage/{item_id}/image")
    def api_triage_image(item_id: str):
        return image_response(services.item_image(item_id))

    @app.post("/api/triage/{item_id}/comment")
    def api_comment(item_id: str, body: CommentRequest):
        return services.comment_item(item_id, body.comment, actor="ui")

    @app.post("/api/triage/{item_id}/confirm")
    def api_confirm(item_id: str):
        return services.confirm_item(item_id, actor="ui")

    @app.post("/api/triage/{item_id}/override")
    def api_override(item_id: str, body: DecisionRequest):
        return services.override_item(item_id, body.category or "", actor="ui")

    @app.post("/api/triage/{item_id}/release-quarantine")
    def api_release(item_id: str):
        return services.release_quarantine(item_id, actor="ui")

    @app.get("/api/items/{item_id}/history")
    def api_item_history(item_id: str):
        return services.item_history(item_id)

    @app.get("/api/pipeline")
    def api_pipeline():
        return services.pipeline_overview()

    @app.get("/api/triage/failed")
    def api_failed():
        return services.failed_summary()

    @app.post("/api/triage/retry-failed")
    def api_retry_failed(body: RetryRequest):
        if not body.confirm:
            raise Refused("retrying every failed item needs confirm: true")
        return services.retry_failed(actor="ui", item_ids=body.item_ids)

    @app.post("/api/triage/{item_id}/retry")
    def api_retry(item_id: str):
        return services.retry_item(item_id, actor="ui")

    @app.post("/api/triage/{item_id}/accept-warning")
    def api_accept_warning(item_id: str):
        return services.accept_warning(item_id, actor="ui")

    # -- JSON API: config drafts and publishes --------------------------------------------------------------
    @app.get("/api/config/files")
    def api_files():
        return {"files": services.workspace.list_files(), "editable": services.workspace.editable}

    @app.get("/api/config/file")
    def api_file(path: str):
        return services.workspace.read(path)

    @app.post("/api/drafts", status_code=201)
    def api_new_draft():
        return services.store.create_draft()

    @app.get("/api/drafts")
    def api_drafts():
        return {"drafts": services.store.list_drafts()}

    @app.get("/api/drafts/{draft_id}")
    def api_draft(draft_id: str):
        d = services.store.get_draft(draft_id)
        if d is None:
            raise NotFound(f"unknown draft '{draft_id}'")
        return d

    @app.put("/api/drafts/{draft_id}/files")
    def api_edit(draft_id: str, body: FileEdit):
        return services.edit_file(draft_id, body.path, body.content, base_hash=body.base_hash, rev=body.rev)

    @app.delete("/api/drafts/{draft_id}")
    def api_discard(draft_id: str):
        return services.discard_draft(draft_id)

    @app.post("/api/drafts/{draft_id}/categories", status_code=201)
    def api_category(draft_id: str, body: CategoryRequest):
        return services.add_category(
            draft_id,
            category_id=body.id,
            label=body.label,
            subflow=body.subflow,
            para_path=body.para_path,
            triage_hint=body.triage_hint,
            rev=body.rev,
        )

    @app.post("/api/drafts/{draft_id}/validate")
    def api_validate(draft_id: str):
        return services.validate_draft(draft_id)

    @app.post("/api/drafts/{draft_id}/smoke")
    def api_smoke(draft_id: str):
        return services.smoke_eval(draft_id)

    @app.get("/api/drafts/{draft_id}/diff")
    def api_diff(draft_id: str):
        return {"files": services.diff_draft(draft_id)}

    @app.post("/api/drafts/{draft_id}/publish")
    def api_publish(draft_id: str, body: PublishRequest):
        result = services.publish_draft(draft_id, summary=body.summary, override_reason=body.override_reason)
        return JSONResponse(result, status_code=200 if result["published"] else 409)

    @app.get("/api/publishes")
    def api_publishes():
        return {"publishes": services.store.list_publishes()}

    @app.get("/api/publishes/{publish_id}/diff", response_class=PlainTextResponse)
    def api_publish_diff(publish_id: str):
        return services.publish_diff(publish_id)

    @app.post("/api/publishes/{publish_id}/rollback")
    def api_rollback(publish_id: str):
        return services.rollback(publish_id)

    @app.get("/api/audit")
    def api_audit(limit: int = 100):
        return {"audit": services.store.audit_log(min(limit, 500))}

    # -- UI pages ---------------------------------------------------------------------------------
    def triage_ctx(**filters: Any) -> dict[str, Any]:
        try:
            return {**services.list_triage(**filters), "error": None}
        except Refused as exc:
            return {"rows": [], "invalid": 0, "conflicts": [], "error": str(exc)}

    def image_response(image: dict[str, Any]) -> Response:
        """An INBOX image for the owner's browser: the bytes only, no script or markup can come with them."""
        return Response(
            image["data"],
            media_type=image["media_type"],
            headers={
                "content-security-policy": "default-src 'none'; sandbox",
                "x-content-type-options": "nosniff",
                "cache-control": "private, max-age=300",
            },
        )

    def pipeline_ctx(message: str | None = None, error: bool = False) -> dict[str, Any]:
        try:
            return {
                "ov": services.pipeline_overview(),
                "pipeline_error": None,
                "message": message,
                "error": error,
            }
        except Refused as exc:
            return {"ov": None, "pipeline_error": str(exc), "message": message, "error": error}

    @app.get("/pipeline", response_class=HTMLResponse)
    def ui_pipeline(request: Request):
        return render(request, "pipeline.html", launch=services.launchable_flows(), **pipeline_ctx())

    @app.get("/pipeline/body", response_class=HTMLResponse)
    def ui_pipeline_body(request: Request):
        return render(request, "_pipeline_body.html", **pipeline_ctx())

    @app.post("/ui/pipeline/{item_id}/{action}", response_class=HTMLResponse)
    def ui_pipeline_action(request: Request, item_id: str, action: str):
        """The audited owner actions of the services, from a card of the Pipeline page (spec 033): no decision
        logic of its own."""
        acts = {
            "accept": services.accept_warning,
            "release": services.release_quarantine,
            "retry": services.retry_item,
            "confirm": services.confirm_item,
        }
        message, error = None, False
        try:
            if action not in acts:
                raise NotFound(f"unknown action '{action}'")
            acts[action](item_id, actor="ui")
            message = f"{action}: {item_id} done"
        except (Refused, NotFound) as exc:
            message, error = str(exc), True
        return render(request, "_pipeline_body.html", **pipeline_ctx(message, error))

    @app.get("/", response_class=HTMLResponse)
    def ui_index(request: Request):
        triage = triage_ctx()
        pending = [r for r in triage["rows"] if r["state"] in ("suggested", "pending")]
        return render(
            request,
            "index.html",
            **pipeline_ctx(),
            runs=services.list_runs(limit=5),
            pending=len(pending),
            publishes=services.store.list_publishes()[:3],
            triage_error=triage["error"],
            launch=services.launchable_flows(),
            recent_items=services.recent_items(8),
        )

    @app.get("/items/{item_id}", response_class=HTMLResponse)
    def ui_item(request: Request, item_id: str):
        return render(request, "item.html", h=services.item_history(item_id))

    @app.get("/items", response_class=HTMLResponse)
    def ui_items(request: Request, q: str = ""):
        q = q.strip()
        if q:
            try:
                return RedirectResponse(f"/items/{services.item_history(q)['item_id']}", status_code=303)
            except NotFound:
                message = f"no item {q}"
        else:
            message = None
        return render(request, "items.html", items=services.recent_items(50), message=message)

    def triage_filters(state: str, category: str, needs_owner: str, outcome: str = "") -> dict[str, Any]:
        """The filters of the triage page from its query string (an empty value is no filter)."""
        return {
            "outcome": outcome or None,
            "state": state or None,
            "category": category or None,
            "needs_owner": {"yes": True, "no": False}.get(needs_owner),
        }

    @app.get("/triage", response_class=HTMLResponse)
    def ui_triage(
        request: Request,
        state: str = "",
        category: str = "",
        needs_owner: str = "",
        outcome: str = "",
        bulk: str = "",
        retried: str = "",
    ):
        filters = triage_filters(state, category, needs_owner, outcome)
        try:
            info = services.triage_info()
        except Refused:
            info = {}
        return render(
            request,
            "triage.html",
            categories=sorted(services.categories()),
            failed=services.failed_summary() if info else {},
            states=[s.value for s in ItemState],
            filters={"state": state, "category": category, "needs_owner": needs_owner, "outcome": outcome},
            outcomes=["done", "ready", "failed", "held", "unprocessed"],
            info=info,
            bulk=bulk,
            retried=retried,
            **triage_ctx(**filters),
        )

    def triage_row(request: Request, item_id: str, message: str | None = None, error: bool = False):
        rows = {r["item_id"]: r for r in services.list_triage()["rows"]}
        if item_id not in rows:
            raise NotFound(f"unknown triage item '{item_id}'")
        return render(
            request,
            "_triage_row.html",
            r=rows[item_id],
            categories=sorted(services.categories()),
            message=message,
            error=error,
        )

    def triage_action(request: Request, item_id: str, action: Callable[[], Any], done: str):
        try:
            action()
        except Refused as exc:
            return triage_row(request, item_id, str(exc), True)
        return triage_row(request, item_id, done)

    @app.post("/ui/triage/bulk-confirm", response_class=HTMLResponse)
    def ui_bulk_confirm(
        request: Request, state: str = Form(""), category: str = Form(""), needs_owner: str = Form("")
    ):
        filters = triage_filters(state, category, needs_owner)
        result = services.bulk_confirm(actor="ui", **filters)
        from urllib.parse import urlencode

        query = urlencode(
            {
                "state": state,
                "category": category,
                "needs_owner": needs_owner,
                "bulk": f"{result['confirmed']} confirmed, {result['skipped']} left alone",
            }
        )
        return Response(status_code=200, headers={"HX-Redirect": f"/triage?{query}"})

    @app.post("/ui/triage/retry-failed", response_class=HTMLResponse)
    def ui_retry_failed(request: Request, confirm: str = Form("")):
        if confirm != "yes":
            raise Refused("retrying every failed item needs the confirm step")
        result = services.retry_failed(actor="ui")
        from urllib.parse import urlencode

        note = f"{result['retried']} failed item(s) go through the next release run"
        if result["capped"]:
            note += f", {result['capped']} reached the retry limit"
        return Response(
            status_code=200,
            headers={"HX-Redirect": "/triage?" + urlencode({"state": "released", "retried": note})},
        )

    @app.get("/ui/triage/{item_id}/image")
    def ui_triage_image(item_id: str):
        return image_response(services.item_image(item_id))

    @app.post("/ui/triage/{item_id}/confirm", response_class=HTMLResponse)
    def ui_confirm(request: Request, item_id: str):
        return triage_action(
            request, item_id, lambda: services.confirm_item(item_id, actor="ui"), "confirmed"
        )

    @app.post("/ui/triage/{item_id}/override", response_class=HTMLResponse)
    def ui_override(request: Request, item_id: str, category: str = Form(""), new_category: str = Form("")):
        # a name typed into "propose a new category" wins over the dropdown; the server checks both alike
        value = new_category.strip() or category
        return triage_action(
            request,
            item_id,
            lambda: services.override_item(item_id, value, actor="ui"),
            "proposes a new category" if new_category.strip() else "overridden",
        )

    @app.post("/ui/triage/{item_id}/comment", response_class=HTMLResponse)
    def ui_comment(request: Request, item_id: str, comment: str = Form("")):
        return triage_action(
            request, item_id, lambda: services.comment_item(item_id, comment, actor="ui"), "comment saved"
        )

    @app.post("/ui/triage/{item_id}/retry", response_class=HTMLResponse)
    def ui_retry(request: Request, item_id: str):
        return triage_action(
            request,
            item_id,
            lambda: services.retry_item(item_id, actor="ui"),
            "released again for the next run",
        )

    @app.post("/ui/triage/{item_id}/accept-warning", response_class=HTMLResponse)
    def ui_accept_warning(request: Request, item_id: str):
        return triage_action(
            request,
            item_id,
            lambda: services.accept_warning(item_id, actor="ui"),
            "classifier warning accepted: released again for the next run",
        )

    @app.post("/ui/triage/{item_id}/release", response_class=HTMLResponse)
    def ui_release(request: Request, item_id: str):
        return triage_action(
            request,
            item_id,
            lambda: services.release_quarantine(item_id, actor="ui"),
            "released from quarantine",
        )

    @app.get("/runs", response_class=HTMLResponse)
    def ui_runs(request: Request):
        return render(request, "runs.html", runs=services.list_runs(), flows=services.flows(), message=None)

    @app.post("/ui/runs", response_class=HTMLResponse)
    def ui_start_run(request: Request, flow: str = Form("")):
        message, error = None, False
        try:
            run = services.start_run(flow, trigger="ui", actor="ui")
            message = f"started {run['flow_id']} ({run['run_id'][:8]})"
        except (Refused, NotFound) as exc:
            message, error = str(exc), True
        return render(request, "_runs_table.html", runs=services.list_runs(), message=message, error=error)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def ui_run(request: Request, run_id: str):
        return render(
            request,
            "run.html",
            run=services.get_run(run_id),
            snapshot=services.snapshot_of_run(run_id),
            plan=None,
        )

    @app.post("/ui/runs/{run_id}/restore", response_class=HTMLResponse)
    def ui_restore(request: Request, run_id: str, apply: str = Form(""), force: str = Form("")):
        plan, message, error = None, None, False
        try:
            plan = services.restore_run(run_id, apply=bool(apply), force=bool(force), actor="ui")
        except (Refused, NotFound) as exc:
            message, error = str(exc), True
        return render(
            request,
            "_restore.html",
            run=services.get_run(run_id),
            snapshot=services.snapshot_of_run(run_id),
            plan=plan,
            message=message,
            error=error,
        )

    @app.get("/config", response_class=HTMLResponse)
    def ui_config(request: Request):
        draft = services.open_draft() if services.workspace.editable else None
        return render(
            request,
            "config.html",
            files=services.workspace.list_files(),
            draft=draft,
            diffs=services.workspace.diff(draft["files"]) if draft else [],
            publishes=services.store.list_publishes(),
            flows=services.flows(),
            result=None,
            form_paths={f["path"] for f in services.workspace.list_files() if forms.stem_of(f["path"])},
        )

    @app.get("/config/edit", response_class=HTMLResponse)
    def ui_edit(request: Request, path: str):
        info = services.workspace.read(path)
        draft = services.open_draft() if services.workspace.editable else None
        content = info["content"]
        if draft and path in draft["files"] and draft["files"][path]["content"] is not None:
            content = draft["files"][path]["content"]
        return render(
            request,
            "edit.html",
            info=info,
            content=content,
            draft=draft,
            message=None,
            error=False,
            form_link=forms.stem_of(path) is not None,
        )

    @app.post("/ui/config/save", response_class=HTMLResponse)
    def ui_save(
        request: Request,
        draft_id: str = Form(...),
        path: str = Form(...),
        content: str = Form(""),
        rev: int = Form(...),
        base_hash: str = Form(...),
    ):
        message, error = "saved to the draft", False
        try:
            services.edit_file(draft_id, path, content, base_hash=base_hash, rev=rev)
        except (Refused, Conflict, RevisionConflict, WorkspaceError, NotFound) as exc:
            message, error = str(exc), True
        info = services.workspace.read(path)
        draft = services.store.get_draft(draft_id)
        return render(
            request, "_editor.html", info=info, content=content, draft=draft, message=message, error=error
        )

    @app.post("/ui/config/category", response_class=HTMLResponse)
    def ui_category(
        request: Request,
        draft_id: str = Form(...),
        rev: int = Form(...),
        id: str = Form(...),
        label: str = Form(...),
        subflow: str = Form(...),
        para_path: str = Form(...),
        triage_hint: str = Form(...),
    ):
        message, error = f"category '{id}' added to the draft", False
        try:
            services.add_category(
                draft_id,
                category_id=id,
                label=label,
                subflow=subflow,
                para_path=para_path,
                triage_hint=triage_hint,
                rev=rev,
            )
        except (Refused, Conflict, RevisionConflict, WorkspaceError, NotFound) as exc:
            message, error = str(exc), True
        return draft_panel(request, draft_id, message=message, error=error)

    # -- config forms (spec 026) ----------------------------------------------------------------------
    def form_hints() -> dict[str, Any]:
        data = services.runs.config().data
        chan = data.get("channels") or {}
        env_names = sorted(
            {
                v[4:]
                for ch in (chan.get("channels") or {}).values()
                for v in ([ch.get("url"), ch.get("token"), ch.get("channel_id")] + list(ch.get("urls") or []))
                if isinstance(v, str) and forms.ENV_REF.match(v)
            }
        )
        return {
            "flows": services.flows(),
            "categories": sorted(services.categories()),
            "channels": sorted((chan.get("channels") or {}).keys()),
            "events": sorted(set(forms.KNOWN_EVENTS) | set((chan.get("routes") or {}).keys())),
            "env": env_names,
        }

    def asset_links(root: forms.Node) -> dict[str, str]:
        """Field name -> editable text file for the ``instruction`` / ``system`` / ``context`` properties."""
        existing = {f["path"] for f in services.workspace.list_files()}
        links: dict[str, str] = {}

        def walk(n: forms.Node) -> None:
            prop = n.name.rsplit(".", 1)[-1]
            if n.widget == "asset" and isinstance(n.value, str) and n.value:
                rel = forms.asset_path(prop, n.value)
                if rel in existing:
                    links[n.name] = rel
            for c in n.children:
                walk(c)

        walk(root)
        return links

    def form_context(
        stem: str, data: Any, draft: dict, info: dict, errors: dict[str, str] | None = None, **extra: Any
    ):
        root = forms.build_form(stem, data)
        if errors:
            forms.apply_errors(root, errors)
        return dict(
            root=root,
            hints=form_hints(),
            env_status={n: bool(os.environ.get(n)) for n in form_hints()["env"]},
            assets=asset_links(root),
            draft=draft,
            path=info["path"],
            source=info["source"],
            base_hash=draft["files"][info["path"]]["base_hash"]
            if info["path"] in draft["files"]
            else info["base_hash"],
            message=extra.get("message"),
            error=extra.get("error", False),
            saved=extra.get("saved", False),
            problems=extra.get("problems", []),
        )

    def current_data(path: str, draft: dict) -> tuple[dict, Any]:
        info = services.workspace.read(path)
        content = info["content"]
        if path in draft["files"] and draft["files"][path]["content"] is not None:
            content = draft["files"][path]["content"]
        try:
            return info, json.loads(content)
        except json.JSONDecodeError as exc:
            raise Refused(
                f"'{path}' is not valid JSON ({exc.msg}, line {exc.lineno}): fix it in the JSON view"
            ) from None

    @app.get("/config/form", response_class=HTMLResponse)
    def ui_form(request: Request, path: str):
        stem = forms.stem_of(path)
        if stem is None:
            return RedirectResponse(f"/config/edit?path={quote(path)}", status_code=303)
        if not services.workspace.editable:
            return render(request, "config_form.html", path=path, draft=None)
        draft = services.open_draft()
        try:
            info, data = current_data(path, draft)
        except (Refused, WorkspaceError) as exc:
            return render(request, "config_form.html", 422, path=path, draft=None, error_text=str(exc))
        return render(request, "config_form.html", **form_context(stem, data, draft, info))

    async def posted(request: Request) -> tuple[dict[str, str], dict[str, str]]:
        form = await request.form()
        items = [(k, str(v)) for k, v in form.multi_items()]
        meta = {k: v for k, v in items if not k.startswith(forms.PREFIX + ".") and k != forms.PREFIX}
        return meta, forms.flat_of(items)

    @app.post("/ui/config/form/action", response_class=HTMLResponse)
    async def ui_form_action(request: Request):
        meta, flat = await posted(request)
        path, draft_id = meta.get("path", ""), meta.get("draft_id", "")
        stem = forms.stem_of(path)
        draft = services.store.get_draft(draft_id)
        if stem is None or draft is None:
            raise NotFound("unknown form or draft")
        try:
            flat = forms.apply_action(stem, flat, meta.get("act", ""))
        except forms.FormError as exc:
            raise Refused(str(exc)) from None
        parsed = forms.parse_form(stem, flat)
        info = services.workspace.read(path)
        return render(request, "_form.html", **form_context(stem, parsed.data, draft, info))

    @app.post("/ui/config/form/save", response_class=HTMLResponse)
    async def ui_form_save(request: Request):
        meta, flat = await posted(request)
        path, draft_id = meta.get("path", ""), meta.get("draft_id", "")
        stem = forms.stem_of(path)
        draft = services.store.get_draft(draft_id)
        if stem is None or draft is None:
            raise NotFound("unknown form or draft")
        info = services.workspace.read(path)
        parsed = forms.parse_form(stem, flat)
        errors = forms.validate_parsed(stem, parsed, base=services.workspace.pack_json(path))
        if errors:
            ctx = form_context(
                stem,
                parsed.data,
                draft,
                info,
                errors,
                message=f"{len(errors)} field(s) need attention",
                error=True,
            )
            return render(request, "_form.html", **ctx)
        value = parsed.data
        if info["source"] == "pack" and path not in draft["files"]:
            value = forms.minimize(stem, json.loads(info["content"]), value)
            if value in ({}, []):
                ctx = form_context(stem, parsed.data, draft, info, message="nothing changed", error=False)
                return render(request, "_form.html", **ctx)
        try:
            services.edit_file(
                draft_id,
                path,
                forms.dumps(value),
                base_hash=meta.get("base_hash"),
                rev=int(meta.get("rev", "0")),
            )
        except (Refused, Conflict, RevisionConflict, WorkspaceError, NotFound) as exc:
            ctx = form_context(stem, parsed.data, draft, info, message=str(exc), error=True)
            return render(request, "_form.html", **ctx)
        saved = services.store.get_draft(draft_id)
        if saved is None:
            raise NotFound(f"unknown draft '{draft_id}'")
        check = services.validate_draft(draft_id)
        problems = [] if check["ok"] else [e for e in check["errors"]]
        ctx = form_context(
            stem,
            parsed.data,
            saved,
            services.workspace.read(path),
            message="saved to the draft"
            if not problems
            else "saved to the draft, but the draft is not valid yet",
            error=bool(problems),
            saved=True,
            problems=problems,
        )
        return render(request, "_form.html", **ctx)

    @app.get("/ui/config/cron-preview", response_class=HTMLResponse)
    def ui_cron_preview(request: Request):
        expr = next((v for k, v in request.query_params.items() if k.startswith(forms.PREFIX + ".")), "")
        result = forms.cron_preview(expr)
        if result["ok"]:
            text = "next: " + "; ".join(result["times"])
            return HTMLResponse(f'<span class="ok">{_esc(text)}</span>')
        return HTMLResponse(f'<span class="err">{_esc(str(result["error"]))}</span>')

    def draft_panel(request: Request, draft_id: str, **extra: Any):
        draft = services.store.get_draft(draft_id)
        if draft is None:
            raise NotFound(f"unknown draft '{draft_id}'")
        return render(
            request,
            "_draft.html",
            draft=draft,
            diffs=services.workspace.diff(draft["files"]),
            result=extra.get("result"),
            message=extra.get("message"),
            error=extra.get("error", False),
            flows=services.flows(),
        )

    @app.post("/ui/drafts/{draft_id}/validate", response_class=HTMLResponse)
    def ui_validate(request: Request, draft_id: str):
        return draft_panel(
            request, draft_id, result={"kind": "validate", **services.validate_draft(draft_id)}
        )

    @app.post("/ui/drafts/{draft_id}/smoke", response_class=HTMLResponse)
    def ui_smoke(request: Request, draft_id: str):
        return draft_panel(request, draft_id, result={"kind": "gate", **services.smoke_eval(draft_id)})

    @app.post("/ui/drafts/{draft_id}/publish", response_class=HTMLResponse)
    def ui_publish(request: Request, draft_id: str, summary: str = Form(""), override_reason: str = Form("")):
        try:
            result = services.publish_draft(
                draft_id, summary=summary, override_reason=override_reason or None
            )
        except (Refused, NotFound) as exc:
            return draft_panel(request, draft_id, message=str(exc), error=True)
        return draft_panel(request, draft_id, result={"kind": "publish", **result})

    @app.post("/ui/drafts/{draft_id}/discard", response_class=HTMLResponse)
    def ui_discard(request: Request, draft_id: str):
        services.discard_draft(draft_id)
        return Response(status_code=200, headers={"HX-Redirect": "/config"})

    @app.post("/ui/publishes/{publish_id}/rollback", response_class=HTMLResponse)
    def ui_rollback(request: Request, publish_id: str):
        try:
            services.rollback(publish_id)
        except (Refused, NotFound) as exc:
            return HTMLResponse(f'<p class="err" id="rollback-msg">{_esc(str(exc))}</p>')
        return Response(status_code=200, headers={"HX-Redirect": "/config"})

    @app.get("/publishes/{publish_id}", response_class=HTMLResponse)
    def ui_publish_detail(request: Request, publish_id: str):
        record = services.store.get_publish(publish_id)
        if record is None:
            raise NotFound(f"unknown publish '{publish_id}'")
        return render(request, "publish.html", p=record, diff=services.publish_diff(publish_id))

    @app.get("/audit", response_class=HTMLResponse)
    def ui_audit(request: Request):
        return render(request, "audit.html", entries=services.store.audit_log(200))

    return app


def _esc(text: str) -> str:
    import html

    return html.escape(text)
