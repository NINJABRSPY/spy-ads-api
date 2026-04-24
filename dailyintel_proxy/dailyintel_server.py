"""
Daily Intel Service Proxy — FastAPI na porta 3020.

Faz fetch de https://dailyintelservice.com/api/members/videos usando cookies
da conta paga (plano $29/mes). Cache 15min in-memory. Filtros client-side.

Deploy: HostDimer /opt/ninja-proxy/dailyintel/
Servico: ninja-dailyintel.service
Exposto via: https://intel.ninjabrhub.online
"""

import asyncio
import json
import logging
import os
import threading
import time as _time
from pathlib import Path
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException, Query, Header, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
import uvicorn

# Native extractor (scraper de VSL sem watermark)
try:
    from native_extractor import extract_native_hls, NativeCache
    _NATIVE_AVAILABLE = True
except Exception as _e:
    _NATIVE_AVAILABLE = False
    _NATIVE_ERR = str(_e)

# ============================================================
# CONFIG
# ============================================================

PORT = int(os.environ.get("DAILYINTEL_PORT", 3020))
API_KEY = os.environ.get("DAILYINTEL_API_KEY", "njspy_dailyintel_2026_r8t9y0")
COOKIES_FILE = os.environ.get(
    "DAILYINTEL_COOKIES_FILE",
    str(Path(__file__).parent / "cookies.json"),
)
CACHE_TTL = int(os.environ.get("DAILYINTEL_CACHE_TTL", 900))  # 15min
LOG_LEVEL = os.environ.get("DAILYINTEL_LOG", "INFO").upper()

BASE_URL = "https://dailyintelservice.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
# BunnyCDN Video library pull zone (extraido da landing page)
BUNNY_PULL_ZONE = "vz-077e15c9-86a"

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("dailyintel")

# ============================================================
# STATE
# ============================================================

_state = {
    "cookies": None,               # dict nome->valor
    "cache": {                     # cache do /api/members/videos
        "videos": None,
        "is_pro": None,
        "fetched_at": 0,
    },
    "stream_cache": {},            # {(row_id, file_type): {"data": ..., "expires_at": ts}}
    "lock": threading.Lock(),
    "native_cache": None,          # NativeCache instance — lazy init
    "native_lock": asyncio.Lock() if False else None,  # async lock, init no startup
}

NATIVE_CACHE_DIR = os.environ.get(
    "DAILYINTEL_NATIVE_CACHE",
    str(Path(__file__).parent / "native_cache"),
)

# TTL do cache do stream — Daily Intel bloqueia chamadas multiplas ao mesmo
# video ("Already streaming"). Mesmo stream retornado por X segundos evita
# conflito entre player + download do mesmo video.
STREAM_CACHE_TTL = 120  # 2min (tokens expiram em ~60min, cache curto e seguro)


def _load_cookies():
    """Le cookies.json (formato [{name, value, ...}, ...])"""
    if not Path(COOKIES_FILE).exists():
        log.warning(f"Cookies file nao encontrado: {COOKIES_FILE}")
        return {}
    try:
        with open(COOKIES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        result = {}
        if isinstance(data, list):
            for c in data:
                name = c.get("name")
                value = c.get("value")
                if name and value is not None:
                    result[name] = value
        elif isinstance(data, dict):
            result = {k: str(v) for k, v in data.items()}
        log.info(f"Carregados {len(result)} cookies de {COOKIES_FILE}")
        return result
    except Exception as e:
        log.error(f"Falha lendo cookies: {e}")
        return {}


def _fetch_videos_upstream() -> dict:
    """Faz GET no /api/members/videos com cookies autenticados."""
    cookies = _state["cookies"] or {}
    if not cookies.get("member_session"):
        raise HTTPException(
            status_code=503,
            detail="Cookie member_session nao configurado. Atualize cookies.json e reinicie.",
        )
    try:
        r = requests.get(
            f"{BASE_URL}/api/members/videos",
            cookies=cookies,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Referer": f"{BASE_URL}/members",
            },
            timeout=30,
        )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail={"error": "upstream_network_error", "msg": str(e)[:200]},
        )

    if r.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "upstream_http_error",
                "status": r.status_code,
                "snippet": r.text[:200],
            },
        )

    content_type = r.headers.get("Content-Type", "")
    if "application/json" not in content_type:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "session_expired",
                "hint": "Cookies invalidos — re-extrair do Chrome e substituir cookies.json",
                "snippet": r.text[:200],
            },
        )

    try:
        return r.json()
    except Exception:
        raise HTTPException(
            status_code=502,
            detail={"error": "json_parse_failed", "snippet": r.text[:200]},
        )


def _get_videos(force_refresh: bool = False) -> dict:
    """Retorna videos do cache (ou busca se expirado/force_refresh)."""
    now = _time.time()
    cache = _state["cache"]
    cache_age = now - cache.get("fetched_at", 0)

    if not force_refresh and cache.get("videos") and cache_age < CACHE_TTL:
        return cache

    with _state["lock"]:
        # Double-check apos pegar lock
        cache_age = _time.time() - _state["cache"].get("fetched_at", 0)
        if not force_refresh and _state["cache"].get("videos") and cache_age < CACHE_TTL:
            return _state["cache"]

        log.info(f"Fetch upstream videos (force={force_refresh}, cache_age={int(cache_age)}s)")
        data = _fetch_videos_upstream()
        videos = data.get("videos") or []
        _state["cache"] = {
            "videos": videos,
            "is_pro": data.get("isPro"),
            "fetched_at": _time.time(),
        }
        log.info(f"Cache atualizado: {len(videos)} videos (isPro={data.get('isPro')})")
        return _state["cache"]


def _enrich(v: dict, base_url: str = "") -> dict:
    """Adiciona URLs de thumb (proxy pelo nosso server).

    Thumbnails e videos reais exigem cookie — por isso passam pelo nosso proxy.
    base_url = URL base do nosso servico (pra o cliente poder chamar direto).
    """
    vsl_id = v.get("bunny_vsl_id") or ""
    ads_id = v.get("bunny_ads_id") or ""
    row_id = v.get("id") or ""
    out = dict(v)
    # Caminho interno do proxy — o consumidor Render expoe via /api/dailyintel/thumb/{bunny_id}?lib=X
    if vsl_id:
        out["vsl_thumb_url"] = f"/api/thumb/{vsl_id}?lib=vsl"
    if ads_id:
        out["ads_thumb_url"] = f"/api/thumb/{ads_id}?lib=ads"
    # Stream endpoint — consumidor chama POST /api/stream com {rowId, fileType}
    out["stream_endpoint"] = "/api/stream"
    out["row_id"] = row_id
    return out


def _check_api_key(x_api_key: Optional[str], key: Optional[str]) -> None:
    provided = x_api_key or key
    if provided != API_KEY:
        raise HTTPException(status_code=401, detail="api key invalida ou ausente")


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(title="Daily Intel Service Proxy", version="1.0.0")

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
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup():
    _state["cookies"] = _load_cookies()
    _state["native_lock"] = asyncio.Lock()
    if _NATIVE_AVAILABLE:
        _state["native_cache"] = NativeCache(NATIVE_CACHE_DIR)
        stats = _state["native_cache"].stats()
        log.info(f"Native cache: {stats}")
    log.info(f"Daily Intel Proxy iniciando na porta {PORT}")


@app.get("/health")
async def health():
    cache = _state["cache"]
    age = int(_time.time() - cache.get("fetched_at", 0)) if cache.get("fetched_at") else None
    return {
        "status": "ok",
        "cookies_loaded": bool(_state["cookies"] and _state["cookies"].get("member_session")),
        "cache_videos": len(cache.get("videos") or []) if cache.get("videos") else 0,
        "cache_age_seconds": age,
        "is_pro": cache.get("is_pro"),
        "cache_ttl": CACHE_TTL,
    }


@app.get("/api/search")
async def search(
    niche: Optional[str] = Query(None, description="Filtrar por nicho (case-insensitive partial match)"),
    platform: Optional[str] = Query(None, description="Facebook, Instagram, etc"),
    traffic_type: Optional[str] = Query(None, description="paid, direct, referral, social, other"),
    is_paid: Optional[bool] = Query(None),
    has_vsl: Optional[bool] = Query(None, description="Apenas com has_clean_vsl=true"),
    has_ads: Optional[bool] = Query(None, description="Apenas com has_clean_ads=true"),
    funnel_stage: Optional[str] = Query(None),
    device_type: Optional[str] = Query(None, description="desktop, mobile"),
    search_q: Optional[str] = Query(None, alias="search", description="Busca em product_name + niche + utm_campaign + utm_medium"),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD"),
    sort: str = Query("date_desc", description="date_desc, date_asc, niche, product"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    nocache: bool = Query(False),
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Busca com filtros + paginacao. Cache 15min, filtros client-side."""
    _check_api_key(x_api_key, key)
    cache = _get_videos(force_refresh=nocache)
    videos = cache.get("videos") or []

    def match(v):
        if niche and niche.lower() not in (v.get("niche") or "").lower():
            return False
        if platform and (v.get("platform") or "").lower() != platform.lower():
            return False
        if traffic_type and (v.get("traffic_type") or "").lower() != traffic_type.lower():
            return False
        if is_paid is not None and bool(v.get("is_paid_traffic")) != bool(is_paid):
            return False
        if has_vsl is not None and bool(v.get("has_clean_vsl")) != bool(has_vsl):
            return False
        if has_ads is not None and bool(v.get("has_clean_ads")) != bool(has_ads):
            return False
        if funnel_stage and (v.get("funnel_stage") or "").lower() != funnel_stage.lower():
            return False
        if device_type and (v.get("device_type") or "").lower() != device_type.lower():
            return False
        if search_q:
            q = search_q.lower()
            blob = " ".join(str(v.get(k) or "") for k in (
                "product_name", "niche", "utm_campaign", "utm_medium",
                "utm_source", "platform",
            )).lower()
            if q not in blob:
                return False
        dr = v.get("daily_reports") or {}
        rd = dr.get("report_date") or ""
        if date_from and rd and rd < date_from:
            return False
        if date_to and rd and rd > date_to:
            return False
        return True

    filtered = [v for v in videos if match(v)]

    # Ordenar
    def sort_key(v):
        dr = v.get("daily_reports") or {}
        rd = dr.get("report_date") or ""
        return (rd, v.get("product_name") or "")

    if sort == "date_desc":
        filtered.sort(key=sort_key, reverse=True)
    elif sort == "date_asc":
        filtered.sort(key=sort_key)
    elif sort == "niche":
        filtered.sort(key=lambda v: (v.get("niche") or "", sort_key(v)[0]), reverse=False)
    elif sort == "product":
        filtered.sort(key=lambda v: v.get("product_name") or "")

    total = len(filtered)
    start = (page - 1) * limit
    page_items = [_enrich(v) for v in filtered[start:start + limit]]

    return {
        "data": page_items,
        "total": total,
        "total_available": len(videos),
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit if total else 0,
        "cache_age_seconds": int(_time.time() - cache.get("fetched_at", 0)) if cache.get("fetched_at") else None,
        "is_pro": cache.get("is_pro"),
    }


@app.get("/api/niches")
async def niches_facet(
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Facet de nichos (para popular dropdown) com contagem."""
    _check_api_key(x_api_key, key)
    cache = _get_videos()
    videos = cache.get("videos") or []
    from collections import Counter
    c = Counter(v.get("niche") or "Unknown" for v in videos)
    return {
        "data": [{"niche": n, "count": cnt} for n, cnt in c.most_common()],
        "total": len(videos),
    }


@app.get("/api/platforms")
async def platforms_facet(
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Facet de platforms."""
    _check_api_key(x_api_key, key)
    cache = _get_videos()
    videos = cache.get("videos") or []
    from collections import Counter
    c = Counter(v.get("platform") or "Unknown" for v in videos)
    return {"data": [{"platform": p, "count": cnt} for p, cnt in c.most_common()]}


@app.get("/api/video/{video_id}")
async def video_detail(
    video_id: str,
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Detalhes de 1 video por id."""
    _check_api_key(x_api_key, key)
    cache = _get_videos()
    for v in cache.get("videos") or []:
        if v.get("id") == video_id:
            return _enrich(v)
    raise HTTPException(status_code=404, detail="video nao encontrado")


@app.get("/api/thumb/{video_id}")
def thumb(
    video_id: str,
    lib: str = Query("vsl", description="vsl ou ads"),
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Proxy da thumbnail — retorna imagem binaria (.webp).

    Thumbnail do dailyintelservice exige cookie member_session — por isso
    tem que passar pelo nosso server que tem o cookie.
    """
    _check_api_key(x_api_key, key)
    cookies = _state["cookies"] or {}
    if not cookies.get("member_session"):
        raise HTTPException(status_code=503, detail="cookies nao carregados")

    try:
        r = requests.get(
            f"{BASE_URL}/api/thumb",
            params={"id": video_id, "lib": lib},
            cookies=cookies,
            headers={"User-Agent": USER_AGENT, "Referer": f"{BASE_URL}/members"},
            stream=True,
            timeout=30,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"upstream: {str(e)[:150]}")

    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text[:200])

    media_type = r.headers.get("Content-Type", "image/webp")
    cache_ctrl = r.headers.get("Cache-Control", "public, max-age=86400")
    return Response(
        content=r.content,
        media_type=media_type,
        headers={"Cache-Control": cache_ctrl},
    )


@app.post("/api/stream")
def stream(
    body: dict = Body(..., description='{"rowId": "<video row id>", "fileType": "vsl" ou "ads"}'),
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Proxy do stream endpoint — retorna {embedUrl, downloadUrl, filename, sessionId}.

    Cache 2min por (rowId, fileType) pra evitar erro "Already streaming on
    another tab or device" quando player + download pedem o mesmo video.

    embedUrl = iframe.mediadelivery.net/embed/637009/{id}?token=X&expires=Y (valido por ~1h)
    downloadUrl = vz-xxx.b-cdn.net/{id}/play_720p.mp4?token=X&expires=Y
    """
    _check_api_key(x_api_key, key)
    row_id = body.get("rowId") or body.get("videoId") or body.get("id")
    file_type = body.get("fileType") or body.get("type") or "vsl"
    if not row_id:
        raise HTTPException(status_code=400, detail="rowId obrigatorio")

    # Cache lookup — reutiliza session recente pra evitar conflito
    cache_key = (str(row_id), str(file_type))
    now = _time.time()
    entry = _state["stream_cache"].get(cache_key)
    if entry and entry["expires_at"] > now:
        cached = dict(entry["data"])
        cached["_cached"] = True
        cached["_cache_age"] = int(now - entry["created_at"])
        return cached

    cookies = _state["cookies"] or {}
    if not cookies.get("member_session"):
        raise HTTPException(status_code=503, detail="cookies nao carregados")

    # Daily Intel bloqueia 409 "Already streaming" se existe session ativa.
    # Estrategia: close + stream com retry (close+stream leva ~10s pra liberar).
    import time as _t

    def _call_close():
        try:
            requests.post(
                f"{BASE_URL}/api/members/videos/session/close",
                cookies=cookies,
                headers={
                    "User-Agent": USER_AGENT,
                    "Referer": f"{BASE_URL}/members",
                    "Content-Type": "application/json",
                },
                data="",
                timeout=10,
            )
        except Exception as e:
            log.warning(f"session/close falhou (nao-bloqueante): {e}")

    def _call_stream():
        return requests.post(
            f"{BASE_URL}/api/members/videos/stream",
            json={"rowId": row_id, "fileType": file_type},
            cookies=cookies,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": f"{BASE_URL}/members",
                "Content-Type": "application/json",
            },
            timeout=30,
        )

    # Preemptive close + 1st attempt
    _call_close()
    try:
        r = _call_stream()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"upstream: {str(e)[:150]}")

    # Se ainda "Already streaming", retry com backoff progressivo (~45s max)
    # Delays: 2s, 4s, 6s, 8s, 10s, 15s = 45s total
    retries = [2, 4, 6, 8, 10, 15]
    retry_count = 0
    for delay in retries:
        if r.status_code != 409:
            break
        retry_count += 1
        log.info(f"409 concurrent — close + retry em {delay}s (tentativa {retry_count}/{len(retries)})")
        _call_close()
        _t.sleep(delay)
        try:
            r = _call_stream()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"upstream retry: {str(e)[:150]}")

    try:
        data = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail=f"upstream returned non-JSON (status {r.status_code})")

    if r.status_code != 200:
        # "Already streaming" — se tiver cache stale (expirou TTL mas <10min), reaproveita
        stale = entry and (now - entry["created_at"]) < 600  # 10min
        if stale:
            cached = dict(entry["data"])
            cached["_stale"] = True
            cached["_cache_age"] = int(now - entry["created_at"])
            return cached
        return {"error": data.get("error") or f"upstream {r.status_code}", "details": data}

    # Salvar no cache
    _state["stream_cache"][cache_key] = {
        "data": data,
        "created_at": now,
        "expires_at": now + STREAM_CACHE_TTL,
    }
    # Limpar entries expirados se cache crescer
    if len(_state["stream_cache"]) > 200:
        for k in [k for k, v in _state["stream_cache"].items() if v["expires_at"] < now]:
            _state["stream_cache"].pop(k, None)

    return data


def _find_video_by_id(row_id: str) -> Optional[dict]:
    """Busca video no cache pela id."""
    cache = _get_videos()
    for v in (cache.get("videos") or []):
        if v.get("id") == row_id:
            return v
    return None


@app.get("/api/native/{row_id}")
async def native_video(
    row_id: str,
    refresh: bool = Query(False, description="Forcar re-scrape mesmo se tem cache"),
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Retorna URL HLS master nativa (sem watermark) do video.

    Visita o page_link do anunciante e extrai a URL master.m3u8 do player
    embutido (ConverteAI, Vidalytics, etc). Resultado cacheado em disco
    permanentemente (so re-scrapa se refresh=true ou ainda nao tentou).
    """
    _check_api_key(x_api_key, key)
    if not _NATIVE_AVAILABLE:
        raise HTTPException(status_code=503, detail=f"native extractor indisponivel: {_NATIVE_ERR}")

    nc: NativeCache = _state["native_cache"]

    if not refresh:
        cached = nc.get(row_id)
        if cached and cached.get("master_url"):
            return {**cached, "row_id": row_id, "cached": True}
        if cached and cached.get("failed"):
            return {**cached, "row_id": row_id, "cached": True}

    video = _find_video_by_id(row_id)
    if not video:
        raise HTTPException(status_code=404, detail="row_id nao encontrado no cache de videos")

    page_link = video.get("page_link") or ""
    if not page_link:
        nc.mark_failed(row_id, "sem page_link")
        raise HTTPException(status_code=404, detail="video sem page_link")

    # Concorrencia: 1 scrape por vez pra nao estourar recursos
    async with _state["native_lock"]:
        # Re-check apos pegar lock (outro request pode ter feito)
        if not refresh:
            cached = nc.get(row_id)
            if cached and cached.get("master_url"):
                return {**cached, "row_id": row_id, "cached": True}

        log.info(f"Scraping native for {row_id} -> {page_link[:80]}")
        try:
            result = await extract_native_hls(page_link, timeout_s=25)
        except Exception as e:
            log.warning(f"extract failed {row_id}: {e}")
            nc.mark_failed(row_id, str(e))
            raise HTTPException(status_code=502, detail=f"scraper falhou: {str(e)[:200]}")

    if not result:
        nc.mark_failed(row_id, "no master playlist detected")
        raise HTTPException(status_code=404, detail="nao conseguimos detectar player na page_link")

    nc.set(row_id, result)
    return {**result, "row_id": row_id, "cached": False}


@app.get("/api/native/stats")
def native_stats(
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Diagnostico do cache de extracao nativa."""
    _check_api_key(x_api_key, key)
    if not _NATIVE_AVAILABLE:
        return {"error": _NATIVE_ERR}
    return _state["native_cache"].stats()


@app.get("/api/native-download/{row_id}")
def native_download(
    row_id: str,
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Stream MP4 SEM WATERMARK via ffmpeg remux do HLS nativo.

    Pega master_url do cache nativo (ConverteAI/Vidalytics), usa ffmpeg pra
    remuxar os segmentos .ts em MP4 sem re-encoding (-c copy = rapido), e
    streama a resposta.

    Se nao tem cache nativo, retorna 404 — consumidor deve chamar
    /api/native/{row_id} antes pra popular cache.
    """
    _check_api_key(x_api_key, key)
    if not _NATIVE_AVAILABLE or not _state["native_cache"]:
        raise HTTPException(status_code=503, detail="native cache indisponivel")

    entry = _state["native_cache"].get(row_id)
    if not entry or not entry.get("master_url"):
        raise HTTPException(
            status_code=404,
            detail="video nativo nao foi extraido ainda — chame /api/native/{row_id} primeiro",
        )

    master_url = entry["master_url"]
    player_name = entry.get("player", "native")

    # Buscar video info pra nome do arquivo
    video = _find_video_by_id(row_id)
    product = (video or {}).get("product_name") or "video"
    # Nome limpo pro filename
    import re as _re
    safe_product = _re.sub(r"[^A-Za-z0-9_-]", "_", product)[:50]
    filename = f"{safe_product}_vsl_720p.mp4"

    import subprocess
    import shlex

    # ffmpeg remux: HLS → MP4 sem re-encoding (rapido, mesma qualidade)
    # -c copy = copia streams sem processar (fast, ~quasi-real-time)
    # -movflags frag_keyframe+empty_moov = fragmented MP4 pra streaming
    # -f mp4 pipe:1 = output na stdout em MP4
    cmd = [
        "ffmpeg", "-y",
        "-loglevel", "error",
        "-i", master_url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",   # fix pra AAC em HLS
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4",
        "pipe:1",
    ]

    def stream_ffmpeg():
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        try:
            while True:
                chunk = proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                proc.kill()

    return StreamingResponse(
        stream_ffmpeg(),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Native-Player": player_name,
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "public, max-age=3600",
        },
    )


@app.post("/api/session/close")
def session_close(
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Encerra a session ativa do user no Daily Intel (libera pra outro stream).

    Usado internamente antes de cada novo stream (auto-close), mas exposto
    pro frontend chamar explicitamente se quiser (ex: ao fechar modal).
    """
    _check_api_key(x_api_key, key)
    cookies = _state["cookies"] or {}
    if not cookies.get("member_session"):
        return {"closed": False, "error": "cookies nao carregados"}

    # Tambem limpar nosso cache local
    _state["stream_cache"].clear()

    try:
        r = requests.post(
            f"{BASE_URL}/api/members/videos/session/close",
            cookies=cookies,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": f"{BASE_URL}/members",
                "Content-Type": "application/json",
            },
            data="",
            timeout=10,
        )
        return {"closed": True, "upstream_status": r.status_code}
    except Exception as e:
        return {"closed": False, "error": str(e)[:150]}


@app.post("/api/reload-cookies")
async def reload_cookies(
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Recarrega cookies.json (depois de substituir o arquivo)."""
    _check_api_key(x_api_key, key)
    _state["cookies"] = _load_cookies()
    # Forca refresh do cache tambem
    _state["cache"]["fetched_at"] = 0
    return {
        "reloaded": True,
        "cookies_count": len(_state["cookies"] or {}),
        "has_member_session": bool((_state["cookies"] or {}).get("member_session")),
    }


if __name__ == "__main__":
    log.info(f"Daily Intel Proxy — porta {PORT}, cookies={COOKIES_FILE}, cache_ttl={CACHE_TTL}s")
    uvicorn.run(app, host="0.0.0.0", port=PORT, access_log=False)
