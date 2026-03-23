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

try:
    from fastapi import FastAPI, Query
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn
except ImportError:
    print("Instalando dependencias...")
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "fastapi", "uvicorn"])
    from fastapi import FastAPI, Query
    from fastapi.middleware.cors import CORSMiddleware
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
# HELPERS
# ============================================================

def load_latest_data():
    """Carrega o JSON mais recente da pasta resultados"""
    files = sorted(glob.glob(f"{OUTPUT_DIR}/unified_*.json"), reverse=True)
    if not files:
        return []
    with open(files[0], "r", encoding="utf-8") as f:
        return json.load(f)

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

@app.get("/api/ads")
def list_ads(
    platform: str = Query(None, description="facebook, instagram, tiktok, google, linkedin"),
    source: str = Query(None, description="bigspy, adyntel_meta, adyntel_google, adyntel_linkedin, adyntel_tiktok"),
    keyword: str = Query(None, description="Filtrar por keyword de busca"),
    search: str = Query(None, description="Buscar no texto/titulo do anuncio"),
    sort: str = Query("collected_at", description="Campo para ordenar"),
    order: str = Query("desc", description="asc ou desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
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
        ads = [a for a in ads if keyword.lower() in (a.get("search_keyword", "") or "").lower()]

    if search:
        search_lower = search.lower()
        ads = [a for a in ads if
               search_lower in (a.get("title", "") or "").lower() or
               search_lower in (a.get("body", "") or "").lower() or
               search_lower in (a.get("advertiser", "") or "").lower()]

    # Ordenacao
    reverse = order == "desc"
    ads.sort(key=lambda x: x.get(sort, ""), reverse=reverse)

    # Paginacao
    total = len(ads)
    start = (page - 1) * limit
    end = start + limit
    page_ads = ads[start:end]

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
    return {
        "total_ads": len(ads),
        "last_sync": summary.get("scrape_date", ""),
        "by_source": summary.get("by_source", {}),
        "by_platform": summary.get("by_platform", {}),
        "top_keywords": dict(sorted(
            summary.get("by_keyword", {}).items(),
            key=lambda x: x[1], reverse=True
        )[:10]),
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
    """Busca livre nos anuncios"""
    ads = load_latest_data()
    q_lower = q.lower()
    results = [a for a in ads if
               q_lower in (a.get("title", "") or "").lower() or
               q_lower in (a.get("body", "") or "").lower() or
               q_lower in (a.get("advertiser", "") or "").lower() or
               q_lower in (a.get("cta", "") or "").lower()]
    return {"data": results, "total": len(results), "query": q}

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
        ]
    }

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
# RUN
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"\n  Spy Ads API rodando em http://localhost:{port}")
    print(f"  Docs interativos em http://localhost:{port}/docs\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
