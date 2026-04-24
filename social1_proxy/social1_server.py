"""
Social1 Proxy Server — Playwright + FastAPI na porta 3019.

Usa Next.js Server Actions de www.social1.ai com sessao persistente.
Hash extraction automatica (estilo verbatik_extract_hashes) com auto-refresh.

Deploy: HostDimer /opt/ninja-proxy/social1/
Servico: ninja-social1.service (systemd)
Exposto via: https://social1.ninjabrhub.online (nginx proxy)
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright, Page
import uvicorn

# ============================================================
# CONFIG
# ============================================================

PORT = int(os.environ.get("SOCIAL1_PORT", 3019))
API_KEY = os.environ.get("SOCIAL1_API_KEY", "njspy_social1_2026_q7w8e9")
USER_DATA_DIR = os.environ.get(
    "SOCIAL1_USER_DATA_DIR",
    str(Path(__file__).parent / "browser_data"),
)
HEADLESS = os.environ.get("SOCIAL1_HEADLESS", "true").lower() == "true"
LOG_LEVEL = os.environ.get("SOCIAL1_LOG", "INFO").upper()

SOCIAL1_BASE = "https://www.social1.ai"
HASH_REFRESH_SECONDS = 6 * 3600  # 6h — actions duram o deploy

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("social1")

# ============================================================
# PLAYWRIGHT STATE
# ============================================================

_state = {
    "playwright": None,
    "context": None,
    "page": None,
    "ready": False,
    "lock": None,
    "last_activity": 0,
    "action_hashes": {
        # Hash padrao capturado em 23/04/2026 pros valores iniciais.
        # Sera atualizado dinamicamente (_refresh_hashes).
        "products": {"hash": "40e417e5510af0e7b4ef01f09a8621a3f346fee315", "refreshed_at": 0},
    },
}


async def _ensure_browser():
    if _state["ready"] and _state["page"] and not _state["page"].is_closed():
        return

    async with _state["lock"]:
        if _state["ready"] and _state["page"] and not _state["page"].is_closed():
            return

        if _state["playwright"] is None:
            log.info("Iniciando Playwright...")
            _state["playwright"] = await async_playwright().start()

        Path(USER_DATA_DIR).mkdir(parents=True, exist_ok=True)

        log.info(f"Chrome persistente em {USER_DATA_DIR} (headless={HEADLESS})")
        context = await _state["playwright"].chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            viewport={"width": 1400, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
        )
        _state["context"] = context

        # Importar cookies do arquivo se existir (login inicial sem UI)
        cookies_file = Path(__file__).parent / "social1_cookies.json"
        if cookies_file.exists():
            try:
                with open(cookies_file, "r", encoding="utf-8") as f:
                    cookies = json.load(f)
                await context.add_cookies(cookies)
                log.info(f"Importados {len(cookies)} cookies de {cookies_file.name}")
            except Exception as e:
                log.warning(f"Falha importando cookies: {e}")

        existing = [p for p in context.pages if "social1" in (p.url or "")]
        page = existing[0] if existing else await context.new_page()
        if not existing:
            await page.goto(f"{SOCIAL1_BASE}/products", wait_until="domcontentloaded", timeout=60_000)

        _state["page"] = page
        _state["ready"] = True
        _state["last_activity"] = time.time()
        log.info(f"Browser pronto em {page.url}")


# Body fingerprints — whitelist + blacklist pra distinguir Server Actions
# Cada rota: { must_have: [campos obrigatorios], must_not: [campos que NAO podem ter] }
_ROUTE_BODY_FINGERPRINTS = {
    "products": {
        "must_have": ["pageSize", "search"],
        "must_not": [],
    },
    "videos": {
        "must_have": ["limit", "page"],
        "must_not": ["pageSize", "shopName"],
    },
    "creators": {
        "must_have": ["region"],
        # Body de creators e so `[{"region":"br"}]` — exclui products/videos/shops
        "must_not": ["page", "pageSize", "limit", "search", "tsStart", "shopName"],
    },
    "shops": {
        "must_have": ["pageSize", "shopName"],
        "must_not": [],
    },
}


async def _refresh_action_hash(route: str = "products") -> Optional[str]:
    """Captura o next-action hash interceptando requests na pagina certa.

    Usa fingerprints (whitelist + blacklist) de body pra distinguir rotas.
    """
    await _ensure_browser()
    page: Page = _state["page"]

    fp = _ROUTE_BODY_FINGERPRINTS.get(route, {"must_have": ["page"], "must_not": []})
    captured_hash = {"val": None, "payload": None}

    async def handle_request(request):
        if captured_hash["val"]:
            return
        h = request.headers.get("next-action")
        if not h:
            return
        body = request.post_data or ""
        # whitelist: precisa ter TODOS os must_have
        if not all(f in body for f in fp["must_have"]):
            return
        # blacklist: NAO pode ter nenhum must_not
        if any(f in body for f in fp["must_not"]):
            return
        captured_hash["val"] = h
        captured_hash["payload"] = body[:300]

    page.on("request", handle_request)
    try:
        # Forcar reload completo com query param pra disparar novas requests
        url = f"{SOCIAL1_BASE}/{route}?_refresh={int(time.time())}"
        log.info(f"Refresh hash: navegando pra {url} (fingerprint={fp})")
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        # Esperar requests async do Next disparar
        for _ in range(15):
            if captured_hash["val"]:
                break
            await asyncio.sleep(1)
    except Exception as e:
        log.warning(f"Nav pra /{route} timeout: {e}")
    finally:
        page.remove_listener("request", handle_request)

    if captured_hash["val"]:
        _state["action_hashes"][route] = {
            "hash": captured_hash["val"],
            "refreshed_at": time.time(),
            "body_sample": captured_hash["payload"],
        }
        log.info(f"Hash /{route} = {captured_hash['val'][:16]}... (body={captured_hash['payload'][:80]})")
        return captured_hash["val"]

    log.warning(f"Nao consegui capturar hash /{route} com fingerprint {fingerprint}")
    return None


async def _get_action_hash(route: str = "products") -> str:
    entry = _state["action_hashes"].get(route, {})
    hash_val = entry.get("hash")
    refreshed_at = entry.get("refreshed_at", 0)
    age = time.time() - refreshed_at

    if hash_val and age < HASH_REFRESH_SECONDS:
        return hash_val

    # Tentar refresh — se falhar e tiver hash antigo, usa ele mesmo
    new_hash = await _refresh_action_hash(route)
    if new_hash:
        return new_hash
    if hash_val:
        log.warning(f"Refresh falhou, usando hash antigo /{route}")
        return hash_val
    raise HTTPException(
        status_code=503,
        detail=f"Nao consegui determinar next-action hash pra /{route}. Sessao expirada?",
    )


# ============================================================
# RSC PARSER
# ============================================================

def _parse_rsc_response(text: str) -> dict:
    """Extrai JSON da resposta RSC (formato N:{json}\nN:{json}...).

    Procura a linha que contem 'results' ou 'data' — tipicamente linha 1.
    """
    if not text:
        raise HTTPException(status_code=502, detail="Resposta vazia do Social1")

    # Detectar pagina de login (deslogado)
    low = text.lower()
    if "<!doctype html" in low[:50] or "<html" in low[:50]:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "session_expired_or_invalid",
                "hint": "Social1 retornou HTML em vez de RSC — refazer login",
                "snippet": text[:300],
            },
        )

    for line in text.split("\n"):
        if not line or ":" not in line:
            continue
        prefix, _, body = line.partition(":")
        if not body.startswith("{") and not body.startswith("["):
            continue
        if "results" not in body and "\"data\"" not in body:
            continue
        try:
            parsed = json.loads(body)
            # Alguns Server Actions devolvem { data: {...} }, outros devolvem direto
            if isinstance(parsed, dict) and "data" in parsed:
                return parsed
            return {"data": parsed}
        except json.JSONDecodeError:
            continue

    # Fallback: tentar qualquer linha JSON parseavel
    for line in text.split("\n"):
        if not line or ":" not in line:
            continue
        _, _, body = line.partition(":")
        try:
            parsed = json.loads(body)
            return {"data": parsed}
        except json.JSONDecodeError:
            continue

    raise HTTPException(
        status_code=502,
        detail={
            "error": "rsc_parse_failed",
            "hint": "Formato RSC nao reconhecido",
            "snippet": text[:300],
        },
    )


# ============================================================
# CALL SERVER ACTION
# ============================================================

async def _call_products_action(
    page: int = 1,
    page_size: int = 20,
    days: int = 7,
    region: str = "us",
    search: Optional[str] = None,
    shop_id: Optional[str] = None,
    category: Optional[str] = None,
    sort: Optional[str] = None,
) -> dict:
    """Chama o Server Action de /products e retorna dict com data/results."""
    await _ensure_browser()
    action_hash = await _get_action_hash("products")

    # Social1 espera datas tsStart/tsEnd (janela)
    now = datetime.utcnow().date()
    ts_end = now.strftime("%Y-%m-%d")
    ts_start = (now - timedelta(days=days)).strftime("%Y-%m-%d")

    body = [{
        "page": page,
        "pageSize": page_size,
        "days": str(days),
        "tsStart": ts_start,
        "tsEnd": ts_end,
        "region": region.lower(),
        "shopId": shop_id or "$undefined",
        "category": category or "$undefined",
        "search": search or "$undefined",
        "sort": sort or "$undefined",
    }]

    js = f"""
    async () => {{
        const resp = await fetch(
            '{SOCIAL1_BASE}/products?tsStart={ts_start}&tsEnd={ts_end}',
            {{
                method: 'POST',
                credentials: 'include',
                headers: {{
                    'next-action': {json.dumps(action_hash)},
                    'Accept': 'text/x-component',
                    'Content-Type': 'text/plain;charset=UTF-8'
                }},
                body: {json.dumps(json.dumps(body))}
            }}
        );
        return {{ status: resp.status, text: await resp.text() }};
    }}
    """
    page_obj: Page = _state["page"]
    result = await page_obj.evaluate(js)
    _state["last_activity"] = time.time()

    if result["status"] == 404 and "next-action" in (result.get("text") or "").lower():
        # Hash invalidado — refresh e tenta de novo
        log.warning("Hash invalidado, forcando refresh e retry")
        _state["action_hashes"]["products"]["refreshed_at"] = 0
        action_hash = await _get_action_hash("products")
        # (a proxima chamada vai usar o novo hash; falhar aqui)

    if result["status"] != 200:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "upstream_error",
                "status": result["status"],
                "snippet": (result.get("text") or "")[:300],
            },
        )

    parsed = _parse_rsc_response(result["text"])
    return parsed


async def _call_creators_action(region: str = "us") -> dict:
    """Chama o Server Action de /creators e retorna dict com lista de creators."""
    await _ensure_browser()
    action_hash = await _get_action_hash("creators")

    body = [{"region": region.lower()}]

    js = f"""
    async () => {{
        const resp = await fetch(
            '{SOCIAL1_BASE}/creators',
            {{
                method: 'POST',
                credentials: 'include',
                headers: {{
                    'next-action': {json.dumps(action_hash)},
                    'Accept': 'text/x-component',
                    'Content-Type': 'text/plain;charset=UTF-8'
                }},
                body: {json.dumps(json.dumps(body))}
            }}
        );
        return {{ status: resp.status, text: await resp.text() }};
    }}
    """
    page_obj: Page = _state["page"]
    result = await page_obj.evaluate(js)
    _state["last_activity"] = time.time()

    if result["status"] != 200:
        if result["status"] == 404:
            log.warning("Hash /creators invalido, forcando refresh")
            _state["action_hashes"].setdefault("creators", {})["refreshed_at"] = 0
        raise HTTPException(
            status_code=502,
            detail={
                "error": "upstream_error",
                "status": result["status"],
                "snippet": (result.get("text") or "")[:300],
            },
        )

    return _parse_rsc_response(result["text"])


def _check_api_key(x_api_key: Optional[str], key_query: Optional[str]) -> None:
    provided = x_api_key or key_query
    if provided != API_KEY:
        raise HTTPException(status_code=401, detail="api key invalida ou ausente")


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(title="Social1 Proxy", version="1.0.0")

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
    _state["lock"] = asyncio.Lock()
    log.info(f"Social1 Proxy iniciando na porta {PORT}")
    try:
        await _ensure_browser()
        # Refresh inicial do hash em background (nao bloqueia boot)
        asyncio.create_task(_refresh_action_hash("products"))
    except Exception as e:
        log.warning(f"Boot do browser falhou: {e}")


@app.on_event("shutdown")
async def _shutdown():
    log.info("Encerrando...")
    try:
        if _state["context"]:
            await _state["context"].close()
        if _state["playwright"]:
            await _state["playwright"].stop()
    except Exception as e:
        log.warning(f"Erro no shutdown: {e}")


@app.get("/health")
async def health():
    hashes_info = {}
    for route, data in _state["action_hashes"].items():
        hashes_info[route] = {
            "hash_preview": (data.get("hash") or "")[:16] + "...",
            "age_seconds": int(time.time() - data.get("refreshed_at", 0)),
        }
    return {
        "status": "ok",
        "ready": _state["ready"],
        "page_url": _state["page"].url if _state["page"] else None,
        "last_activity_seconds_ago": int(time.time() - _state["last_activity"]) if _state["last_activity"] else None,
        "action_hashes": hashes_info,
    }


@app.get("/api/search")
async def search(
    keyword: Optional[str] = Query(None, description="Termo de busca"),
    region: str = Query("us", description="us, uk, br, de, fr, es, it, mx"),
    days: int = Query(7, ge=1, le=30),
    page: int = Query(1, ge=1, le=50),
    limit: int = Query(20, ge=1, le=50),
    shop_id: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    sort: Optional[str] = Query(None, description="ex: gmv, units_sold, views"),
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Busca produtos com filtros (ou top se keyword vazia)."""
    _check_api_key(x_api_key, key)
    return await _call_products_action(
        page=page,
        page_size=limit,
        days=days,
        region=region,
        search=keyword,
        shop_id=shop_id,
        category=category,
        sort=sort,
    )


@app.get("/api/products")
async def products(
    region: str = Query("us"),
    days: int = Query(7, ge=1, le=30),
    page: int = Query(1, ge=1, le=50),
    limit: int = Query(20, ge=1, le=50),
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Top products (alias de /api/search sem keyword)."""
    _check_api_key(x_api_key, key)
    return await _call_products_action(
        page=page, page_size=limit, days=days, region=region
    )


@app.get("/api/creators")
async def creators(
    region: str = Query("us", description="us, uk, br, de, fr, es, it, mx"),
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Top creators TikTok Shop por regiao (via Server Action)."""
    _check_api_key(x_api_key, key)
    return await _call_creators_action(region=region)


@app.post("/api/refresh-hash")
async def refresh_hash_endpoint(
    route: str = Query("products", description="products, videos, creators, shops"),
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    """Forca refresh do next-action hash (debug/admin)."""
    _check_api_key(x_api_key, key)
    new_hash = await _refresh_action_hash(route)
    return {
        "route": route,
        "refreshed": bool(new_hash),
        "hash_preview": (new_hash or "")[:16] + "..." if new_hash else None,
    }


if __name__ == "__main__":
    log.info(f"Social1 Proxy — porta {PORT}, user_data={USER_DATA_DIR}, headless={HEADLESS}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, access_log=False)
