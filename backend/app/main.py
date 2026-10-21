"""HTTP API (FastAPI) + раздача фронтенда. OpenAPI: /docs."""
from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent.chat import answer, answer_stream
from app.agent.orchestrator import cached_insight, get_or_create_insight
from app.agent.scenario import Inputs as ScenarioInputs
from app.agent.scenario import bank_baseline, relevant_levers
from app.agent.scenario import calc as scenario_calc
from app.agent.scenario import defaults as scenario_defaults
from app.agent.scenario import scenarios as scenario_set
from app.agent.schemas import Insight
from app.agent.trace import load_trace
from app.bootstrap import bootstrap, load_demo_users
from app.config import REPO_ROOT, get_settings, load_yaml
from app.data.graph import okved_class_name
from app.data.profiles import companies_of_user, get_profile
from app.data.sensitivity import compute_exposures, role_titles
from app.knowledge.bank import get_knowledge
from app.llm.client import MSK, budget_day, get_llm
from app.news.ingest import add_news, get_event, list_events, run_ingest, sources_health
from app.news.models import NewsItem
from app.news.sources import UnsafeURL, fetch_url
from app.news.stories import get_story
from app.ranking.personal import apply_feedback, apply_overrides, get_overrides, set_manual
from app.ranking.personal import reset as reset_overrides
from app.ranking.rerank import rerank
from app.ranking.scorer import rank_feed, score_event
from app.security import auth
from app.security.guard import AccessDenied, MaskIdsFilter, audit, check_company_access
from app.storage import db
from app.storage.snapshot import snapshot_dir

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")
for _h in logging.getLogger().handlers:
    _h.addFilter(MaskIdsFilter())
logging.getLogger("uvicorn.access").addFilter(MaskIdsFilter())  # в адресах запросов тоже есть company_id


@asynccontextmanager
async def lifespan(app: FastAPI):
    stats = bootstrap()
    log.info("Bootstrap: %s", stats)
    scheduler = None
    if get_settings().enable_scheduler:
        from apscheduler.schedulers.background import BackgroundScheduler

        cfg = load_yaml("sources.yaml")
        scheduler = BackgroundScheduler()
        # первый проход сразу после старта (в фоне), дальше — по расписанию
        scheduler.add_job(run_ingest, "interval", minutes=cfg.get("schedule_minutes", 30),
                          kwargs={"max_per_source": cfg.get("max_per_source", 40)},
                          next_run_time=datetime.now(UTC) + timedelta(seconds=20), max_instances=1, coalesce=True)
        scheduler.start()
    yield
    if scheduler:
        scheduler.shutdown()


app = FastAPI(title="Alfa News Insights", version="0.1.0", lifespan=lifespan,
              description="Персонализированная новостная аналитика для клиентов КИБ (прототип хакатона).")

# Заголовки безопасности: скрипты и стили только свои (без inline-скриптов), страница не встраивается в чужие фреймы
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


def current_user(request: Request, x_user_id: str | None = Header(default=None),
                 authorization: str | None = Header(default=None)) -> str:
    """Пользователь из подписанной сессии (cookie или Bearer). Заголовок X-User-Id — только при ALLOW_HEADER_AUTH."""
    token = request.cookies.get(auth.COOKIE)
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    user = auth.verify(token)
    if user:
        return user
    if x_user_id and get_settings().allow_header_auth:
        return x_user_id
    raise HTTPException(401, "Требуется вход в интернет-банк")


def throttle(user: str, bucket: str, per_min: int) -> None:
    """Ограничение частоты для дорогих операций (LLM): защищает бюджет и сервис от перебора."""
    if auth.rate_limited(f"{bucket}:{user}", per_min):
        audit(user, f"rate_limit:{bucket}", "-", False)
        raise HTTPException(429, "Слишком много запросов — попробуйте через минуту")


# ------------------------------------------------------------------ вход
class LoginRequest(BaseModel):
    login: str = Field(..., max_length=64)
    password: str = Field(..., max_length=128)


@app.post("/api/auth/login")
def login(req: LoginRequest, request: Request, response: Response):
    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "?")
    if auth.rate_limited(f"login:{ip}", 10):
        raise HTTPException(429, "Слишком много попыток входа — подождите минуту")
    user = req.login.strip()
    ok = auth.check_password(user, req.password)
    audit(user, "login", ip, ok)
    if not ok:
        raise HTTPException(401, "Неверный логин или пароль")
    secure = get_settings().cookie_secure or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(auth.COOKIE, auth.issue(user), max_age=auth.TTL_S, httponly=True, samesite="lax",
                        secure=secure, path="/")
    return {"ok": True, "user_id": user}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


def authorized_profile(company_id: str, user: str):
    try:
        check_company_access(user, company_id)
    except AccessDenied as e:
        raise HTTPException(403, str(e)) from e
    p = get_profile(company_id)
    if not p:
        raise HTTPException(404, "Компания не найдена")
    return p


# ------------------------------------------------------------------ служебное
@app.get("/api/health")
def health():
    return {"status": "ok", "llm_mode": get_llm().mode}


@app.get("/api/demo-users")
def demo_users():
    """Тестовые учётные записи для прохождения сценария (данные обезличены). Пароль демо-учёток публичный —
    его передают жюри вместе с логинами (рекомендация организаторов); в продакшене здесь вход через IAM банка."""
    return {"users": load_demo_users(), "roles": role_titles(), "demo_password": get_settings().demo_password}


@app.get("/api/me")
def me(user: str = Depends(current_user)):
    uids = companies_of_user(user)
    companies = []
    for uid in uids:
        p = get_profile(uid)
        if p:
            companies.append({"uid": p.uid, "name": p.display_name(), "okved": p.okved, "okved_name": p.okved_name,
                              "region": p.region})
    audit(user, "me", ",".join(uids), True)
    return {"user_id": user, "companies": companies}


# ------------------------------------------------------------------ компания и лента
@app.get("/api/companies/{company_id}")
def company(company_id: str, role: str | None = None, user: str = Depends(current_user)):
    p = authorized_profile(company_id, user)
    titles = {k: v["title"] for k, v in load_yaml("factors.yaml")["factors"].items()}
    exp = sorted(apply_overrides(p.uid, compute_exposures(p, role)).values(), key=lambda e: e.weight, reverse=True)
    overrides = get_overrides(p.uid)
    from app.data.peers import peer_benchmark

    return {"profile": p.model_dump(), "peers": peer_benchmark(p), "exposures": [
        {"factor": e.factor, "title": titles.get(e.factor, e.factor), "weight": round(e.weight, 2), "sign": e.sign,
         "reasons": e.reasons, "override": overrides.get(e.factor)} for e in exp if e.weight > 0 or e.factor in overrides][:12],
        "overrides": [{**o, "title": titles.get(f, f)} for f, o in overrides.items()],
        "factors": [{"factor": k, "title": v} for k, v in titles.items()]}


class PreferenceRequest(BaseModel):
    factor: str
    level: str = Field(..., pattern="^(more|normal|mute)$")


@app.put("/api/companies/{company_id}/preferences")
def set_preference(company_id: str, req: PreferenceRequest, user: str = Depends(current_user)):
    """Ручная настройка интересов: тема «важнее», «как обычно» или «не показывать». Видна в причинах ленты."""
    p = authorized_profile(company_id, user)
    if req.factor not in load_yaml("factors.yaml")["factors"]:
        raise HTTPException(400, "Неизвестный фактор")
    set_manual(p.uid, req.factor, req.level)
    audit(user, "preference", f"{p.uid}:{req.factor}={req.level}", True)
    return {"ok": True, "overrides": get_overrides(p.uid)}


@app.delete("/api/companies/{company_id}/preferences")
def reset_preferences(company_id: str, user: str = Depends(current_user)):
    p = authorized_profile(company_id, user)
    reset_overrides(p.uid)
    return {"ok": True}


_rank_cache: dict[tuple, list] = {}


def cached_rank(p, role: str | None, limit: int) -> list:
    """Ранжирование ленты с кэшем: ключ меняется при новых событиях и при изменении персональных настроек компании.

    Лента — самый частый запрос; без кэша каждый запрос пересчитывает сотни событий (нагрузочный тест: docs/SECURITY.md).
    """
    events = list_events(300)
    from app.news.ingest import _events_cache

    overrides = tuple(sorted((f, o["multiplier"]) for f, o in get_overrides(p.uid).items()))
    hour = datetime.now(UTC).strftime("%Y%m%d%H")  # свежесть событий зависит от времени — кэш живёт не дольше часа
    key = (p.uid, role, limit, _events_cache["sig"], overrides, hour)
    hit = _rank_cache.get(key)
    if hit is None:
        if len(_rank_cache) > 512:
            _rank_cache.clear()
        hit = _rank_cache[key] = rank_feed(events, p, role, limit)
    return hit


@app.get("/api/feed")
def feed(company_id: str, role: str | None = None, limit: int = Query(20, le=50), show_hidden: bool = False,
         user: str = Depends(current_user)):
    p = authorized_profile(company_id, user)
    hidden = {r["event_id"] for r in db.query("SELECT event_id FROM hidden_events WHERE user_id=? AND company_uid=?",
                                              (user, p.uid))}
    # Сюжет сворачивается в одну карточку (rank_feed): самое релевантное событие, остальные — в таймлайне
    items = [s for s in cached_rank(p, role, limit + len(hidden)) if show_hidden or s.event.id not in hidden][:limit]
    kb = get_knowledge()
    stories = {s.event.story_id: get_story(s.event.story_id) for s in items if s.event.story_id}
    today = datetime.now(UTC).date()

    def card(s) -> dict:
        cls = s.event.classification
        st = stories.get(s.event.story_id) if s.event.story_id else None
        upcoming = [d for d in cls.effective_dates if d >= today.isoformat()]
        return {
            "event_id": s.event.id, "title": s.event.title, "published_at": s.event.published_at,
            "summary": cls.summary, "score": s.score, "effect": s.effect,
            "reasons": s.reasons[:3], "sources": s.event.sources, "importance": cls.importance,
            "trust": cls.trust, "regions": cls.regions,
            "industries": [{"code": c, "name": okved_class_name(c)} for c in cls.industries],
            "effective": ({"date": upcoming[0], "days": (date.fromisoformat(upcoming[0]) - today).days}
                          if upcoming else None),
            "story": ({"id": st["id"], "count": len(st["events"]), "updated": st["last_at"] or "",
                       "timeline": [{k: e[k] for k in ("event_id", "title", "published_at", "direction")}
                                    for e in st["events"]]} if st and len(st["events"]) > 1 else None),
            "has_bank_view": bool(kb.theses_for([f.factor for f in cls.factors]))}

    # «Тишина — тоже ответ»: за неделю ничего значимого — говорим прямо и показываем последнее важное
    week_ago = (datetime.now(UTC) - timedelta(days=QUIET_DAYS)).isoformat()
    fresh = [s for s in items if (s.event.published_at or "") >= week_ago[:10]]
    quiet = None if fresh else {"days": QUIET_DAYS, "last": ({"event_id": items[0].event.id, "title": items[0].event.title,
                                                              "published_at": items[0].event.published_at} if items else None)}
    return {"company_id": company_id, "role": role, "quiet": quiet, "hidden_count": len(hidden),
            "items": [{**card(s), "hidden": s.event.id in hidden} for s in items]}


class HideRequest(BaseModel):
    company_id: str
    event_id: str
    hidden: bool = True


@app.post("/api/feed/hide")
def hide_event(req: HideRequest, user: str = Depends(current_user)):
    """Скрыть карточку из ленты (или вернуть). Скрытие видно только этому пользователю в этой компании."""
    p = authorized_profile(req.company_id, user)
    with db.tx() as conn:
        if req.hidden:
            conn.execute("INSERT OR IGNORE INTO hidden_events(user_id, company_uid, event_id) VALUES (?,?,?)",
                         (user, p.uid, req.event_id))
        else:
            conn.execute("DELETE FROM hidden_events WHERE user_id=? AND company_uid=? AND event_id=?", (user, p.uid, req.event_id))
    return {"ok": True}


ACTION_KINDS = {"manager": "Связаться с менеджером", "callback": "Обратный звонок", "application": "Заявка на продукт"}


class ActionRequest(BaseModel):
    company_id: str
    event_id: str | None = None
    kind: str = Field(..., pattern="^(manager|callback|application)$")
    product: str | None = Field(None, max_length=200)
    comment: str | None = Field(None, max_length=1000)


@app.post("/api/actions")
def request_action(req: ActionRequest, user: str = Depends(current_user)):
    """Естественное действие после инсайта. В прототипе запрос сохраняется; в банке уходит в CRM менеджеру клиента."""
    p = authorized_profile(req.company_id, user)
    throttle(user, "action", 5)
    comment = " · ".join(x for x in (req.product, req.comment) if x)
    with db.tx() as conn:
        cur = conn.execute("INSERT INTO action_requests(user_id, company_uid, event_id, kind, comment) VALUES (?,?,?,?,?)",
                           (user, p.uid, req.event_id, req.kind, comment or None))
    audit(user, f"action:{req.kind}", p.uid, True)
    return {"ok": True, "ticket": f"AB-{cur.lastrowid:06d}", "title": ACTION_KINDS[req.kind],
            "message": "Запрос передан менеджеру. Он свяжется с вами в течение рабочего дня (демо: запрос сохранён, "
                       "в банке он уходит в CRM)."}


QUIET_DAYS = 7


@app.get("/api/feed/rerank")
def feed_rerank(company_id: str, role: str | None = None, limit: int = Query(20, le=50), user: str = Depends(current_user)):
    """Уточнение порядка верха ленты моделью (Gemma). Фронтенд вызывает после показа ленты и плавно переставляет карточки."""
    p = authorized_profile(company_id, user)
    throttle(user, "rerank", 20)
    return rerank(cached_rank(p, role, limit), p, role)


class ScenarioRequest(BaseModel):
    company_id: str
    event_id: str | None = None
    inputs: ScenarioInputs = Field(default_factory=ScenarioInputs)


@app.post("/api/scenario")
def scenario(req: ScenarioRequest, user: str = Depends(current_user)):
    """«Что если» без LLM: эффект шоков ставки, курса и зарплат на компанию; база — прогноз Альфа-Банка.

    Параметры компании (долг, импорт, ФОТ) по умолчанию оцениваются по профилю, пользователь может ввести свои.
    """
    p = authorized_profile(req.company_id, user)
    factors = []
    if req.event_id and (ev := get_event(req.event_id)):
        factors = [f.factor for f in ev.classification.factors]
    defaults, estimated = scenario_defaults(p)
    return {"result": scenario_calc(p, req.inputs), "defaults": defaults, "estimated": estimated,
            "levers": relevant_levers(factors) if factors else ["rate", "fx", "wages"], "baseline": bank_baseline(),
            "scenarios": scenario_set(p, factors, req.inputs)}


ROLE_FOCUS = {
    "owner": "стратегия, регуляторика и спрос", "ceo": "спрос, кадры и внешнеторговые ограничения",
    "cfo": "стоимость денег, курс и кредитные условия", "treasurer": "ставки, курс и ликвидность",
    "accountant": "налоги, отчётность и сроки вступления норм в силу",
}


@app.get("/api/digest")
def digest(company_id: str, role: str | None = None, period: str = Query("week", pattern="^(day|week)$"),
           user: str = Depends(current_user)):
    """Дайджест под роль за день или неделю: главное, сроки, сюжеты, пересмотры прогнозов банка. Без LLM — быстро."""
    p = authorized_profile(company_id, user)
    days = 1 if period == "day" else 7
    since = (datetime.now(UTC) - timedelta(days=days)).date().isoformat()
    items = cached_rank(p, role, 30)
    fresh = [s for s in items if (s.event.published_at or "")[:10] >= since]
    top = (fresh or items)[:5]
    today = datetime.now(UTC).date()
    dates = []
    for s in items:
        for d in s.event.classification.effective_dates:
            if d >= today.isoformat():
                dates.append({"date": d, "days": (date.fromisoformat(d) - today).days, "event_id": s.event.id,
                              "title": s.event.title})
    stories = []
    for sid in dict.fromkeys(s.event.story_id for s in items if s.event.story_id):
        st = get_story(sid)
        if st and len(st["events"]) > 1 and (st["last_at"] or "")[:10] >= since:
            stories.append({"id": sid, "title": st["title"], "count": len(st["events"]), "last_at": st["last_at"]})
    kb = get_knowledge()
    titles = role_titles()
    counts = {e: sum(1 for s in fresh if s.effect == e) for e in ("risk", "opportunity", "mixed", "watch")}
    return {
        "period": period, "since": since, "role": role, "role_title": titles.get(role or "", "Руководитель"),
        "focus": ROLE_FOCUS.get(role or "", "главные риски и возможности"),
        "company": p.display_name(), "quiet": not fresh, "counts": counts,
        "top": [{"event_id": s.event.id, "title": s.event.title, "published_at": s.event.published_at,
                 "effect": s.effect, "why": (s.reasons[0]["why"] if s.reasons else ""),
                 "summary": s.event.classification.summary} for s in top],
        "dates": sorted(dates, key=lambda x: x["date"])[:5], "stories": stories[:3],
        "bank_revisions": [{"title": f["title"], "period": f["period"], "text": f["text"]} for f in kb.revisions()][:3],
    }


@app.get("/api/stories/{story_id}")
def story(story_id: str):
    """Сюжет: хронология связанных событий (решение → реакция рынка → новые прогнозы)."""
    st = get_story(story_id)
    if not st:
        raise HTTPException(404, "Сюжет не найден")
    return st


@app.get("/api/sources/health")
def sources_health_api():
    """Источники новостей: уровень доверия, последний запуск, ошибки, сколько новостей и шума принесли."""
    return sources_health()


@app.get("/api/insights/{event_id}", response_model=Insight)
def insight(event_id: str, company_id: str, role: str | None = None, refresh: bool = False,
            user: str = Depends(current_user)):
    p = authorized_profile(company_id, user)
    ev = get_event(event_id)
    if not ev:
        raise HTTPException(404, "Событие не найдено")
    if refresh or not cached_insight(ev.id, p.uid, role):
        throttle(user, "insight", 12)
    return get_or_create_insight(ev, p, role, force=refresh)


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False, default=str)}\n\n"


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@app.get("/api/insights/{event_id}/stream")
def insight_stream(event_id: str, company_id: str, role: str | None = None, refresh: bool = False,
                   user: str = Depends(current_user)):
    """Прогресс агента в реальном времени (SSE): step_start/step_end по каждому шагу, затем done с инсайтом."""
    p = authorized_profile(company_id, user)
    ev = get_event(event_id)
    if not ev:
        raise HTTPException(404, "Событие не найдено")
    if refresh or not cached_insight(ev.id, p.uid, role):
        throttle(user, "insight", 12)

    def gen() -> Iterator[str]:
        if not refresh and (hit := cached_insight(ev.id, p.uid, role)):
            yield _sse({"type": "done", "cached": True, "insight": hit.model_dump()})
            return
        q: queue.Queue = queue.Queue()
        result: dict = {}

        def work() -> None:
            try:
                result["insight"] = get_or_create_insight(ev, p, role, force=refresh, listener=q.put)
            except Exception as e:  # ошибка агента не должна обрывать поток без объяснения
                log.exception("Ошибка агента")
                result["error"] = str(e)
            finally:
                q.put(None)

        threading.Thread(target=work, daemon=True).start()
        while (item := q.get()) is not None:
            yield _sse(item)
        if "error" in result:
            yield _sse({"type": "error", "detail": result["error"]})
        else:
            yield _sse({"type": "done", "cached": False, "insight": result["insight"].model_dump()})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/api/trace/{trace_id}")
def trace(trace_id: str, user: str = Depends(current_user)):
    row = db.query_one("SELECT company_uid FROM insights WHERE trace_id=?", (trace_id,))
    if not row:
        raise HTTPException(404, "Трассировка не найдена")
    authorized_profile(row["company_uid"], user)
    return {"trace_id": trace_id, "steps": load_trace(trace_id)}


# ------------------------------------------------------------------ новости
class AnalyzeRequest(BaseModel):
    text: str | None = Field(None, description="Текст новости")
    title: str | None = None
    url: str | None = Field(None, description="Ссылка на новость")
    company_id: str | None = None
    role: str | None = None


@app.post("/api/news/analyze")
def analyze(req: AnalyzeRequest, user: str = Depends(current_user)):
    """Проверка на «невиданной» новости: текст или ссылка -> событие -> (опционально) инсайт для компании."""
    p = authorized_profile(req.company_id, user) if req.company_id else None  # права — до любой записи в БД
    throttle(user, "analyze", 6)
    if req.url:
        if not get_settings().allow_url_fetch:
            raise HTTPException(400, "Загрузка по ссылке отключена")
        try:
            item = fetch_url(req.url)
        except UnsafeURL as e:
            audit(user, "fetch_url_blocked", req.url[:200], False)
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # детали сетевой ошибки пользователю не показываем
            log.info("Загрузка %s не удалась: %s", req.url[:200], e)
            raise HTTPException(400, "Не удалось загрузить страницу по ссылке") from e
    elif req.text:
        title = req.title or req.text.strip().split(".")[0][:150]
        import hashlib

        item = NewsItem(id="user-" + hashlib.sha1(req.text.encode()).hexdigest()[:12], source="user_text",
                        source_title="Текст пользователя", title=title, text=req.text,
                        published_at=datetime.now(MSK).isoformat(timespec="seconds"))
    else:
        raise HTTPException(400, "Передайте text или url")
    event_id, is_new = add_news(item)
    ev = get_event(event_id)
    out = {"event_id": event_id, "new_event": is_new, "classification": ev.classification.model_dump()}
    if p:
        out["insight"] = get_or_create_insight(ev, p, req.role).model_dump()
    return out


@app.post("/api/ingest/run")
def ingest(sources: list[str] | None = None, user: str = Depends(current_user)):
    throttle(user, "ingest", 1)  # сбор идёт по расписанию; ручной запуск — не чаще раза в минуту
    audit(user, "ingest", ",".join(sources or ["all"]), True)
    return run_ingest(sources)


# ------------------------------------------------------------------ чат и обратная связь
class ChatRequest(BaseModel):
    event_id: str
    company_id: str
    role: str | None = None
    question: str = Field(..., max_length=1000)


@app.post("/api/chat")
def chat(req: ChatRequest, user: str = Depends(current_user)):
    p = authorized_profile(req.company_id, user)
    throttle(user, "chat", 20)
    ev = get_event(req.event_id)
    if not ev:
        raise HTTPException(404, "Событие не найдено")
    ins = get_or_create_insight(ev, p, req.role)
    return answer(req.question, ins, p)


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest, user: str = Depends(current_user)):
    """Потоковый ответ ассистента (SSE): meta, delta..., done."""
    p = authorized_profile(req.company_id, user)
    throttle(user, "chat", 20)
    ev = get_event(req.event_id)
    if not ev:
        raise HTTPException(404, "Событие не найдено")
    ins = get_or_create_insight(ev, p, req.role)
    return StreamingResponse((_sse(e) for e in answer_stream(req.question, ins, p)),
                             media_type="text/event-stream", headers=SSE_HEADERS)


class FeedbackRequest(BaseModel):
    event_id: str
    company_id: str
    role: str | None = None
    rating: int = Field(..., ge=-1, le=1)
    reason: str | None = Field(None, max_length=500)


@app.post("/api/feedback")
def feedback(req: FeedbackRequest, user: str = Depends(current_user)):
    """👍/👎 с причиной. «Не про мой бизнес» ослабляет главные факторы события для этой компании, 👍 — усиливает."""
    p = authorized_profile(req.company_id, user)
    key = f"{req.event_id}|{req.company_id}|{req.role or '-'}"
    with db.tx() as conn:
        conn.execute("INSERT INTO feedback(user_id, insight_key, rating, reason) VALUES (?,?,?,?)",
                     (user, key, req.rating, req.reason))
    ev = get_event(req.event_id)
    changed = []
    if ev:
        sc = score_event(ev, p, compute_exposures(p, req.role))
        matched = [r["factor"] for r in sc.reasons if r["factor"] not in ("country", "industry", "region")]
        changed = apply_feedback(p.uid, ev, matched, req.rating, req.reason)
    return {"ok": True, "changed": changed}


@app.get("/api/metrics")
def metrics():
    """Мониторинг: бюджет LLM, качество (проверки критика), обратная связь."""
    usage = [dict(r) for r in db.query("SELECT * FROM llm_usage WHERE day=?", (budget_day(),))]
    rows = db.query("SELECT payload FROM insights ORDER BY created_at DESC LIMIT 200")
    total = len(rows)
    payloads = [json.loads(r["payload"]) for r in rows]
    passed = sum(1 for p in payloads if all(c["passed"] for c in p.get("quality", [])))
    # LLM-судья (критик) ставит 1–5 по пяти осям: средние по осям и общая — метрика качества разборов
    judged = [p["scores"] for p in payloads if p.get("scores")]
    axes = sorted({k for s in judged for k in s})
    judge = {"n": len(judged), "avg": round(sum(sum(s.values()) / len(s) for s in judged) / len(judged), 2) if judged else None,
             "by_axis": {a: round(sum(s[a] for s in judged if a in s) / max(1, sum(a in s for s in judged)), 2) for a in axes}}
    fb = db.query_one("SELECT COUNT(*) n, COALESCE(SUM(rating>0),0) pos, COALESCE(SUM(rating<0),0) neg FROM feedback")
    return {
        "llm": {"mode": get_llm().mode, **get_llm().budget_status(), "by_model": usage},
        "insights": {"total": total, "quality_passed_share": round(passed / total, 3) if total else None, "judge": judge},
        "feedback": dict(fb), "events": db.query_one("SELECT COUNT(*) n FROM events")["n"],
        "companies": db.query_one("SELECT COUNT(*) n FROM companies")["n"],
    }


@app.get("/api/bank/forecasts")
def bank_forecasts():
    """Макропрогноз Альфа-Банка (последние значения + пересмотры) и траектория ключевой ставки."""
    kb = get_knowledge()
    rows = kb.forecasts_for()
    return {"forecasts": rows, "revisions": kb.revisions(), "rate_path": kb.rate_path,
            "documents": list(kb.documents.values()), "theses_count": len(kb.theses)}


@app.get("/api/data/quality")
def data_quality():
    """Отчёт о качестве данных кейса и о том, как сервис обрабатывает пробелы."""
    row = db.query_one("SELECT value FROM meta WHERE key='data_quality'")
    return json.loads(row["value"]) if row else {}


@app.get("/api/llm/budget")
def llm_budget():
    """Остаток дневного бюджета на модели: у провайдера (общий для команды) или по локальному учёту."""
    return get_llm().budget_status()


@app.get("/api/llm/status")
def llm_status():
    """Режим LLM, бюджет дня, роутинг задач по моделям и сведения о снапшоте."""
    llm = get_llm()
    mf = snapshot_dir() / "manifest.json"
    return {"mode": llm.mode, "budget": llm.budget_status(),
            "models": {k: {"id": m["id"], "title": m.get("title")} for k, m in llm.cfg["models"].items()},
            "tasks": llm.cfg["tasks"],
            "snapshot": json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else None}


# ------------------------------------------------------------------ фронтенд
FRONTEND = REPO_ROOT / "frontend"
if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        """index.html с версией статики в адресах (?v=хэш), чтобы после деплоя браузер не брал старый JS/CSS из кэша."""
        import hashlib
        import re

        from fastapi.responses import HTMLResponse

        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        for name in sorted(set(re.findall(r"/static/([\w.-]+\.(?:js|css))", html))):
            v = hashlib.sha1((FRONTEND / name).read_bytes()).hexdigest()[:10]
            html = html.replace(f"/static/{name}", f"/static/{name}?v={v}")
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
