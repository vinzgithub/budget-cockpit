"""Budget Cockpit web application (FastAPI, server-rendered pages).

Run with:  uvicorn --factory budget_cockpit.app:create_app
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .config import Config, secret_key
from .engine import KINDS, close_month, project, yearly_summary
from .i18n import LOCALES, fmt_money, fmt_month, pick_locale, plain_amount, t
from .security import (
    MIN_PASSWORD_LENGTH,
    LoginThrottle,
    csrf_token,
    csrf_valid,
    hash_password,
    new_token,
    token_hash,
    verify_password,
)
from .store import (
    Store,
    ValidationError,
    line_from_dict,
    parse_amount,
    parse_month_value,
    settings_from_dict,
    settings_to_dict,
)

BASE = Path(__file__).parent
MAX_IMPORT_BYTES = 2 * 1024 * 1024


class NeedSetup(Exception):
    pass


class NeedLogin(Exception):
    pass


class CsrfError(Exception):
    pass


def current_month() -> str:
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"


def parse_yearly_table(text: str) -> dict[str, str]:
    """'2027 = 1200' lines -> {'2027': '1200'}. Separators: '=', ':' or spaces."""
    table: dict[str, str] = {}
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        for sep in ("=", ":"):
            if sep in s:
                year, amount = s.split(sep, 1)
                break
        else:
            parts = s.split(None, 1)
            if len(parts) != 2:
                raise ValidationError("error.yearly_table", raw)
            year, amount = parts
        table[year.strip()] = amount.strip()
    return table


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config.from_env()
    store = Store(config.db_path)
    throttle = LoginThrottle()
    templates = Jinja2Templates(directory=BASE / "templates")

    app = FastAPI(title="Budget Cockpit", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.config = config
    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            f"form-action 'self'; base-uri 'none'; object-src 'none'; frame-ancestors {config.frame_ancestors}"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.add_middleware(
        SessionMiddleware, secret_key=secret_key(config.data_dir), session_cookie="budget_cockpit_session",
        max_age=config.session_max_age, same_site="lax", https_only=config.cookie_secure,
    )

    # ------------------------------------------------------------- helpers
    def settings_now():
        return store.get_settings(current_month(), config.default_locale)

    def lang_of(request: Request) -> str:
        lang = request.session.get("lang")
        if lang in LOCALES:
            return lang
        if store.get_meta("settings"):
            return settings_now().locale
        return pick_locale(request.headers.get("accept-language"), config.default_locale)

    def user_of(request: Request):
        uid = request.session.get("uid")
        return store.get_user(uid) if uid else None

    def require_user(request: Request):
        if store.user_count() == 0:
            raise NeedSetup()
        user = user_of(request)
        if user is None:
            raise NeedLogin()
        return user

    def check_csrf(request: Request, form=None) -> None:
        provided = request.headers.get("x-csrf-token")
        if not provided and form is not None:
            provided = form.get("csrf")
        if not csrf_valid(request.session, provided):
            raise CsrfError()

    def flash(request: Request, level: str, key: str, **kw) -> None:
        request.session.setdefault("flash", []).append([level, key, {k: str(v) for k, v in kw.items()}])

    def render(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
        lang = lang_of(request)
        s = settings_now()
        context = dict(
            lang=lang, settings=s, version=__version__, user=user_of(request),
            csrf=csrf_token(request.session), flashes=request.session.pop("flash", []),
            t=lambda key, **kw: t(lang, key, **kw),
            money=lambda v, d=2: fmt_money(v, lang, s.currency, d),
            month=lambda m, long=False: fmt_month(m, lang, long),
            plain=plain_amount, kinds=KINDS,
        )
        context.update(ctx)
        return templates.TemplateResponse(request, name, context, status_code=status)

    def wants_json(request: Request) -> bool:
        return request.headers.get("content-type", "").startswith("application/json")

    def redirect(url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    def json_error(request: Request, err: ValidationError, status: int = 400) -> JSONResponse:
        return JSONResponse({"error": t(lang_of(request), err.key) + (f" ({err.detail})" if err.detail else "")},
                            status_code=status)

    # ---------------------------------------------------------- exceptions
    @app.exception_handler(NeedSetup)
    async def _need_setup(request: Request, exc: NeedSetup):
        if wants_json(request):
            return JSONResponse({"error": "setup required"}, status_code=401)
        return redirect("/setup")

    @app.exception_handler(NeedLogin)
    async def _need_login(request: Request, exc: NeedLogin):
        if wants_json(request):
            return JSONResponse({"error": "login required"}, status_code=401)
        return redirect("/login")

    @app.exception_handler(CsrfError)
    async def _csrf(request: Request, exc: CsrfError):
        if request.headers.get("x-csrf-token") is not None or wants_json(request):
            return JSONResponse({"error": "invalid CSRF token"}, status_code=403)
        return render(request, "error.html", status=403, message=t(lang_of(request), "error.csrf"))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
        return render(request, "error.html", status=exc.status_code,
                      message=t(lang_of(request), "error.not_found" if exc.status_code == 404 else "error.generic"))

    # ------------------------------------------------------------- health
    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "version": __version__}

    # -------------------------------------------------------- setup/login
    @app.get("/")
    async def index(request: Request):
        require_user(request)
        return redirect("/budget")

    @app.get("/setup")
    async def setup_page(request: Request):
        if store.user_count() > 0:
            return redirect("/login")
        return render(request, "setup.html", min_length=MIN_PASSWORD_LENGTH)

    @app.post("/setup")
    async def setup_submit(request: Request):
        if store.user_count() > 0:
            return redirect("/login")
        form = await request.form()
        check_csrf(request, form)
        username = str(form.get("username", "")).strip()
        password = str(form.get("password", ""))
        lang = str(form.get("lang", "fr"))
        lang = lang if lang in LOCALES else "fr"
        request.session["lang"] = lang
        error = None
        if not 1 <= len(username) <= 40:
            error = "error.username"
        elif len(password) < MIN_PASSWORD_LENGTH:
            error = "error.password_short"
        elif password != form.get("password2"):
            error = "error.password_mismatch"
        if error:
            return render(request, "setup.html", status=400, error=t(lang, error, n=MIN_PASSWORD_LENGTH),
                          min_length=MIN_PASSWORD_LENGTH, username=username)
        uid = store.create_user(username, hash_password(password))
        if form.get("demo"):
            demo = json.loads((BASE / "data" / f"demo.{lang}.json").read_text(encoding="utf-8"))
            demo["settings"]["start_month"] = current_month()
            demo["settings"]["locale"] = lang
            store.import_data(demo)
        else:
            s = settings_now()
            s.locale = lang
            store.save_settings(s)
        request.session.clear()
        request.session["uid"] = uid
        request.session["lang"] = lang
        if form.get("demo"):
            return redirect("/budget")
        flash(request, "info", "flash.welcome")
        return redirect("/settings")

    @app.get("/login")
    async def login_page(request: Request):
        if store.user_count() == 0:
            return redirect("/setup")
        return render(request, "login.html")

    @app.post("/login")
    async def login_submit(request: Request):
        form = await request.form()
        check_csrf(request, form)
        key = request.client.host if request.client else "unknown"
        lang = lang_of(request)
        if throttle.locked(key):
            return render(request, "login.html", status=429, error=t(lang, "error.locked"))
        user = store.get_user_by_name(str(form.get("username", "")).strip())
        if user is None or not verify_password(str(form.get("password", "")), user["pw_hash"]):
            throttle.failure(key)
            return render(request, "login.html", status=401, error=t(lang, "error.login"))
        throttle.success(key)
        request.session.clear()
        request.session["uid"] = user["id"]
        request.session["lang"] = lang
        return redirect("/budget")

    @app.post("/logout")
    async def logout(request: Request):
        form = await request.form()
        check_csrf(request, form)
        lang = lang_of(request)
        request.session.clear()
        request.session["lang"] = lang
        return redirect("/login")

    @app.post("/lang")
    async def set_lang(request: Request):
        form = await request.form()
        check_csrf(request, form)
        lang = str(form.get("lang", ""))
        if lang in LOCALES:
            request.session["lang"] = lang
        back = str(form.get("back", "/budget"))
        return redirect(back if back.startswith("/") and not back.startswith("//") else "/budget")

    # -------------------------------------------------------------- budget
    @app.get("/budget")
    async def budget(request: Request):
        require_user(request)
        s = settings_now()
        lines = store.list_lines()
        p = project(s, lines, store.list_overrides())
        sections = [(kind, [ln for ln in lines if ln.kind == kind]) for kind in KINDS]
        return render(request, "budget.html", p=p, sections=sections, s=s)

    async def _json_body(request: Request) -> dict:
        try:
            data = await request.json()
        except (ValueError, UnicodeDecodeError):
            data = None
        if not isinstance(data, dict):
            raise ValidationError("error.generic", "")
        return data

    @app.post("/budget/cell")
    async def budget_cell(request: Request):
        require_user(request)
        check_csrf(request)
        try:
            data = await _json_body(request)
            line = store.get_line(int(data.get("line_id", 0)))
            month = parse_month_value(data.get("month"), allow_empty=False)
            if line is None:
                raise ValidationError("error.not_found", "")
            value = parse_amount(data.get("value"), allow_empty=True)
        except ValidationError as e:
            return json_error(request, e)
        except (TypeError, ValueError):
            return json_error(request, ValidationError("error.generic", ""))
        if value is None:
            store.delete_override(line.id, month)
        else:
            store.set_override(line.id, month, value)
        return {"ok": True}

    @app.post("/budget/balance")
    async def budget_balance(request: Request):
        require_user(request)
        form = await request.form()
        check_csrf(request, form)
        try:
            value = parse_amount(form.get("current_balance"))
        except ValidationError as e:
            flash(request, "error", e.key)
            return redirect("/budget")
        s = settings_now()
        s.current_balance = value
        store.save_settings(s)
        flash(request, "ok", "flash.balance_saved")
        return redirect("/budget")

    @app.post("/budget/paid")
    async def budget_paid(request: Request):
        require_user(request)
        check_csrf(request)
        try:
            data = await _json_body(request)
            line = store.get_line(int(data.get("line_id", 0)))
            month = parse_month_value(data.get("month"), allow_empty=False)
            if line is None:
                raise ValidationError("error.not_found", "")
        except ValidationError as e:
            return json_error(request, e)
        except (TypeError, ValueError):
            return json_error(request, ValidationError("error.generic", ""))
        existing = store.get_override(line.id, month)
        if existing is not None and existing.paid:
            store.delete_override(line.id, month)
            return {"ok": True, "paid": False}
        store.set_override(line.id, month, Decimal(0), paid=True)
        return {"ok": True, "paid": True}

    # --------------------------------------------------------------- lines
    @app.get("/lines")
    async def lines_page(request: Request):
        require_user(request)
        lines = store.list_lines()
        sections = [(kind, [ln for ln in lines if ln.kind == kind]) for kind in KINDS]
        return render(request, "lines.html", sections=sections)

    def _groups() -> list[str]:
        return sorted({ln.group for ln in store.list_lines() if ln.group})

    @app.get("/lines/new")
    async def line_new(request: Request, kind: str = "expense"):
        require_user(request)
        from .engine import Line
        line = Line(0, "", kind if kind in KINDS else "expense")
        return render(request, "line_form.html", line=line, groups=_groups(), table_text="")

    @app.get("/lines/{line_id}/edit")
    async def line_edit(request: Request, line_id: int):
        require_user(request)
        line = store.get_line(line_id)
        if line is None:
            raise StarletteHTTPException(404)
        table_text = "\n".join(f"{y} = {plain_amount(v)}" for y, v in sorted(line.yearly_table.items()))
        return render(request, "line_form.html", line=line, groups=_groups(), table_text=table_text)

    @app.post("/lines/save")
    async def line_save(request: Request):
        require_user(request)
        form = await request.form()
        check_csrf(request, form)
        line_id = int(form.get("id") or 0)
        existing = store.get_line(line_id) if line_id else None
        if line_id and existing is None:
            raise StarletteHTTPException(404)
        data = {
            "name": form.get("name"), "kind": form.get("kind"), "group": form.get("group"),
            "amount": form.get("amount"), "frequency": form.get("frequency"),
            "month": form.get("month"), "start": form.get("start"), "end": form.get("end"),
            "profile": [form.get(f"profile_{i}", "") for i in range(1, 13)],
            "active": form.get("active") == "on", "note": form.get("note"),
        }
        table_text = str(form.get("yearly_table", ""))
        try:
            data["yearly_table"] = parse_yearly_table(table_text)
            line = line_from_dict(data, line_id, existing.position if existing else 0)
        except ValidationError as e:
            from .engine import Line
            draft = Line(line_id, str(data["name"] or ""), str(data["kind"] or "expense"))
            draft.group = str(data["group"] or "")
            draft.frequency = str(data["frequency"] or "monthly")
            draft.note = str(data["note"] or "")
            return render(request, "line_form.html", status=400, line=draft, groups=_groups(),
                          table_text=table_text, raw=data,
                          error=t(lang_of(request), e.key) + (f" ({e.detail})" if e.detail else ""))
        store.save_line(line)
        flash(request, "ok", "flash.line_saved", name=line.name)
        return redirect("/lines")

    @app.post("/lines/{line_id}/delete")
    async def line_delete(request: Request, line_id: int):
        require_user(request)
        form = await request.form()
        check_csrf(request, form)
        line = store.get_line(line_id)
        if line:
            store.delete_line(line_id)
            flash(request, "ok", "flash.line_deleted", name=line.name)
        return redirect("/lines")

    @app.post("/lines/{line_id}/move")
    async def line_move(request: Request, line_id: int):
        require_user(request)
        form = await request.form()
        check_csrf(request, form)
        store.move_line(line_id, -1 if form.get("direction") == "up" else 1)
        return redirect("/lines")

    # ------------------------------------------------------------- summary
    @app.get("/summary")
    async def summary(request: Request):
        require_user(request)
        s = settings_now()
        p = project(s, store.list_lines(), store.list_overrides())
        return render(request, "summary.html", years=yearly_summary(p), groups=sorted(p.groups))

    # ------------------------------------------------------------ settings
    def settings_page(request: Request, status: int = 200, **ctx):
        s = settings_now()
        p = project(s, store.list_lines(), store.list_overrides())
        return render(request, "settings.html", status=status, s=s, p=p,
                      tokens=store.list_tokens(), **ctx)

    @app.get("/settings")
    async def settings_get(request: Request):
        require_user(request)
        return settings_page(request)

    @app.post("/settings")
    async def settings_post(request: Request):
        require_user(request)
        form = await request.form()
        check_csrf(request, form)
        data = {k: form.get(k) for k in ("start_month", "horizon", "current_balance", "savings_balance",
                                         "safety_threshold", "currency", "locale")}
        try:
            data["horizon"] = int(data.get("horizon") or 24)
            s = settings_from_dict(data)
        except (ValidationError, ValueError) as e:
            key = e.key if isinstance(e, ValidationError) else "error.horizon"
            return settings_page(request, status=400, error=t(lang_of(request), key))
        store.save_settings(s)
        request.session["lang"] = s.locale
        flash(request, "ok", "flash.settings_saved")
        return redirect("/settings")

    @app.post("/settings/close-month")
    async def settings_close(request: Request):
        require_user(request)
        form = await request.form()
        check_csrf(request, form)
        try:
            balance = parse_amount(form.get("balance"), allow_empty=True)
            savings = parse_amount(form.get("savings"), allow_empty=True)
        except ValidationError as e:
            return settings_page(request, status=400, error=t(lang_of(request), e.key))
        s = settings_now()
        closed = s.start_month
        new, kept = close_month(s, store.list_lines(), store.list_overrides(), balance, savings)
        store.save_settings(new)
        store.replace_overrides(kept)
        flash(request, "ok", "flash.month_closed", month=fmt_month(closed, lang_of(request), long=True))
        return redirect("/budget")

    @app.post("/settings/password")
    async def settings_password(request: Request):
        user = require_user(request)
        form = await request.form()
        check_csrf(request, form)
        lang = lang_of(request)
        new = str(form.get("new", ""))
        if not verify_password(str(form.get("current", "")), user["pw_hash"]):
            return settings_page(request, status=400, error=t(lang, "error.password_current"))
        if len(new) < MIN_PASSWORD_LENGTH:
            return settings_page(request, status=400, error=t(lang, "error.password_short", n=MIN_PASSWORD_LENGTH))
        if new != form.get("new2"):
            return settings_page(request, status=400, error=t(lang, "error.password_mismatch"))
        store.set_password(user["id"], hash_password(new))
        flash(request, "ok", "flash.password_changed")
        return redirect("/settings")

    @app.post("/settings/token")
    async def settings_token(request: Request):
        require_user(request)
        form = await request.form()
        check_csrf(request, form)
        name = str(form.get("name", "")).strip()[:60] or "Home Assistant"
        token = new_token()
        store.add_token(name, token_hash(token))
        # Shown once, directly in this response: never stored in clear, never put in the cookie.
        return settings_page(request, new_token=token, new_token_name=name)

    @app.post("/settings/token/{token_id}/revoke")
    async def settings_token_revoke(request: Request, token_id: int):
        require_user(request)
        form = await request.form()
        check_csrf(request, form)
        store.revoke_token(token_id)
        flash(request, "ok", "flash.token_revoked")
        return redirect("/settings#api")

    @app.get("/settings/export")
    async def settings_export(request: Request):
        require_user(request)
        data = store.export_data(settings_now())
        filename = f"budget-cockpit-backup-{date.today().isoformat()}.json"
        return Response(json.dumps(data, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.post("/settings/import")
    async def settings_import(request: Request):
        require_user(request)
        form = await request.form()
        check_csrf(request, form)
        lang = lang_of(request)
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            return settings_page(request, status=400, error=t(lang, "error.import_format"))
        raw = await upload.read(MAX_IMPORT_BYTES + 1)
        if len(raw) > MAX_IMPORT_BYTES:
            return settings_page(request, status=400, error=t(lang, "error.import_size"))
        try:
            data = json.loads(raw.decode("utf-8"))
            s = store.import_data(data)
        except (ValueError, UnicodeDecodeError) as e:
            key = e.key if isinstance(e, ValidationError) else "error.import_format"
            detail = f" ({e.detail})" if isinstance(e, ValidationError) and e.detail else ""
            return settings_page(request, status=400, error=t(lang, key) + detail)
        request.session["lang"] = s.locale
        flash(request, "ok", "flash.imported")
        return redirect("/budget")

    # ----------------------------------------------------------------- API
    @app.get("/api/v1/summary")
    async def api_summary(request: Request):
        auth = request.headers.get("authorization", "")
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or not token or not store.check_token(token_hash(token.strip())):
            return JSONResponse({"error": "unauthorized"}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
        s = settings_now()
        p = project(s, store.list_lines(), store.list_overrides())

        def num(v: Decimal) -> float:
            return float(v.quantize(Decimal("0.01")))

        lowest = p.lowest
        first_alert = p.first_alert
        return {
            "version": __version__,
            "currency": s.currency,
            "current_month": p.months[0],
            "balance_end_of_month": num(p.balance[0]),
            "savings_total": num(p.savings_total[0]),
            "safety_threshold": num(s.safety_threshold),
            "alert": p.alerts[0] or "ok",
            "next_alert": {"month": first_alert[0], "level": first_alert[1]} if first_alert else None,
            "lowest": {"month": lowest[0], "balance": num(lowest[1])},
            "months": [
                {"month": m, "balance": num(b), "income": num(i), "expense": num(e),
                 "savings": num(sv), "alert": a or "ok"}
                for m, b, i, e, sv, a in zip(p.months, p.balance, p.income, p.expense, p.savings, p.alerts,
                                             strict=True)
            ],
            "groups": {g: {"month": num(v[0]), "year_to_date": num(p.groups_ytd[g][0])}
                       for g, v in p.groups.items()},
            "settings": {k: v for k, v in settings_to_dict(s).items() if k in ("start_month", "horizon")},
        }

    return app
