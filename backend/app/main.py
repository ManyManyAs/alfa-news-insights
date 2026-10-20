"""HTTP API (FastAPI) + раздача фронтенда. OpenAPI: /docs."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent.chat import answer
from app.agent.orchestrator import get_or_create_insight
from app.agent.schemas import Insight
from app.agent.trace import load_trace
from app.bootstrap import bootstrap, load_demo_users
from app.config import REPO_ROOT, get_settings, load_yaml
from app.data.profiles import companies_of_user, get_profile
from app.data.sensitivity import compute_exposures, role_titles
from app.knowledge.bank import get_knowledge
from app.llm.client import get_llm, spent_today
from app.news.ingest import add_news, get_event, list_events, run_ingest
from app.news.models import NewsItem
from app.news.sources import fetch_url
from app.ranking.scorer import rank_feed
from app.security.guard import AccessDenied, audit, check_company_access
from app.storage import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    stats = bootstrap()
    log.info("Bootstrap: %s", stats)
    scheduler = None
    if get_settings().enable_scheduler:
        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler()
        scheduler.add_job(run_ingest, "interval", minutes=load_yaml("sources.yaml").get("schedule_minutes", 30))
        scheduler.start()
    yield
    if scheduler:
        scheduler.shutdown()


app = FastAPI(title="Alfa News Insights", version="0.1.0", lifespan=lifespan,
              description="Персонализированная новостная аналитика для клиентов КИБ (прототип хакатона).")


def current_user(x_user_id: str | None = Header(default=None)) -> str:
    if not x_user_id:
        raise HTTPException(401, "Укажите пользователя (заголовок X-User-Id)")
    return x_user_id


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
    """Тестовые учётные записи для прохождения сценария (данные обезличены)."""
    return {"users": load_demo_users(), "roles": role_titles()}


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
    exp = sorted(compute_exposures(p, role).values(), key=lambda e: e.weight, reverse=True)
    return {"profile": p.model_dump(), "exposures": [
        {"factor": e.factor, "title": titles.get(e.factor, e.factor), "weight": round(e.weight, 2), "sign": e.sign,
         "reasons": e.reasons} for e in exp[:10]]}


@app.get("/api/feed")
def feed(company_id: str, role: str | None = None, limit: int = Query(20, le=50), user: str = Depends(current_user)):
    p = authorized_profile(company_id, user)
    items = rank_feed(list_events(300), p, role, limit)
    kb = get_knowledge()
    return {"company_id": company_id, "role": role, "items": [
        {"event_id": s.event.id, "title": s.event.title, "published_at": s.event.published_at,
         "summary": s.event.classification.summary, "score": s.score, "effect": s.effect,
         "reasons": s.reasons[:3], "sources": s.event.sources, "importance": s.event.classification.importance,
         "has_bank_view": bool(kb.theses_for([f.factor for f in s.event.classification.factors]))} for s in items]}


@app.get("/api/insights/{event_id}", response_model=Insight)
def insight(event_id: str, company_id: str, role: str | None = None, refresh: bool = False,
            user: str = Depends(current_user)):
    p = authorized_profile(company_id, user)
    ev = get_event(event_id)
    if not ev:
        raise HTTPException(404, "Событие не найдено")
    return get_or_create_insight(ev, p, role, force=refresh)


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
    if req.url:
        if not get_settings().allow_url_fetch:
            raise HTTPException(400, "Загрузка по ссылке отключена")
        try:
            item = fetch_url(req.url)
        except Exception as e:
            raise HTTPException(400, f"Не удалось загрузить страницу: {e}") from e
    elif req.text:
        title = req.title or req.text.strip().split(".")[0][:150]
        import hashlib

        item = NewsItem(id="user-" + hashlib.sha1(req.text.encode()).hexdigest()[:12], source="user_text",
                        source_title="Текст пользователя", title=title, text=req.text,
                        published_at=date.today().isoformat() + "T12:00:00+03:00")
    else:
        raise HTTPException(400, "Передайте text или url")
    event_id, is_new = add_news(item)
    ev = get_event(event_id)
    out = {"event_id": event_id, "new_event": is_new, "classification": ev.classification.model_dump()}
    if req.company_id:
        p = authorized_profile(req.company_id, user)
        out["insight"] = get_or_create_insight(ev, p, req.role).model_dump()
    return out


@app.post("/api/ingest/run")
def ingest(sources: list[str] | None = None, user: str = Depends(current_user)):
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
    ev = get_event(req.event_id)
    if not ev:
        raise HTTPException(404, "Событие не найдено")
    ins = get_or_create_insight(ev, p, req.role)
    return answer(req.question, ins, p)


class FeedbackRequest(BaseModel):
    event_id: str
    company_id: str
    role: str | None = None
    rating: int = Field(..., ge=-1, le=1)
    reason: str | None = Field(None, max_length=500)


@app.post("/api/feedback")
def feedback(req: FeedbackRequest, user: str = Depends(current_user)):
    authorized_profile(req.company_id, user)
    key = f"{req.event_id}|{req.company_id}|{req.role or '-'}"
    with db.tx() as conn:
        conn.execute("INSERT INTO feedback(user_id, insight_key, rating, reason) VALUES (?,?,?,?)",
                     (user, key, req.rating, req.reason))
    return {"ok": True}


@app.get("/api/metrics")
def metrics():
    """Мониторинг: бюджет LLM, качество (проверки критика), обратная связь."""
    usage = [dict(r) for r in db.query("SELECT * FROM llm_usage WHERE day=?", (date.today().isoformat(),))]
    rows = db.query("SELECT payload FROM insights ORDER BY created_at DESC LIMIT 200")
    import json

    total = len(rows)
    passed = sum(1 for r in rows if all(c["passed"] for c in json.loads(r["payload"]).get("quality", [])))
    fb = db.query_one("SELECT COUNT(*) n, COALESCE(SUM(rating>0),0) pos, COALESCE(SUM(rating<0),0) neg FROM feedback")
    return {
        "llm": {"mode": get_llm().mode, "spent_today_rub": round(spent_today(), 2),
                "daily_budget_rub": load_yaml("models.yaml")["daily_budget_rub"], "by_model": usage},
        "insights": {"total": total, "quality_passed_share": round(passed / total, 3) if total else None},
        "feedback": dict(fb), "events": db.query_one("SELECT COUNT(*) n FROM events")["n"],
        "companies": db.query_one("SELECT COUNT(*) n FROM companies")["n"],
    }


# ------------------------------------------------------------------ фронтенд
FRONTEND = REPO_ROOT / "frontend"
if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(FRONTEND / "index.html")
