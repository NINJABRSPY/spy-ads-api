"""AdPlexity Native Proxy Server (FastAPI porta 3021).

Expoe a API de https://native.adplexity.com com cookie da conta paga do user,
cache interno, normalizacao pro formato unified do NinjaSpy.

Deploy: HostDimer /opt/ninja-proxy/adplexity/
Servico: ninja-adplexity.service
URL publica: https://native.ninjabrhub.online
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time as _time
import urllib.parse
from pathlib import Path
from typing import Any, Optional

import requests
from fastapi import FastAPI, HTTPException, Query, Header, Body
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ============================================================
# CONFIG
# ============================================================

PORT = int(os.environ.get("ADPLEXITY_PORT", 3021))
API_KEY = os.environ.get("ADPLEXITY_API_KEY", "njspy_adplexity_2026_m3k7r2")
COOKIES_FILE = os.environ.get(
    "ADPLEXITY_COOKIES_FILE",
    str(Path(__file__).parent / "cookies.json"),
)
CACHE_TTL = int(os.environ.get("ADPLEXITY_CACHE_TTL", 900))  # 15min
LOG_LEVEL = os.environ.get("ADPLEXITY_LOG", "INFO").upper()

BASE_URL = "https://native.adplexity.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("adplexity")

# ============================================================
# STATE
# ============================================================

_state = {
    "cookies": None,
    "xsrf_header": None,
    "cache": {},   # {cache_key: {"data": ..., "expires_at": ts}}
    "lock": threading.Lock(),
}


def _load_cookies():
    if not Path(COOKIES_FILE).exists():
        log.warning(f"cookies.json nao encontrado: {COOKIES_FILE}")
        return {}, None
    try:
        with open(COOKIES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        cookies = {c["name"]: c["value"] for c in data if c.get("name")}
        xsrf = urllib.parse.unquote(cookies.get("XSRF-TOKEN", ""))
        log.info(f"Carregados {len(cookies)} cookies (XSRF {'OK' if xsrf else 'MISSING'})")
        return cookies, xsrf
    except Exception as e:
        log.error(f"Falha lendo cookies: {e}")
        return {}, None


def _headers(referer: str = "/") -> dict:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Origin": BASE_URL,
        "Referer": BASE_URL + referer,
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-XSRF-TOKEN": _state["xsrf_header"] or "",
    }


def _upstream_get(path: str, **kw) -> requests.Response:
    return requests.get(
        BASE_URL + path,
        cookies=_state["cookies"] or {},
        headers=_headers(path),
        timeout=kw.pop("timeout", 30),
        **kw,
    )


def _upstream_post(path: str, body: dict, **kw) -> requests.Response:
    return requests.post(
        BASE_URL + path,
        cookies=_state["cookies"] or {},
        headers=_headers(path),
        json=body,
        timeout=kw.pop("timeout", 30),
        **kw,
    )


def _cache_key(parts: Any) -> str:
    import hashlib
    if isinstance(parts, dict):
        s = json.dumps(parts, sort_keys=True, ensure_ascii=False)
    else:
        s = str(parts)
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _cache_get(key: str):
    entry = _state["cache"].get(key)
    if not entry:
        return None
    if _time.time() > entry["expires_at"]:
        _state["cache"].pop(key, None)
        return None
    return entry["data"]


def _cache_set(key: str, data: Any, ttl: Optional[int] = None):
    _state["cache"][key] = {
        "data": data,
        "expires_at": _time.time() + (ttl or CACHE_TTL),
    }
    # Cleanup periodico
    if len(_state["cache"]) > 500:
        now = _time.time()
        for k in [k for k, v in _state["cache"].items() if v["expires_at"] < now]:
            _state["cache"].pop(k, None)


def _check_api_key(x_api_key: Optional[str], key_query: Optional[str]):
    if (x_api_key or key_query) != API_KEY:
        raise HTTPException(status_code=401, detail="api key invalida ou ausente")


def _is_logged_in(text: str) -> bool:
    t = (text or "")[:500].lower()
    return "doctype html" not in t and "login" not in t


# ============================================================
# BODY BUILDERS
# ============================================================

ALL_FILTER_KEYS = [
    "deviceType", "adType", "adCategory", "imageSize", "country",
    "connection", "network", "affNetwork", "arbitrageNetwork",
    "technology", "tracking", "language", "videoType", "videoCategory",
]


def _empty_filters() -> dict:
    return {k: {"values": [], "exclusiveSearch": False} for k in ALL_FILTER_KEYS}


def _build_search_body(
    *,
    mode: str = "keyword",
    sub_mode: str = "ad",
    query: str = "",
    query_subject: str = "keyword.ad_or_lp",
    order: str = "newest",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    days_min: int = 1,
    days_max: Optional[int] = None,
    start: int = 0,
    count: int = 20,
    filters: Optional[dict] = None,
    bid_price_from: float = 0,
    bid_price_to: Optional[float] = None,
    video_length_from: int = 0,
    video_length_to: Optional[int] = None,
    video_likes_from: int = 0,
    video_likes_to: Optional[int] = None,
    video_views_from: int = 0,
    video_views_to: Optional[int] = None,
    countries_count_from: int = 1,
    countries_count_to: Optional[int] = None,
    image_tags: Optional[list] = None,
) -> dict:
    from datetime import datetime, timedelta
    today = datetime.utcnow().date()
    date_to = date_to or today.strftime("%Y-%m-%d")
    date_from = date_from or (today - timedelta(days=30)).strftime("%Y-%m-%d")

    body = {
        "mode": mode,
        "subMode": sub_mode,
        "from": date_from,
        "to": date_to,
        "query": query or "",
        "querySubject": query_subject,
        "order": order,
        "daysRunningFrom": days_min,
        "daysRunningTo": days_max,
        "alertDomains": {},
        "imageTags": image_tags or [],
        "bidPriceFrom": bid_price_from,
        "bidPriceTo": bid_price_to,
        "videoLengthFrom": video_length_from,
        "videoLengthTo": video_length_to,
        "videoLikesFrom": video_likes_from,
        "videoLikesTo": video_likes_to,
        "videoViewsFrom": video_views_from,
        "videoViewsTo": video_views_to,
        "countriesCountFrom": countries_count_from,
        "countriesCountTo": countries_count_to,
        "favFolderId": None,
        "advancedFilter": {},
        "start": start,
        "count": count,
    }
    body.update(_empty_filters())
    if filters:
        for k, v in filters.items():
            if k in ALL_FILTER_KEYS and isinstance(v, list):
                body[k] = {"values": v, "exclusiveSearch": False}
    return body


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(title="AdPlexity Native Proxy", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://ninjabrhub.io",
        "https://www.ninjabrhub.io",
        "https://spy-ads-api.onrender.com",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8000",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup():
    cookies, xsrf = _load_cookies()
    _state["cookies"] = cookies
    _state["xsrf_header"] = xsrf
    log.info(f"AdPlexity Proxy iniciando na porta {PORT}")


@app.get("/health")
async def health():
    """Health + status da sessao."""
    try:
        r = _upstream_get("/api/user/profile", timeout=10)
        ok = r.status_code == 200 and _is_logged_in(r.text)
        body = r.json() if ok else None
    except Exception as e:
        return {"status": "degraded", "error": str(e)[:200]}

    return {
        "status": "ok" if ok else "session_expired",
        "logged_in": ok,
        "user": body.get("name") if ok and isinstance(body, dict) else None,
        "export_limit": body.get("adsExport", {}).get("exportLimit") if ok and isinstance(body, dict) else None,
        "cookies_loaded": bool(_state["cookies"] and _state["cookies"].get("laravel_session")),
        "cache_entries": len(_state["cache"]),
    }


@app.get("/api/filters")
async def filters(
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Dicionarios pra popular dropdowns do frontend.

    Retorna: adType, adCategory, adCategorySource, imageSize, country,
    connection, deviceType, network, affNetwork, arbitrageNetwork,
    technology, tracking, language, videoType, videoCategory.
    """
    _check_api_key(x_api_key, key)
    ck = _cache_key("filters")
    cached = _cache_get(ck)
    if cached:
        return cached

    r = _upstream_get("/api/search/filters", timeout=20)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"upstream {r.status_code}: {r.text[:200]}")
    data = r.json()
    _cache_set(ck, data, ttl=3600)  # 1h (filtros mudam pouco)
    return data


@app.get("/api/profile")
async def profile(
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Info do user logado."""
    _check_api_key(x_api_key, key)
    r = _upstream_get("/api/user/profile")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text[:200])
    return r.json()


@app.post("/api/search")
@app.get("/api/search")
async def search(
    query: str = Query("", description="Termo de busca"),
    sub_mode: str = Query("ad", regex="^(ad|lp)$", description="ad ou lp"),
    mode: str = Query("keyword", description="keyword | domain"),
    query_subject: str = Query("keyword.ad_or_lp", description="keyword.ad_or_lp | keyword.ad | keyword.lp | keyword.headline | keyword.description"),
    order: str = Query("newest"),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(None),
    days_min: int = Query(1, ge=0),
    days_max: Optional[int] = Query(None),
    start: int = Query(0, ge=0),
    count: int = Query(20, ge=1, le=100),
    countries: Optional[str] = Query(None, description="Codigos separados por virgula (US,BR,GB)"),
    networks: Optional[str] = Query(None, description="IDs separados por virgula (ver /api/filters)"),
    ad_categories: Optional[str] = Query(None),
    aff_networks: Optional[str] = Query(None),
    devices: Optional[str] = Query(None),
    languages: Optional[str] = Query(None),
    nocache: bool = Query(False),
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Busca de ads (ou landing pages) com filtros e paginacao.

    Total de ~22M ads disponivel no banco. Cache 15min por (query+filtros+pagina).
    """
    _check_api_key(x_api_key, key)

    filters = {}
    if countries:
        filters["country"] = [c.strip().upper() for c in countries.split(",") if c.strip()]
    if networks:
        filters["network"] = [int(n) for n in networks.split(",") if n.strip().isdigit()]
    if ad_categories:
        filters["adCategory"] = [int(n) for n in ad_categories.split(",") if n.strip().isdigit()]
    if aff_networks:
        filters["affNetwork"] = [int(n) for n in aff_networks.split(",") if n.strip().isdigit()]
    if devices:
        filters["deviceType"] = [int(n) for n in devices.split(",") if n.strip().isdigit()]
    if languages:
        filters["language"] = [int(n) for n in languages.split(",") if n.strip().isdigit()]

    body = _build_search_body(
        mode=mode, sub_mode=sub_mode, query=query, query_subject=query_subject,
        order=order, date_from=date_from, date_to=date_to,
        days_min=days_min, days_max=days_max,
        start=start, count=count, filters=filters,
    )

    ck = _cache_key(body) if not nocache else None
    if ck:
        cached = _cache_get(ck)
        if cached:
            return {**cached, "_cached": True}

    r = _upstream_post("/api/search", body)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"upstream {r.status_code}: {r.text[:200]}")
    data = r.json()
    if ck:
        _cache_set(ck, data)
    return data


@app.post("/api/counters")
@app.get("/api/counters")
async def counters(
    query: str = Query(""),
    sub_mode: str = Query("ad"),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Contadores agregados por categoria/network/etc pra uma busca."""
    _check_api_key(x_api_key, key)
    body = _build_search_body(query=query, sub_mode=sub_mode, date_from=date_from, date_to=date_to, count=1)
    ck = _cache_key({"counters": body})
    cached = _cache_get(ck)
    if cached:
        return cached
    r = _upstream_post("/api/search/counters", body)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"upstream {r.status_code}: {r.text[:200]}")
    data = r.json()
    _cache_set(ck, data, ttl=1800)
    return data


@app.get("/api/trending")
async def trending(
    category: str = Query("lp-domain", description="lp-domain (top advertisers) — unica funcional testada"),
    period: str = Query("7d", description="7d, 30d, 90d"),
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Top 400 anunciantes com mais ads rodando (trending).

    Campos por item: advertiserName, adsCount, networks, daysRunning, countries, newestAds.
    """
    _check_api_key(x_api_key, key)
    ck = _cache_key({"trending": category, "p": period})
    cached = _cache_get(ck)
    if cached:
        return cached
    r = _upstream_get(
        f"/api/trending?category={category}&period={period}&arbitrageKeywordQuery=",
        timeout=30,
    )
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"upstream {r.status_code}: {r.text[:200]}")
    data = r.json()
    _cache_set(ck, data, ttl=3600)  # trending atualiza diario
    return data


@app.get("/api/trending/params")
async def trending_params(
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Paises e periodos disponiveis no trending."""
    _check_api_key(x_api_key, key)
    ck = _cache_key("trending_params")
    cached = _cache_get(ck)
    if cached:
        return cached
    r = _upstream_get("/api/trending/params")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"upstream {r.status_code}: {r.text[:200]}")
    data = r.json()
    _cache_set(ck, data, ttl=86400)  # 24h
    return data


@app.get("/api/thumb/{ad_id}")
async def thumb(ad_id: str, size: str = Query("256", description="256 (default, thumbnail) | full (original)")):
    """Proxy de thumbnail — retorna imagem binaria.

    CDN AdPlexity serve 2 formatos:
    - `{hash}` sem suffix = imagem original (pode ser 1MB+)
    - `{hash};256x-` = thumbnail 256px (recomendado pra grid)

    Tentamos primeiro a size pedida, com fallback pra original se falhar.
    """
    from fastapi.responses import Response

    # Remover suffix se vier no hash (nao deveria mas user pode passar)
    clean_id = ad_id.split(";")[0]

    candidates = []
    if size == "256":
        candidates = [f"{clean_id};256x-", clean_id]
    elif size == "full":
        candidates = [clean_id, f"{clean_id};256x-"]
    else:
        candidates = [f"{clean_id};{size}x-", f"{clean_id};256x-", clean_id]

    last_status = 0
    last_text = ""
    for suffix in candidates:
        url = f"https://n01.adplexity.com/storage/images/native/{suffix}"
        try:
            r = requests.get(
                url,
                cookies=_state["cookies"] or {},
                headers={"User-Agent": USER_AGENT, "Referer": BASE_URL},
                timeout=30,
            )
        except Exception as e:
            last_text = str(e)
            continue
        if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image"):
            return Response(
                content=r.content,
                media_type=r.headers.get("Content-Type", "image/png"),
                headers={"Cache-Control": "public, max-age=86400"},
            )
        last_status = r.status_code
        last_text = r.text[:200]

    raise HTTPException(status_code=last_status or 502, detail=f"thumb upstream: {last_text[:200]}")


@app.post("/api/reload-cookies")
async def reload_cookies(
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Recarrega cookies.json apos substituir o arquivo."""
    _check_api_key(x_api_key, key)
    cookies, xsrf = _load_cookies()
    _state["cookies"] = cookies
    _state["xsrf_header"] = xsrf
    _state["cache"].clear()
    return {"reloaded": True, "cookies_count": len(cookies), "has_session": bool(cookies.get("laravel_session"))}


@app.get("/api/cache-stats")
async def cache_stats(
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    _check_api_key(x_api_key, key)
    now = _time.time()
    active = sum(1 for v in _state["cache"].values() if v["expires_at"] > now)
    return {"total": len(_state["cache"]), "active": active, "default_ttl": CACHE_TTL}


if __name__ == "__main__":
    log.info(f"AdPlexity Proxy — porta {PORT}, cookies={COOKIES_FILE}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, access_log=False)
