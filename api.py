"""
Spy Ads API - Serve os dados coletados via REST
Roda com: python api.py
Acessa em: http://localhost:8000/docs
"""

import json
import glob
import os
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
from typing import Optional

try:
    from fastapi import FastAPI, Query, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import RedirectResponse
    import uvicorn
except ImportError:
    print("Instalando dependencias...")
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "fastapi", "uvicorn"])
    from fastapi import FastAPI, Query, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import RedirectResponse
    import uvicorn

from config import OUTPUT_DIR

app = FastAPI(
    title="Spy Ads API",
    description="API unificada de anuncios - BigSpy + Adyntel",
    version="1.0.0",
)

# CORS para Lovable consumir
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# ANTI-SCRAPING — Rate limiting + deteccao de bots
# ============================================================
import time as _time
from collections import defaultdict

# Limites por tipo de comportamento
RATE_LIMITS = {
    "normal": {"requests": 60, "window": 60},      # 60 req/min (cliente normal)
    "search": {"requests": 20, "window": 60},       # 20 buscas/min
    "heavy": {"requests": 10, "window": 60},         # 10 analises/min (AI, uncloak)
    "export": {"requests": 5, "window": 60},         # 5 exports/min
}

# Tracking por IP
_rate_tracker = defaultdict(lambda: {"counts": defaultdict(list), "warnings": 0, "blocked_until": 0})

# Endpoints pesados
HEAVY_ENDPOINTS = ["/api/analyze/", "/api/youtube/analyze", "/api/generate-script/", "/api/strategy-room"]
SEARCH_ENDPOINTS = ["/api/ads", "/api/hooks", "/api/search", "/api/offer-tracker", "/api/uncloak"]


def _get_client_ip(request):
    """Extrai IP real do cliente"""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str, path: str) -> dict:
    """Verifica rate limit e retorna status"""
    now = _time.time()
    tracker = _rate_tracker[ip]

    # IP bloqueado temporariamente?
    if tracker["blocked_until"] > now:
        remaining = int(tracker["blocked_until"] - now)
        return {"allowed": False, "reason": "blocked", "retry_after": remaining}

    # Determinar tipo de endpoint
    if any(path.startswith(ep) for ep in HEAVY_ENDPOINTS):
        limit_type = "heavy"
    elif any(path.startswith(ep) for ep in SEARCH_ENDPOINTS):
        limit_type = "search"
    else:
        limit_type = "normal"

    limit = RATE_LIMITS[limit_type]
    window = limit["window"]
    max_requests = limit["requests"]

    # Limpar requests antigos
    tracker["counts"][limit_type] = [t for t in tracker["counts"][limit_type] if t > now - window]

    # Verificar limite
    current = len(tracker["counts"][limit_type])
    if current >= max_requests:
        tracker["warnings"] += 1

        # 3 warnings = bloqueio progressivo
        if tracker["warnings"] >= 10:
            tracker["blocked_until"] = now + 3600  # 1 hora
            return {"allowed": False, "reason": "banned_1h", "retry_after": 3600}
        elif tracker["warnings"] >= 5:
            tracker["blocked_until"] = now + 300  # 5 min
            return {"allowed": False, "reason": "cooldown_5m", "retry_after": 300}
        else:
            return {"allowed": False, "reason": "rate_limited", "retry_after": 10}

    # Registrar request
    tracker["counts"][limit_type].append(now)

    # Detectar comportamento de scraper
    # Scraper: muitos requests em sequencia rapida (< 1s entre cada)
    all_times = []
    for times in tracker["counts"].values():
        all_times.extend(times)
    all_times.sort()
    if len(all_times) >= 10:
        recent = all_times[-10:]
        avg_interval = (recent[-1] - recent[0]) / 9
        if avg_interval < 0.5:  # Menos de 0.5s entre requests = bot
            tracker["warnings"] += 2
            return {"allowed": False, "reason": "bot_detected", "retry_after": 60}

    return {"allowed": True, "remaining": max_requests - current - 1, "type": limit_type}


from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
import httpx

# Supabase config para validacao de JWT
SUPABASE_URL = "https://bbwgequqwrsmbrkdmsxm.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJid2dlcXVxd3JzbWJya2Rtc3htIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzEzNjAxMDksImV4cCI6MjA4NjkzNjEwOX0.Nh0_9bgXmJNFdaK6Faur_L86nELS17hs9OOpJ7vxMoM"

# Endpoints que NAO precisam de autenticacao
PUBLIC_ENDPOINTS = ["/", "/health", "/docs", "/openapi.json", "/api/sync/status", "/api/auth-check"]

# Paths que sao publicos por prefixo (usados em <iframe src> e <img src> — nao
# passam JWT). So retornam conteudo embed/imagem, nao dados de ads.
PUBLIC_PATH_PREFIXES = [
    "/api/dailyintel/player/",
    "/api/dailyintel/native-player/",
    "/api/dailyintel/thumb/",
    "/api/dailyintel/download/",
    "/api/dailyintel/native-download/",
    "/api/dailyintel/session/close",
    "/api/adplexity/thumb/",
]

# Cache de tokens validados (evita chamar Supabase a cada request)
_token_cache = {}  # {token_hash: {"valid": True, "user_id": "...", "expires": timestamp}}
TOKEN_CACHE_TTL = 300  # 5 minutos de cache


async def _validate_supabase_token(token: str) -> dict:
    """Valida token JWT do Supabase chamando /auth/v1/user"""
    # Check cache primeiro
    token_hash = hash(token)
    now = _time.time()
    if token_hash in _token_cache:
        cached = _token_cache[token_hash]
        if cached["expires"] > now:
            return cached

    # Validar com Supabase
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "apikey": SUPABASE_ANON_KEY,
                },
                timeout=5,
            )
            if r.status_code == 200:
                user = r.json()
                result = {
                    "valid": True,
                    "user_id": user.get("id", ""),
                    "email": user.get("email", ""),
                    "expires": now + TOKEN_CACHE_TTL,
                }
                _token_cache[token_hash] = result
                return result
    except:
        pass

    return {"valid": False}


_sub_cache = {}  # {user_id: {"active": bool, "expires": timestamp}}

async def _check_subscription(token: str, user_id: str) -> bool:
    """Verifica se usuario tem assinatura ativa no Supabase"""
    now = _time.time()

    # Cache 10 minutos
    if user_id in _sub_cache and _sub_cache[user_id]["expires"] > now:
        return _sub_cache[user_id]["active"]

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/user_subscriptions?user_id=eq.{user_id}&is_active=eq.true&select=id",
                headers={
                    "Authorization": f"Bearer {token}",
                    "apikey": SUPABASE_ANON_KEY,
                },
                timeout=5,
            )
            if r.status_code == 200:
                subs = r.json()
                is_active = len(subs) > 0
                _sub_cache[user_id] = {"active": is_active, "expires": now + 600}
                return is_active
    except:
        pass

    # Em caso de erro, liberar (fail-open para nao bloquear clientes)
    return True


def _is_trusted_origin(request) -> bool:
    """Verifica se o request vem de uma origem confiavel"""
    origin = request.headers.get("origin", "")
    referer = request.headers.get("referer", "")
    combined = (origin + referer).lower()
    return "ninjabrhub" in combined or "lovable" in combined or "localhost" in combined


class AuthAndRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path

        # Endpoints publicos
        if any(path == ep or path.startswith(ep + "?") for ep in PUBLIC_ENDPOINTS):
            return await call_next(request)

        # Paths publicos por prefixo (iframe/img embed — nao passam JWT)
        if any(path.startswith(pref) for pref in PUBLIC_PATH_PREFIXES):
            return await call_next(request)

        # ===== ORIGEM CONFIAVEL — exige token mas sem rate limit =====
        if _is_trusted_origin(request):
            auth_header = request.headers.get("authorization", "")
            token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
            if token:
                auth_result = await _validate_supabase_token(token)
                if auth_result.get("valid"):
                    return await call_next(request)
                # Token invalido mas do hub — logar e liberar mesmo assim
                # (pode ser refresh em andamento)
                print(f"[AUTH-WARN] Hub request with invalid token on {path}")
                return await call_next(request)
            # Sem token mas do hub — liberar (fallback seguro)
            print(f"[AUTH-WARN] Hub request WITHOUT token on {path}")
            return await call_next(request)

        # ===== REQUESTS DE FORA — exige autenticacao + assinatura ativa =====
        auth_header = request.headers.get("authorization", "")
        token = ""

        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        elif request.query_params.get("token"):
            token = request.query_params.get("token")

        if not token:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Authentication required",
                    "message": "Token de autenticação necessário. Faça login no NinjaBR Hub."
                }
            )

        # Validar token
        auth_result = await _validate_supabase_token(token)
        if not auth_result.get("valid"):
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Invalid token",
                    "message": "Token inválido ou expirado. Faça login novamente."
                }
            )

        # Verificar assinatura ativa no Supabase
        user_id = auth_result.get("user_id", "")
        if user_id:
            sub_valid = await _check_subscription(token, user_id)
            if not sub_valid:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "Subscription required",
                        "message": "Assinatura inativa. Acesse o NinjaBR Hub para ativar."
                    }
                )

        # Rate limit para requests de fora (mais restritivo)
        rate_key = user_id or _get_client_ip(request)
        check = _check_rate_limit(rate_key, path)

        if not check["allowed"]:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "reason": check["reason"],
                    "retry_after": check.get("retry_after", 10),
                    "message": "Muitas requisições. Aguarde antes de tentar novamente."
                },
                headers={"Retry-After": str(check.get("retry_after", 10))}
            )

        return await call_next(request)

app.add_middleware(AuthAndRateLimitMiddleware)


# ============================================================
# CACHE - Carrega dados uma vez, nao a cada request
# ============================================================
_cache = {"ads": None, "loaded_at": None, "file": None}

def load_latest_data():
    """Carrega dados com cache em memoria - so rele se arquivo mudou"""
    import gzip

    # Tentar JSON normal primeiro, depois gzip
    files = sorted(glob.glob(f"{OUTPUT_DIR}/unified_*.json"), reverse=True)
    gz_files = sorted(glob.glob(f"{OUTPUT_DIR}/unified_*.json.gz"), reverse=True)

    latest = None
    is_gz = False
    if files and gz_files:
        # Usar o mais recente entre os dois
        if os.path.getmtime(gz_files[0]) >= os.path.getmtime(files[0]):
            latest = gz_files[0]
            is_gz = True
        else:
            latest = files[0]
    elif gz_files:
        latest = gz_files[0]
        is_gz = True
    elif files:
        latest = files[0]
    else:
        return []

    file_mtime = os.path.getmtime(latest)

    # Usar cache se mesmo arquivo e nao mudou
    if _cache["ads"] is not None and _cache["file"] == latest and _cache["loaded_at"] == file_mtime:
        return _cache["ads"]

    # Carregar e filtrar
    if is_gz:
        with gzip.open(latest, "rt", encoding="utf-8") as f:
            ads = json.load(f)
    else:
        with open(latest, "r", encoding="utf-8") as f:
            ads = json.load(f)

    clean = []
    for ad in ads:
        title = ad.get("title", "") or ""
        image = ad.get("image_url", "") or ""
        video = ad.get("video_url", "") or ""

        if "{{" in title:
            ad["title"] = ""
        if not image and not video:
            continue

        ad["has_media"] = True
        clean.append(ad)

    # Salvar no cache
    _cache["ads"] = clean
    _cache["file"] = latest
    _cache["loaded_at"] = file_mtime

    return clean

def load_latest_keywords():
    """Carrega keywords mais recentes"""
    files = sorted(glob.glob(f"{OUTPUT_DIR}/keywords_*.json"), reverse=True)
    if not files:
        return []
    with open(files[0], "r", encoding="utf-8") as f:
        return json.load(f)

def load_latest_summary():
    """Carrega resumo mais recente"""
    files = sorted(glob.glob(f"{OUTPUT_DIR}/resumo_*.json"), reverse=True)
    if not files:
        return {}
    with open(files[0], "r", encoding="utf-8") as f:
        return json.load(f)

# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/")
def root():
    return {"message": "Spy Ads API", "docs": "/docs"}

@app.get("/health")
def health():
    """Health check - mantem a API acordada no Render"""
    return {"status": "ok"}

@app.get("/api/auth-check")
def auth_check():
    """Verifica quantos requests do hub vem sem token"""
    hub_no_token = _rate_tracker.get("__hub_no_token__", {}).get("warnings", 0)
    return {"hub_requests_without_token": hub_no_token}

# Campos essenciais para listagem (reduz payload ~80%)
COMPACT_FIELDS = [
    "ad_id", "source", "platform", "advertiser", "advertiser_image",
    "title", "body", "cta", "landing_page", "image_url", "video_url",
    "ad_type", "first_seen", "last_seen", "days_running",
    "likes", "comments", "shares", "impressions", "total_engagement",
    "heat", "potential_score", "estimated_spend", "country",
    "ai_niche", "ai_strategy", "ai_copy_quality", "ai_emotion", "ai_language",
    "also_on", "has_store", "store_daily_revenue", "search_keyword",
    "social1_region", "social1_units_sold", "social1_gmv",
    # PiPiAds v3 AI fields (hook, script, tags, presenter, CPM/CPA)
    "pipi_hook", "pipi_script", "pipi_tags", "pipi_has_presenter",
    "pipi_language", "pipi_cpm", "pipi_cpa",
]

def _detect_country(ad):
    """Detecta pais do ad por campo direto, idioma ou keyword"""
    # Campo direto
    c = ad.get("country") or ""
    if c and len(c) == 2:
        return c.upper()
    countries = ad.get("all_countries") or []
    if countries and isinstance(countries, list) and countries[0]:
        return str(countries[0]).upper()
    region = ad.get("social1_region") or ""
    if region:
        return region.upper()

    # Inferir por idioma
    lang = (ad.get("ai_language") or "").lower()
    if lang == "pt":
        return "BR"
    elif lang == "es":
        return "ES"
    elif lang == "en":
        return "US"
    elif lang == "de":
        return "DE"
    elif lang == "fr":
        return "FR"
    elif lang == "it":
        return "IT"

    # Inferir por keyword
    kw = (ad.get("search_keyword") or "").lower()
    pt_words = ["emagrecer", "suplemento", "renda extra", "trafego", "curso online",
                "afiliado", "cabelo", "academia", "cozinha", "advogado", "dentista",
                "shopee", "cachorro", "gato", "decoracao", "roupa feminina", "tenis",
                "saude", "cosmeticos", "coaching", "mentoria", "investimentos",
                "infoproduto", "marketing digital", "loja virtual", "emagrecimento"]
    if any(w in kw for w in pt_words):
        return "BR"

    return ""


@app.get("/api/ads")
def list_ads(
    platform: str = Query(None, description="facebook, instagram, tiktok, google, linkedin"),
    source: str = Query(None, description="bigspy, adyntel_meta, adyntel_google, adyntel_linkedin, adyntel_tiktok, social1, meta_official"),
    keyword: str = Query(None, description="Filtrar por keyword de busca"),
    search: str = Query(None, description="Buscar no texto/titulo do anuncio"),
    niche: str = Query(None, description="Filtrar por nicho IA"),
    country: str = Query(None, description="Filtrar por pais (US, BR, GB, DE, FR, ES, IT, etc)"),
    language: str = Query(None, description="Filtrar por idioma (pt, en, es, de, fr)"),
    min_score: int = Query(None, description="Score minimo de potencial"),
    sort: str = Query("collected_at", description="Campo para ordenar"),
    order: str = Query("desc", description="asc ou desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    compact: bool = Query(True, description="Retornar campos reduzidos (mais rapido)"),
):
    """Lista anuncios com filtros e paginacao"""
    ads = load_latest_data()

    # Filtros
    if platform:
        platforms = platform.split(",")
        ads = [a for a in ads if a.get("platform") in platforms]
    if source:
        sources = source.split(",")
        ads = [a for a in ads if a.get("source") in sources]
    if keyword:
        kw = keyword.lower()
        ads = [a for a in ads if kw in (a.get("search_keyword", "") or "").lower()]
    if search:
        sl = search.lower()
        words = sl.split()
        def _searchable_text(a):
            # Juntar todos os campos relevantes para busca (case-insensitive, partial match)
            parts = [
                a.get("title", "") or "",
                a.get("body", "") or "",
                a.get("advertiser", "") or "",
                a.get("search_keyword", "") or "",
                a.get("ai_niche", "") or "",
                a.get("ai_target_audience", "") or "",
                a.get("ai_strategy", "") or "",
                a.get("ai_hook_type", "") or "",
                a.get("cta", "") or "",
                # PiPiAds v3 AI fields
                a.get("pipi_hook", "") or "",
                a.get("pipi_script", "") or "",
                a.get("pipi_has_presenter", "") or "",
                a.get("pipi_language", "") or "",
            ]
            tags = a.get("pipi_tags", []) or []
            if isinstance(tags, list):
                parts.append(" ".join(str(t) for t in tags))
            return " ".join(parts).lower()

        ads = [a for a in ads if sl in _searchable_text(a)
               or all(w in _searchable_text(a) for w in words)]
    if niche:
        ads = [a for a in ads if niche.lower() in (a.get("ai_niche", "") or "").lower()]
    if country:
        country_upper = country.upper()
        ads = [a for a in ads if _detect_country(a) == country_upper]
    if language:
        lang_lower = language.lower()
        ads = [a for a in ads if (a.get("ai_language") or "").lower() == lang_lower]
    if min_score:
        ads = [a for a in ads if (a.get("potential_score", 0) or 0) >= min_score]

    # Ordenacao
    reverse = order == "desc"
    try:
        ads.sort(key=lambda x: x.get(sort, "") or "", reverse=reverse)
    except:
        pass

    # Paginacao
    total = len(ads)
    start = (page - 1) * limit
    page_ads = ads[start:start + limit]

    # Modo compacto - so campos essenciais
    if compact:
        page_ads = [{k: a.get(k) for k in COMPACT_FIELDS if a.get(k) is not None} for a in page_ads]

    return {
        "data": page_ads,
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit if total > 0 else 0,
        "limit": limit,
    }

@app.get("/api/stats")
def get_stats():
    """Estatisticas gerais"""
    summary = load_latest_summary()
    ads = load_latest_data()
    from collections import Counter
    by_source = dict(Counter(a.get("source", "unknown") for a in ads).most_common())
    by_platform = dict(Counter(a.get("platform", "unknown") for a in ads).most_common())
    by_keyword = dict(Counter(a.get("search_keyword", "") for a in ads if a.get("search_keyword")).most_common(10))
    return {
        "total_ads": len(ads),
        "last_sync": summary.get("scrape_date", ""),
        "by_source": by_source,
        "by_platform": by_platform,
        "top_keywords": by_keyword,
    }

@app.get("/api/trending")
def trending(
    platform: str = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """Anuncios com mais impressoes/engajamento"""
    ads = load_latest_data()
    if platform:
        ads = [a for a in ads if a.get("platform") == platform]

    # Ordenar por impressoes + likes + comments
    ads.sort(key=lambda x: (x.get("impressions", 0) or 0) + (x.get("likes", 0) or 0) * 10, reverse=True)
    return {"data": ads[:limit]}

@app.get("/api/search")
def search_ads(q: str = Query(..., description="Termo de busca")):
    """Busca ampla — busca em titulo, body, advertiser, keyword, nicho"""
    ads = load_latest_data()
    q_lower = q.lower()
    words = q_lower.split()

    results = []
    for a in ads:
        text = (
            (a.get("title", "") or "") + " " +
            (a.get("body", "") or "") + " " +
            (a.get("advertiser", "") or "") + " " +
            (a.get("search_keyword", "") or "") + " " +
            (a.get("ai_niche", "") or "") + " " +
            (a.get("cta", "") or "")
        ).lower()

        # Match: all words must appear OR exact phrase
        if q_lower in text or all(w in text for w in words):
            results.append(a)

    # Sort by impressions
    results.sort(key=lambda x: x.get("impressions", 0) or 0, reverse=True)

    return {"data": results[:100], "total": len(results), "query": q}

@app.get("/api/top-advertisers")
def top_advertisers(
    platform: str = Query(None),
    keyword: str = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """Top anunciantes por volume de ads"""
    ads = load_latest_data()
    if platform:
        ads = [a for a in ads if a.get("platform") == platform]
    if keyword:
        ads = [a for a in ads if keyword.lower() in (a.get("search_keyword", "") or "").lower()]

    # Agrupar por anunciante
    advertisers = {}
    for ad in ads:
        name = ad.get("advertiser", "Desconhecido")
        if name not in advertisers:
            advertisers[name] = {
                "advertiser": name,
                "total_ads": 0,
                "platforms": set(),
                "total_impressions": 0,
                "total_engagement": 0,
                "avg_days_running": 0,
                "days_list": [],
                "has_video": False,
                "keywords": set(),
                "countries": set(),
            }
        a = advertisers[name]
        a["total_ads"] += 1
        a["platforms"].add(ad.get("platform", ""))
        a["total_impressions"] += int(ad.get("impressions", 0) or 0)
        a["total_engagement"] += int(ad.get("total_engagement", 0) or 0)
        days = int(ad.get("days_running", 0) or 0)
        if days > 0:
            a["days_list"].append(days)
        if ad.get("video_url"):
            a["has_video"] = True
        if ad.get("search_keyword"):
            a["keywords"].add(ad["search_keyword"])
        if ad.get("country"):
            a["countries"].add(ad["country"])

    # Calcular medias e converter sets
    result = []
    for a in advertisers.values():
        a["platforms"] = list(a["platforms"])
        a["keywords"] = list(a["keywords"])
        a["countries"] = list(a["countries"])
        a["avg_days_running"] = round(sum(a["days_list"]) / len(a["days_list"]), 1) if a["days_list"] else 0
        del a["days_list"]
        result.append(a)

    result.sort(key=lambda x: x["total_ads"], reverse=True)
    return {"data": result[:limit]}


@app.get("/api/benchmark")
def benchmark(
    keyword: str = Query(..., description="Keyword para analisar"),
):
    """Benchmarking completo de uma keyword: metricas, top ads, formatos, CTAs"""
    ads = load_latest_data()
    filtered = [a for a in ads if keyword.lower() in (a.get("search_keyword", "") or "").lower()]

    if not filtered:
        return {"error": "Nenhum ad encontrado para essa keyword"}

    # Metricas gerais
    total = len(filtered)
    with_video = len([a for a in filtered if a.get("video_url")])
    with_image = total - with_video
    platforms = {}
    ctas = {}
    for ad in filtered:
        p = ad.get("platform", "unknown")
        platforms[p] = platforms.get(p, 0) + 1
        cta = ad.get("cta", "") or "Sem CTA"
        ctas[cta] = ctas.get(cta, 0) + 1

    # Top ads por engajamento
    top_engagement = sorted(filtered,
        key=lambda x: int(x.get("total_engagement", 0) or 0) + int(x.get("impressions", 0) or 0),
        reverse=True)[:10]

    # Top ads por duracao
    top_duration = sorted(filtered,
        key=lambda x: int(x.get("days_running", 0) or 0),
        reverse=True)[:10]

    # Anunciantes unicos
    unique_advertisers = list(set(a.get("advertiser", "") for a in filtered if a.get("advertiser")))

    return {
        "keyword": keyword,
        "total_ads": total,
        "unique_advertisers": len(unique_advertisers),
        "format_split": {"video": with_video, "image": with_image},
        "by_platform": dict(sorted(platforms.items(), key=lambda x: x[1], reverse=True)),
        "top_ctas": dict(sorted(ctas.items(), key=lambda x: x[1], reverse=True)[:10]),
        "top_by_engagement": top_engagement,
        "top_by_duration": top_duration,
        "advertisers": unique_advertisers[:30],
    }


@app.get("/api/keywords")
def get_keywords():
    """Dados de keywords pagas vs organicas por dominio"""
    return {"data": load_latest_keywords()}

@app.get("/api/ad/{ad_id}")
def get_ad(ad_id: str):
    """Detalhes de um anuncio"""
    ads = load_latest_data()
    for ad in ads:
        if ad.get("ad_id") == ad_id:
            return ad
    return {"error": "Ad not found"}

@app.get("/api/sources")
def get_sources():
    """Lista fontes disponiveis"""
    return {
        "sources": [
            {"id": "bigspy", "name": "BigSpy", "platforms": ["facebook", "instagram", "tiktok", "twitter", "pinterest"]},
            {"id": "adyntel_meta", "name": "Adyntel Meta", "platforms": ["facebook", "instagram"]},
            {"id": "adyntel_google", "name": "Adyntel Google", "platforms": ["google"]},
            {"id": "adyntel_linkedin", "name": "Adyntel LinkedIn", "platforms": ["linkedin"]},
            {"id": "adyntel_tiktok", "name": "Adyntel TikTok", "platforms": ["tiktok"]},
            {"id": "minea", "name": "Minea", "platforms": ["facebook", "tiktok", "pinterest"]},
            {"id": "pipiads", "name": "PiPiAds", "platforms": ["tiktok", "facebook"]},
        ]
    }

def _get_ai_client():
    from openai import OpenAI
    return OpenAI(api_key="sk-75b1ddd6be014170a52a790133025c07", base_url="https://api.deepseek.com")

def _find_ad(ad_id):
    ads = load_latest_data()
    for ad in ads:
        if ad.get("ad_id") == ad_id:
            return ad
    return None

def _save_ad_update(ad_id, updates):
    files = sorted(glob.glob(f"{OUTPUT_DIR}/unified_*.json"), reverse=True)
    if files:
        with open(files[0], "r", encoding="utf-8") as f:
            all_ads = json.load(f)
        for i, a in enumerate(all_ads):
            if a.get("ad_id") == ad_id:
                all_ads[i].update(updates)
                break
        with open(files[0], "w", encoding="utf-8") as f:
            json.dump(all_ads, f, ensure_ascii=False)

def _ai_call(prompt, max_tokens=1200):
    import re as _re
    client = _get_ai_client()
    r = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": "Voce SEMPRE retorna JSON valido. Sem markdown, sem explicacao, apenas o JSON puro. Use aspas duplas. Nao use caracteres especiais dentro de strings."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=max_tokens, temperature=0.2,
    )
    text = r.choices[0].message.content.strip()

    # Limpar markdown
    text = text.replace("```json", "").replace("```", "").strip()

    # Remover texto antes do primeiro {
    start = text.find("{")
    if start > 0:
        text = text[start:]

    # Remover texto depois do ultimo }
    end = text.rfind("}")
    if end >= 0:
        text = text[:end + 1]

    # Corrigir problemas comuns
    text = text.replace("\n", " ").replace("\r", " ")
    text = text.replace("\\n", " ").replace("\\r", " ")
    # Remover tabs dentro de strings
    text = text.replace("\t", " ")
    # Aspas simples -> duplas em chaves
    text = _re.sub(r"(?<={|,)\s*'([^']+)'\s*:", r' "\1":', text)

    import json as jl
    try:
        return jl.loads(text)
    except jl.JSONDecodeError:
        # Tentar corrigir JSON truncado adicionando fechamentos
        for fix in ["}", "]}", "\"}", "\"]}", "\"]}}"]:
            try:
                return jl.loads(text + fix)
            except:
                continue
        # Ultimo recurso: retornar dict basico
        return {"error": "JSON malformado", "raw": text[:200]}


# ============================================================
# 1. CREATIVE DECONSTRUCTION (Autopsia completa)
# ============================================================
@app.post("/api/analyze/{ad_id}")
def analyze_ad(ad_id: str):
    """Autopsia completa do criativo: psicologia, persona, brief reverso, prompts de IA"""
    target = _find_ad(ad_id)
    if not target:
        return {"error": "Ad nao encontrado"}

    # Se ja tem analise completa, retorna
    if target.get("ai_creative_brief"):
        return {"status": "already_analyzed", "ad_id": ad_id,
                **{k: v for k, v in target.items() if k.startswith("ai_")},
                "estimated_spend": target.get("estimated_spend"),
                "estimated_roas": target.get("estimated_roas"),
                "potential_score": target.get("potential_score")}

    body = target.get("body", "") or ""
    title = target.get("title", "") or ""
    if not body and not title:
        return {"error": "Ad sem texto para analisar"}

    try:
        # Calcular ROAS estimado
        spend = target.get("estimated_spend", 0) or 0
        revenue = target.get("store_daily_revenue", 0) or target.get("estimated_daily_revenue", 0) or 0
        roas = round((revenue * 30) / spend, 2) if spend > 0 and revenue > 0 else 0

        # Dados enriquecidos
        days = target.get('days_running', 0) or 0
        impressions = target.get('impressions', 0) or 0
        likes = target.get('likes', 0) or 0
        comments = target.get('comments', 0) or 0
        shares = target.get('shares', 0) or 0
        engagement = likes + comments + shares
        eng_rate = round(engagement / max(impressions, 1) * 100, 2)
        has_video = bool(target.get('video_url'))
        source = target.get('source', '')

        # Social1 specific
        units_sold = target.get('social1_units_sold', 0) or 0
        gmv = target.get('social1_gmv', 0) or 0
        creators = target.get('social1_creators', 0) or 0

        analysis = _ai_call(f"""Voce e uma equipe de 5 especialistas analisando este anuncio. Cada um contribui com sua area:
1. DIRETOR CRIATIVO: analisa o conceito, narrativa visual e storytelling
2. COPYWRITER SENIOR: disseca cada palavra, framework e gatilho do texto
3. MEDIA BUYER: avalia metricas, performance e eficiencia do gasto
4. PSICOLOGO DO CONSUMIDOR: decodifica as emocoes, vieses cognitivos e motivacoes
5. ESTRATEGISTA DE MARCA: posicionamento, diferenciacao e vantagem competitiva

DADOS DO ANUNCIO:
- Anunciante: {target.get('advertiser', '')}
- Plataforma: {target.get('platform', '')}
- Titulo: {title}
- Copy completo: {body[:800]}
- CTA: {target.get('cta', '')}
- Landing page: {target.get('landing_page', '')}
- Tipo: {'video' if has_video else 'imagem'}
- Fonte: {source}
- Dias rodando: {days}
- Impressoes: {impressions:,}
- Curtidas: {likes:,} | Comentarios: {comments:,} | Shares: {shares:,}
- Taxa de engajamento: {eng_rate}%
- Gasto estimado: ${spend:,.0f}
- Paises: {target.get('all_countries', target.get('country', ''))}
{"- Unidades vendidas: " + str(units_sold) + " | GMV: $" + str(gmv) + " | Creators: " + str(creators) if units_sold else ""}

Retorne APENAS JSON valido com esta estrutura:
{{
  "verdict": "EXCELENTE/BOM/MEDIANO/FRACO (1 palavra)",
  "verdict_emoji": "emoji que representa o nivel",
  "overall_score": 8.5,
  "headline": "frase de 1 linha resumindo o anuncio de forma impactante, como manchete de revista",

  "creative_direction": {{
    "concept": "qual e o conceito criativo central em 1 frase",
    "narrative_arc": "qual historia esta sendo contada (heroi, transformacao, descoberta, urgencia)",
    "visual_strategy": "o que a imagem/video provavelmente mostra e por que funciona",
    "attention_hook": "o que prende a atencao nos primeiros 2 segundos e por que",
    "scroll_stopper_score": 8,
    "what_makes_it_work": "em 2 frases, o segredo deste criativo"
  }},

  "copy_dissection": {{
    "framework": "PAS/AIDA/BAB/FAB/Story/Before-After-Bridge",
    "framework_explanation": "como o framework foi aplicado neste texto especificamente",
    "hook_line": "a primeira frase exata que funciona como gancho",
    "hook_type": "pergunta/estatistica/dor/curiosidade/beneficio/choque/contraste",
    "power_words": ["lista de palavras de poder usadas no texto"],
    "emotional_triggers": ["gatilho1 com explicacao", "gatilho2 com explicacao"],
    "objection_handling": "como o texto lida com objecoes do comprador",
    "cta_analysis": "analise do CTA - e forte? urgente? claro?",
    "copy_grade": "A+/A/B/C/D com justificativa",
    "weakness": "o ponto mais fraco do texto"
  }},

  "audience_profile": {{
    "primary_persona": {{
      "name_fictional": "nome ficticio para a persona (ex: Maria, 34, mae solteira)",
      "demographics": "genero, idade, localizacao, renda",
      "psychographics": "valores, estilo de vida, aspiracoes",
      "pain_points": ["dor1 especifica", "dor2 especifica", "dor3 especifica"],
      "desires": ["desejo1", "desejo2"],
      "objections": ["objecao1 antes de comprar", "objecao2"],
      "where_they_hang": "onde essa pessoa passa tempo online",
      "what_they_googled": "o que essa pessoa pesquisou antes de ver o anuncio"
    }},
    "secondary_audience": "quem mais pode ser impactado por esse anuncio"
  }},

  "psychology_deep_dive": {{
    "cognitive_biases": [
      {{"bias": "nome do vies cognitivo", "how_used": "como e explorado neste anuncio"}},
      {{"bias": "segundo vies", "how_used": "explicacao"}}
    ],
    "emotional_journey": "descreva a jornada emocional: o que a pessoa sente ao ler linha por linha (medo -> curiosidade -> esperanca -> urgencia)",
    "trust_signals": ["sinal de confianca 1", "sinal 2"],
    "scarcity_urgency": "como escassez ou urgencia e usada (se for)",
    "social_proof_type": "tipo de prova social (numeros, depoimentos, autoridade, bandwagon)",
    "decision_trigger": "o momento exato que faz a pessoa clicar"
  }},

  "performance_analysis": {{
    "engagement_verdict": "ALTO/MEDIO/BAIXO com contexto",
    "estimated_ctr": "X.X% (estimado baseado nas metricas)",
    "longevity_prediction": "quanto tempo mais esse ad pode rodar antes de fadigar",
    "best_performing_element": "qual elemento deste ad e o mais forte",
    "weakest_element": "o que esta puxando a performance para baixo",
    "ab_test_suggestions": ["teste A/B 1 para melhorar", "teste 2", "teste 3"]
  }},

  "competitive_intel": {{
    "market_position": "como este anunciante se posiciona vs concorrentes",
    "differentiation": "o que torna este anuncio diferente dos demais do nicho",
    "threat_level": "se voce e concorrente, quao ameacador e este ad (1-10)",
    "vulnerability": "onde este anunciante e vulneravel - como atacar"
  }},

  "replication_blueprint": {{
    "step1": "primeiro passo para replicar a estrategia deste ad",
    "step2": "segundo passo",
    "step3": "terceiro passo",
    "adapted_hook": "reescreva o hook adaptado para um concorrente",
    "adapted_cta": "reescreva o CTA melhorado",
    "platforms_to_test": ["plataformas onde essa abordagem funcionaria"],
    "estimated_budget_to_test": "quanto investir para testar ($)"
  }},

  "ai_generation": {{
    "image_prompt": "prompt detalhado para gerar imagem similar com IA (Midjourney/DALL-E)",
    "video_script": "roteiro de 30s inspirado neste ad",
    "copy_variations": ["variacao 1 do copy", "variacao 2", "variacao 3"],
    "headline_variations": ["titulo alternativo 1", "titulo 2", "titulo 3"]
  }},

  "niche": "nicho especifico",
  "product_type": "fisico/digital/servico/SaaS/curso",
  "language": "pt/en/es",
  "summary": "resumo executivo em 3 frases como se estivesse apresentando para um CEO"
}}""", max_tokens=2500)

        # Salvar todos os campos
        copy_diss = analysis.get("copy_dissection", {})
        psych = analysis.get("psychology_deep_dive", {})
        updates = {
            "ai_niche": analysis.get("niche", ""),
            "ai_target_audience": analysis.get("audience_profile", {}).get("primary_persona", {}).get("demographics", ""),
            "ai_strategy": analysis.get("summary", ""),
            "ai_hook_type": copy_diss.get("hook_type", ""),
            "ai_product_type": analysis.get("product_type", ""),
            "ai_copy_quality": analysis.get("overall_score", 0),
            "ai_urgency_level": psych.get("scarcity_urgency", ""),
            "ai_emotion": psych.get("emotional_journey", ""),
            "ai_language": analysis.get("language", ""),
            "ai_summary": analysis.get("summary", ""),
            "ai_headline": analysis.get("headline", ""),
            "ai_verdict": analysis.get("verdict", ""),
            "ai_verdict_emoji": analysis.get("verdict_emoji", ""),
            "ai_overall_score": analysis.get("overall_score", 0),
            "ai_creative_direction": analysis.get("creative_direction", {}),
            "ai_copy_dissection": copy_diss,
            "ai_persona": analysis.get("audience_profile", {}),
            "ai_creative_brief": analysis.get("competitive_intel", {}),
            "ai_psychology": psych,
            "ai_performance": analysis.get("performance_analysis", {}),
            "ai_replication": analysis.get("replication_blueprint", {}),
            "ai_generation": analysis.get("ai_generation", {}),
            "ai_prompts": analysis.get("ai_generation", {}),
            "ai_recommendations": analysis.get("performance_analysis", {}).get("ab_test_suggestions", []),
            "estimated_roas": roas,
        }
        _save_ad_update(ad_id, updates)

        return {"status": "analyzed", "ad_id": ad_id, **updates}

    except Exception as e:
        return {"error": str(e)}


# ============================================================
# 2. SCRIPT GENERATOR (Creative Co-Pilot)
# ============================================================
@app.post("/api/generate-script/{ad_id}")
def generate_script(ad_id: str):
    """Gera roteiro de video baseado na analise do anuncio"""
    target = _find_ad(ad_id)
    if not target:
        return {"error": "Ad nao encontrado"}

    body = target.get("body", "") or target.get("title", "") or ""
    if not body:
        return {"error": "Ad sem texto"}

    try:
        result = _ai_call(f"""Voce e um copywriter especialista em anuncios de video para redes sociais.
Baseado neste anuncio que esta performando bem, gere um roteiro de video adaptado.

ANUNCIO ORIGINAL:
- Copy: {body[:500]}
- CTA: {target.get('cta', '')}
- Nicho: {target.get('ai_niche', 'nao identificado')}
- Plataforma: {target.get('platform', '')}
- Publico: {target.get('ai_target_audience', '')}

Retorne JSON:
{{
  "hook": "frase de abertura impactante (primeiros 3 segundos)",
  "script": [
    {{"timestamp": "0-3s", "visual": "descricao da cena", "narration": "texto falado", "text_overlay": "texto na tela"}},
    {{"timestamp": "3-8s", "visual": "descricao", "narration": "texto", "text_overlay": "texto"}},
    {{"timestamp": "8-15s", "visual": "descricao", "narration": "texto", "text_overlay": "texto"}},
    {{"timestamp": "15-25s", "visual": "descricao", "narration": "texto", "text_overlay": "texto"}},
    {{"timestamp": "25-30s", "visual": "CTA final", "narration": "texto", "text_overlay": "texto"}}
  ],
  "music_suggestion": "tipo de musica sugerida",
  "format": "vertical 9:16 / horizontal 16:9",
  "estimated_duration": "30 segundos",
  "style": "estilo visual sugerido",
  "variations": [
    "variacao 1: mudar o hook para...",
    "variacao 2: testar angulo de...",
    "variacao 3: usar formato de..."
  ]
}}""", max_tokens=1000)

        return {"status": "generated", "ad_id": ad_id, **result}

    except Exception as e:
        return {"error": str(e)}


# ============================================================
# 3. STRATEGY ROOM (Comparar 3 concorrentes)
# ============================================================
@app.post("/api/strategy-room")
def strategy_room(
    advertisers: str = Query(..., description="3 anunciantes separados por virgula"),
):
    """Compara 3 concorrentes e gera inteligencia estrategica"""
    names = [n.strip() for n in advertisers.split(",")][:3]
    ads = load_latest_data()

    competitors = {}
    for name in names:
        name_lower = name.lower()
        comp_ads = [a for a in ads if name_lower in (a.get("advertiser", "") or "").lower()]
        if comp_ads:
            competitors[name] = {
                "total_ads": len(comp_ads),
                "platforms": list(set(a.get("platform", "") for a in comp_ads)),
                "avg_days": round(sum(a.get("days_running", 0) or 0 for a in comp_ads) / len(comp_ads), 1),
                "total_engagement": sum(a.get("total_engagement", 0) or 0 for a in comp_ads),
                "total_impressions": sum(a.get("impressions", 0) or 0 for a in comp_ads),
                "video_ratio": round(len([a for a in comp_ads if a.get("video_url")]) / len(comp_ads) * 100),
                "top_ctas": {},
                "niches": list(set(a.get("ai_niche", "") for a in comp_ads if a.get("ai_niche"))),
                "strategies": list(set(a.get("ai_strategy", "") for a in comp_ads if a.get("ai_strategy")))[:5],
                "sample_ads": comp_ads[:3],
            }
            for a in comp_ads:
                cta = a.get("cta", "") or "Sem CTA"
                competitors[name]["top_ctas"][cta] = competitors[name]["top_ctas"].get(cta, 0) + 1

    if not competitors:
        return {"error": "Nenhum anunciante encontrado"}

    # IA gera analise estrategica
    try:
        comp_summary = json.dumps({n: {k: v for k, v in d.items() if k != "sample_ads"} for n, d in competitors.items()}, ensure_ascii=False, default=str)

        ai_analysis = _ai_call(f"""Voce e um consultor de estrategia de marketing.
Analise estes 3 concorrentes e gere recomendacoes estrategicas.

DADOS DOS CONCORRENTES:
{comp_summary[:1500]}

Retorne JSON:
{{
  "market_leader": "quem esta ganhando e por que",
  "share_of_voice": {{"nome1": "X%", "nome2": "Y%", "nome3": "Z%"}},
  "gaps": ["oportunidade1 que ninguem esta explorando", "oportunidade2"],
  "recommendation": "recomendacao estrategica em 3 frases",
  "best_angle": "melhor angulo para atacar agora",
  "timing": "melhor momento para lancar campanha"
}}""")

        return {
            "competitors": competitors,
            "ai_analysis": ai_analysis,
        }
    except Exception as e:
        return {"competitors": competitors, "ai_analysis": {"error": str(e)}}


# ============================================================
# 4. SATURATION METER
# ============================================================
@app.get("/api/saturation")
def saturation(keyword: str = Query(...)):
    """Mede saturacao de mercado para uma keyword"""
    ads = load_latest_data()
    filtered = [a for a in ads if keyword.lower() in (a.get("search_keyword", "") or "").lower()
                or keyword.lower() in (a.get("body", "") or "").lower()
                or keyword.lower() in (a.get("title", "") or "").lower()]

    if not filtered:
        return {"keyword": keyword, "saturation": 0, "message": "Nenhum ad encontrado"}

    unique_advertisers = len(set(a.get("advertiser", "") for a in filtered if a.get("advertiser")))
    total_ads = len(filtered)
    avg_days = sum(a.get("days_running", 0) or 0 for a in filtered) / len(filtered) if filtered else 0
    with_video = len([a for a in filtered if a.get("video_url")])

    # Score de saturacao (0-100)
    score = min(100, int(
        (unique_advertisers / 50 * 30) +  # Mais anunciantes = mais saturado
        (total_ads / 200 * 30) +           # Mais ads = mais saturado
        (avg_days / 30 * 20) +             # Ads durando muito = mercado maduro
        (20 if avg_days > 14 else 0)       # Bonus se media > 14 dias
    ))

    level = "baixa" if score < 30 else "media" if score < 60 else "alta" if score < 80 else "muito alta"

    return {
        "keyword": keyword,
        "saturation_score": score,
        "saturation_level": level,
        "unique_advertisers": unique_advertisers,
        "total_ads": total_ads,
        "avg_days_running": round(avg_days, 1),
        "video_ratio": round(with_video / total_ads * 100) if total_ads > 0 else 0,
        "recommendation": "Mercado saturado - busque angulo diferenciado" if score > 60
                          else "Mercado com espaco - boa oportunidade" if score < 30
                          else "Mercado competitivo - precisa de criativo forte",
    }


# ============================================================
# 5. PIXEL DETECTION (da landing page)
# ============================================================
@app.get("/api/pixel-detect")
def pixel_detect(url: str = Query(..., description="URL da landing page")):
    """Detecta pixels de tracking instalados em uma landing page"""
    import requests as req

    try:
        r = req.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
        html = r.text.lower()

        pixels = {
            "meta_pixel": "fbq(" in html or "facebook.com/tr" in html or "connect.facebook" in html,
            "google_analytics": "gtag(" in html or "google-analytics.com" in html or "googletagmanager" in html,
            "google_ads": "googleads" in html or "conversion.js" in html or "google_conversion" in html,
            "tiktok_pixel": "tiktok.com/i18n/pixel" in html or "analytics.tiktok" in html,
            "linkedin_pixel": "snap.licdn.com" in html or "linkedin.com/px" in html,
            "pinterest_tag": "pintrk(" in html or "pinterest.com/ct" in html,
            "snapchat_pixel": "sc-static.net/scevent" in html or "snapchat" in html,
            "hotjar": "hotjar.com" in html,
            "clarity": "clarity.ms" in html,
            "shopify": "shopify" in html or "myshopify" in html,
            "wordpress": "wp-content" in html or "wordpress" in html,
            "klaviyo": "klaviyo" in html,
            "mailchimp": "mailchimp" in html,
        }

        active = [k for k, v in pixels.items() if v]
        platform = "shopify" if pixels["shopify"] else "wordpress" if pixels["wordpress"] else "outro"

        return {
            "url": url,
            "final_url": r.url,
            "platform": platform,
            "pixels_detected": active,
            "pixel_count": len(active),
            "all_checks": pixels,
            "has_retargeting": pixels["meta_pixel"] or pixels["google_ads"] or pixels["tiktok_pixel"],
            "has_analytics": pixels["google_analytics"] or pixels["hotjar"] or pixels["clarity"],
        }
    except Exception as e:
        return {"url": url, "error": str(e)}


# ============================================================
# FAVORITOS (salvar ads em colecoes)
# ============================================================
_favorites = {}  # {user_id: {collection_name: [ad_ids]}}

@app.post("/api/favorites/save")
def save_favorite(
    ad_id: str = Query(...),
    collection: str = Query("Meus Favoritos"),
    user_id: str = Query("default"),
):
    """Salva um ad nos favoritos"""
    if user_id not in _favorites:
        _favorites[user_id] = {}
    if collection not in _favorites[user_id]:
        _favorites[user_id][collection] = []
    if ad_id not in _favorites[user_id][collection]:
        _favorites[user_id][collection].append(ad_id)
    return {"status": "saved", "collection": collection, "total": len(_favorites[user_id][collection])}

@app.delete("/api/favorites/remove")
def remove_favorite(
    ad_id: str = Query(...),
    collection: str = Query("Meus Favoritos"),
    user_id: str = Query("default"),
):
    """Remove um ad dos favoritos"""
    if user_id in _favorites and collection in _favorites[user_id]:
        _favorites[user_id][collection] = [x for x in _favorites[user_id][collection] if x != ad_id]
    return {"status": "removed"}

@app.get("/api/favorites")
def list_favorites(
    collection: str = Query(None),
    user_id: str = Query("default"),
):
    """Lista favoritos com dados completos dos ads"""
    if user_id not in _favorites:
        return {"collections": {}, "ads": []}

    if collection:
        ids = _favorites[user_id].get(collection, [])
        ads = load_latest_data()
        fav_ads = [a for a in ads if a.get("ad_id") in ids]
        return {"collection": collection, "total": len(fav_ads), "ads": fav_ads}

    return {"collections": {k: len(v) for k, v in _favorites[user_id].items()}}

@app.get("/api/favorites/check")
def check_favorite(
    ad_id: str = Query(...),
    user_id: str = Query("default"),
):
    """Verifica se um ad esta nos favoritos"""
    saved_in = []
    if user_id in _favorites:
        for col, ids in _favorites[user_id].items():
            if ad_id in ids:
                saved_in.append(col)
    return {"ad_id": ad_id, "is_saved": len(saved_in) > 0, "collections": saved_in}


# ============================================================
# HISTORICO DE BUSCA
# ============================================================
_search_history = {}  # {user_id: [{query, timestamp, results_count}]}

@app.get("/api/history")
def get_history(user_id: str = Query("default"), limit: int = Query(20)):
    """Retorna historico de buscas"""
    history = _search_history.get(user_id, [])
    return {"history": history[-limit:][::-1]}

@app.post("/api/history/add")
def add_history(
    query: str = Query(...),
    results_count: int = Query(0),
    user_id: str = Query("default"),
):
    """Registra uma busca no historico"""
    if user_id not in _search_history:
        _search_history[user_id] = []
    _search_history[user_id].append({
        "query": query,
        "timestamp": datetime.now().isoformat(),
        "results_count": results_count,
    })
    # Manter max 100
    if len(_search_history[user_id]) > 100:
        _search_history[user_id] = _search_history[user_id][-100:]
    return {"status": "saved"}


# ============================================================
# MONITORAMENTO DE ANUNCIANTES (alertas de novos ads)
# ============================================================
_watchlist = {}  # {user_id: [advertiser_names]}

@app.post("/api/watchlist/add")
def add_to_watchlist(
    advertiser: str = Query(...),
    user_id: str = Query("default"),
):
    """Adiciona anunciante a lista de monitoramento"""
    if user_id not in _watchlist:
        _watchlist[user_id] = []
    if advertiser not in _watchlist[user_id]:
        _watchlist[user_id].append(advertiser)
    return {"status": "added", "watchlist": _watchlist[user_id]}

@app.delete("/api/watchlist/remove")
def remove_from_watchlist(
    advertiser: str = Query(...),
    user_id: str = Query("default"),
):
    """Remove anunciante do monitoramento"""
    if user_id in _watchlist:
        _watchlist[user_id] = [x for x in _watchlist[user_id] if x != advertiser]
    return {"status": "removed"}

@app.get("/api/watchlist")
def get_watchlist(user_id: str = Query("default")):
    """Lista anunciantes monitorados com contagem de ads"""
    names = _watchlist.get(user_id, [])
    ads = load_latest_data()
    result = []
    for name in names:
        name_lower = name.lower()
        advertiser_ads = [a for a in ads if name_lower in (a.get("advertiser", "") or "").lower()]
        latest = max([a.get("collected_at", "") for a in advertiser_ads]) if advertiser_ads else ""
        result.append({
            "advertiser": name,
            "total_ads": len(advertiser_ads),
            "latest_ad": latest,
            "platforms": list(set(a.get("platform", "") for a in advertiser_ads)),
        })
    return {"watchlist": result}


# ============================================================
# COMPARADOR DE LOJAS (dados Minea)
# ============================================================
@app.get("/api/compare-stores")
def compare_stores(
    domains: str = Query(..., description="Dominios separados por virgula, ex: loja1.com,loja2.com,loja3.com"),
):
    """Compara ate 5 lojas usando dados da Minea"""
    domain_list = [d.strip().lower() for d in domains.split(",")][:5]
    ads = load_latest_data()

    stores = {}
    for ad in ads:
        domain = (ad.get("store_domain", "") or "").lower()
        if not domain:
            landing = (ad.get("landing_page", "") or "").lower()
            for d in domain_list:
                if d in landing:
                    domain = d
                    break
        if domain and domain in domain_list and domain not in stores:
            stores[domain] = {
                "domain": domain,
                "store_url": ad.get("store_url", ""),
                "country": ad.get("store_country", ""),
                "created_at": ad.get("store_created_at", ""),
                "products_listed": ad.get("store_products_listed", 0),
                "monthly_visits": ad.get("store_monthly_visits", 0),
                "daily_revenue": ad.get("store_daily_revenue", 0),
                "monthly_revenue": round((ad.get("store_daily_revenue", 0) or 0) * 30, 2),
                "brand_total_ads": ad.get("brand_total_ads", 0),
                "brand_active_ads": ad.get("brand_active_ads", 0),
                "brand_estimated_spend": ad.get("brand_estimated_spend", 0),
                "estimated_roas": round(
                    ((ad.get("store_daily_revenue", 0) or 0) * 30) /
                    (ad.get("brand_estimated_spend", 0) or 1), 2
                ) if ad.get("brand_estimated_spend", 0) else 0,
                "advertiser": ad.get("advertiser", ""),
                "platforms": [],
                "total_ads_found": 0,
            }

    # Enriquecer com mais dados
    for ad in ads:
        for domain in stores:
            if domain in (ad.get("store_domain", "") or "").lower() or \
               domain in (ad.get("landing_page", "") or "").lower():
                stores[domain]["total_ads_found"] += 1
                p = ad.get("platform", "")
                if p and p not in stores[domain]["platforms"]:
                    stores[domain]["platforms"].append(p)

    store_list = list(stores.values())

    # Ranking
    if store_list:
        best_revenue = max(store_list, key=lambda x: x.get("daily_revenue", 0))
        best_traffic = max(store_list, key=lambda x: x.get("monthly_visits", 0))
        best_products = max(store_list, key=lambda x: x.get("products_listed", 0))
    else:
        best_revenue = best_traffic = best_products = {}

    # Se tem pelo menos 2 lojas, IA compara
    ai_comparison = {}
    if len(store_list) >= 2:
        try:
            store_summary = json.dumps([{k: v for k, v in s.items()} for s in store_list], ensure_ascii=False, default=str)
            ai_comparison = _ai_call(f"""Compare estas lojas e-commerce e diga qual esta melhor posicionada.

DADOS:
{store_summary[:2000]}

Retorne JSON:
{{
  "winner": "dominio da melhor loja",
  "reason": "por que ela ganha",
  "revenue_comparison": "comparacao de receita",
  "traffic_comparison": "comparacao de trafego",
  "strengths": {{"loja1.com": "ponto forte", "loja2.com": "ponto forte"}},
  "weaknesses": {{"loja1.com": "ponto fraco", "loja2.com": "ponto fraco"}},
  "recommendation": "recomendacao para quem quer competir nesse mercado"
}}""")
        except:
            pass

    return {
        "stores": store_list,
        "not_found": [d for d in domain_list if d not in stores],
        "rankings": {
            "best_revenue": best_revenue.get("domain", ""),
            "best_traffic": best_traffic.get("domain", ""),
            "best_catalog": best_products.get("domain", ""),
        },
        "ai_comparison": ai_comparison,
    }


@app.get("/api/stores/top")
def top_stores(limit: int = Query(20)):
    """Ranking das melhores lojas por receita"""
    ads = load_latest_data()
    stores = {}
    for ad in ads:
        domain = ad.get("store_domain", "")
        if domain and domain not in stores and ad.get("store_daily_revenue", 0) > 0:
            stores[domain] = {
                "domain": domain,
                "store_url": ad.get("store_url", ""),
                "advertiser": ad.get("advertiser", ""),
                "country": ad.get("store_country", ""),
                "daily_revenue": ad.get("store_daily_revenue", 0),
                "monthly_revenue": round((ad.get("store_daily_revenue", 0) or 0) * 30, 2),
                "monthly_visits": ad.get("store_monthly_visits", 0),
                "products_listed": ad.get("store_products_listed", 0),
                "brand_estimated_spend": ad.get("brand_estimated_spend", 0),
                "brand_total_ads": ad.get("brand_total_ads", 0),
            }
    ranked = sorted(stores.values(), key=lambda x: x["daily_revenue"], reverse=True)
    return {"stores": ranked[:limit]}


# ============================================================
# AFILIADOS NA GRINGA (ClickBank, BuyGoods, Digistore24, MaxWeb)
# ============================================================

_affiliate_cache = {"data": None, "loaded_at": None, "file": None}

def load_affiliate_products():
    """Carrega produtos de afiliacao com cache"""
    files = sorted(glob.glob(f"{OUTPUT_DIR}/affiliate_products_*.json"), reverse=True)
    if not files:
        return []

    latest = files[0]
    file_mtime = os.path.getmtime(latest)

    if _affiliate_cache["data"] is not None and _affiliate_cache["file"] == latest and _affiliate_cache["loaded_at"] == file_mtime:
        return _affiliate_cache["data"]

    with open(latest, "r", encoding="utf-8") as f:
        data = json.load(f)

    _affiliate_cache["data"] = data.get("products", [])
    _affiliate_cache["file"] = latest
    _affiliate_cache["loaded_at"] = file_mtime

    return _affiliate_cache["data"]


@app.get("/api/affiliate/products")
def list_affiliate_products(
    platform: str = Query(None, description="clickbank, buygoods, digistore24, maxweb"),
    niche: str = Query(None, description="health, fitness, wealth, beauty, etc"),
    trend: str = Query(None, description="rising_fast, rising, declining, stable"),
    competition: str = Query(None, description="low, medium, high"),
    min_score: float = Query(None, description="Ninja Score minimo (1-10)"),
    search: str = Query(None, description="Buscar no nome do produto"),
    sort: str = Query("ninja_score", description="Campo para ordenar"),
    order: str = Query("desc", description="asc ou desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Lista produtos de afiliacao internacional com filtros"""
    products = load_affiliate_products()

    if platform:
        products = [p for p in products if p.get("platform") == platform]
    if niche:
        products = [p for p in products if p.get("niche") == niche.lower()]
    if trend:
        products = [p for p in products if p.get("trend_direction") == trend]
    if competition:
        products = [p for p in products if p.get("competition_level") == competition]
    if min_score:
        products = [p for p in products if (p.get("ninja_score", 0) or 0) >= min_score]
    if search:
        sl = search.lower()
        products = [p for p in products if sl in (p.get("name", "") or "").lower()]

    # Ordenacao
    reverse = order == "desc"
    try:
        products.sort(key=lambda x: x.get(sort, 0) or 0, reverse=reverse)
    except:
        pass

    total = len(products)
    start = (page - 1) * limit
    page_products = products[start:start + limit]

    return {
        "data": page_products,
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit if total > 0 else 0,
        "limit": limit,
    }


@app.get("/api/affiliate/stats")
def affiliate_stats():
    """Estatisticas dos produtos de afiliacao"""
    products = load_affiliate_products()
    if not products:
        return {"total": 0}

    niches = {}
    platforms = {}
    trends = {}
    for p in products:
        n = p.get("niche", "other")
        niches[n] = niches.get(n, 0) + 1
        pl = p.get("platform", "unknown")
        platforms[pl] = platforms.get(pl, 0) + 1
        t = p.get("trend_direction", "unknown")
        trends[t] = trends.get(t, 0) + 1

    scores = [p.get("ninja_score", 0) for p in products if p.get("ninja_score")]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0

    return {
        "total_products": len(products),
        "avg_ninja_score": avg_score,
        "by_niche": dict(sorted(niches.items(), key=lambda x: x[1], reverse=True)),
        "by_platform": dict(sorted(platforms.items(), key=lambda x: x[1], reverse=True)),
        "by_trend": dict(sorted(trends.items(), key=lambda x: x[1], reverse=True)),
        "top_rising": sorted(
            [p for p in products if p.get("trend_direction", "").startswith("rising")],
            key=lambda x: x.get("ninja_score", 0), reverse=True
        )[:10],
    }


@app.get("/api/affiliate/trending")
def affiliate_trending(
    platform: str = Query(None),
    limit: int = Query(20, ge=1, le=50),
):
    """Produtos em alta - subindo rapido"""
    products = load_affiliate_products()
    if platform:
        products = [p for p in products if p.get("platform") == platform]

    # Filtrar apenas rising e ordenar por trend_7d
    rising = [p for p in products if (p.get("trend_7d", 0) or 0) > 0]
    rising.sort(key=lambda x: x.get("trend_7d", 0) or 0, reverse=True)

    return {"data": rising[:limit], "total": len(rising)}


@app.get("/api/affiliate/opportunities")
def affiliate_opportunities(
    platform: str = Query(None),
    limit: int = Query(20, ge=1, le=50),
):
    """Oportunidades - alto score + baixa competicao"""
    products = load_affiliate_products()
    if platform:
        products = [p for p in products if p.get("platform") == platform]

    opportunities = [p for p in products
                     if (p.get("ninja_score", 0) or 0) >= 5
                     and p.get("competition_level") in ("low", "medium")]
    opportunities.sort(key=lambda x: x.get("opportunity_score", 0) or 0, reverse=True)

    return {"data": opportunities[:limit], "total": len(opportunities)}


@app.get("/api/affiliate/saturation-clock")
def saturation_clock(
    zone: str = Query(None, description="gold_rush, early_majority, growth, mature, saturation"),
    niche: str = Query(None),
    platform: str = Query(None),
    limit: int = Query(50, ge=1, le=100),
):
    """Predictive Saturation Clock - janela de oportunidade em tempo real"""
    products = load_affiliate_products()

    if platform:
        products = [p for p in products if p.get("platform") == platform]
    if niche:
        products = [p for p in products if p.get("niche") == niche.lower()]
    if zone:
        products = [p for p in products if p.get("saturation_zone") == zone]

    # Ordenar por opportunity_score
    products.sort(key=lambda x: x.get("opportunity_score", 0) or 0, reverse=True)

    # Stats por zona
    zones = {}
    for p in load_affiliate_products():
        z = p.get("saturation_zone", "unknown")
        zones[z] = zones.get(z, 0) + 1

    return {
        "data": products[:limit],
        "total": len(products),
        "zones_overview": zones,
    }


@app.get("/api/affiliate/gold-rush")
def gold_rush(
    niche: str = Query(None),
    limit: int = Query(20, ge=1, le=50),
):
    """Gold Rush - produtos explodindo AGORA com pouca competicao"""
    products = load_affiliate_products()

    if niche:
        products = [p for p in products if p.get("niche") == niche.lower()]

    gold = [p for p in products if p.get("saturation_zone") == "gold_rush"]
    gold.sort(key=lambda x: x.get("opportunity_score", 0) or 0, reverse=True)

    return {"data": gold[:limit], "total": len(gold)}


# ============================================================
# SATURATION CLOCK GERAL (Cross-source: Ads + Afiliados)
# ============================================================

def _build_market_intelligence():
    """Cruza dados de ads (6 fontes) com produtos afiliados para gerar inteligencia de mercado"""
    ads = load_latest_data()
    affiliates = load_affiliate_products()

    # Agrupar ads por keyword/nicho
    keyword_stats = {}
    for ad in ads:
        kw = (ad.get("search_keyword") or ad.get("ai_niche") or "").lower()
        if not kw:
            continue
        if kw not in keyword_stats:
            keyword_stats[kw] = {
                "keyword": kw,
                "total_ads": 0,
                "advertisers": set(),
                "platforms": set(),
                "sources": set(),
                "total_impressions": 0,
                "total_engagement": 0,
                "total_spend": 0,
                "days_running_list": [],
                "heat_list": [],
                "with_video": 0,
                "recent_ads": 0,  # ads com menos de 7 dias
            }
        s = keyword_stats[kw]
        s["total_ads"] += 1
        if ad.get("advertiser"):
            s["advertisers"].add(ad["advertiser"])
        if ad.get("platform"):
            s["platforms"].add(ad["platform"])
        if ad.get("source"):
            s["sources"].add(ad["source"])
        s["total_impressions"] += int(ad.get("impressions", 0) or 0)
        s["total_engagement"] += int(ad.get("total_engagement", 0) or 0)
        s["total_spend"] += float(ad.get("estimated_spend", 0) or 0)
        days = int(ad.get("days_running", 0) or 0)
        if days > 0:
            s["days_running_list"].append(days)
        if days <= 7:
            s["recent_ads"] += 1
        heat = float(ad.get("heat", 0) or 0)
        if heat > 0:
            s["heat_list"].append(heat)
        if ad.get("video_url"):
            s["with_video"] += 1

    # Calcular metricas finais por keyword
    markets = []
    for kw, s in keyword_stats.items():
        if s["total_ads"] < 2:
            continue

        unique_advertisers = len(s["advertisers"])
        avg_days = sum(s["days_running_list"]) / len(s["days_running_list"]) if s["days_running_list"] else 0
        avg_heat = sum(s["heat_list"]) / len(s["heat_list"]) if s["heat_list"] else 0
        freshness = s["recent_ads"] / s["total_ads"] if s["total_ads"] > 0 else 0
        source_count = len(s["sources"])
        platform_count = len(s["platforms"])

        # Saturacao (0-100)
        sat_score = min(100, int(
            (unique_advertisers / 50 * 25) +
            (s["total_ads"] / 200 * 25) +
            (avg_days / 30 * 20) +
            (15 if avg_days > 14 else 0) +
            (15 if unique_advertisers > 20 else 0)
        ))

        # Momentum: freshness alta = mercado aquecendo, baixa = estagnado
        momentum = round(freshness * 100)

        # Multi-source signal: aparece em multiplas fontes = sinal forte
        cross_source = min(10, source_count * 2 + platform_count)

        # Zona
        if momentum > 60 and sat_score < 40:
            zone = "gold_rush"
        elif momentum > 40 and sat_score < 60:
            zone = "early_majority"
        elif momentum > 20 and sat_score < 70:
            zone = "growth"
        elif sat_score >= 70:
            zone = "saturation"
        else:
            zone = "mature"

        # Opportunity (0-100)
        opp = max(0, min(100, int(
            momentum * 0.4 +
            (100 - sat_score) * 0.3 +
            cross_source * 3 +
            min(20, avg_heat * 0.3)
        )))

        markets.append({
            "keyword": kw,
            "total_ads": s["total_ads"],
            "unique_advertisers": unique_advertisers,
            "platforms": sorted(s["platforms"]),
            "sources": sorted(s["sources"]),
            "source_count": source_count,
            "total_impressions": s["total_impressions"],
            "total_engagement": s["total_engagement"],
            "total_spend": round(s["total_spend"], 2),
            "avg_days_running": round(avg_days, 1),
            "avg_heat": round(avg_heat, 1),
            "video_ratio": round(s["with_video"] / s["total_ads"] * 100) if s["total_ads"] > 0 else 0,
            "freshness": momentum,
            "saturation_score": sat_score,
            "cross_source_signal": cross_source,
            "zone": zone,
            "opportunity_score": opp,
            # Cruzamento com afiliados
            "affiliate_products": [],
        })

    # Cruzar com produtos afiliados por nicho
    aff_by_niche = {}
    for p in affiliates:
        n = p.get("niche", "other")
        if n not in aff_by_niche:
            aff_by_niche[n] = []
        aff_by_niche[n].append(p)

    for m in markets:
        kw = m["keyword"]
        # Match por keyword em nichos de afiliados
        matched = []
        for niche, prods in aff_by_niche.items():
            if kw in niche or niche in kw:
                top = sorted(prods, key=lambda x: x.get("opportunity_score", 0), reverse=True)[:3]
                matched.extend(top)
        # Tambem buscar por nome
        if not matched:
            for p in affiliates:
                if kw in p.get("name", "").lower():
                    matched.append(p)
                    if len(matched) >= 3:
                        break
        m["affiliate_products"] = matched[:3]
        m["has_affiliate_match"] = len(matched) > 0

    markets.sort(key=lambda x: x["opportunity_score"], reverse=True)
    return markets


@app.get("/api/market-intelligence")
def market_intelligence(
    zone: str = Query(None, description="gold_rush, early_majority, growth, mature, saturation"),
    min_ads: int = Query(5, description="Minimo de ads para considerar o mercado"),
    limit: int = Query(30, ge=1, le=100),
):
    """Inteligencia de mercado geral - cruza 6 fontes de ads + produtos afiliados"""
    markets = _build_market_intelligence()

    if min_ads:
        markets = [m for m in markets if m["total_ads"] >= min_ads]
    if zone:
        markets = [m for m in markets if m["zone"] == zone]

    # Stats
    zones = {}
    for m in _build_market_intelligence():
        z = m["zone"]
        zones[z] = zones.get(z, 0) + 1

    return {
        "data": markets[:limit],
        "total": len(markets),
        "zones_overview": zones,
    }


@app.get("/api/market-intelligence/gold-rush")
def market_gold_rush(limit: int = Query(20, ge=1, le=50)):
    """Mercados em Gold Rush - cruzamento de todas as fontes"""
    markets = _build_market_intelligence()
    gold = [m for m in markets if m["zone"] == "gold_rush" and m["total_ads"] >= 3]
    return {"data": gold[:limit], "total": len(gold)}


@app.get("/api/market-intelligence/cross-source")
def cross_source_signals(
    min_sources: int = Query(3, description="Minimo de fontes para considerar sinal forte"),
    limit: int = Query(20, ge=1, le=50),
):
    """Sinais cross-source - mercados detectados em multiplas fontes simultaneamente"""
    markets = _build_market_intelligence()
    strong = [m for m in markets if m["source_count"] >= min_sources]
    strong.sort(key=lambda x: x["cross_source_signal"], reverse=True)
    return {"data": strong[:limit], "total": len(strong)}


# ============================================================
# YOUTUBE SPY (SearchAPI.io) — Video analysis + VSL transcription
# ============================================================

SEARCHAPI_KEY = "ZFDmiHTH75sZT3wjDBc7vGay"

# Transcript search index
_transcript_cache = {"data": None, "loaded_at": None}

def load_transcripts():
    files = sorted(glob.glob(f"{OUTPUT_DIR}/transcript_index.json"), reverse=True)
    if not files:
        return {}
    latest = files[0]
    mtime = os.path.getmtime(latest)
    if _transcript_cache["data"] and _transcript_cache["loaded_at"] == mtime:
        return _transcript_cache["data"]
    with open(latest, "r", encoding="utf-8") as f:
        data = json.load(f)
    _transcript_cache["data"] = data.get("videos", {})
    _transcript_cache["loaded_at"] = mtime
    return _transcript_cache["data"]


@app.get("/api/youtube/transcript-search")
def transcript_search(
    q: str = Query(..., description="Buscar palavra FALADA nos videos"),
    language: str = Query(None, description="Filtrar por idioma (en, pt)"),
    min_views: int = Query(None, description="Views minimos"),
    limit: int = Query(20, ge=1, le=50),
    page: int = Query(1, ge=1),
):
    """Busca por transcrição — encontra videos onde a keyword é FALADA"""
    videos = load_transcripts()
    q_lower = q.lower()

    results = []
    for vid_id, v in videos.items():
        transcript = (v.get("transcript") or "").lower()
        if q_lower not in transcript:
            continue

        if language and v.get("language") != language:
            continue
        if min_views and (v.get("views", 0) or 0) < min_views:
            continue

        # Find snippet where the keyword appears
        idx = transcript.find(q_lower)
        start = max(0, idx - 80)
        end = min(len(transcript), idx + len(q_lower) + 80)
        snippet = "..." + transcript[start:end].strip() + "..."
        # Highlight the keyword
        snippet = snippet.replace(q_lower, f"**{q_lower}**")

        # Count occurrences
        count = transcript.count(q_lower)

        results.append({
            "video_id": v.get("video_id", ""),
            "title": v.get("title", ""),
            "channel_name": v.get("channel_name", ""),
            "channel_verified": v.get("channel_verified", False),
            "views": v.get("views", 0),
            "link": v.get("link", ""),
            "embed_url": f"https://www.youtube.com/embed/{v.get('video_id', '')}",
            "thumbnail": v.get("thumbnail", ""),
            "published": v.get("published", ""),
            "duration": v.get("duration", ""),
            "language": v.get("language", ""),
            "keyword_count": count,
            "snippet": snippet,
            "word_count": v.get("word_count", 0),
            "keyword": v.get("keyword", ""),
        })

    # Sort by keyword count (most mentions first), then views
    results.sort(key=lambda x: (x["keyword_count"], x["views"]), reverse=True)

    total = len(results)
    start = (page - 1) * limit

    return {
        "query": q,
        "total_results": total,
        "total_transcribed": len(videos),
        "data": results[start:start + limit],
        "page": page,
        "pages": (total + limit - 1) // limit if total > 0 else 0,
    }


@app.get("/api/youtube/transcript-stats")
def transcript_stats():
    """Estatisticas do banco de transcricoes"""
    videos = load_transcripts()
    langs = {}
    keywords = {}
    total_words = 0
    for v in videos.values():
        lang = v.get("language", "?")
        langs[lang] = langs.get(lang, 0) + 1
        kw = v.get("keyword", "?")
        keywords[kw] = keywords.get(kw, 0) + 1
        total_words += v.get("word_count", 0)

    return {
        "total_transcribed": len(videos),
        "total_words": total_words,
        "by_language": dict(sorted(langs.items(), key=lambda x: x[1], reverse=True)),
        "by_keyword": dict(sorted(keywords.items(), key=lambda x: x[1], reverse=True)[:20]),
    }


@app.get("/api/google-ads/{domain}")
def google_ads_spy(domain: str, region: str = Query("US")):
    """Google Ads oficiais de qualquer domínio — via Google Ads Transparency"""
    import requests as req
    try:
        r = req.get("https://www.searchapi.io/api/v1/search", params={
            "engine": "google_ads_transparency_center",
            "domain": domain,
            "region": region,
            "num": 40,
            "api_key": SEARCHAPI_KEY,
        }, timeout=15)
        data = r.json()
        ads = data.get("ad_creatives", [])

        results = []
        for ad in ads:
            advertiser = ad.get("advertiser", {})
            results.append({
                "ad_id": ad.get("id", ""),
                "domain": ad.get("target_domain", domain),
                "advertiser_name": advertiser.get("name", ""),
                "advertiser_id": advertiser.get("id", ""),
                "format": ad.get("format", ""),
                "image": ad.get("image", {}).get("link", "") if ad.get("image") else "",
                "first_shown": ad.get("first_shown_datetime", ""),
                "last_shown": ad.get("last_shown_datetime", ""),
                "total_days": ad.get("total_days_shown", 0),
                "details_link": ad.get("details_link", ""),
            })

        return {
            "domain": domain,
            "region": region,
            "total": data.get("search_information", {}).get("total_results", len(results)),
            "data": results,
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/google-trends")
def google_trends(
    q: str = Query(..., description="Keyword para analisar tendencia"),
    geo: str = Query("", description="Pais (US, BR, GB, etc)"),
    time: str = Query("today 12-m", description="Periodo (today 12-m, today 3-m, today 1-m)"),
):
    """Google Trends — volume de busca ao longo do tempo"""
    import requests as req
    try:
        r = req.get("https://www.searchapi.io/api/v1/search", params={
            "engine": "google_trends",
            "q": q,
            "geo": geo,
            "data_type": "TIMESERIES",
            "time": time,
            "api_key": SEARCHAPI_KEY,
        }, timeout=15)
        data = r.json()

        timeline = data.get("interest_over_time", {}).get("timeline_data", [])
        points = []
        for point in timeline:
            values = point.get("values", [{}])
            points.append({
                "date": point.get("date", ""),
                "value": values[0].get("extracted_value", 0) if values else 0,
            })

        # Calculate trend (growing or declining)
        if len(points) >= 4:
            recent_avg = sum(p["value"] for p in points[-4:]) / 4
            older_avg = sum(p["value"] for p in points[:4]) / 4
            trend = "rising" if recent_avg > older_avg * 1.1 else "declining" if recent_avg < older_avg * 0.9 else "stable"
            change_pct = round((recent_avg - older_avg) / max(older_avg, 1) * 100)
        else:
            trend = "unknown"
            change_pct = 0

        # Related queries
        related = data.get("related_queries", {})
        rising_queries = []
        if related.get("rising"):
            rising_queries = [{"query": q.get("query", ""), "value": q.get("extracted_value", 0)} for q in related["rising"][:10]]

        return {
            "query": q,
            "geo": geo or "Worldwide",
            "period": time,
            "trend": trend,
            "change_pct": change_pct,
            "data_points": points,
            "peak_value": max(p["value"] for p in points) if points else 0,
            "current_value": points[-1]["value"] if points else 0,
            "rising_queries": rising_queries,
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/linkedin-ads")
def linkedin_ads_search(
    q: str = Query(..., description="Keyword de busca"),
):
    """LinkedIn Ad Library — ads oficiais do LinkedIn"""
    import requests as req
    try:
        r = req.get("https://www.searchapi.io/api/v1/search", params={
            "engine": "linkedin_ad_library",
            "q": q,
            "api_key": SEARCHAPI_KEY,
        }, timeout=15)
        data = r.json()
        ads = data.get("ads", [])

        results = []
        for ad in ads:
            advertiser = ad.get("advertiser", {})
            content = ad.get("content", {})
            results.append({
                "ad_id": ad.get("id", ""),
                "advertiser_name": advertiser.get("name", ""),
                "advertiser_position": advertiser.get("position", ""),
                "advertiser_thumbnail": advertiser.get("thumbnail", ""),
                "ad_type": ad.get("ad_type", ""),
                "headline": content.get("headline", ""),
                "description": content.get("description", ""),
                "cta": content.get("cta", ""),
                "image": content.get("image", ""),
                "link": ad.get("link", ""),
            })

        return {
            "query": q,
            "total": data.get("search_information", {}).get("total_results", len(results)),
            "data": results,
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/youtube/search")
def youtube_search(
    q: str = Query(..., description="Keyword de busca"),
    num: int = Query(20, ge=1, le=50),
):
    """Busca videos no YouTube por keyword — encontra VSLs e ads"""
    import requests as req
    try:
        r = req.get("https://www.searchapi.io/api/v1/search", params={
            "engine": "youtube",
            "q": q,
            "num": num,
            "api_key": SEARCHAPI_KEY,
        }, timeout=15)
        data = r.json()
        videos = data.get("videos", []) or data.get("video_results", [])

        results = []
        for v in videos:
            channel = v.get("channel", {})
            # Thumbnail pode ser string ou objeto {static, rich}
            thumb = v.get("thumbnail", "")
            if isinstance(thumb, dict):
                thumb = thumb.get("rich") or thumb.get("static") or ""
            # Video embed URL para reproduzir inline
            vid_id = v.get("id", "")
            embed_url = f"https://www.youtube.com/embed/{vid_id}" if vid_id else ""

            results.append({
                "video_id": vid_id,
                "title": v.get("title", ""),
                "description": (v.get("description", "") or "")[:300],
                "views": v.get("views", 0),
                "link": v.get("link", ""),
                "embed_url": embed_url,
                "thumbnail": thumb,
                "published": v.get("published_time", ""),
                "duration": v.get("length", ""),
                "channel_name": channel.get("title", ""),
                "channel_id": channel.get("id", ""),
                "channel_verified": channel.get("is_verified", False),
                "channel_thumbnail": channel.get("thumbnail", ""),
            })

        return {
            "query": q,
            "total_results": data.get("search_information", {}).get("total_results", 0),
            "data": results,
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/youtube/channel/{channel_id}")
def youtube_channel_videos(
    channel_id: str,
    num: int = Query(20, ge=1, le=50),
):
    """Lista videos de um canal do YouTube — espionar anunciantes"""
    import requests as req
    try:
        r = req.get("https://www.searchapi.io/api/v1/search", params={
            "engine": "youtube_channel_videos",
            "channel_id": channel_id,
            "num": num,
            "api_key": SEARCHAPI_KEY,
        }, timeout=15)
        data = r.json()

        channel_info = data.get("channel", {})
        videos = []
        for v in data.get("videos", []):
            videos.append({
                "video_id": v.get("id", ""),
                "title": v.get("title", ""),
                "views": v.get("views", 0),
                "link": v.get("link", ""),
                "thumbnail": v.get("thumbnail", ""),
                "published": v.get("published_time", ""),
                "duration": v.get("length", ""),
            })

        return {
            "channel": {
                "name": channel_info.get("title", ""),
                "subscribers": channel_info.get("subscribers", 0),
                "videos_count": channel_info.get("videos", 0),
                "description": channel_info.get("description", ""),
                "thumbnail": channel_info.get("thumbnail", ""),
            },
            "videos": videos,
            "total": len(videos),
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/youtube/comments/{video_id}")
def youtube_comments(
    video_id: str,
    num: int = Query(20, ge=1, le=50),
):
    """Comentarios de um video — entender objecoes do publico"""
    import requests as req
    try:
        # First get video info for comment count
        r = req.get("https://www.searchapi.io/api/v1/search", params={
            "engine": "youtube_video",
            "video_id": video_id,
            "api_key": SEARCHAPI_KEY,
        }, timeout=15)
        data = r.json()

        video = data.get("video", {})
        comments_data = data.get("comments", [])

        return {
            "video_id": video_id,
            "title": video.get("title", ""),
            "total_comments": data.get("comment", {}).get("total", 0),
            "comments": comments_data[:num] if isinstance(comments_data, list) else [],
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/youtube/analyze")
def youtube_analyze(url: str = Query(..., description="URL ou ID do video do YouTube")):
    """Analisa um video do YouTube — metadata completa + transcrição"""
    import requests as req

    # Extrair video_id da URL
    video_id = url
    if "youtube.com" in url or "youtu.be" in url:
        if "v=" in url:
            video_id = url.split("v=")[1].split("&")[0]
        elif "youtu.be/" in url:
            video_id = url.split("youtu.be/")[1].split("?")[0]

    # Buscar metadata do video
    try:
        r = req.get("https://www.searchapi.io/api/v1/search", params={
            "engine": "youtube_video",
            "video_id": video_id,
            "api_key": SEARCHAPI_KEY,
        }, timeout=15)
        video_data = r.json()
    except Exception as e:
        return {"error": f"Erro ao buscar video: {e}"}

    if "error" in video_data:
        return {"error": video_data["error"]}

    video = video_data.get("video", {})
    channel = video_data.get("channel", {})
    comment = video_data.get("comment", {})

    # Buscar transcrição
    transcript_text = ""
    transcript_segments = []
    try:
        r2 = req.get("https://www.searchapi.io/api/v1/search", params={
            "engine": "youtube_transcripts",
            "video_id": video_id,
            "lang": "en",
            "api_key": SEARCHAPI_KEY,
        }, timeout=15)
        trans_data = r2.json()
        segments = trans_data.get("transcripts", [])
        transcript_segments = segments
        transcript_text = " ".join(s.get("text", "") for s in segments)
    except:
        pass

    # Se não achou em inglês, tenta português
    if not transcript_text:
        try:
            r3 = req.get("https://www.searchapi.io/api/v1/search", params={
                "engine": "youtube_transcripts",
                "video_id": video_id,
                "lang": "pt",
                "api_key": SEARCHAPI_KEY,
            }, timeout=15)
            trans_data = r3.json()
            segments = trans_data.get("transcripts", [])
            transcript_segments = segments
            transcript_text = " ".join(s.get("text", "") for s in segments)
        except:
            pass

    result = {
        "video_id": video_id,
        "title": video.get("title", ""),
        "description": video.get("description", ""),
        "views": video.get("views", 0),
        "likes": video.get("likes", 0),
        "comments_count": comment.get("total", 0),
        "duration_seconds": video.get("length_seconds", 0),
        "published": video.get("published_time", ""),
        "category": video.get("category", ""),
        "keywords": video.get("keywords", []),
        "thumbnail": video.get("thumbnail", ""),
        "is_family_safe": video.get("is_family_safe", True),
        "channel": {
            "name": channel.get("name", ""),
            "subscribers": channel.get("subscribers", 0),
            "link": channel.get("link", ""),
            "thumbnail": channel.get("thumbnail", ""),
        },
        "transcript": {
            "full_text": transcript_text,
            "segments": transcript_segments[:200],
            "word_count": len(transcript_text.split()) if transcript_text else 0,
            "has_transcript": bool(transcript_text),
        },
        "available_languages": trans_data.get("available_languages", []) if transcript_text else [],
    }

    return result


@app.post("/api/youtube/analyze-vsl")
def youtube_analyze_vsl(url: str = Query(..., description="URL do YouTube")):
    """Transcreve VSL do YouTube e analisa com IA — o script completo + estratégia"""
    # Primeiro buscar video + transcript
    video_data = youtube_analyze(url)
    if "error" in video_data:
        return video_data

    transcript = video_data.get("transcript", {}).get("full_text", "")
    if not transcript:
        return {"error": "Video sem transcrição disponível", "video": video_data}

    # Limitar transcript para IA
    transcript_trimmed = transcript[:3000]

    try:
        analysis = _ai_call(f"""Voce e um especialista em VSLs (Video Sales Letters) de Direct Response.
Analise esta transcrição de VSL e extraia toda a inteligencia competitiva.

VIDEO: {video_data.get('title', '')}
CANAL: {video_data.get('channel', {}).get('name', '')}
VIEWS: {video_data.get('views', 0):,}
DURAÇÃO: {video_data.get('duration_seconds', 0)}s

TRANSCRIÇÃO:
{transcript_trimmed}

Retorne JSON:
{{
  "vsl_type": "tipo da VSL (whiteboard, talking head, slides, animation, documentary)",
  "product_name": "nome do produto mencionado",
  "product_type": "fisico/digital/suplemento/software",
  "mechanism": "mecanismo unico de venda (o ingrediente secreto, a descoberta, o metodo)",
  "target_audience": "publico-alvo detalhado",
  "main_promise": "a promessa principal em 1 frase",
  "hook_analysis": {{
    "opening_hook": "primeiras 2-3 frases da VSL",
    "hook_type": "curiosity/fear/story/statistic/authority",
    "attention_retention": "como mantem atencao ao longo do video"
  }},
  "copy_structure": {{
    "framework": "framework usado (PAS, AIDA, Star-Story-Solution, etc)",
    "sections": ["secao1: o que acontece", "secao2: o que acontece", "secao3"],
    "emotional_arc": "jornada emocional do viewer"
  }},
  "objections_handled": ["objecao1 e como foi tratada", "objecao2"],
  "social_proof": ["prova social 1", "prova social 2"],
  "urgency_scarcity": "como cria urgencia ou escassez",
  "offer_structure": {{
    "main_offer": "o que esta vendendo",
    "price_anchoring": "como ancora o preco",
    "bonuses": ["bonus1", "bonus2"],
    "guarantee": "tipo de garantia"
  }},
  "cta": "call to action final",
  "key_phrases": ["frase poderosa 1 do script", "frase 2", "frase 3", "frase 4", "frase 5"],
  "replication_guide": {{
    "angle": "angulo que pode ser replicado",
    "adapted_hook": "hook adaptado para usar em outro produto",
    "script_template": "template de 5 linhas baseado na estrutura desta VSL"
  }},
  "score": 8,
  "verdict": "EXCELENTE/BOM/MEDIANO/FRACO"
}}""", max_tokens=2000)

        return {
            "video": video_data,
            "vsl_analysis": analysis,
            "status": "analyzed",
        }

    except Exception as e:
        return {
            "video": video_data,
            "vsl_analysis": {"error": str(e)},
            "status": "transcript_only",
        }


@app.get("/api/meta-ads/search")
def meta_ads_search(
    q: str = Query(..., description="Keyword de busca"),
    country: str = Query("US", description="Pais (US, BR, GB, etc)"),
    active_only: bool = Query(True),
    media_type: str = Query("all", description="all, video, image"),
):
    """Busca ads oficiais do Meta Ad Library via SearchAPI — enriquecido com metricas"""
    import requests as req

    params = {
        "engine": "meta_ad_library",
        "q": q,
        "country": country,
        "active_status": "active" if active_only else "all",
        "media_type": media_type,
        "sort_by": "impressions_high_to_low",
        "api_key": SEARCHAPI_KEY,
    }

    try:
        r = req.get("https://www.searchapi.io/api/v1/search", params=params, timeout=30)
        data = r.json()

        if "error" in data:
            return {"error": data["error"]}

        ads_raw = data.get("ads", [])
        total = data.get("search_information", {}).get("total_results", 0)

        # Carregar dados existentes para enriquecimento
        all_existing = load_latest_data()
        existing_by_adv = {}
        for a in all_existing:
            name = (a.get("advertiser") or "").lower().strip()
            if name:
                if name not in existing_by_adv:
                    existing_by_adv[name] = []
                existing_by_adv[name].append(a)

        simplified = []
        for ad in ads_raw:
            snap = ad.get("snapshot", {})
            images = snap.get("images", [])
            videos = snap.get("videos", [])
            cards = snap.get("cards", [])

            body_raw = snap.get("body")
            body = ""
            if body_raw:
                if isinstance(body_raw, dict):
                    body = body_raw.get("text", str(body_raw))
                elif isinstance(body_raw, list):
                    body = " ".join(b.get("text", str(b)) if isinstance(b, dict) else str(b) for b in body_raw)
                else:
                    body = str(body_raw)

            image_url = ""
            if images and isinstance(images[0], dict):
                image_url = images[0].get("original_image_url", "")

            video_url = ""
            if videos and isinstance(videos[0], dict):
                video_url = videos[0].get("video_hd_url", "") or videos[0].get("video_sd_url", "")
                if not image_url:
                    image_url = videos[0].get("video_preview_image_url", "")

            title = snap.get("title", "")
            if isinstance(title, dict):
                title = title.get("text", "")

            cta = ""
            link = ""
            if cards and isinstance(cards[0], dict):
                cta = cards[0].get("cta_text", "")
                link = cards[0].get("link_url", "")
                if not title:
                    title = cards[0].get("title", "")

            page_name = snap.get("page_name", "")

            # Enriquecer com metricas dos nossos dados
            adv_lower = page_name.lower().strip()
            metrics = {"impressions": 0, "likes": 0, "comments": 0, "engagement": 0,
                       "heat": 0, "spend": 0, "score": 0, "niche": "", "sources_matched": []}
            if adv_lower in existing_by_adv:
                matches = existing_by_adv[adv_lower]
                metrics["impressions"] = max((a.get("impressions", 0) or 0) for a in matches)
                metrics["likes"] = max((a.get("likes", 0) or 0) for a in matches)
                metrics["comments"] = max((a.get("comments", 0) or 0) for a in matches)
                metrics["engagement"] = max((a.get("total_engagement", 0) or 0) for a in matches)
                metrics["heat"] = max((a.get("heat", 0) or 0) for a in matches)
                metrics["spend"] = round(sum((a.get("estimated_spend", 0) or 0) for a in matches), 2)
                metrics["score"] = max((a.get("potential_score", 0) or 0) for a in matches)
                metrics["niche"] = next((a.get("ai_niche", "") for a in matches if a.get("ai_niche")), "")
                metrics["sources_matched"] = list(set(a.get("source", "") for a in matches))

            simplified.append({
                "ad_id": ad.get("ad_archive_id", ""),
                "source": "meta_official",
                "page_name": page_name,
                "page_picture": snap.get("page_profile_picture_url", ""),
                "body": body[:500],
                "title": title[:200],
                "cta": cta,
                "link": link,
                "image_url": image_url,
                "video_url": video_url,
                "is_active": ad.get("is_active", True),
                "start_date": ad.get("start_date", ""),
                "platforms": ad.get("publisher_platform", []),
                "display_format": snap.get("display_format", ""),
                # Metricas enriquecidas
                "impressions": metrics["impressions"],
                "likes": metrics["likes"],
                "comments": metrics["comments"],
                "total_engagement": metrics["engagement"],
                "heat": metrics["heat"],
                "estimated_spend": metrics["spend"],
                "potential_score": metrics["score"],
                "niche": metrics["niche"],
                "matched_sources": metrics["sources_matched"],
                "is_enriched": len(metrics["sources_matched"]) > 0,
            })

        return {
            "data": simplified,
            "total": total,
            "query": q,
            "country": country,
            "enriched_count": sum(1 for s in simplified if s["is_enriched"]),
        }

    except Exception as e:
        return {"error": str(e)}


# ============================================================
# SIMILARWEB TRAFFIC DATA
# ============================================================

_sw_cache = {"data": None, "loaded_at": None}

def load_similarweb():
    files = sorted(glob.glob(f"{OUTPUT_DIR}/similarweb_*.json"), reverse=True)
    # Exclude cache file (flat dict, not {domains: {...}} structure)
    files = [f for f in files if "cache" not in os.path.basename(f)]
    if not files:
        return {}
    latest = files[0]
    mtime = os.path.getmtime(latest)
    if _sw_cache["data"] and _sw_cache["loaded_at"] == mtime:
        return _sw_cache["data"]
    with open(latest, "r", encoding="utf-8") as f:
        data = json.load(f)
    _sw_cache["data"] = data.get("domains", {})
    _sw_cache["loaded_at"] = mtime
    return _sw_cache["data"]


# SimilarWeb on-demand — tenta localhost primeiro (dev), depois URL publica (Render)
_SW_ENDPOINTS = [
    ("http://localhost:4000", None),  # Dev local
    ("https://traffic.ninjabrhub.online", "njspy_traffic_2026_x9k"),  # VPS via SSH tunnel
]


def _fetch_similarweb_live(domain: str):
    """Tenta buscar dados do SimilarWeb em servidores on-demand (local ou VPS)."""
    import requests as req
    for base_url, api_key in _SW_ENDPOINTS:
        try:
            url = f"{base_url}/api/traffic/{domain}"
            if api_key:
                url += f"?key={api_key}"
            r = req.get(url, timeout=40)
            if r.status_code == 200:
                data = r.json()
                if data.get("monthly_visits"):
                    return {"live": True, "endpoint": base_url, **data}
        except Exception:
            continue
    return None


@app.get("/api/traffic/{domain}")
def get_traffic(domain: str):
    """Dados de tráfego de qualquer domínio via SimilarWeb (cache + on-demand)."""
    domains = load_similarweb()
    if domain in domains:
        return {"domain": domain, "cached": True, **domains[domain]}
    if f"www.{domain}" in domains:
        return {"domain": domain, "cached": True, **domains[f"www.{domain}"]}

    # Try on-demand (local dev server OR VPS via SSH tunnel)
    live_data = _fetch_similarweb_live(domain)
    if live_data:
        return {"domain": domain, "cached": False, **live_data}

    return {
        "domain": domain,
        "cached": False,
        "message": "Domínio não analisado ainda. SSH tunnel offline ou SimilarWeb server parado. Tente novamente em alguns minutos.",
    }


@app.get("/api/traffic")
def list_traffic(
    sort: str = Query("monthly_visits", description="monthly_visits, global_rank, bounce_rate"),
    limit: int = Query(20, ge=1, le=100),
):
    """Lista todos os domínios analisados com tráfego"""
    domains = load_similarweb()
    items = list(domains.values())
    reverse = sort != "global_rank"
    items.sort(key=lambda x: x.get(sort, 0) or 0, reverse=reverse)
    return {"data": items[:limit], "total": len(items)}


# ============================================================
# HOOK BANK — Banco de ganchos validados
# ============================================================

def _build_hook_bank():
    """Extrai e classifica hooks (primeira frase) de todos os ads"""
    ads = load_latest_data()
    hooks = []
    seen = set()

    for ad in ads:
        # Ignorar ads do Social1 (body é dados formatados, não copy real)
        if ad.get("source") == "social1":
            continue
        # Ignorar ads sem copy real
        if ad.get("ad_type") in ("product", "organic_video"):
            continue

        body = (ad.get("body") or "").strip()
        title = (ad.get("title") or "").strip()

        # Ignorar bodies que são dados, não copy
        if body.startswith("Units sold:") or body.startswith("{"):
            continue

        # O hook e a primeira frase do body ou o titulo
        text = body or title
        if not text or len(text) < 10:
            continue
        # Ignorar templates de ads dinamicos
        if "{{" in text or "{{product" in text:
            continue

        # Extrair primeira frase
        hook = text
        for sep in ["\n", ". ", "! ", "? ", "...", "👉", "⬇", "🔥"]:
            if sep in text:
                parts = text.split(sep)
                if len(parts[0]) >= 10:
                    hook = parts[0].strip()
                    break

        # Limitar tamanho
        if len(hook) > 200:
            hook = hook[:200]
        if len(hook) < 10:
            continue

        # Deduplicar
        hook_key = hook.lower()[:60]
        if hook_key in seen:
            continue
        seen.add(hook_key)

        impressions = ad.get("impressions", 0) or 0
        likes = ad.get("likes", 0) or 0
        engagement = ad.get("total_engagement", 0) or 0

        # Classificar tipo de hook
        hl = hook.lower()
        if "?" in hook:
            hook_type = "question"
        elif any(w in hl for w in ["%", "milhões", "milhares", "mil", "100", "500", "1000", "studies", "research", "scientists"]):
            hook_type = "statistic"
        elif any(w in hl for w in ["cansado", "tired", "sick of", "frustrated", "struggling", "sofrendo", "dor", "pain", "problem"]):
            hook_type = "pain"
        elif any(w in hl for w in ["segredo", "secret", "ninguém", "nobody", "hidden", "descobr", "discover", "revealed"]):
            hook_type = "curiosity"
        elif any(w in hl for w in ["grátis", "free", "ganhe", "win", "earn", "lucro", "profit", "resultado", "result"]):
            hook_type = "benefit"
        elif any(w in hl for w in ["pare", "stop", "nunca", "never", "não", "don't", "avoid", "warning", "cuidado"]):
            hook_type = "shock"
        elif any(w in hl for w in ["antes", "before", "depois", "after", "era", "agora", "now", "transformação"]):
            hook_type = "contrast"
        elif any(w in hl for w in ["eu ", "i ", "minha", "my ", "quando", "when i", "meu"]):
            hook_type = "story"
        else:
            hook_type = "direct"

        # Detectar idioma simples
        if any(w in hl for w in ["você", "para", "como", "não", "uma", "que", "isso"]):
            language = "pt"
        elif any(w in hl for w in ["the", "you", "how", "what", "this", "your", "are"]):
            language = "en"
        elif any(w in hl for w in ["el", "los", "como", "para", "que", "una"]):
            language = "es"
        else:
            language = "other"

        hooks.append({
            "hook": hook,
            "hook_type": hook_type,
            "language": language,
            "source": ad.get("source", ""),
            "platform": ad.get("platform", ""),
            "advertiser": ad.get("advertiser", ""),
            "niche": ad.get("ai_niche") or ad.get("search_keyword", ""),
            "impressions": impressions,
            "engagement": engagement,
            "likes": likes,
            "days_running": ad.get("days_running", 0) or 0,
            "ad_id": ad.get("ad_id", ""),
            "score": min(10, round(
                min(3, impressions / 100000) +
                min(3, engagement / 1000) +
                min(2, (ad.get("days_running", 0) or 0) / 15) +
                min(2, (ad.get("heat", 0) or 0) / 200)
            , 1)),
        })

    hooks.sort(key=lambda x: x["score"], reverse=True)
    return hooks


@app.get("/api/hooks")
def hook_bank(
    hook_type: str = Query(None, description="question, statistic, pain, curiosity, benefit, shock, contrast, story, direct"),
    language: str = Query(None, description="pt, en, es"),
    niche: str = Query(None, description="Filtrar por nicho"),
    platform: str = Query(None),
    min_score: float = Query(None),
    search: str = Query(None, description="Buscar no texto do hook"),
    sort: str = Query("score", description="score, impressions, engagement"),
    limit: int = Query(50, ge=1, le=200),
    page: int = Query(1, ge=1),
):
    """Banco de hooks validados — os melhores ganchos com milhoes de impressoes"""
    hooks = _build_hook_bank()

    if hook_type:
        hooks = [h for h in hooks if h["hook_type"] == hook_type]
    if language:
        hooks = [h for h in hooks if h["language"] == language]
    if niche:
        hooks = [h for h in hooks if niche.lower() in (h.get("niche") or "").lower()]
    if platform:
        hooks = [h for h in hooks if h["platform"] == platform]
    if min_score:
        hooks = [h for h in hooks if h["score"] >= min_score]
    if search:
        sl = search.lower()
        hooks = [h for h in hooks if sl in h["hook"].lower()]

    try:
        hooks.sort(key=lambda x: x.get(sort, 0) or 0, reverse=True)
    except:
        pass

    # Stats
    types = {}
    langs = {}
    for h in hooks:
        types[h["hook_type"]] = types.get(h["hook_type"], 0) + 1
        langs[h["language"]] = langs.get(h["language"], 0) + 1

    total = len(hooks)
    start = (page - 1) * limit

    return {
        "data": hooks[start:start + limit],
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit if total > 0 else 0,
        "stats": {
            "by_type": dict(sorted(types.items(), key=lambda x: x[1], reverse=True)),
            "by_language": dict(sorted(langs.items(), key=lambda x: x[1], reverse=True)),
        }
    }


@app.get("/api/hooks/top")
def hooks_top(
    language: str = Query(None),
    limit: int = Query(20, ge=1, le=50),
):
    """Top hooks da semana — os ganchos com melhor performance"""
    hooks = _build_hook_bank()
    if language:
        hooks = [h for h in hooks if h["language"] == language]
    return {"data": hooks[:limit], "total": len(hooks)}


# ============================================================
# DAILY BRIEFING — Radar diario de inteligencia
# ============================================================

@app.get("/api/briefing")
def daily_briefing():
    """Briefing diario de inteligencia — o que mudou nas ultimas 24h"""
    ads = load_latest_data()
    affiliates = load_affiliate_products()

    # Ads mais recentes (ultimas 24h)
    from datetime import datetime, timedelta
    now = datetime.now()
    recent_ads = [a for a in ads if a.get("days_running", 999) <= 3]
    recent_ads.sort(key=lambda x: x.get("impressions", 0) or 0, reverse=True)

    # Top novo da semana (poucos dias rodando + alto engajamento)
    rising_stars = [a for a in ads if 1 <= (a.get("days_running", 0) or 0) <= 7
                    and (a.get("impressions", 0) or 0) > 10000]
    rising_stars.sort(key=lambda x: x.get("impressions", 0) or 0, reverse=True)

    # Ads longevos (validados)
    evergreen = [a for a in ads if (a.get("days_running", 0) or 0) > 30]
    evergreen.sort(key=lambda x: x.get("impressions", 0) or 0, reverse=True)

    # Uncloak stats
    uncloak = _build_uncloak_data()
    new_revealed = [u for u in uncloak if u["is_revealed"]]

    # Affiliate gold rush
    gold_rush = [p for p in affiliates if p.get("saturation_zone") == "gold_rush"]

    # Top hooks recentes
    hooks = _build_hook_bank()
    recent_hooks = [h for h in hooks if h.get("days_running", 999) <= 7][:5]

    # Nichos em alta
    niche_count = {}
    for a in recent_ads[:500]:
        n = a.get("ai_niche") or a.get("search_keyword", "")
        if n:
            niche_count[n] = niche_count.get(n, 0) + 1

    briefing = {
        "date": now.strftime("%d/%m/%Y"),
        "greeting": f"Bom dia! Aqui esta seu briefing de inteligencia.",

        "highlights": {
            "new_ads_today": len(recent_ads),
            "rising_stars": len(rising_stars),
            "evergreen_validated": len(evergreen),
            "uncloaked_total": len(new_revealed),
            "gold_rush_products": len(gold_rush),
        },

        "rising_stars": [{
            "advertiser": a.get("advertiser", ""),
            "title": (a.get("title") or "")[:80],
            "platform": a.get("platform", ""),
            "impressions": a.get("impressions", 0),
            "days_running": a.get("days_running", 0),
            "ad_id": a.get("ad_id", ""),
        } for a in rising_stars[:5]],

        "top_new_hooks": [{
            "hook": h["hook"][:100],
            "type": h["hook_type"],
            "impressions": h["impressions"],
            "score": h["score"],
        } for h in recent_hooks],

        "gold_rush_alert": [{
            "name": p.get("name", ""),
            "ninja_score": p.get("ninja_score", 0),
            "trend_7d": p.get("trend_7d", 0),
            "competition": p.get("competition_level", ""),
        } for p in sorted(gold_rush, key=lambda x: x.get("opportunity_score", 0), reverse=True)[:5]],

        "uncloak_alert": [{
            "advertiser": u["advertiser"],
            "cloaking_score": u["cloaking_score"],
            "video_ads": u["video_ads"],
            "estimated_spend": u["estimated_spend"],
        } for u in new_revealed[:5]],

        "hot_niches": dict(sorted(niche_count.items(), key=lambda x: x[1], reverse=True)[:10]),

        "velocity_rockets": [{
            "advertiser": a.get("advertiser", ""),
            "title": (a.get("title") or "")[:80],
            "platform": a.get("platform", ""),
            "impressions": a.get("impressions", 0),
            "days_running": a.get("days_running", 0),
            "velocity_per_day": round((a.get("impressions", 0) or 0) / max(int(a.get("days_running", 1) or 1), 1)),
        } for a in sorted(
            [a for a in ads if 1 <= (a.get("days_running", 0) or 0) <= 5 and (a.get("impressions", 0) or 0) > 30000],
            key=lambda x: (x.get("impressions", 0) or 0) / max(int(x.get("days_running", 1) or 1), 1),
            reverse=True
        )[:5]],

        "total_monitored": {
            "ads": len(ads),
            "affiliates": len(affiliates),
            "sources": 12,
            "transcripts": len(load_transcripts()),
        }
    }

    return briefing


# ============================================================
# OFFER TRACKER — Rastrear ofertas cross-platform
# ============================================================

@app.get("/api/offer-tracker")
def offer_tracker(
    search: str = Query(None, description="Nome do produto ou oferta"),
    min_advertisers: int = Query(2, description="Minimo de anunciantes diferentes"),
    limit: int = Query(30, ge=1, le=100),
):
    """Rastreia uma oferta em todas as plataformas e fontes"""
    ads = load_latest_data()
    affiliates = load_affiliate_products()

    # Agrupar ads por keywords/termos no titulo e body
    offers = {}
    for ad in ads:
        # Usar search_keyword ou ai_niche como chave
        kw = (ad.get("search_keyword") or "").lower().strip()
        if not kw or len(kw) < 3:
            continue

        if kw not in offers:
            offers[kw] = {
                "keyword": kw,
                "ads": [],
                "advertisers": set(),
                "platforms": set(),
                "sources": set(),
                "total_impressions": 0,
                "total_spend": 0,
                "has_video": False,
                "has_image": False,
            }

        o = offers[kw]
        o["ads"].append(ad)
        if ad.get("advertiser"):
            o["advertisers"].add(ad["advertiser"])
        if ad.get("platform"):
            o["platforms"].add(ad["platform"])
        if ad.get("source"):
            o["sources"].add(ad["source"])
        o["total_impressions"] += ad.get("impressions", 0) or 0
        o["total_spend"] += ad.get("estimated_spend", 0) or 0
        if ad.get("video_url"):
            o["has_video"] = True
        if ad.get("image_url") and not ad.get("video_url"):
            o["has_image"] = True

    # Filtrar e formatar
    results = []
    for kw, o in offers.items():
        num_advertisers = len(o["advertisers"])
        if num_advertisers < min_advertisers:
            continue

        if search and search.lower() not in kw:
            continue

        # Encontrar produto afiliado correspondente
        matching_affiliate = None
        for p in affiliates:
            if kw in (p.get("name", "") or "").lower() or kw in (p.get("niche", "") or "").lower():
                matching_affiliate = {
                    "name": p.get("name", ""),
                    "ninja_score": p.get("ninja_score", 0),
                    "sales_volume": p.get("sales_volume", 0),
                    "trend_7d": p.get("trend_7d", 0),
                    "saturation_zone": p.get("saturation_zone", ""),
                }
                break

        # Top ads por impressoes
        top_ads = sorted(o["ads"], key=lambda x: x.get("impressions", 0) or 0, reverse=True)

        # Angulos unicos (hooks dos top ads)
        angles = []
        seen_angles = set()
        for a in top_ads[:20]:
            hook = (a.get("title") or a.get("body", ""))[:100]
            if hook and hook.lower()[:40] not in seen_angles:
                seen_angles.add(hook.lower()[:40])
                angles.append({
                    "hook": hook,
                    "advertiser": a.get("advertiser", ""),
                    "platform": a.get("platform", ""),
                    "impressions": a.get("impressions", 0),
                })

        # Saturation score
        days_list = [a.get("days_running", 0) or 0 for a in o["ads"]]
        avg_days = sum(days_list) / len(days_list) if days_list else 0
        saturation = min(100, int(
            (num_advertisers / 30 * 30) +
            (len(o["ads"]) / 100 * 30) +
            (avg_days / 30 * 20) +
            (20 if avg_days > 14 else 0)
        ))

        results.append({
            "offer": kw.title(),
            "total_ads": len(o["ads"]),
            "unique_advertisers": num_advertisers,
            "platforms": sorted(o["platforms"]),
            "sources": sorted(o["sources"]),
            "total_impressions": o["total_impressions"],
            "total_spend": round(o["total_spend"], 2),
            "has_video_and_image": o["has_video"] and o["has_image"],
            "saturation_score": saturation,
            "avg_days_running": round(avg_days, 1),
            "top_angles": angles[:5],
            "top_ads": [{
                "ad_id": a.get("ad_id", ""),
                "advertiser": a.get("advertiser", ""),
                "title": (a.get("title") or "")[:80],
                "platform": a.get("platform", ""),
                "impressions": a.get("impressions", 0),
                "image_url": a.get("image_url", ""),
                "video_url": a.get("video_url", ""),
            } for a in top_ads[:6]],
            "affiliate_match": matching_affiliate,
        })

    results.sort(key=lambda x: x["unique_advertisers"], reverse=True)

    return {
        "data": results[:limit],
        "total": len(results),
    }


@app.get("/api/offer-tracker/search")
def offer_search(q: str = Query(..., description="Buscar oferta especifica")):
    """Busca uma oferta especifica em todas as plataformas"""
    ads = load_latest_data()
    q_lower = q.lower()

    matched = [a for a in ads if
               q_lower in (a.get("title") or "").lower() or
               q_lower in (a.get("body") or "").lower() or
               q_lower in (a.get("advertiser") or "").lower() or
               q_lower in (a.get("search_keyword") or "").lower()]

    advertisers = list(set(a.get("advertiser", "") for a in matched if a.get("advertiser")))
    platforms = list(set(a.get("platform", "") for a in matched if a.get("platform")))
    sources = list(set(a.get("source", "") for a in matched if a.get("source")))

    # Angulos
    angles = {}
    for a in matched:
        hook = (a.get("title") or a.get("body", ""))[:80]
        if hook:
            angles[hook] = angles.get(hook, 0) + 1

    top_angles = sorted(angles.items(), key=lambda x: x[1], reverse=True)[:10]

    matched.sort(key=lambda x: x.get("impressions", 0) or 0, reverse=True)

    return {
        "query": q,
        "total_ads": len(matched),
        "unique_advertisers": len(advertisers),
        "platforms": platforms,
        "sources": sources,
        "top_angles": [{"hook": h, "count": c} for h, c in top_angles],
        "top_ads": matched[:20],
    }


# ============================================================
# PREDICTIVE AI — Previsao de mercado cruzando 15 fontes
# ============================================================

OPENROUTER_KEY = "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"

def _ai_predict(prompt, max_tokens=2000):
    """Chama OpenRouter (modelo premium) para previsao preditiva, fallback DeepSeek"""
    import requests as req

    # Try OpenRouter first (better model)
    for api_url, api_key, model in [
        ("https://openrouter.ai/api/v1/chat/completions", OPENROUTER_KEY, "qwen/qwen-plus"),
        ("https://api.deepseek.com/chat/completions", "sk-75b1ddd6be014170a52a790133025c07", "deepseek-chat"),
    ]:
        try:
            r = req.post(api_url, json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "Voce e um analista senior de inteligencia de mercado especializado em marketing digital, direct response, e-commerce e afiliados. Voce analisa dados REAIS de 15 fontes de spy ads para fazer previsoes precisas e acionaveis. SEMPRE retorne APENAS JSON valido sem markdown, sem explicacao, apenas o JSON puro."},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": max_tokens,
                "temperature": 0.2,
            }, headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }, timeout=45)
            data = r.json()
            if "choices" not in data:
                continue
            text = data["choices"][0]["message"]["content"].strip()
            text = text.replace("```json", "").replace("```", "").strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
        except:
            continue

    return {"error": "All AI providers failed"}


@app.get("/api/predict/dashboard")
def predict_dashboard():
    """Dashboard preditivo — analisa os TOP nichos automaticamente e preve o futuro de cada um"""
    ads = load_latest_data()
    affiliates = load_affiliate_products()

    # Sub-nichos ESPECIFICOS para rastrear (PT + EN)
    SPECIFIC_NICHES = {
        # Health / Supplements
        "Suplemento Emagrecimento": ["weight loss", "belly fat", "keto", "slim", "fat burn", "emagrecer", "emagrecimento", "dieta", "perder peso", "barriga"],
        "Suplemento para Próstata": ["prostate", "prostadine", "prostavive", "prostata", "prostate health"],
        "Suplemento Glicemia / Diabetes": ["blood sugar", "glucose", "diabetes", "sugar defender", "glucotrust", "glicemia", "insulina"],
        "Suplemento para Cérebro / Memória": ["brain", "memory", "cognitive", "neuro", "brain wave", "memoria", "cerebro", "foco"],
        "Testosterona / Saúde Masculina": ["testosterone", "male enhancement", "libido", "nitric boost", "stamina", "testosterona", "virilidade"],
        "Suplemento Articulações / Dor": ["joint pain", "joint genesis", "arthritis", "mobility", "articulacao", "dor nas costas", "coluna"],
        "Saúde Intestinal / Probiótico": ["gut health", "probiotic", "digestive", "bloating", "biome", "intestino", "probiotico", "digestao"],
        "Anti-Aging / Rejuvenescimento": ["anti aging", "wrinkle", "collagen", "youthful", "aging", "rugas", "colageno", "rejuvenesc"],
        "Crescimento Capilar": ["hair growth", "hair loss", "thinning hair", "cabelo", "queda de cabelo", "calvicie", "capilar"],
        "Clareamento Dental": ["teeth whiten", "dental", "dent", "oral", "clareamento", "sorriso", "branqueamento"],
        "Suplemento para Sono": ["sleep", "insomnia", "melatonin", "sleep aid", "insonia", "dormir", "sono"],
        "Suplemento para Visão": ["vision", "eye health", "sight", "eyesight", "visao", "olhos"],
        # Fitness / Nutrition
        "Whey Protein / Creatina": ["whey", "protein", "creatina", "creatine", "suplemento", "academia", "hipertrofia"],
        "Fitness / Treino": ["workout", "fitness", "exercise", "treino", "musculacao", "gym"],
        # Wealth / DR
        "Ganhar Dinheiro Online": ["make money", "passive income", "earn online", "ganhar dinheiro", "renda extra", "trabalhar em casa"],
        "Marketing de Afiliados": ["affiliate", "commission", "clickbank", "afiliado", "comissao"],
        "Cripto / Trading": ["crypto", "bitcoin", "trading", "forex", "cripto", "criptomoeda"],
        # Other
        "Manifestação / Espiritualidade": ["manifestation", "abundance", "law of attraction", "manifestacao", "abundancia", "frequencia"],
        "Adestramento de Cães": ["dog training", "puppy", "cachorro", "adestramento", "adestrar", "pet shop", "gato", "pet"],
        "Skincare / Cuidados com Pele": ["skincare", "skin care", "acne", "pele", "hidratante", "protetor solar"],
    }

    # Identificar os sub-nichos ESPECIFICOS
    niche_data = {}
    for ad in ads:
        text = ((ad.get("title", "") or "") + " " + (ad.get("body", "") or "") + " " + (ad.get("search_keyword", "") or "")).lower()

        for niche_name, keywords in SPECIFIC_NICHES.items():
            if any(kw in text for kw in keywords):
                if niche_name not in niche_data:
                    niche_data[niche_name] = {"total": 0, "impressions": 0, "rockets": 0, "sources": set(), "days_list": []}
                nd = niche_data[niche_name]
                nd["total"] += 1
                nd["impressions"] += int(ad.get("impressions", 0) or 0)
                nd["sources"].add(ad.get("source", ""))
                days = int(ad.get("days_running", 0) or 0)
                if days > 0:
                    nd["days_list"].append(days)
                if 1 <= days <= 7 and (ad.get("impressions", 0) or 0) > 10000:
                    nd["rockets"] += 1
                break  # Um ad so conta para o primeiro nicho que match

    # Calcular scores e selecionar top 15 mais interessantes
    scored = []
    for kw, nd in niche_data.items():
        if nd["total"] < 5:
            continue
        avg_days = sum(nd["days_list"]) / len(nd["days_list"]) if nd["days_list"] else 0
        freshness = sum(1 for d in nd["days_list"] if d <= 7) / max(nd["total"], 1)
        score = (
            nd["rockets"] * 10 +
            len(nd["sources"]) * 5 +
            min(20, nd["total"] / 10) +
            freshness * 30
        )
        scored.append({
            "niche": kw,
            "total_ads": nd["total"],
            "total_impressions": nd["impressions"],
            "rockets": nd["rockets"],
            "sources": len(nd["sources"]),
            "freshness_pct": round(freshness * 100),
            "avg_days": round(avg_days, 1),
            "interest_score": round(score),
        })

    scored.sort(key=lambda x: x["interest_score"], reverse=True)
    top_niches = scored[:15]

    # Para cada top nicho, gerar previsao com IA
    # Coletar Google Trends para os top 5
    import requests as req
    for niche in top_niches[:5]:
        try:
            r = req.get("https://www.searchapi.io/api/v1/search", params={
                "engine": "google_trends", "q": niche["niche"], "data_type": "TIMESERIES",
                "time": "today 3-m", "api_key": SEARCHAPI_KEY,
            }, timeout=10)
            trends = r.json().get("interest_over_time", {}).get("timeline_data", [])
            if trends:
                values = [t["values"][0]["extracted_value"] for t in trends if t.get("values")]
                if len(values) >= 4:
                    recent = sum(values[-4:]) / 4
                    older = sum(values[:4]) / 4
                    niche["google_trend"] = "rising" if recent > older * 1.1 else "declining" if recent < older * 0.9 else "stable"
                    niche["trend_change"] = round((recent - older) / max(older, 1) * 100)
                    niche["trend_current"] = values[-1]
                else:
                    niche["google_trend"] = "unknown"
        except:
            niche["google_trend"] = "unknown"

    # Affiliate products match
    for niche in top_niches:
        kw = niche["niche"]
        matched = [p for p in affiliates if kw in (p.get("name", "") or "").lower() or kw in (p.get("niche", "") or "").lower()]
        niche["affiliate_count"] = len(matched)
        if matched:
            best = max(matched, key=lambda p: p.get("ninja_score", 0) or 0)
            niche["top_product"] = best.get("name", "")
            niche["top_product_score"] = best.get("ninja_score", 0)
            niche["saturation_zone"] = best.get("saturation_zone", "")

    # Generate AI predictions for top 5
    context_parts = []
    for n in top_niches[:5]:
        context_parts.append(f"- {n['niche'].upper()}: {n['total_ads']} ads, {n['rockets']} escalando, {n['sources']} fontes, {n['freshness_pct']}% recentes, Google Trends: {n.get('google_trend','?')} ({n.get('trend_change','?')}%), Afiliados: {n.get('affiliate_count',0)}, Zona: {n.get('saturation_zone','?')}")

    ai_context = "\n".join(context_parts)

    predictions = _ai_predict(f"""DADOS REAIS DE 15 FONTES DE SPY ADS — TOP 5 SUB-NICHOS MAIS ATIVOS:

{ai_context}

IMPORTANTE: Analise cada sub-nicho de forma ESPECIFICA. Nao fale de forma generica. Seja especifico sobre qual PRODUTO, qual TIPO de oferta, qual ANGULO funciona. O cliente quer saber exatamente o que fazer.

Para CADA um dos 5 sub-nichos, faca uma previsao detalhada e especifica. Retorne JSON:
{{
  "predictions": [
    {{
      "niche": "nome especifico do sub-nicho",
      "status": "EXPLODING/HOT/WARMING/STABLE/COOLING/SATURATED",
      "emoji": "emoji representativo",
      "action": "ENTER_NOW/WAIT/MONITOR/AVOID/EXIT",
      "confidence": 85,
      "headline": "frase impactante que faz o cliente agir",
      "prediction": "previsao especifica: qual tipo de produto vai vender, qual plataforma usar, quanto investir",
      "opportunity": "oportunidade ESPECIFICA: qual produto ou angulo explorar",
      "risk": "risco CONCRETO: por que pode dar errado",
      "tip": "dica PRATICA e acionavel: primeiro passo exato para lucrar",
      "estimated_roi": "estimativa de ROI se entrar agora (ex: 2-3x em 30 dias)",
      "best_product_type": "tipo de produto que mais vende (fisico/digital/suplemento/curso)",
      "best_platform_to_advertise": "melhor plataforma para anunciar (Facebook/TikTok/YouTube/Google)"
    }}
  ],
  "market_summary": "resumo ESPECIFICO do mercado: quais sub-nichos estao bombando e quais estao morrendo",
  "hottest_niche": "sub-nicho com MAIS potencial de lucro imediato",
  "avoid_niche": "sub-nicho que vai dar PREJUIZO se entrar agora",
  "golden_opportunity": "a oportunidade ESCONDIDA que 99% das pessoas nao perceberam nos dados"
}}""", max_tokens=2000)

    return {
        "generated_at": datetime.now().isoformat(),
        "total_ads_analyzed": len(ads),
        "total_niches_found": len(scored),
        "top_niches": top_niches,
        "ai_predictions": predictions,
    }


@app.get("/api/predict")
def predict_market(
    q: str = Query(..., description="Produto, nicho ou keyword para prever"),
):
    """IA Preditiva — Cruza 15 fontes e preve o futuro do mercado"""
    ads = load_latest_data()
    affiliates = load_affiliate_products()

    q_lower = q.lower()

    # Coletar dados de todas as fontes
    # 1. Ads relacionados
    matched_ads = [a for a in ads if q_lower in (
        (a.get("title", "") or "") + " " + (a.get("body", "") or "") + " " +
        (a.get("search_keyword", "") or "") + " " + (a.get("ai_niche", "") or "")
    ).lower()]
    total_ads = len(matched_ads)

    # Metricas dos ads
    avg_impressions = round(sum(a.get("impressions", 0) or 0 for a in matched_ads) / max(total_ads, 1))
    avg_days = round(sum(a.get("days_running", 0) or 0 for a in matched_ads) / max(total_ads, 1))
    sources = list(set(a.get("source", "") for a in matched_ads))
    platforms = list(set(a.get("platform", "") for a in matched_ads))

    # Velocity (ads recentes escalando)
    rockets = [a for a in matched_ads if 1 <= (a.get("days_running", 0) or 0) <= 7 and (a.get("impressions", 0) or 0) > 10000]

    # 2. Produtos afiliados
    matched_products = [p for p in affiliates if q_lower in (p.get("name", "") or "").lower() or q_lower in (p.get("niche", "") or "").lower()]

    # 3. Hooks
    hooks = _build_hook_bank()
    matched_hooks = [h for h in hooks if q_lower in h.get("hook", "").lower()]

    # 4. Google Trends (on-demand)
    trends_data = None
    try:
        import requests as req
        r = req.get("https://www.searchapi.io/api/v1/search", params={
            "engine": "google_trends", "q": q, "data_type": "TIMESERIES",
            "time": "today 3-m", "api_key": SEARCHAPI_KEY,
        }, timeout=10)
        trends = r.json().get("interest_over_time", {}).get("timeline_data", [])
        if trends:
            values = [t["values"][0]["extracted_value"] for t in trends if t.get("values")]
            trends_data = {
                "current": values[-1] if values else 0,
                "peak": max(values) if values else 0,
                "avg": round(sum(values) / len(values)) if values else 0,
                "trend": "rising" if len(values) >= 4 and sum(values[-4:]) / 4 > sum(values[:4]) / 4 * 1.1 else "declining" if len(values) >= 4 and sum(values[-4:]) / 4 < sum(values[:4]) / 4 * 0.9 else "stable",
            }
    except:
        pass

    # 5. Uncloak
    uncloak = _build_uncloak_data()
    matched_uncloak = [u for u in uncloak if q_lower in u.get("advertiser", "").lower()]

    # Build context for AI
    context = f"""DADOS DE MERCADO PARA "{q}" (coletados de 15 fontes em tempo real):

ADS ENCONTRADOS: {total_ads} anuncios
- Plataformas: {', '.join(platforms)}
- Fontes: {', '.join(sources)}
- Media de impressoes: {avg_impressions:,}
- Media de dias rodando: {avg_days}
- Ads escalando agora (< 7 dias, > 10K imp): {len(rockets)}

PRODUTOS DE AFILIACAO: {len(matched_products)} encontrados
{chr(10).join(f'- {p.get("name","")}: score {p.get("ninja_score",0)}, tendencia 7d: {p.get("trend_7d",0)}, zona: {p.get("saturation_zone","")}' for p in matched_products[:5])}

GOOGLE TRENDS: {json.dumps(trends_data) if trends_data else 'Nao disponivel'}

HOOKS VALIDADOS: {len(matched_hooks)} hooks usando essa keyword

CLOAKING: {len(matched_uncloak)} anunciantes com sinais de cloaking

VELOCIDADE: {len(rockets)} ads novos escalando nos ultimos 7 dias"""

    # Call AI for prediction
    prediction = _ai_predict(f"""{context}

Baseado nesses dados REAIS de 15 fontes, faca uma previsao completa para o mercado de "{q}".

Retorne JSON:
{{
  "market_status": "HOT/WARMING/STABLE/COOLING/SATURATED",
  "confidence": 85,
  "prediction_30d": "O que vai acontecer nos proximos 30 dias",
  "prediction_90d": "O que vai acontecer nos proximos 90 dias",
  "opportunity_window": "Janela de oportunidade: X dias/semanas",
  "recommended_action": "ENTER_NOW/WAIT/AVOID/EXIT",
  "risk_level": "LOW/MEDIUM/HIGH",
  "risks": ["risco 1", "risco 2"],
  "opportunities": ["oportunidade 1", "oportunidade 2"],
  "best_angle": "Melhor angulo de copy baseado nos dados",
  "best_platform": "Melhor plataforma para comecar",
  "estimated_competition": "BAIXA/MEDIA/ALTA",
  "similar_markets": ["mercado similar 1 para diversificar", "mercado 2"],
  "key_insight": "O insight mais importante que poucos percebem",
  "summary": "Resumo executivo em 3 frases"
}}""")

    return {
        "query": q,
        "data_collected": {
            "total_ads": total_ads,
            "sources": sources,
            "platforms": platforms,
            "avg_impressions": avg_impressions,
            "velocity_rockets": len(rockets),
            "affiliate_products": len(matched_products),
            "hooks": len(matched_hooks),
            "trends": trends_data,
            "uncloak_signals": len(matched_uncloak),
        },
        "prediction": prediction,
    }


# ============================================================
# VELOCITY ALERT — Ads escalando AGORA
# ============================================================

@app.get("/api/velocity")
def velocity_alerts(
    min_impressions: int = Query(10000, description="Impressoes minimas"),
    max_days: int = Query(7, description="Maximo de dias rodando"),
    platform: str = Query(None),
    niche: str = Query(None),
    country: str = Query(None),
    limit: int = Query(30, ge=1, le=100),
):
    """Ads que estão ESCALANDO agora — poucos dias rodando + muitas impressões"""
    ads = load_latest_data()

    # Filtrar: ads recentes com alto volume
    rockets = []
    for ad in ads:
        days = int(ad.get("days_running", 0) or 0)
        impressions = int(ad.get("impressions", 0) or 0)
        if days > max_days or days < 1 or impressions < min_impressions:
            continue
        if platform and ad.get("platform") != platform:
            continue
        if niche and niche.lower() not in (ad.get("ai_niche", "") or "").lower():
            continue
        if country and _detect_country(ad) != country.upper():
            continue

        # Velocity score: impressões por dia
        velocity = round(impressions / max(days, 1))

        # Explosion score (0-100): quanto mais impressões em menos dias, mais explosivo
        explosion = min(100, int(
            min(40, impressions / 50000 * 10) +
            min(30, (8 - days) * 5) +
            min(15, (ad.get("likes", 0) or 0) / 500) +
            min(15, (ad.get("total_engagement", 0) or 0) / 1000)
        ))

        rockets.append({
            **{k: ad.get(k) for k in [
                "ad_id", "source", "platform", "advertiser", "advertiser_image",
                "title", "body", "cta", "landing_page", "image_url", "video_url",
                "ad_type", "first_seen", "last_seen", "days_running",
                "likes", "comments", "shares", "impressions", "total_engagement",
                "heat", "potential_score", "ai_niche", "search_keyword", "country",
            ]},
            "velocity_per_day": velocity,
            "explosion_score": explosion,
        })

    rockets.sort(key=lambda x: x["explosion_score"], reverse=True)

    # Stats
    platforms = {}
    niches = {}
    for r in rockets:
        p = r.get("platform", "?")
        platforms[p] = platforms.get(p, 0) + 1
        n = r.get("ai_niche", "?")
        if n:
            niches[n] = niches.get(n, 0) + 1

    return {
        "data": rockets[:limit],
        "total": len(rockets),
        "stats": {
            "by_platform": dict(sorted(platforms.items(), key=lambda x: x[1], reverse=True)[:10]),
            "by_niche": dict(sorted(niches.items(), key=lambda x: x[1], reverse=True)[:10]),
            "avg_velocity": round(sum(r["velocity_per_day"] for r in rockets) / max(len(rockets), 1)) if rockets else 0,
        }
    }


# ============================================================
# ANGLE DETECTOR — Descobre quais ângulos funcionam por nicho
# ============================================================

@app.get("/api/angles")
def angle_detector(
    niche: str = Query(None, description="Nicho para analisar (skincare, weight loss, etc)"),
    keyword: str = Query(None, description="Keyword para analisar"),
    platform: str = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """Detecta quais ÂNGULOS de copy estão funcionando num nicho"""
    ads = load_latest_data()

    # Filtrar por nicho/keyword
    filtered = []
    for ad in ads:
        body = (ad.get("body") or "").strip()
        title = (ad.get("title") or "").strip()
        if not body and not title:
            continue
        if ad.get("source") == "social1":
            continue

        match = False
        if niche and niche.lower() in (ad.get("ai_niche", "") or ad.get("search_keyword", "") or "").lower():
            match = True
        if keyword and keyword.lower() in (body + " " + title + " " + (ad.get("search_keyword", "") or "")).lower():
            match = True
        if not niche and not keyword:
            match = True

        if match:
            if platform and ad.get("platform") != platform:
                continue
            filtered.append(ad)

    if not filtered:
        return {"error": "Nenhum ad encontrado para esse nicho/keyword", "total": 0}

    # Classificar ângulos
    angles = {
        "testimonial": {"keywords": ["before and after", "antes e depois", "testimonial", "depoimento", "review", "i lost", "eu perdi", "changed my life", "mudou minha vida", "my results", "meus resultados"], "count": 0, "top_ads": [], "label": "Depoimento / Antes e Depois"},
        "authority": {"keywords": ["doctor", "dr.", "médico", "scientist", "study", "estudo", "research", "pesquisa", "harvard", "clinical", "clínico", "expert", "especialista", "approved", "aprovado"], "count": 0, "top_ads": [], "label": "Autoridade / Médico / Estudo"},
        "secret_ingredient": {"keywords": ["secret", "segredo", "ingredient", "ingrediente", "discovered", "descobriu", "ancient", "antigo", "breakthrough", "revolutionary", "hidden", "escondido", "nobody knows", "ninguem sabe"], "count": 0, "top_ads": [], "label": "Ingrediente Secreto / Descoberta"},
        "urgency_scarcity": {"keywords": ["limited", "limitado", "last chance", "última chance", "only today", "só hoje", "selling out", "esgotando", "hurry", "corra", "exclusive", "exclusivo", "ends today", "termina hoje", "few left", "poucos restam"], "count": 0, "top_ads": [], "label": "Urgência / Escassez"},
        "pain_problem": {"keywords": ["tired of", "cansado de", "struggling", "sofrendo", "frustrated", "frustrado", "sick of", "pain", "dor", "problem", "problema", "can't sleep", "insomnia", "embarrassing", "vergonha", "worried", "preocupado"], "count": 0, "top_ads": [], "label": "Dor / Problema"},
        "benefit_result": {"keywords": ["lose weight", "emagrecer", "boost energy", "energia", "look younger", "mais jovem", "save money", "economizar", "earn", "ganhar", "transform", "transformar", "finally", "finalmente", "guaranteed", "garantido"], "count": 0, "top_ads": [], "label": "Benefício / Resultado"},
        "curiosity": {"keywords": ["you won't believe", "você não vai acreditar", "shocking", "chocante", "this is why", "é por isso", "the truth about", "a verdade sobre", "what they don't tell", "o que não te contam", "weird trick", "truque estranho", "banned", "proibido"], "count": 0, "top_ads": [], "label": "Curiosidade / Choque"},
        "social_proof": {"keywords": ["million", "milhão", "thousands", "milhares", "best seller", "mais vendido", "rated", "avaliado", "trending", "viral", "everyone", "todo mundo", "celebrities", "famosos", "recommended", "recomendado"], "count": 0, "top_ads": [], "label": "Prova Social / Números"},
        "story": {"keywords": ["i was", "eu era", "my story", "minha história", "one day", "um dia", "happened to me", "aconteceu comigo", "when i", "quando eu", "i remember", "eu lembro", "years ago", "anos atrás", "journey", "jornada"], "count": 0, "top_ads": [], "label": "Storytelling / História Pessoal"},
        "educational": {"keywords": ["how to", "como", "tutorial", "step by step", "passo a passo", "guide", "guia", "learn", "aprenda", "tips", "dicas", "strategy", "estratégia", "method", "método", "course", "curso"], "count": 0, "top_ads": [], "label": "Educacional / How-to"},
    }

    unclassified = 0
    for ad in filtered:
        text = ((ad.get("body") or "") + " " + (ad.get("title") or "")).lower()
        classified = False

        for angle_key, angle in angles.items():
            for kw in angle["keywords"]:
                if kw in text:
                    angle["count"] += 1
                    if len(angle["top_ads"]) < 3:
                        angle["top_ads"].append({
                            "ad_id": ad.get("ad_id", ""),
                            "title": (ad.get("title") or "")[:80],
                            "advertiser": ad.get("advertiser", ""),
                            "impressions": ad.get("impressions", 0),
                            "platform": ad.get("platform", ""),
                        })
                    classified = True
                    break

        if not classified:
            unclassified += 1

    # Build results
    total_classified = sum(a["count"] for a in angles.values())
    results = []
    for key, angle in angles.items():
        if angle["count"] == 0:
            continue
        pct = round(angle["count"] / max(total_classified, 1) * 100, 1)
        results.append({
            "angle": key,
            "label": angle["label"],
            "count": angle["count"],
            "percentage": pct,
            "top_ads": angle["top_ads"],
        })

    results.sort(key=lambda x: x["count"], reverse=True)

    return {
        "niche": niche or keyword or "all",
        "total_ads_analyzed": len(filtered),
        "total_classified": total_classified,
        "unclassified": unclassified,
        "angles": results,
    }


# ============================================================
# UNCLOAK ENGINE — Revelar criativos escondidos
# ============================================================

_AFFILIATE_NETWORKS = {
    "clickbank": ["clickbank.net", "hop.clickbank.net", "*.hop.clickbank.net", "clkbank"],
    "buygoods": ["buygoods.com", "securechkout.com", "bg-checkout"],
    "maxweb": ["maxweboffers.com", "mw-redirect", "maxweb"],
    "digistore24": ["digistore24.com", "digistore"],
    "hotmart": ["hotmart.com", "go.hotmart.com", "pay.hotmart.com"],
    "kiwify": ["kiwify.com.br", "pay.kiwify.com.br"],
    "monetizze": ["monetizze.com.br", "app.monetizze.com.br"],
    "warrior_plus": ["warriorplus.com", "jvzoo.com"],
}


def _detect_affiliate_network(landing_page: str) -> dict:
    """Detecta rede de afiliados pela URL"""
    lp = (landing_page or "").lower()
    if not lp:
        return {}
    for network, patterns in _AFFILIATE_NETWORKS.items():
        for pat in patterns:
            if pat in lp:
                return {"network": network, "match": pat}
    return {}


def _build_uncloak_data():
    """Cruza todas as fontes para encontrar criativos escondidos atras de catalogos"""
    ads = load_latest_data()
    sw_data = load_similarweb()

    # Agrupar por advertiser
    by_advertiser = {}
    for ad in ads:
        name = (ad.get("advertiser") or "").lower().strip()
        if not name or len(name) < 3:
            continue
        if name not in by_advertiser:
            by_advertiser[name] = []
        by_advertiser[name].append(ad)

    # Domínios genéricos que devem ser ignorados no cross-reference
    NOISE_DOMAINS = {
        "adstransparency.google.com", "linkedin.com", "facebook.com",
        "instagram.com", "tiktok.com", "youtube.com", "twitter.com",
        "google.com", "meta.com", "pinterest.com", "reddit.com",
        "t.co", "bit.ly", "linktr.ee", "linktree.com",
        "play.google.com", "apps.apple.com", "itunes.apple.com",
        "apple.com", "amazon.com", "amazon.com.br",
        "wa.me", "api.whatsapp.com", "whatsapp.com",
        "fb.me", "m.me", "messenger.com",
        "shopify.com", "myshopify.com",
        "ad.doubleclick.net", "doubleclick.net",
    }

    # Agrupar por dominio da landing page
    by_domain = {}
    for ad in ads:
        lp = ad.get("landing_page", "") or ""
        if not lp or len(lp) < 10:
            continue
        try:
            domain = urlparse(lp).hostname
            if domain:
                domain = domain.replace("www.", "")
                if domain in NOISE_DOMAINS:
                    continue
                if domain not in by_domain:
                    by_domain[domain] = []
                by_domain[domain].append(ad)
        except:
            pass

    # --- DOMAIN CROSS-REFERENCE: mesmo domínio, anunciantes diferentes ---
    def _are_similar_names(a, b):
        """Filtra variações do mesmo anunciante (ex: 'tefuny shop' vs 'tefuny store')"""
        # Remove common suffixes
        for suf in [" shop", " store", " us", " uk", " br", " official", ".com", " 2", " co.", " co"]:
            a = a.replace(suf, "")
            b = b.replace(suf, "")
        a, b = a.strip(), b.strip()
        if not a or not b:
            return True
        # Same base name or one contains the other
        return a == b or a in b or b in a

    domain_xref = {}
    for domain, domain_ads in by_domain.items():
        advertisers = set()
        for a in domain_ads:
            adv = (a.get("advertiser") or "").lower().strip()
            if adv and len(adv) >= 3:
                advertisers.add(adv)
        # Filter out name variations — keep only truly different advertisers
        if len(advertisers) >= 2:
            unique_advs = list(advertisers)
            truly_different = []
            for adv in unique_advs:
                is_dup = any(_are_similar_names(adv, existing) for existing in truly_different)
                if not is_dup:
                    truly_different.append(adv)
            if len(truly_different) >= 2:
                domain_xref[domain] = {
                    "advertisers": truly_different,
                    "ad_count": len(domain_ads),
                    "ads": domain_ads,
                }

    results = []

    for name, ad_list in by_advertiser.items():
        if len(ad_list) < 2:
            continue

        sources = list(set(a.get("source", "") for a in ad_list))
        platforms = list(set(a.get("platform", "") for a in ad_list))

        image_ads = [a for a in ad_list if a.get("image_url") and not a.get("video_url")]
        video_ads = [a for a in ad_list if a.get("video_url")]

        # Cloaking signals
        signals = []
        cloaking_score = 0

        # Signal 1: tem imagem-only com alto impressions (possivel catalogo)
        high_imp_images = [a for a in image_ads if (a.get("impressions", 0) or 0) > 50000]
        if high_imp_images:
            signals.append({
                "signal": "Anúncios de imagem com alto alcance",
                "detail": f"{len(high_imp_images)} ads com 50K+ impressões sem vídeo — possível catálogo escondendo criativo",
                "severity": "high" if len(high_imp_images) > 3 else "medium"
            })
            cloaking_score += min(30, len(high_imp_images) * 5)

        # Signal 2: tem video E imagem (possivel reveal)
        if video_ads and image_ads:
            signals.append({
                "signal": "Criativo revelado em outra plataforma",
                "detail": f"Encontramos {len(video_ads)} vídeos do mesmo anunciante que podem ser o criativo real por trás dos {len(image_ads)} anúncios de imagem",
                "severity": "critical"
            })
            cloaking_score += 30

        # Signal 3: aparece em multiplas fontes
        if len(sources) >= 2:
            signals.append({
                "signal": "Detectado em múltiplas fontes",
                "detail": f"Presente em {', '.join(sources)} — cross-reference confirma atividade",
                "severity": "high"
            })
            cloaking_score += 20

        # Signal 4: multiplas plataformas
        if len(platforms) >= 2:
            signals.append({
                "signal": "Ativo em múltiplas plataformas",
                "detail": f"Rodando em {', '.join(platforms)} — criativos podem ser diferentes entre plataformas",
                "severity": "medium"
            })
            cloaking_score += 10

        # Signal 5: alto gasto
        total_spend = sum(a.get("estimated_spend", 0) or 0 for a in ad_list)
        if total_spend > 10000:
            signals.append({
                "signal": "Alto investimento detectado",
                "detail": f"Gasto estimado total: ${total_spend:,.0f} — operação de escala",
                "severity": "high"
            })
            cloaking_score += 10

        # Signal 6: muitos dias rodando
        max_days = max((a.get("days_running", 0) or 0) for a in ad_list)
        if max_days > 14:
            signals.append({
                "signal": "Longevidade alta",
                "detail": f"Rodando há {max_days} dias — criativo validado e escalado",
                "severity": "medium"
            })
            cloaking_score += 10

        # Domains do anunciante
        domains = list(set(
            d.replace("www.", "") for a in ad_list
            for d in [urlparse(a.get("landing_page", "")).hostname or ""]
            if d and d != ""
        ))

        # === NEW Signal 7: Domain Cross-Reference ===
        # Mesmo domínio usado por múltiplos anunciantes = altamente suspeito
        xref_matches = []
        for dom in domains:
            if dom in domain_xref:
                other_advs = [a for a in domain_xref[dom]["advertisers"] if a != name]
                if other_advs:
                    xref_matches.append({"domain": dom, "other_advertisers": other_advs})

        if xref_matches:
            all_others = set()
            for m in xref_matches:
                all_others.update(m["other_advertisers"])
            signals.append({
                "signal": "🔗 Mesmo domínio, anunciantes diferentes",
                "detail": f"Landing page compartilhada com {len(all_others)} outro(s) anunciante(s): {', '.join(a.title() for a in list(all_others)[:5])} — forte indicador de cloaking ou operação coordenada",
                "severity": "critical",
                "domains_shared": [m["domain"] for m in xref_matches],
            })
            cloaking_score += min(35, 15 + len(all_others) * 10)

        # === NEW Signal 8: SimilarWeb Traffic vs Declared Impressions ===
        traffic_intel = {}
        for dom in domains:
            sw = sw_data.get(dom) or sw_data.get(f"www.{dom}")
            if sw and sw.get("monthly_visits"):
                monthly = sw["monthly_visits"]
                total_imp = sum(a.get("impressions", 0) or 0 for a in ad_list)

                traffic_intel[dom] = {
                    "monthly_visits": monthly,
                    "global_rank": sw.get("global_rank"),
                    "bounce_rate": sw.get("bounce_rate"),
                    "avg_duration": sw.get("avg_duration"),
                    "top_countries": sw.get("geography", [])[:3],
                    "social_breakdown": sw.get("social_breakdown"),
                    "branded_search": sw.get("branded_search"),
                    "ad_publishers": sw.get("ad_publishers"),
                    "traffic_sources": sw.get("traffic_sources"),
                    "declared_impressions": total_imp,
                }

                # Tráfego muito baixo vs impressões altas = cloaking confirmado
                if total_imp > 100000 and monthly < 10000:
                    signals.append({
                        "signal": "⚠️ Tráfego real vs Impressões: DISCREPÂNCIA",
                        "detail": f"{dom}: {monthly:,} visitas reais/mês vs {total_imp:,} impressões declaradas — tráfego real é {total_imp // max(monthly, 1)}x menor. Forte indicador de cloaking ou redirect.",
                        "severity": "critical",
                    })
                    cloaking_score += 25
                elif total_imp > 50000 and monthly > 100000:
                    signals.append({
                        "signal": "✅ Tráfego real confirma escala",
                        "detail": f"{dom}: {monthly:,} visitas/mês — operação legítima de alto volume",
                        "severity": "info",
                    })

                # Bounce rate muito alto + impressões altas = landing page de redirect
                # SimilarWeb retorna bounce_rate como porcentagem (ex: 85.3 = 85.3%)
                bounce = sw.get("bounce_rate") or 0
                if bounce > 85 and total_imp > 50000:
                    signals.append({
                        "signal": "🔄 Bounce rate suspeito",
                        "detail": f"{dom}: {bounce:.1f}% bounce rate — possível redirect ou página de cloaking",
                        "severity": "high",
                    })
                    cloaking_score += 15

        # === NEW Signal 9: Affiliate Network Detection ===
        # Check landing page URLs AND ad body text for network mentions
        networks_found = {}
        for a in ad_list:
            net = _detect_affiliate_network(a.get("landing_page", ""))
            if net:
                networks_found[net["network"]] = net["match"]
            # Also check body/title for network mentions (useful when landing_page is empty)
            body_text = (a.get("body", "") or "") + " " + (a.get("title", "") or "")
            if body_text.strip():
                net_body = _detect_affiliate_network(body_text)
                if net_body and net_body["network"] not in networks_found:
                    networks_found[net_body["network"]] = f"body:{net_body['match']}"

        if networks_found:
            net_names = ", ".join(n.replace("_", " ").title() for n in networks_found.keys())
            signals.append({
                "signal": "🏷️ Rede de afiliados detectada",
                "detail": f"Identificado como produto de: {net_names} — operação de afiliados/DR (Direct Response)",
                "severity": "high",
                "networks": list(networks_found.keys()),
            })
            cloaking_score += 10

        if not signals or cloaking_score < 20:
            continue

        cloaking_score = min(100, cloaking_score)

        # Selecionar ads representativos (com fallback de imagem)
        def _ensure_image(ad_item):
            """Garante que o ad tem alguma imagem para exibir no frontend"""
            if not ad_item.get("image_url") and ad_item.get("advertiser_image"):
                ad_item = {**ad_item, "image_url": ad_item["advertiser_image"]}
            if not ad_item.get("image_url") and ad_item.get("meta_snapshot_url"):
                ad_item = {**ad_item, "image_fallback_url": ad_item["meta_snapshot_url"]}
            return ad_item

        top_images = [_ensure_image(a) for a in sorted(image_ads, key=lambda x: x.get("impressions", 0) or 0, reverse=True)[:3]]
        top_videos = [_ensure_image(a) for a in sorted(video_ads, key=lambda x: x.get("impressions", 0) or 0, reverse=True)[:3]]

        entry = {
            "advertiser": name.title(),
            "total_ads": len(ad_list),
            "image_ads": len(image_ads),
            "video_ads": len(video_ads),
            "cloaking_score": cloaking_score,
            "cloaking_level": "critical" if cloaking_score >= 70 else "high" if cloaking_score >= 50 else "medium" if cloaking_score >= 30 else "low",
            "signals": signals,
            "sources": sources,
            "platforms": platforms,
            "domains": domains[:5],
            "estimated_spend": round(total_spend, 2),
            "max_days_running": max_days,
            "revealed_videos": top_videos,
            "catalog_images": top_images,
            "is_revealed": len(video_ads) > 0 and len(image_ads) > 0,
        }

        # Add new intel fields
        if traffic_intel:
            entry["traffic_intel"] = traffic_intel
        if networks_found:
            entry["affiliate_networks"] = list(networks_found.keys())
        if xref_matches:
            entry["domain_xref"] = xref_matches

        results.append(entry)

    # --- ADD domain-only entries (anunciantes diferentes, mesmo domínio, sem match por nome) ---
    seen_advertisers = set(r["advertiser"].lower() for r in results)
    for domain, xref in domain_xref.items():
        # Skip if all advertisers already in results
        new_advs = [a for a in xref["advertisers"] if a not in seen_advertisers]
        if not new_advs or len(xref["advertisers"]) < 2:
            continue

        dom_ads = xref["ads"]
        dom_sources = list(set(a.get("source", "") for a in dom_ads))
        dom_platforms = list(set(a.get("platform", "") for a in dom_ads))
        dom_image = [a for a in dom_ads if a.get("image_url") and not a.get("video_url")]
        dom_video = [a for a in dom_ads if a.get("video_url")]
        dom_spend = sum(a.get("estimated_spend", 0) or 0 for a in dom_ads)

        dom_signals = [{
            "signal": "🔗 Múltiplos anunciantes no mesmo domínio",
            "detail": f"{len(xref['advertisers'])} anunciantes diferentes apontam para {domain}: {', '.join(a.title() for a in xref['advertisers'][:5])}",
            "severity": "critical",
        }]

        dom_score = 30 + len(xref["advertisers"]) * 10

        # Check SimilarWeb for this domain
        sw = sw_data.get(domain) or sw_data.get(f"www.{domain}")
        dom_traffic = {}
        if sw and sw.get("monthly_visits"):
            dom_traffic = {
                domain: {
                    "monthly_visits": sw["monthly_visits"],
                    "global_rank": sw.get("global_rank"),
                    "bounce_rate": sw.get("bounce_rate"),
                    "top_countries": sw.get("geography", [])[:3],
                }
            }

        # Network detection
        dom_nets = {}
        for a in dom_ads:
            net = _detect_affiliate_network(a.get("landing_page", ""))
            if net:
                dom_nets[net["network"]] = net["match"]
        if dom_nets:
            dom_signals.append({
                "signal": "🏷️ Rede de afiliados detectada",
                "detail": f"Produto de: {', '.join(n.replace('_', ' ').title() for n in dom_nets.keys())}",
                "severity": "high",
            })
            dom_score += 10

        dom_score = min(100, dom_score)

        entry = {
            "advertiser": f"[Domain] {domain}",
            "total_ads": len(dom_ads),
            "image_ads": len(dom_image),
            "video_ads": len(dom_video),
            "cloaking_score": dom_score,
            "cloaking_level": "critical" if dom_score >= 70 else "high" if dom_score >= 50 else "medium",
            "signals": dom_signals,
            "sources": dom_sources,
            "platforms": dom_platforms,
            "domains": [domain],
            "estimated_spend": round(dom_spend, 2),
            "max_days_running": max((a.get("days_running", 0) or 0) for a in dom_ads),
            "revealed_videos": sorted(dom_video, key=lambda x: x.get("impressions", 0) or 0, reverse=True)[:3],
            "catalog_images": sorted(dom_image, key=lambda x: x.get("impressions", 0) or 0, reverse=True)[:3],
            "is_revealed": len(dom_video) > 0 and len(dom_image) > 0,
            "linked_advertisers": [a.title() for a in xref["advertisers"]],
        }
        if dom_traffic:
            entry["traffic_intel"] = dom_traffic
        if dom_nets:
            entry["affiliate_networks"] = list(dom_nets.keys())

        results.append(entry)

    results.sort(key=lambda x: x["cloaking_score"], reverse=True)
    return results


@app.get("/api/uncloak")
def uncloak_dashboard(
    min_score: int = Query(30, description="Cloaking score minimo"),
    revealed_only: bool = Query(False, description="Apenas com criativos revelados"),
    limit: int = Query(30, ge=1, le=100),
):
    """Dashboard de criativos desmascarados — revela ads escondidos atras de catalogos"""
    results = _build_uncloak_data()

    if min_score:
        results = [r for r in results if r["cloaking_score"] >= min_score]
    if revealed_only:
        results = [r for r in results if r["is_revealed"]]

    # Stats
    total = len(results)
    revealed = len([r for r in results if r["is_revealed"]])
    critical = len([r for r in results if r["cloaking_level"] == "critical"])
    with_traffic = len([r for r in results if r.get("traffic_intel")])
    with_xref = len([r for r in results if r.get("domain_xref") or r.get("linked_advertisers")])
    with_network = len([r for r in results if r.get("affiliate_networks")])

    return {
        "data": results[:limit],
        "total": total,
        "revealed_count": revealed,
        "critical_count": critical,
        "stats": {
            "total_suspicious": total,
            "total_revealed": revealed,
            "critical": critical,
            "high": len([r for r in results if r["cloaking_level"] == "high"]),
            "medium": len([r for r in results if r["cloaking_level"] == "medium"]),
            "with_traffic_intel": with_traffic,
            "domain_crossref_hits": with_xref,
            "affiliate_networks_detected": with_network,
        }
    }


@app.get("/api/uncloak/search")
def uncloak_search(
    q: str = Query(..., description="Nome do anunciante ou dominio"),
):
    """Busca especifica — verifica se um anunciante esta usando cloaking"""
    results = _build_uncloak_data()
    q_lower = q.lower()

    matched = [r for r in results if
               q_lower in r["advertiser"].lower() or
               any(q_lower in d for d in r.get("domains", [])) or
               any(q_lower in n for n in r.get("affiliate_networks", [])) or
               any(q_lower in a.lower() for a in r.get("linked_advertisers", []))]

    if not matched:
        return {"found": False, "message": f"Nenhum resultado para '{q}'", "data": []}

    return {"found": True, "total": len(matched), "data": matched}


@app.get("/api/uncloak/revealed")
def uncloak_revealed(limit: int = Query(20, ge=1, le=50)):
    """Top criativos REVELADOS — catalogos desmascarados com video real encontrado"""
    results = _build_uncloak_data()
    revealed = [r for r in results if r["is_revealed"]]
    revealed.sort(key=lambda x: x["cloaking_score"], reverse=True)
    return {"data": revealed[:limit], "total": len(revealed)}


@app.get("/api/uncloak/networks")
def uncloak_networks(limit: int = Query(30, ge=1, le=100)):
    """Anunciantes por rede de afiliados — ClickBank, BuyGoods, Hotmart, etc."""
    results = _build_uncloak_data()
    by_net = {}
    for r in results:
        for net in r.get("affiliate_networks", []):
            if net not in by_net:
                by_net[net] = []
            by_net[net].append(r)

    output = []
    for net, entries in sorted(by_net.items(), key=lambda x: len(x[1]), reverse=True):
        output.append({
            "network": net.replace("_", " ").title(),
            "count": len(entries),
            "avg_cloaking_score": round(sum(e["cloaking_score"] for e in entries) / len(entries)),
            "top_advertisers": [{"advertiser": e["advertiser"], "score": e["cloaking_score"]} for e in entries[:10]],
        })
    return {"data": output, "total_networks": len(output)}


@app.get("/api/uncloak/domain-xref")
def uncloak_domain_xref(limit: int = Query(20, ge=1, le=50)):
    """Domínios compartilhados entre múltiplos anunciantes — operações coordenadas"""
    results = _build_uncloak_data()
    xref_entries = [r for r in results if r.get("domain_xref") or r.get("linked_advertisers")]
    xref_entries.sort(key=lambda x: x["cloaking_score"], reverse=True)
    return {"data": xref_entries[:limit], "total": len(xref_entries)}


# ============================================================
# TIKTOK SHOP (Social1 data)
# ============================================================

_tiktok_cache = {"data": None, "loaded_at": None, "file": None}

def load_tiktok_shop():
    """Carrega dados TikTok Shop com cache"""
    files = sorted(glob.glob(f"{OUTPUT_DIR}/tiktok_shop_*.json"), reverse=True)
    if not files:
        return {"products": [], "videos": [], "creators": []}

    latest = files[0]
    file_mtime = os.path.getmtime(latest)

    if _tiktok_cache["data"] is not None and _tiktok_cache["file"] == latest and _tiktok_cache["loaded_at"] == file_mtime:
        return _tiktok_cache["data"]

    with open(latest, "r", encoding="utf-8") as f:
        data = json.load(f)

    _tiktok_cache["data"] = data
    _tiktok_cache["file"] = latest
    _tiktok_cache["loaded_at"] = file_mtime
    return data


@app.get("/api/tiktok-shop/products")
def tiktok_products(
    region: str = Query(None, description="us, uk, br, de, fr, es, it, mx"),
    category: str = Query(None, description="Filtrar por categoria"),
    competition: str = Query(None, description="low, medium, high"),
    min_score: float = Query(None, description="Viral score minimo (1-10)"),
    search: str = Query(None, description="Buscar no nome"),
    sort: str = Query("viral_score", description="viral_score, units_sold, gmv, video_views, creator_count"),
    order: str = Query("desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Produtos virais do TikTok Shop - 8 paises"""
    data = load_tiktok_shop()
    products = data.get("products", [])

    if region:
        products = [p for p in products if p.get("region") == region]
    if category:
        products = [p for p in products if category.lower() in (p.get("category", "") or "").lower()]
    if competition:
        products = [p for p in products if p.get("competition_level") == competition]
    if min_score:
        products = [p for p in products if (p.get("viral_score", 0) or 0) >= min_score]
    if search:
        sl = search.lower()
        products = [p for p in products if sl in (p.get("name", "") or "").lower()]

    reverse = order == "desc"
    try:
        products.sort(key=lambda x: x.get(sort, 0) or 0, reverse=reverse)
    except:
        pass

    total = len(products)
    start = (page - 1) * limit
    return {
        "data": products[start:start + limit],
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit if total > 0 else 0,
    }


@app.get("/api/tiktok-shop/videos")
def tiktok_videos(
    region: str = Query(None),
    is_ad: bool = Query(None, description="Filtrar apenas ads"),
    has_insights: bool = Query(None, description="Apenas com AI insights"),
    search: str = Query(None),
    sort: str = Query("views", description="views, likes, engagement_rate"),
    order: str = Query("desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Videos virais do TikTok Shop com AI insights"""
    data = load_tiktok_shop()
    videos = data.get("videos", [])

    if region:
        videos = [v for v in videos if v.get("region") == region]
    if is_ad is not None:
        videos = [v for v in videos if v.get("is_ad") == is_ad]
    if has_insights:
        videos = [v for v in videos if v.get("has_insights")]
    if search:
        sl = search.lower()
        videos = [v for v in videos if sl in (v.get("description", "") or "").lower()]

    reverse = order == "desc"
    try:
        videos.sort(key=lambda x: x.get(sort, 0) or 0, reverse=reverse)
    except:
        pass

    total = len(videos)
    start = (page - 1) * limit
    return {
        "data": videos[start:start + limit],
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit if total > 0 else 0,
    }


@app.get("/api/tiktok-shop/creators")
def tiktok_creators(
    region: str = Query(None),
    sort: str = Query("gmv_30d", description="gmv_30d, followers, influence_score"),
    order: str = Query("desc"),
    limit: int = Query(20, ge=1, le=50),
):
    """Top creators do TikTok Shop por GMV"""
    data = load_tiktok_shop()
    creators = data.get("creators", [])

    if region:
        creators = [c for c in creators if c.get("region") == region]

    reverse = order == "desc"
    creators.sort(key=lambda x: x.get(sort, 0) or 0, reverse=reverse)

    return {"data": creators[:limit], "total": len(creators)}


@app.get("/api/tiktok-shop/product/{product_id}")
def tiktok_product_detail(product_id: str, region: str = Query("us")):
    """Detalhe de um produto TikTok Shop + videos associados"""
    # Buscar nos dados locais primeiro
    data = load_tiktok_shop()
    local = None
    for p in data.get("products", []):
        pid = p.get("product_id", "").replace("tks_", "")
        if pid == product_id:
            local = p
            break

    # Buscar videos associados nos dados locais
    related_videos = []
    for v in data.get("videos", []):
        desc = (v.get("description", "") or "").lower()
        name = (local.get("name", "") if local else "").lower()
        # Match por palavras do nome do produto na descricao do video
        if local and name:
            words = [w for w in name.split()[:3] if len(w) > 3]
            if any(w in desc for w in words):
                related_videos.append(v)

    return {
        "product": local,
        "related_videos": related_videos[:12],
        "total_videos": len(related_videos),
    }


@app.get("/api/tiktok-shop/creator/{handle}")
def tiktok_creator_detail(handle: str, region: str = Query("us")):
    """Detalhe de um creator + seus videos"""
    data = load_tiktok_shop()

    # Buscar creator (dados incluem videos embutidos)
    creator = None
    for c in data.get("creators", []):
        if c.get("handle", "").lower() == handle.lower():
            creator = c
            break

    if not creator:
        return {"creator": None, "videos": [], "total_videos": 0}

    # Videos vem embutidos no creator
    creator_videos = creator.get("videos", [])

    # Se nao tem videos embutidos, buscar nos videos gerais
    if not creator_videos:
        creator_videos = [v for v in data.get("videos", [])
                          if v.get("creator_handle", "").lower() == handle.lower()]
        creator_videos.sort(key=lambda x: x.get("views", 0) or 0, reverse=True)

    # Retornar creator sem o campo videos (pra nao duplicar)
    creator_info = {k: v for k, v in creator.items() if k != "videos"}

    return {
        "creator": creator_info,
        "videos": creator_videos[:24],
        "total_videos": creator.get("video_count", len(creator_videos)),
        "total_views": creator.get("total_views", 0),
    }


@app.get("/api/tiktok-shop/shops")
def tiktok_shops(
    sort: str = Query("total_gmv", description="total_gmv, day7_gmv, rating, total_sold, creators_count"),
    order: str = Query("desc"),
    limit: int = Query(20, ge=1, le=50),
):
    """Top lojas do TikTok Shop com trend de vendas"""
    data = load_tiktok_shop()
    shops = data.get("shops", [])
    reverse = order == "desc"
    shops.sort(key=lambda x: x.get(sort, 0) or 0, reverse=reverse)
    return {"data": shops[:limit], "total": len(shops)}


@app.get("/api/tiktok-shop/ads")
def tiktok_ads_roas(
    sort: str = Query("roas", description="roas, views, estimated_cost, likes"),
    order: str = Query("desc"),
    commission_only: bool = Query(False),
    limit: int = Query(20, ge=1, le=50),
):
    """Ads do TikTok com ROAS real — dados exclusivos"""
    data = load_tiktok_shop()
    ads = data.get("tiktok_ads", [])
    if commission_only:
        ads = [a for a in ads if a.get("is_commission")]
    reverse = order == "desc"
    ads.sort(key=lambda x: x.get(sort, 0) or 0, reverse=reverse)
    return {"data": ads[:limit], "total": len(ads)}


@app.get("/api/tiktok-shop/stats")
def tiktok_stats():
    """Estatisticas do TikTok Shop"""
    data = load_tiktok_shop()
    products = data.get("products", [])
    videos = data.get("videos", [])
    creators = data.get("creators", [])

    categories = {}
    regions = {}
    for p in products:
        cat = p.get("category", "Unknown")
        categories[cat] = categories.get(cat, 0) + 1
        r = p.get("region", "?")
        regions[r] = regions.get(r, 0) + 1

    total_gmv = sum(p.get("gmv", 0) or 0 for p in products)
    total_units = sum(p.get("units_sold", 0) or 0 for p in products)
    total_views = sum(v.get("views", 0) or 0 for v in videos)
    with_insights = sum(1 for v in videos if v.get("has_insights"))

    shops = data.get("shops", [])
    tiktok_ads = data.get("tiktok_ads", [])
    total_shop_gmv = sum(s.get("total_gmv", 0) or 0 for s in shops)
    avg_roas = round(sum(a.get("roas", 0) or 0 for a in tiktok_ads) / max(len(tiktok_ads), 1), 2)

    return {
        "total_products": len(products),
        "total_videos": len(videos),
        "total_creators": len(creators),
        "total_shops": len(shops),
        "total_tiktok_ads": len(tiktok_ads),
        "total_gmv": round(total_gmv, 2),
        "total_units_sold": total_units,
        "total_video_views": total_views,
        "total_shop_gmv": round(total_shop_gmv, 2),
        "avg_roas": avg_roas,
        "videos_with_insights": with_insights,
        "has_fastmoss": data.get("fastmoss_integrated", False),
        "by_category": dict(sorted(categories.items(), key=lambda x: x[1], reverse=True)[:15]),
        "by_region": dict(sorted(regions.items(), key=lambda x: x[1], reverse=True)),
    }


# ============================================================
# SEOTOOLS INTEGRATION
# ============================================================
@app.get("/api/seotools/list")
def seotools_list():
    """Lista todas as ferramentas SEOTools disponiveis"""
    tools_file = Path("resultados/seotools_ferramentas.json")
    if tools_file.exists():
        with open(tools_file, "r", encoding="utf-8") as f:
            tools = json.load(f)
        return {"tools": tools, "total": len(tools),
                "socket_url": "http://127.0.0.1:3992",
                "cookie": "d18a1e33-3944-4d0d-9043-b73bd2f3a60d",
                "version": "20250906"}
    return {"tools": [], "total": 0}


@app.post("/api/sync/trigger")
def trigger_sync():
    """Dispara nova coleta (roda o scraper)"""
    import subprocess, sys
    try:
        subprocess.Popen([sys.executable, "unified_scraper.py"],
                         cwd=str(Path(__file__).parent))
        return {"status": "started", "message": "Scraper iniciado em background"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/sync/status")
def sync_status():
    """Status da ultima coleta"""
    summary = load_latest_summary()
    return {
        "last_sync": summary.get("scrape_date", "nunca"),
        "total_ads": summary.get("unique_ads", 0),
        "sources": summary.get("sources", {}),
    }

# ============================================================
# PIPIADS LIVE SEARCH (on-demand via API v3)
# ============================================================
# Consulta em tempo real a API do PipiAds quando usuario busca no NinjaSpy.
# Cache em memoria de 1h por params hash para economizar creditos (20/busca).
# Novos ads sao mergeados no unified em background para persistencia.

import hashlib
import threading
import time as _time

_pipi_live_cache = {}  # {cache_key: {"data": [...], "expires_at": ts, "total_real": int}}
_PIPI_CACHE_TTL = 3600  # 1h

def _pipi_cache_key(params: dict) -> str:
    """Gera chave de cache estavel a partir dos params."""
    normalized = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()

def _pipi_cache_get(key: str):
    entry = _pipi_live_cache.get(key)
    if not entry:
        return None
    if _time.time() > entry["expires_at"]:
        _pipi_live_cache.pop(key, None)
        return None
    return entry

def _pipi_cache_set(key: str, data: list, total_real: int):
    _pipi_live_cache[key] = {
        "data": data,
        "expires_at": _time.time() + _PIPI_CACHE_TTL,
        "total_real": total_real,
    }
    # Limpar entries expirados se cache crescer muito
    if len(_pipi_live_cache) > 500:
        now = _time.time()
        expired = [k for k, v in _pipi_live_cache.items() if v["expires_at"] < now]
        for k in expired:
            _pipi_live_cache.pop(k, None)

def _pipi_merge_background(new_ads: list):
    """Mergeia ads novos no unified em background thread (nao bloqueia resposta)."""
    def _merge():
        try:
            uf = sorted(glob.glob(f"{OUTPUT_DIR}/unified_2*.json"), reverse=True)
            if not uf:
                return
            latest = uf[0]
            with open(latest, "r", encoding="utf-8") as f:
                existing = json.load(f)
            ids = {a.get("ad_id", "") for a in existing if a.get("ad_id")}
            to_add = [a for a in new_ads if a.get("ad_id") and a["ad_id"] not in ids]
            if not to_add:
                return
            tmp_file = latest + ".tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(existing + to_add, f, ensure_ascii=False)
            os.replace(tmp_file, latest)
        except Exception:
            pass  # nao falha se o merge nao conseguir
    t = threading.Thread(target=_merge, daemon=True)
    t.start()


@app.get("/api/pipiads/search")
def pipiads_live_search(
    keyword: str = Query(..., description="Termo de busca (obrigatorio)"),
    region: str = Query(None, description="US, BR, UK, FR, DE, ES, IT, MX, etc"),
    platform: str = Query(None, description="tiktok (plat_type=1) ou facebook (plat_type=2)"),
    sort: str = Query("relevance", description="relevance, impressions, engagement, recent"),
    page: int = Query(1, ge=1, le=50, description="Pagina (1-50)"),
    limit: int = Query(20, ge=1, le=50, description="Ads por pagina (max 50)"),
    language: str = Query(None, description="pt, en, es, fr, de, etc"),
    has_presenter: str = Query(None, description="yes ou no"),
    days_min: int = Query(None, description="Minimo de dias rodando"),
    days_max: int = Query(None, description="Maximo de dias rodando"),
    nocache: bool = Query(False, description="Ignorar cache e forcar chamada live"),
):
    """Busca AO VIVO no PipiAds v3 — consome creditos (conta ADVANCED: 100k/mes).

    Retorna ads com AI analysis completa (pipi_hook, pipi_script, pipi_tags,
    pipi_cpm, pipi_cpa, pipi_language, pipi_has_presenter).

    Cache de 1h por combinacao de params para economizar creditos.
    Ads novos sao mergeados no unified em background para persistencia.
    """
    import requests as _req
    from pipi_auto import HEADERS as _PIPI_HEADERS, normalize as _pipi_normalize

    # Montar body da busca PipiAds v3
    body = {
        "is_participle": False,
        "search_type": 1,
        "extend_keywords": [{"type": 1, "keyword": keyword}],
        "sort_type": "desc",
        "current_page": page,
        "page_size": limit,
    }
    # Sort: 999=relevance, 2=impressions, 3=engagement, 1=recent
    sort_map = {"relevance": 999, "impressions": 2, "engagement": 3, "recent": 1}
    body["sort"] = sort_map.get(sort, 999)
    if region:
        body["country"] = region.upper()
    if platform:
        plat_map = {"tiktok": 1, "facebook": 2}
        body["plat_type"] = plat_map.get(platform.lower(), 0)
    if language:
        body["language"] = language.lower()
    if days_min is not None:
        body["days_min"] = str(days_min)
    if days_max is not None:
        body["days_max"] = str(days_max)

    # Cache lookup
    cache_key = _pipi_cache_key({
        "k": keyword.lower(), "r": region, "p": platform, "s": sort,
        "pg": page, "l": limit, "lang": language, "hp": has_presenter,
        "dm": days_min, "dx": days_max,
    })
    if not nocache:
        cached = _pipi_cache_get(cache_key)
        if cached:
            return {
                "data": cached["data"],
                "total": len(cached["data"]),
                "total_available": cached["total_real"],
                "page": page, "limit": limit,
                "cached": True,
                "source": "pipiads_live",
            }

    # Chamada live
    try:
        r = _req.post(
            "https://www.pipiads.com/v3/api/search4/at/video/search",
            headers=_PIPI_HEADERS, json=body, timeout=30,
        )
        if r.status_code != 200:
            return {
                "data": [], "total": 0, "error": f"PipiAds retornou {r.status_code}",
                "page": page, "limit": limit, "cached": False,
            }
        result = r.json().get("result", {})
        raw_ads = result.get("data", []) or []
    except Exception as e:
        return {
            "data": [], "total": 0, "error": f"Falha na chamada PipiAds: {str(e)[:120]}",
            "page": page, "limit": limit, "cached": False,
        }

    # Get total real (contagem total disponivel)
    total_real = len(raw_ads)
    try:
        count_body = {**body, "search_model": "ALL"}
        cr = _req.post(
            "https://www.pipiads.com/v3/api/search4/at/video/get-adlib-search-count",
            headers=_PIPI_HEADERS, json=count_body, timeout=15,
        )
        if cr.status_code == 200:
            total_real = cr.json().get("data", {}).get("count", total_real) or total_real
    except Exception:
        pass

    # Normalizar pro formato unified (pipi_auto.normalize ja faz isso)
    normalized = [_pipi_normalize(item, keyword) for item in raw_ads]

    # Filtros pos-processamento (campos que PipiAds nao filtra direto)
    if has_presenter:
        want = has_presenter.lower()
        normalized = [a for a in normalized if (a.get("pipi_has_presenter") or "").lower() == want]

    # Adicionar marcador de fonte live
    for ad in normalized:
        ad["live_fetched"] = True
        ad["collected_at"] = datetime.now().isoformat()

    # Cache
    _pipi_cache_set(cache_key, normalized, total_real)

    # Merge em background
    if normalized:
        _pipi_merge_background(normalized)

    return {
        "data": normalized,
        "total": len(normalized),
        "total_available": total_real,
        "page": page,
        "limit": limit,
        "cached": False,
        "source": "pipiads_live",
    }


@app.get("/api/pipiads/cache-stats")
def pipiads_cache_stats():
    """Diagnostico do cache do PipiAds live search."""
    now = _time.time()
    active = [k for k, v in _pipi_live_cache.items() if v["expires_at"] > now]
    return {
        "total_entries": len(_pipi_live_cache),
        "active_entries": len(active),
        "cache_ttl_seconds": _PIPI_CACHE_TTL,
    }


# ============================================================
# SOCIAL1 LIVE SEARCH (on-demand via HostDimer proxy)
# ============================================================
# Consulta em tempo real a Social1 via proxy Playwright no HostDimer.
# Proxy roda em https://social1.ninjabrhub.online (porta 3019 interna).
# Mesmo pattern do PipiAds: cache 1h, background merge no unified.

_SOCIAL1_PROXY_URL = "https://social1.ninjabrhub.online"
_SOCIAL1_API_KEY = "njspy_social1_2026_q7w8e9"
_SOCIAL1_CACHE = {}  # {cache_key: {"data": {...}, "expires_at": ts}}
_SOCIAL1_CACHE_TTL = 3600  # 1h


def _social1_cache_key(params: dict) -> str:
    normalized = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def _social1_cache_get(key: str):
    entry = _SOCIAL1_CACHE.get(key)
    if not entry:
        return None
    if _time.time() > entry["expires_at"]:
        _SOCIAL1_CACHE.pop(key, None)
        return None
    return entry["data"]


def _social1_cache_set(key: str, data: dict):
    _SOCIAL1_CACHE[key] = {"data": data, "expires_at": _time.time() + _SOCIAL1_CACHE_TTL}
    if len(_SOCIAL1_CACHE) > 500:
        now = _time.time()
        expired = [k for k, v in _SOCIAL1_CACHE.items() if v["expires_at"] < now]
        for k in expired:
            _SOCIAL1_CACHE.pop(k, None)


def _social1_normalize_product(p: dict, keyword: str, region: str) -> dict:
    """Converte produto do Social1 pro formato unified do NinjaSpy."""
    product_id = str(p.get("product_id") or "")
    timeseries = p.get("timeseries") or []
    return {
        "ad_id": f"social1_product_{product_id}",
        "source": "social1",
        "platform": "tiktok",
        "advertiser": p.get("shop_name") or "Shop " + str(p.get("shop_id") or ""),
        "advertiser_image": p.get("shop_image_url") or "",
        "title": (p.get("product_name") or "")[:200],
        "body": (p.get("product_name") or "")[:500],
        "cta": "Shop Now",
        "image_url": p.get("product_image_url") or "",
        "video_url": "",
        "likes": 0,
        "comments": 0,
        "shares": 0,
        "impressions": int(p.get("views") or 0),
        "total_engagement": int(p.get("units_sold") or 0),
        "days_running": len(timeseries),
        "heat": int(p.get("creator_count") or 0),
        "ad_type": "product",
        "country": region.upper(),
        "channels": "tiktok",
        "has_media": bool(p.get("product_image_url")),
        "has_store": True,
        "search_keyword": keyword or "",
        "collected_at": datetime.now().isoformat(),
        "live_fetched": True,
        # Social1-specific
        "social1_product_id": product_id,
        "social1_shop_id": str(p.get("shop_id") or ""),
        "social1_gmv": float(p.get("gmv") or 0),
        "social1_units_sold": int(p.get("units_sold") or 0),
        "social1_views": int(p.get("views") or 0),
        "social1_video_count": int(p.get("video_count") or 0),
        "social1_creator_count": int(p.get("creator_count") or 0),
        "social1_price": float(p.get("price_value") or 0),
        "social1_region": region.lower(),
        "social1_timeseries": timeseries,
    }


def _social1_merge_background(new_ads: list):
    """Mergeia ads no unified em background (nao bloqueia resposta)."""
    def _merge():
        try:
            uf = sorted(glob.glob(f"{OUTPUT_DIR}/unified_2*.json"), reverse=True)
            if not uf:
                return
            latest = uf[0]
            with open(latest, "r", encoding="utf-8") as f:
                existing = json.load(f)
            ids = {a.get("ad_id", "") for a in existing if a.get("ad_id")}
            to_add = [a for a in new_ads if a.get("ad_id") and a["ad_id"] not in ids]
            if not to_add:
                return
            tmp_file = latest + ".tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(existing + to_add, f, ensure_ascii=False)
            os.replace(tmp_file, latest)
        except Exception:
            pass
    threading.Thread(target=_merge, daemon=True).start()


@app.get("/api/social1/search")
def social1_live_search(
    keyword: Optional[str] = Query(None, description="Termo de busca (opcional — se vazio, retorna top)"),
    region: str = Query("us", description="us, uk, br, de, fr, es, it, mx"),
    days: int = Query(7, ge=1, le=30),
    page: int = Query(1, ge=1, le=50),
    limit: int = Query(20, ge=1, le=50),
    shop_id: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    sort: Optional[str] = Query(None, description="gmv, units_sold, views (server-side TBD)"),
    nocache: bool = Query(False),
):
    """Busca AO VIVO produtos TikTok Shop via Social1 no HostDimer.

    Retorna produtos normalizados no formato unified com campos social1_* extras:
    social1_gmv, social1_units_sold, social1_views, social1_video_count,
    social1_creator_count, social1_timeseries (dados diarios).

    Cache de 1h. Merge em background pro unified.
    """
    import requests as _req

    cache_key = _social1_cache_key({
        "k": (keyword or "").lower(), "r": region, "d": days,
        "pg": page, "l": limit, "s": shop_id, "c": category, "sort": sort,
    })
    if not nocache:
        cached = _social1_cache_get(cache_key)
        if cached:
            return {**cached, "cached": True, "source": "social1_live"}

    # Proxy call
    params = {
        "region": region,
        "days": days,
        "page": page,
        "limit": limit,
        "key": _SOCIAL1_API_KEY,
    }
    if keyword:
        params["keyword"] = keyword
    if shop_id:
        params["shop_id"] = shop_id
    if category:
        params["category"] = category
    if sort:
        params["sort"] = sort

    try:
        r = _req.get(f"{_SOCIAL1_PROXY_URL}/api/search", params=params, timeout=60)
        if r.status_code != 200:
            return {
                "data": [], "total": 0, "cached": False,
                "error": f"social1 proxy retornou {r.status_code}",
                "snippet": r.text[:200],
            }
        raw = r.json()
    except Exception as e:
        return {"data": [], "total": 0, "cached": False, "error": f"Falha chamando social1 proxy: {str(e)[:150]}"}

    # Normalizar
    results = raw.get("data", {}).get("results", []) or []
    normalized = [_social1_normalize_product(p, keyword or "", region) for p in results]

    response = {
        "data": normalized,
        "total": len(normalized),
        "page": page,
        "limit": limit,
        "cached": False,
        "source": "social1_live",
    }

    _social1_cache_set(cache_key, response)

    if normalized:
        _social1_merge_background(normalized)

    return response


def _social1_normalize_creator(c: dict, region: str) -> dict:
    """Converte creator do Social1 pro formato unified do NinjaSpy."""
    oecuid = str(c.get("creator_oecuid") or "")
    handle = c.get("handle") or ""
    followers = int(c.get("follower_cnt") or 0)
    gmv = float(c.get("med_gmv_revenue") or 0)
    return {
        "ad_id": f"social1_creator_{oecuid}",
        "source": "social1",
        "platform": "tiktok",
        "advertiser": c.get("nickname") or handle or "Creator",
        "advertiser_image": c.get("profilePicture") or "",
        "title": (c.get("nickname") or handle),
        "body": f"@{handle} — {followers:,} followers — ${gmv:,.0f} GMV",
        "cta": "View Creator",
        "image_url": c.get("profilePicture") or "",
        "video_url": "",
        "likes": 0,
        "comments": 0,
        "shares": 0,
        "impressions": followers,
        "total_engagement": int(gmv),
        "days_running": 0,
        "heat": min(1000, int(gmv / 1000)) if gmv else 0,
        "ad_type": "creator",
        "country": region.upper(),
        "channels": "tiktok",
        "has_media": bool(c.get("profilePicture")),
        "has_store": True,
        "search_keyword": "",
        "collected_at": datetime.now().isoformat(),
        "live_fetched": True,
        # Social1 creator-specific
        "social1_creator_id": oecuid,
        "social1_handle": handle,
        "social1_nickname": c.get("nickname") or "",
        "social1_followers": followers,
        "social1_gmv": gmv,
        "social1_region": region.lower(),
        "social1_tiktok_url": f"https://www.tiktok.com/@{handle}" if handle else "",
    }


@app.get("/api/social1/creators")
def social1_live_creators(
    region: str = Query("us", description="us, uk, br, de, fr, es, it, mx"),
    nocache: bool = Query(False),
):
    """Top creators TikTok Shop AO VIVO por regiao.

    Retorna creators normalizados com campos social1_* extras:
    social1_creator_id, social1_handle, social1_nickname,
    social1_followers, social1_gmv, social1_tiktok_url.

    Cache de 1h por regiao.
    """
    import requests as _req

    cache_key = _social1_cache_key({"type": "creators", "r": region})
    if not nocache:
        cached = _social1_cache_get(cache_key)
        if cached:
            return {**cached, "cached": True, "source": "social1_live"}

    params = {"region": region, "key": _SOCIAL1_API_KEY}
    try:
        r = _req.get(f"{_SOCIAL1_PROXY_URL}/api/creators", params=params, timeout=60)
        if r.status_code != 200:
            return {
                "data": [], "total": 0, "cached": False,
                "error": f"social1 proxy retornou {r.status_code}",
                "snippet": r.text[:200],
            }
        raw = r.json()
    except Exception as e:
        return {"data": [], "total": 0, "cached": False, "error": f"Falha chamando social1 proxy: {str(e)[:150]}"}

    # Creators vem como raw["data"] (array direto)
    creators_list = raw.get("data") or []
    if not isinstance(creators_list, list):
        creators_list = []

    normalized = [_social1_normalize_creator(c, region) for c in creators_list]

    response = {
        "data": normalized,
        "total": len(normalized),
        "region": region,
        "cached": False,
        "source": "social1_live",
    }
    _social1_cache_set(cache_key, response)

    if normalized:
        _social1_merge_background(normalized)

    return response


@app.get("/api/social1/cache-stats")
def social1_cache_stats():
    now = _time.time()
    active = [k for k, v in _SOCIAL1_CACHE.items() if v["expires_at"] > now]
    return {
        "total_entries": len(_SOCIAL1_CACHE),
        "active_entries": len(active),
        "cache_ttl_seconds": _SOCIAL1_CACHE_TTL,
        "proxy_url": _SOCIAL1_PROXY_URL,
    }


@app.get("/api/social1/health")
def social1_proxy_health():
    """Ping do proxy Social1 no HostDimer."""
    import requests as _req
    try:
        r = _req.get(f"{_SOCIAL1_PROXY_URL}/health", timeout=10)
        return {"proxy_reachable": r.status_code == 200, "proxy_response": r.json() if r.status_code == 200 else r.text[:200]}
    except Exception as e:
        return {"proxy_reachable": False, "error": str(e)[:200]}


# ============================================================
# DAILY INTEL SERVICE (on-demand via HostDimer proxy)
# ============================================================
# Cliente busca no NinjaSpy -> Render consulta intel.ninjabrhub.online
# (FastAPI no HostDimer porta 3020, conta paga $29/mes).
# Retorna VSLs + Ad Creatives scaling agora em 28 nichos.

_DAILYINTEL_PROXY_URL = "https://intel.ninjabrhub.online"
_DAILYINTEL_API_KEY = "njspy_dailyintel_2026_r8t9y0"
_DAILYINTEL_CACHE = {}
_DAILYINTEL_CACHE_TTL = 3600  # 1h


def _dailyintel_cache_key(params: dict) -> str:
    normalized = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def _dailyintel_cache_get(key: str):
    entry = _DAILYINTEL_CACHE.get(key)
    if not entry:
        return None
    if _time.time() > entry["expires_at"]:
        _DAILYINTEL_CACHE.pop(key, None)
        return None
    return entry["data"]


def _dailyintel_cache_set(key: str, data: dict):
    _DAILYINTEL_CACHE[key] = {"data": data, "expires_at": _time.time() + _DAILYINTEL_CACHE_TTL}
    if len(_DAILYINTEL_CACHE) > 500:
        now = _time.time()
        expired = [k for k, v in _DAILYINTEL_CACHE.items() if v["expires_at"] < now]
        for k in expired:
            _DAILYINTEL_CACHE.pop(k, None)


def _dailyintel_normalize(v: dict) -> dict:
    """Converte video do Daily Intel pro formato unified.

    Thumbs e videos passam pelo proxy Render (precisam cookie upstream).
    """
    vid = str(v.get("id") or "")
    product = v.get("product_name") or "Unknown"
    niche = v.get("niche") or ""
    platform_low = (v.get("platform") or "").lower()
    report = v.get("daily_reports") or {}
    vsl_id = v.get("bunny_vsl_id") or ""
    ads_id = v.get("bunny_ads_id") or ""

    # URLs absolutas dos endpoints do Render (browser chama direto)
    base = "https://spy-ads-api.onrender.com"
    vsl_thumb = f"{base}/api/dailyintel/thumb/{vsl_id}?lib=vsl" if vsl_id else ""
    ads_thumb = f"{base}/api/dailyintel/thumb/{ads_id}?lib=ads" if ads_id else ""
    stream_endpoint = f"{base}/api/dailyintel/stream"
    # Player wrapper: HTML com iframe no-referrer — usar direto em <iframe src=...>
    vsl_player = f"{base}/api/dailyintel/player/{vid}?fileType=vsl" if vid else ""
    ads_player = f"{base}/api/dailyintel/player/{vid}?fileType=ads" if vid else ""
    # Player NATIVO (sem watermark, HLS direto da fonte original)
    # Fallback automatico pro player Bunny se nao conseguir extrair nativo
    vsl_native_player = f"{base}/api/dailyintel/native-player/{vid}?fileType=vsl" if vid else ""
    # Download direto — use em <a href download>
    vsl_download = f"{base}/api/dailyintel/download/{vid}?fileType=vsl" if vid else ""
    ads_download = f"{base}/api/dailyintel/download/{vid}?fileType=ads" if vid else ""
    # Download NATIVO (sem watermark, ffmpeg remux do HLS original)
    # Fallback auto pro download Bunny se nao tiver extracao nativa
    vsl_native_download = f"{base}/api/dailyintel/native-download/{vid}" if vid else ""

    return {
        "ad_id": f"dailyintel_{vid}",
        "source": "dailyintel",
        "platform": platform_low or "unknown",
        "advertiser": product,
        "advertiser_image": vsl_thumb or ads_thumb,
        "title": f"{product} — {niche}" if niche else product,
        "body": (v.get("utm_campaign") or "")[:300],
        "cta": "View Offer",
        "image_url": ads_thumb or vsl_thumb,
        "video_url": "",  # vazio — pegar via stream endpoint quando for tocar
        "landing_page": v.get("page_link") or "",
        "first_seen": report.get("report_date") or "",
        "last_seen": report.get("report_date") or "",
        "likes": 0,
        "comments": 0,
        "shares": 0,
        "impressions": 0,
        "total_engagement": 0,
        "heat": 500 if v.get("has_clean_vsl") and v.get("has_clean_ads") else 200,
        "ad_type": "vsl" if v.get("has_clean_vsl") else "creative",
        "channels": platform_low,
        "has_media": bool(vsl_thumb or ads_thumb),
        "has_store": True,
        "country": v.get("country") or "",
        "collected_at": datetime.now().isoformat(),
        "live_fetched": True,
        # Daily Intel specific
        "dailyintel_id": vid,
        "dailyintel_row_id": vid,  # alias — usado no POST /api/dailyintel/stream
        "dailyintel_niche": niche,
        "dailyintel_platform": v.get("platform") or "",
        "dailyintel_traffic_type": v.get("traffic_type") or "",
        "dailyintel_is_paid": bool(v.get("is_paid_traffic")),
        "dailyintel_funnel_stage": v.get("funnel_stage") or "",
        "dailyintel_device_type": v.get("device_type") or "",
        "dailyintel_utm_source": v.get("utm_source") or "",
        "dailyintel_utm_medium": v.get("utm_medium") or "",
        "dailyintel_utm_campaign": v.get("utm_campaign") or "",
        "dailyintel_vsl_id": vsl_id,
        "dailyintel_ads_id": ads_id,
        "dailyintel_vsl_thumb": vsl_thumb,  # URL absoluta pro browser usar em <img>
        "dailyintel_ads_thumb": ads_thumb,
        "dailyintel_vsl_player": vsl_player,  # Player Bunny CDN (fallback, COM watermark)
        "dailyintel_vsl_native_player": vsl_native_player,  # Player HLS nativo (SEM watermark)
        "dailyintel_ads_player": ads_player,
        "dailyintel_vsl_download": vsl_download,  # MP4 COM watermark (Bunny)
        "dailyintel_vsl_native_download": vsl_native_download,  # MP4 SEM watermark (ffmpeg remux HLS)
        "dailyintel_ads_download": ads_download,
        "dailyintel_stream_endpoint": stream_endpoint,  # POST {rowId, fileType} → {embedUrl, downloadUrl}
        "dailyintel_has_clean_vsl": bool(v.get("has_clean_vsl")),
        "dailyintel_has_clean_ads": bool(v.get("has_clean_ads")),
        "dailyintel_page_link": v.get("page_link") or "",
        "dailyintel_checkout_link": v.get("checkout_link") or "",
        "dailyintel_campaign_status": v.get("campaign_status") or "",
        "dailyintel_report_date": report.get("report_date") or "",
        "dailyintel_report_title": report.get("title") or "",
    }


def _dailyintel_merge_background(new_ads: list):
    def _merge():
        try:
            uf = sorted(glob.glob(f"{OUTPUT_DIR}/unified_2*.json"), reverse=True)
            if not uf:
                return
            latest = uf[0]
            with open(latest, "r", encoding="utf-8") as f:
                existing = json.load(f)
            ids = {a.get("ad_id", "") for a in existing if a.get("ad_id")}
            to_add = [a for a in new_ads if a.get("ad_id") and a["ad_id"] not in ids]
            if not to_add:
                return
            tmp_file = latest + ".tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(existing + to_add, f, ensure_ascii=False)
            os.replace(tmp_file, latest)
        except Exception:
            pass
    threading.Thread(target=_merge, daemon=True).start()


@app.get("/api/dailyintel/search")
def dailyintel_live_search(
    niche: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    traffic_type: Optional[str] = Query(None),
    is_paid: Optional[bool] = Query(None),
    has_vsl: Optional[bool] = Query(None),
    has_ads: Optional[bool] = Query(None),
    funnel_stage: Optional[str] = Query(None),
    device_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    sort: str = Query("date_desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    nocache: bool = Query(False),
):
    """Busca AO VIVO em VSLs + Ad Creatives do Daily Intel Service.

    ~1400 videos em 28 nichos (Weight Loss, Memory, Diabetes, ED, Vision, etc).
    Cada item tem vsl_preview_url, ads_preview_url, page_link, checkout_link,
    utm_campaign (identifica campanha), traffic_type, funnel_stage.
    """
    import requests as _req

    cache_key = _dailyintel_cache_key({
        "n": niche, "p": platform, "tt": traffic_type, "ip": is_paid,
        "hv": has_vsl, "ha": has_ads, "fs": funnel_stage, "dt": device_type,
        "s": search, "df": date_from, "dto": date_to, "sort": sort,
        "pg": page, "l": limit,
    })
    if not nocache:
        cached = _dailyintel_cache_get(cache_key)
        if cached:
            return {**cached, "cached": True, "source": "dailyintel_live"}

    # Montar params pro proxy
    params = {"page": page, "limit": limit, "sort": sort, "key": _DAILYINTEL_API_KEY}
    if niche: params["niche"] = niche
    if platform: params["platform"] = platform
    if traffic_type: params["traffic_type"] = traffic_type
    if is_paid is not None: params["is_paid"] = str(is_paid).lower()
    if has_vsl is not None: params["has_vsl"] = str(has_vsl).lower()
    if has_ads is not None: params["has_ads"] = str(has_ads).lower()
    if funnel_stage: params["funnel_stage"] = funnel_stage
    if device_type: params["device_type"] = device_type
    if search: params["search"] = search
    if date_from: params["date_from"] = date_from
    if date_to: params["date_to"] = date_to
    if nocache: params["nocache"] = "true"

    try:
        r = _req.get(f"{_DAILYINTEL_PROXY_URL}/api/search", params=params, timeout=30)
        if r.status_code != 200:
            return {
                "data": [], "total": 0, "cached": False,
                "error": f"dailyintel proxy retornou {r.status_code}",
                "snippet": r.text[:200],
            }
        raw = r.json()
    except Exception as e:
        return {"data": [], "total": 0, "cached": False, "error": f"Falha chamando dailyintel proxy: {str(e)[:150]}"}

    videos = raw.get("data") or []
    normalized = [_dailyintel_normalize(v) for v in videos]

    response = {
        "data": normalized,
        "total": raw.get("total", len(normalized)),
        "total_available": raw.get("total_available"),
        "page": page,
        "limit": limit,
        "pages": raw.get("pages"),
        "cached": False,
        "source": "dailyintel_live",
    }

    _dailyintel_cache_set(cache_key, response)

    if normalized:
        _dailyintel_merge_background(normalized)

    return response


@app.get("/api/dailyintel/niches")
def dailyintel_niches():
    """Facet de nichos com contagem (dropdown filter)."""
    import requests as _req
    try:
        r = _req.get(f"{_DAILYINTEL_PROXY_URL}/api/niches?key={_DAILYINTEL_API_KEY}", timeout=20)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        return {"error": str(e)[:200], "data": []}
    return {"error": f"proxy status {r.status_code}", "data": []}


@app.get("/api/dailyintel/platforms")
def dailyintel_platforms():
    """Facet de platforms (Facebook, Instagram)."""
    import requests as _req
    try:
        r = _req.get(f"{_DAILYINTEL_PROXY_URL}/api/platforms?key={_DAILYINTEL_API_KEY}", timeout=20)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        return {"error": str(e)[:200], "data": []}
    return {"error": f"proxy status {r.status_code}", "data": []}


@app.get("/api/dailyintel/thumb/{video_id}")
def dailyintel_thumb(video_id: str, lib: str = Query("vsl")):
    """Proxy da thumbnail pro browser (redireciona pro HostDimer proxy com key).

    Usamos 302 redirect — browser carrega imagem direto do HostDimer sem
    passar pelo Render (economia de bandwidth).
    """
    from fastapi.responses import RedirectResponse
    url = f"{_DAILYINTEL_PROXY_URL}/api/thumb/{video_id}?lib={lib}&key={_DAILYINTEL_API_KEY}"
    return RedirectResponse(url=url, status_code=302)


@app.post("/api/dailyintel/stream")
def dailyintel_stream(body: dict):
    """Pega URL assinada pra tocar o video.

    Body: {"rowId": "<dailyintel_id do ad>", "fileType": "vsl" ou "ads"}
    Retorna: {"embedUrl": "iframe.mediadelivery.net/embed/...token=X&expires=Y",
              "downloadUrl": "vz-xxx.b-cdn.net/....mp4?token=X&expires=Y",
              "filename": "X_vsl_720p.mp4"}

    Uso no frontend:
      const r = await fetch('/api/dailyintel/stream', {method:'POST', body: JSON.stringify({rowId, fileType})});
      const {embedUrl} = await r.json();
      // renderizar <iframe src={embedUrl}>
    """
    import requests as _req
    row_id = body.get("rowId") or body.get("videoId") or body.get("id")
    file_type = body.get("fileType") or body.get("type") or "vsl"
    if not row_id:
        raise HTTPException(status_code=400, detail="rowId obrigatorio")

    if str(row_id).startswith("dailyintel_"):
        row_id = str(row_id)[len("dailyintel_"):]

    try:
        r = _req.post(
            f"{_DAILYINTEL_PROXY_URL}/api/stream",
            params={"key": _DAILYINTEL_API_KEY},
            json={"rowId": row_id, "fileType": file_type},
            timeout=30,
        )
        return r.json()
    except Exception as e:
        return {"error": f"falha: {str(e)[:150]}"}


@app.get("/api/dailyintel/native-download/{row_id}")
def dailyintel_native_download(row_id: str):
    """Download do VSL SEM WATERMARK via HLS nativo (ffmpeg remux streaming).

    Precisa do cache nativo populado — se nao tiver, tenta extrair on-the-fly.

    Uso: <a href="/api/dailyintel/native-download/{id}" download referrerpolicy="no-referrer">Baixar</a>
    """
    import requests as _req
    from fastapi.responses import StreamingResponse, RedirectResponse
    if str(row_id).startswith("dailyintel_"):
        row_id = str(row_id)[len("dailyintel_"):]

    # Garantir que extracao nativa foi feita (cache-first)
    try:
        r0 = _req.get(
            f"{_DAILYINTEL_PROXY_URL}/api/native/{row_id}",
            params={"key": _DAILYINTEL_API_KEY},
            timeout=60,
        )
        data0 = r0.json()
        if not data0.get("master_url"):
            # Sem nativo — fallback pro download Bunny (com watermark)
            return RedirectResponse(
                url=f"/api/dailyintel/download/{row_id}?fileType=vsl",
                status_code=302,
            )
    except Exception:
        return RedirectResponse(
            url=f"/api/dailyintel/download/{row_id}?fileType=vsl",
            status_code=302,
        )

    # Stream ffmpeg remux via HostDimer
    def proxy_stream():
        with _req.get(
            f"{_DAILYINTEL_PROXY_URL}/api/native-download/{row_id}",
            params={"key": _DAILYINTEL_API_KEY},
            stream=True,
            timeout=(10, 300),  # 10s connect, 5min total
        ) as rr:
            for chunk in rr.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk

    # Pegar filename do upstream
    try:
        head = _req.head(
            f"{_DAILYINTEL_PROXY_URL}/api/native-download/{row_id}",
            params={"key": _DAILYINTEL_API_KEY},
            timeout=15,
            allow_redirects=False,
        )
        cd = head.headers.get("Content-Disposition", "")
        import re as _re
        m = _re.search(r'filename="([^"]+)"', cd)
        filename = m.group(1) if m else f"{row_id}_vsl_720p.mp4"
    except Exception:
        filename = f"{row_id}_vsl_720p.mp4"

    return StreamingResponse(
        proxy_stream(),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Referrer-Policy": "no-referrer",
        },
    )


@app.get("/api/dailyintel/native-player/{row_id}")
def dailyintel_native_player(row_id: str, fileType: str = Query("vsl")):
    """Player HLS NATIVO — serve VSL direto da fonte original (sem watermark).

    Scraper extraiu a URL master.m3u8 da landing page do anunciante
    (ConverteAI/Vidalytics/etc). Aqui retorna HTML com video.js + hls.js
    reproduzindo direto do CDN original.

    Se nao tem cache nativo, retorna HTML que tenta extrair on-demand
    ou redireciona pro /player/ (fallback com watermark).

    Para VSL: usa o row_id da tabela daily_intel.
    Para Ads: ads nao tem landing page separado — usa fallback Bunny.
    """
    import requests as _req
    from fastapi.responses import HTMLResponse, RedirectResponse
    if str(row_id).startswith("dailyintel_"):
        row_id = str(row_id)[len("dailyintel_"):]

    # Ads nao tem page_link → fallback
    if fileType != "vsl":
        return RedirectResponse(url=f"/api/dailyintel/player/{row_id}?fileType={fileType}", status_code=302)

    # Tentar pegar master nativo do cache
    try:
        r = _req.get(
            f"{_DAILYINTEL_PROXY_URL}/api/native/{row_id}",
            params={"key": _DAILYINTEL_API_KEY},
            timeout=60,
        )
        data = r.json()
        master = data.get("master_url")
    except Exception as e:
        data = {}
        master = None

    # Fallback: se falhou extraction, usa player Bunny com watermark
    if not master:
        return RedirectResponse(url=f"/api/dailyintel/player/{row_id}?fileType=vsl", status_code=302)

    # HLS player usando hls.js
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Player</title>
<style>
  html,body{{margin:0;padding:0;height:100%;background:#000;overflow:hidden;font-family:system-ui,-apple-system,sans-serif}}
  video{{width:100%;height:100%;object-fit:contain;background:#000}}
  .err{{color:#fff;display:flex;align-items:center;justify-content:center;height:100%;padding:20px;text-align:center}}
  .tag{{position:absolute;top:8px;left:8px;background:rgba(0,0,0,0.5);color:#6cf;padding:3px 8px;border-radius:4px;font-size:11px;z-index:10}}
</style>
</head>
<body>
<div class="tag">NATIVE · {data.get('player','hls')}</div>
<video id="v" controls playsinline preload="metadata" referrerpolicy="no-referrer"></video>
<script src="https://cdn.jsdelivr.net/npm/hls.js@1.5.13/dist/hls.min.js"></script>
<script>
(function(){{
  var v = document.getElementById('v');
  var src = {json.dumps(master)};
  if (v.canPlayType('application/vnd.apple.mpegurl')) {{
    // Safari nativo
    v.src = src;
  }} else if (window.Hls && Hls.isSupported()) {{
    var hls = new Hls({{
      enableWorker: true,
      lowLatencyMode: false,
      backBufferLength: 90,
    }});
    hls.loadSource(src);
    hls.attachMedia(v);
    hls.on(Hls.Events.ERROR, function(event, data){{
      if (data.fatal) {{
        document.body.innerHTML = '<div class="err"><div><h2>Erro ao carregar video nativo</h2><p>Tente recarregar a pagina.</p></div></div>';
      }}
    }});
  }} else {{
    document.body.innerHTML = '<div class="err">Seu navegador nao suporta HLS.</div>';
  }}
  v.addEventListener('error', function(){{
    document.body.innerHTML = '<div class="err"><div><h2>Nao conseguimos carregar o video</h2></div></div>';
  }});
}})();
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


@app.get("/api/dailyintel/player/{row_id}")
def dailyintel_player(row_id: str, fileType: str = Query("vsl")):
    """HTML player — usa iframe BunnyCDN pra gestao de session automatica.

    Iframe tem WebSocket com iframe.mediadelivery.net que libera a session
    quando o usuario fecha o modal (sem erro "Already streaming").

    Nota sobre watermark: a marca d'agua aparece embutida no arquivo MP4
    original do Daily Intel — nao e removivel, aparece em qualquer player
    (inclusive no site deles).
    """
    import requests as _req
    from fastapi.responses import HTMLResponse
    if str(row_id).startswith("dailyintel_"):
        row_id = str(row_id)[len("dailyintel_"):]

    try:
        r = _req.post(
            f"{_DAILYINTEL_PROXY_URL}/api/stream",
            params={"key": _DAILYINTEL_API_KEY},
            json={"rowId": row_id, "fileType": fileType},
            timeout=90,  # Proxy pode ter retry backoff
        )
        data = r.json()
        embed = data.get("embedUrl", "")
        download = data.get("downloadUrl", "")
        err = data.get("error")
    except Exception as e:
        embed = ""
        err = str(e)[:150]

    if not embed:
        err_msg = err or "video indisponivel"
        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Erro</title>
<style>body{{margin:0;background:#04192c;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;padding:20px}}</style>
</head><body><div><h2>Video indisponivel</h2><p>{err_msg}</p><p style="opacity:0.7;font-size:14px">Tente novamente em alguns segundos.</p></div></body></html>"""
        return HTMLResponse(content=html, status_code=404)

    # iframe BunnyCDN com referrerpolicy=no-referrer + <meta referrer>
    # BunnyCDN aceita quando Referer vazio (so bloqueia "estrangeiros").
    # WebSocket do iframe gerencia session automaticamente — ao fechar
    # modal/iframe, session eh liberada naturalmente.
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Player</title>
<style>
  html,body{{margin:0;padding:0;height:100%;background:#000;overflow:hidden}}
  iframe{{width:100%;height:100%;border:0;display:block}}
</style>
</head>
<body>
<iframe src="{embed}"
        referrerpolicy="no-referrer"
        allow="accelerometer;autoplay;encrypted-media;gyroscope;picture-in-picture"
        allowfullscreen></iframe>
</body>
</html>"""
    return HTMLResponse(content=html)


@app.get("/api/dailyintel/download/{row_id}")
def dailyintel_download(row_id: str, fileType: str = Query("vsl")):
    """Forca download do MP4 via 302 redirect pro BunnyCDN assinado.

    Uso: <a href="/api/dailyintel/download/<row_id>?fileType=vsl" download>Baixar</a>
    """
    import requests as _req
    from fastapi.responses import RedirectResponse, JSONResponse
    if str(row_id).startswith("dailyintel_"):
        row_id = str(row_id)[len("dailyintel_"):]

    try:
        r = _req.post(
            f"{_DAILYINTEL_PROXY_URL}/api/stream",
            params={"key": _DAILYINTEL_API_KEY},
            json={"rowId": row_id, "fileType": fileType},
            timeout=30,
        )
        data = r.json()
    except Exception as e:
        return JSONResponse({"error": f"falha: {str(e)[:150]}"}, status_code=502)

    dl = data.get("downloadUrl")
    if not dl:
        return JSONResponse({"error": data.get("error") or "sem downloadUrl"}, status_code=404)
    # BunnyCDN tem whitelist de Referer — enviar Referrer-Policy pra browser
    # nao mandar Referer ao seguir o redirect. Funciona em conjunto com o
    # referrerpolicy="no-referrer" do frontend <a href download>.
    filename = data.get("filename") or f"{row_id}_{fileType}.mp4"
    return RedirectResponse(
        url=dl,
        status_code=302,
        headers={
            "Referrer-Policy": "no-referrer",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.post("/api/dailyintel/session/close")
@app.get("/api/dailyintel/session/close")
def dailyintel_session_close():
    """Encerra a session ativa no Daily Intel (libera pro proximo stream).

    Frontend DEVE chamar isto quando fechar o modal do player. Sem isto,
    o proximo video vai esperar ~15s (retry) antes de abrir.

    Uso:
      fetch('https://spy-ads-api.onrender.com/api/dailyintel/session/close',
            {method:'POST', referrerPolicy:'no-referrer'});
      // ou GET tambem funciona pra facilitar <a href> ou navegador.sendBeacon

    Sem JWT — publico.
    """
    import requests as _req
    try:
        r = _req.post(
            f"{_DAILYINTEL_PROXY_URL}/api/session/close",
            params={"key": _DAILYINTEL_API_KEY},
            timeout=15,
        )
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else {"closed": r.status_code == 200}
    except Exception as e:
        return {"closed": False, "error": str(e)[:150]}


@app.get("/api/dailyintel/health")
def dailyintel_proxy_health():
    """Ping do proxy Daily Intel no HostDimer."""
    import requests as _req
    try:
        r = _req.get(f"{_DAILYINTEL_PROXY_URL}/health", timeout=10)
        return {"proxy_reachable": r.status_code == 200, "proxy_response": r.json() if r.status_code == 200 else r.text[:200]}
    except Exception as e:
        return {"proxy_reachable": False, "error": str(e)[:200]}


@app.get("/api/dailyintel/cache-stats")
def dailyintel_cache_stats():
    now = _time.time()
    active = [k for k, v in _DAILYINTEL_CACHE.items() if v["expires_at"] > now]
    return {
        "total_entries": len(_DAILYINTEL_CACHE),
        "active_entries": len(active),
        "cache_ttl_seconds": _DAILYINTEL_CACHE_TTL,
        "proxy_url": _DAILYINTEL_PROXY_URL,
    }


# ============================================================
# ADPLEXITY NATIVE (on-demand via HostDimer proxy)
# ============================================================
# native.ninjabrhub.online -> conta paga ninjabr_forum (22.9M ads nativos)
# Proxy mantem cookie laravel_session + XSRF-TOKEN

_ADPLEXITY_PROXY_URL = "https://native.ninjabrhub.online"
_ADPLEXITY_API_KEY = "njspy_adplexity_2026_m3k7r2"
_ADPLEXITY_CACHE = {}
_ADPLEXITY_CACHE_TTL = 1800  # 30min


def _adplexity_cache_key(params: dict) -> str:
    normalized = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def _adplexity_cache_get(key: str):
    entry = _ADPLEXITY_CACHE.get(key)
    if not entry:
        return None
    if _time.time() > entry["expires_at"]:
        _ADPLEXITY_CACHE.pop(key, None)
        return None
    return entry["data"]


def _adplexity_cache_set(key: str, data: dict, ttl: Optional[int] = None):
    _ADPLEXITY_CACHE[key] = {
        "data": data,
        "expires_at": _time.time() + (ttl or _ADPLEXITY_CACHE_TTL),
    }
    if len(_ADPLEXITY_CACHE) > 500:
        now = _time.time()
        for k in [k for k, v in _ADPLEXITY_CACHE.items() if v["expires_at"] < now]:
            _ADPLEXITY_CACHE.pop(k, None)


def _adplexity_normalize_ad(a: dict, sub_mode: str = "ad") -> dict:
    """Converte ad do AdPlexity pro formato unified NinjaSpy."""
    ad_id = str(a.get("id") or a.get("lp_id") or "")
    title = a.get("title_en") or a.get("title") or ""
    desc = a.get("description_en") or a.get("description") or ""
    thumb = a.get("thumb_url") or a.get("image_url") or ""
    image = a.get("image_url") or thumb
    # Normalizar URL — Render proxy da thumb (ja tem /api/adplexity/thumb/)
    import re as _re
    thumb_hash = ""
    m = _re.search(r"/native/([^/]+?)(;[^;]*)?$", thumb or "")
    if m:
        thumb_hash = m.group(1)
        thumb = f"https://spy-ads-api.onrender.com/api/adplexity/thumb/{thumb_hash}"
        image = thumb

    countries = a.get("countries") or []
    country_str = ",".join(countries[:5]) if isinstance(countries, list) else ""
    return {
        "ad_id": f"adplexity_{ad_id}",
        "source": "adplexity_native",
        "platform": "native",
        "advertiser": title[:60] or "Native Ad",
        "advertiser_image": thumb,
        "title": title[:200],
        "body": desc[:500] if desc else title[:500],
        "cta": "",
        "image_url": image,
        "video_url": "",
        "landing_page": "",
        "first_seen": a.get("first_seen") or "",
        "last_seen": a.get("last_seen") or "",
        "is_active": True,
        "likes": 0,
        "comments": 0,
        "shares": 0,
        "impressions": int(a.get("hits_total") or a.get("hits") or 0),
        "total_engagement": int(a.get("hits_total") or 0),
        "days_running": int(a.get("days_total") or a.get("days") or 0),
        "heat": min(1000, int((a.get("hits_total") or 0) // 100)),
        "ad_type": "image" if sub_mode == "ad" else "landing_page",
        "country": country_str,
        "all_countries": countries,
        "channels": "native",
        "has_media": bool(thumb),
        "has_store": False,
        "total_countries": len(countries) if isinstance(countries, list) else 0,
        "collected_at": datetime.now().isoformat(),
        "live_fetched": True,
        # AdPlexity-specific
        "adplexity_id": ad_id,
        "adplexity_lp_id": str(a.get("lp_id") or "") if a.get("lp_id") else "",
        "adplexity_sub_mode": sub_mode,
        "adplexity_type": a.get("type"),
        "adplexity_title_orig": a.get("title_orig") or "",
        "adplexity_description_orig": a.get("description_orig") or "",
        "adplexity_networks": a.get("networks") or [],
        "adplexity_aff_networks": a.get("aff_networks") or [],
        "adplexity_devices": a.get("devices") or [],
        "adplexity_connections": a.get("connections") or [],
        "adplexity_tracking_tools": a.get("tracking_tools") or [],
        "adplexity_publishers_count": int(a.get("publishers_count") or 0),
        "adplexity_image_sizes": a.get("image_sizes") or {},
        "adplexity_thumb_hash": thumb_hash,
        # YouTube (quando aplicavel)
        "adplexity_youtube_id": a.get("youtube_id"),
        "adplexity_youtube_views": a.get("youtube_views_count"),
        "adplexity_youtube_likes": a.get("youtube_likes_count"),
    }


def _adplexity_get(path: str, params: dict = None, timeout: int = 30):
    import requests as _req
    p = dict(params or {})
    p["key"] = _ADPLEXITY_API_KEY
    return _req.get(f"{_ADPLEXITY_PROXY_URL}{path}", params=p, timeout=timeout)


@app.get("/api/adplexity/search")
def adplexity_search(
    query: str = Query("", description="Termo de busca"),
    sub_mode: str = Query("ad", description="ad (creatives) | lp (landing pages)"),
    mode: str = Query("keyword"),
    query_subject: str = Query("keyword.ad_or_lp", description="keyword.ad_or_lp | keyword.ad | keyword.lp | keyword.headline | keyword.description"),
    order: str = Query("newest"),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    days_min: int = Query(1, ge=0),
    start: int = Query(0, ge=0),
    count: int = Query(20, ge=1, le=100),
    countries: Optional[str] = Query(None, description="codigos iso2 separados por virgula (US,BR)"),
    networks: Optional[str] = Query(None, description="IDs (ver /api/adplexity/filters)"),
    ad_categories: Optional[str] = Query(None),
    aff_networks: Optional[str] = Query(None),
    devices: Optional[str] = Query(None),
    languages: Optional[str] = Query(None),
    nocache: bool = Query(False),
):
    """Busca ads nativos ou landing pages via AdPlexity (22.9M ads).

    Dados ricos: hits (views), days_running, publishers_count, networks,
    countries, aff_networks, devices, tracking_tools.
    """
    cache_key = _adplexity_cache_key({
        "q": query, "sm": sub_mode, "m": mode, "qs": query_subject, "o": order,
        "df": date_from, "dt": date_to, "dm": days_min,
        "s": start, "c": count,
        "co": countries, "n": networks, "ac": ad_categories,
        "an": aff_networks, "d": devices, "l": languages,
    })
    if not nocache:
        cached = _adplexity_cache_get(cache_key)
        if cached:
            return {**cached, "cached": True, "source": "adplexity_live"}

    params = {
        "query": query, "sub_mode": sub_mode, "mode": mode,
        "query_subject": query_subject, "order": order,
        "days_min": days_min, "start": start, "count": count,
    }
    if date_from: params["date_from"] = date_from
    if date_to: params["date_to"] = date_to
    if countries: params["countries"] = countries
    if networks: params["networks"] = networks
    if ad_categories: params["ad_categories"] = ad_categories
    if aff_networks: params["aff_networks"] = aff_networks
    if devices: params["devices"] = devices
    if languages: params["languages"] = languages

    try:
        r = _adplexity_get("/api/search", params, timeout=45)
    except Exception as e:
        return {"data": [], "total": 0, "cached": False, "error": f"falha: {str(e)[:150]}"}

    if r.status_code != 200:
        return {"data": [], "total": 0, "cached": False, "error": f"proxy {r.status_code}", "snippet": r.text[:150]}

    raw = r.json()
    ads = raw.get("ads") or []
    normalized = [_adplexity_normalize_ad(a, sub_mode) for a in ads]

    response = {
        "data": normalized,
        "total": raw.get("total") or 0,
        "returned": len(normalized),
        "start": start,
        "count": count,
        "pages": ((raw.get("total") or 0) + count - 1) // count if count else 0,
        "cached": False,
        "source": "adplexity_live",
        "sub_mode": sub_mode,
    }
    _adplexity_cache_set(cache_key, response)
    return response


@app.get("/api/adplexity/filters")
def adplexity_filters():
    """Dicionarios pra popular filtros no frontend.

    Retorna 109 adCategories, 249 countries, 100 networks, 248 affNetworks,
    11 deviceTypes, linguagens, trackingTools, technology, etc.
    """
    cache_key = _adplexity_cache_key("filters")
    cached = _adplexity_cache_get(cache_key)
    if cached: return cached
    try:
        r = _adplexity_get("/api/filters", timeout=20)
        if r.status_code == 200:
            data = r.json()
            _adplexity_cache_set(cache_key, data, ttl=3600)
            return data
        return {"error": f"proxy {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:150]}


@app.get("/api/adplexity/trending")
def adplexity_trending(
    category: str = Query("lp-domain", description="lp-domain = top 400 advertisers"),
    period: str = Query("7d", description="7d | 30d | 90d"),
):
    """Top 400 advertisers rodando ads nativos (ranking por adsCount).

    Cada item: advertiserName, adsCount, networks, daysRunning, countries, newestAds.
    """
    cache_key = _adplexity_cache_key({"trending": category, "p": period})
    cached = _adplexity_cache_get(cache_key)
    if cached: return {"data": cached, "cached": True}
    try:
        r = _adplexity_get("/api/trending", {"category": category, "period": period}, timeout=30)
        if r.status_code == 200:
            data = r.json()
            _adplexity_cache_set(cache_key, data, ttl=3600)
            return {"data": data, "cached": False, "total": len(data) if isinstance(data, list) else 0}
        return {"error": f"proxy {r.status_code}", "data": []}
    except Exception as e:
        return {"error": str(e)[:150], "data": []}


@app.get("/api/adplexity/counters")
def adplexity_counters(
    query: str = Query(""),
    sub_mode: str = Query("ad"),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    """Agregados por filtros pra uma busca — util pra dashboards."""
    params = {"query": query, "sub_mode": sub_mode}
    if date_from: params["date_from"] = date_from
    if date_to: params["date_to"] = date_to
    cache_key = _adplexity_cache_key({"counters": params})
    cached = _adplexity_cache_get(cache_key)
    if cached: return cached
    try:
        r = _adplexity_get("/api/counters", params, timeout=30)
        if r.status_code == 200:
            data = r.json()
            _adplexity_cache_set(cache_key, data, ttl=1800)
            return data
        return {"error": f"proxy {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:150]}


@app.get("/api/adplexity/thumb/{ad_hash}")
def adplexity_thumb(ad_hash: str):
    """Proxy da thumbnail (302 redirect pro HostDimer)."""
    from fastapi.responses import RedirectResponse
    url = f"{_ADPLEXITY_PROXY_URL}/api/thumb/{ad_hash}?key={_ADPLEXITY_API_KEY}"
    return RedirectResponse(url=url, status_code=302)


@app.get("/api/adplexity/profile")
def adplexity_profile():
    """Info da conta AdPlexity (username, exportLimit, folders)."""
    try:
        r = _adplexity_get("/api/profile", timeout=15)
        return r.json() if r.status_code == 200 else {"error": f"proxy {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:150]}


@app.get("/api/adplexity/health")
def adplexity_health():
    """Ping do proxy AdPlexity."""
    import requests as _req
    try:
        r = _req.get(f"{_ADPLEXITY_PROXY_URL}/health", timeout=10)
        return {"proxy_reachable": r.status_code == 200, "proxy_response": r.json() if r.status_code == 200 else r.text[:200]}
    except Exception as e:
        return {"proxy_reachable": False, "error": str(e)[:200]}


@app.get("/api/adplexity/cache-stats")
def adplexity_cache_stats():
    now = _time.time()
    active = [k for k, v in _ADPLEXITY_CACHE.items() if v["expires_at"] > now]
    return {
        "total_entries": len(_ADPLEXITY_CACHE),
        "active_entries": len(active),
        "cache_ttl_seconds": _ADPLEXITY_CACHE_TTL,
        "proxy_url": _ADPLEXITY_PROXY_URL,
    }


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"\n  Spy Ads API rodando em http://localhost:{port}")
    print(f"  Docs interativos em http://localhost:{port}/docs\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
