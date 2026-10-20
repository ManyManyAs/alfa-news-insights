"""HTTP API (FastAPI) + раздача фронтенда. OpenAPI: /docs."""
from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent.chat import answer, answer_stream
from app.agent.orchestrator import cached_insight, get_or_create_insight
from app.agent.schemas import Insight
from app.agent.trace import load_trace
from app.bootstrap import bootstrap, load_demo_users
from app.config import REPO_ROOT, get_settings, load_yaml
from app.data.profiles import companies_of_user, get_profile
from app.data.sensitivity import compute_exposures, role_titles
from app.knowledge.bank import get_knowledge
from app.llm.client import MSK, budget_day, get_llm
from app.news.ingest import add_news, get_event, list_events, run_ingest
from app.news.models import NewsItem
from app.news.sources import fetch_url
from app.ranking.scorer import rank_feed
from app.security.guard import AccessDenied, audit, check_company_access
from app.storage import db
from app.storage.snapshot import snapshot_dir

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
    from app.data.peers import peer_benchmark

    return {"profile": p.model_dump(), "peers": peer_benchmark(p), "exposures": [
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
                        published_at=datetime.now(MSK).isoformat(timespec="seconds"))
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


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest, user: str = Depends(current_user)):
    """Потоковый ответ ассистента (SSE): meta, delta..., done."""
    p = authorized_profile(req.company_id, user)
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
    authorized_profile(req.company_id, user)
    key = f"{req.event_id}|{req.company_id}|{req.role or '-'}"
    with db.tx() as conn:
        conn.execute("INSERT INTO feedback(user_id, insight_key, rating, reason) VALUES (?,?,?,?)",
                     (user, key, req.rating, req.reason))
    return {"ok": True}


@app.get("/api/metrics")
def metrics():
    """Мониторинг: бюджет LLM, качество (проверки критика), обратная связь."""
    usage = [dict(r) for r in db.query("SELECT * FROM llm_usage WHERE day=?", (budget_day(),))]
    rows = db.query("SELECT payload FROM insights ORDER BY created_at DESC LIMIT 200")
    total = len(rows)
    passed = sum(1 for r in rows if all(c["passed"] for c in json.loads(r["payload"]).get("quality", [])))
    fb = db.query_one("SELECT COUNT(*) n, COALESCE(SUM(rating>0),0) pos, COALESCE(SUM(rating<0),0) neg FROM feedback")
    return {
        "llm": {"mode": get_llm().mode, **get_llm().budget_status(), "by_model": usage},
        "insights": {"total": total, "quality_passed_share": round(passed / total, 3) if total else None},
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

        from fastapi.responses import HTMLResponse

        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        for name in ("app.js", "styles.css"):
            v = hashlib.sha1((FRONTEND / name).read_bytes()).hexdigest()[:10]
            html = html.replace(f"/static/{name}", f"/static/{name}?v={v}")
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
