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
    """Carrega o JSON mais recente da pasta resultados, filtrando ads sem qualidade"""
    files = sorted(glob.glob(f"{OUTPUT_DIR}/unified_*.json"), reverse=True)
    if not files:
        return []
    with open(files[0], "r", encoding="utf-8") as f:
        ads = json.load(f)

    # Filtrar ads com dados incompletos/templates
    clean = []
    for ad in ads:
        title = ad.get("title", "") or ""
        body = ad.get("body", "") or ""
        image = ad.get("image_url", "") or ""
        video = ad.get("video_url", "") or ""

        # Limpar templates do titulo
        if "{{" in title:
            ad["title"] = ""

        # Pular ads sem imagem nem video (nao tem valor visual)
        if not image and not video:
            continue

        ad["has_media"] = True
        clean.append(ad)

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
        ]
    }

@app.post("/api/analyze/{ad_id}")
def analyze_ad(ad_id: str):
    """Analisa um ad especifico com IA na hora e salva o resultado"""
    from openai import OpenAI

    AI_KEY = "sk-75b1ddd6be014170a52a790133025c07"
    AI_URL = "https://api.deepseek.com"

    # Encontrar o ad
    ads = load_latest_data()
    target = None
    target_idx = None
    for i, ad in enumerate(ads):
        if ad.get("ad_id") == ad_id:
            target = ad
            target_idx = i
            break

    if not target:
        return {"error": "Ad nao encontrado"}

    # Se ja tem analise, retorna direto
    if target.get("ai_niche"):
        return {
            "status": "already_analyzed",
            "ad_id": ad_id,
            "ai_niche": target.get("ai_niche"),
            "ai_target_audience": target.get("ai_target_audience"),
            "ai_strategy": target.get("ai_strategy"),
            "ai_hook_type": target.get("ai_hook_type"),
            "ai_product_type": target.get("ai_product_type"),
            "ai_copy_quality": target.get("ai_copy_quality"),
            "ai_urgency_level": target.get("ai_urgency_level"),
            "ai_emotion": target.get("ai_emotion"),
            "estimated_spend": target.get("estimated_spend"),
            "potential_score": target.get("potential_score"),
        }

    # Analisar com DeepSeek
    body = target.get("body", "") or ""
    title = target.get("title", "") or ""
    if not body and not title:
        return {"error": "Ad sem texto para analisar"}

    try:
        client = OpenAI(api_key=AI_KEY, base_url=AI_URL)

        prompt = f"""Analise este anúncio e retorne APENAS um JSON. Não invente dados.

Anúncio:
- Anunciante: {target.get('advertiser', '')}
- Plataforma: {target.get('platform', '')}
- Título: {title}
- Copy: {body[:500]}
- CTA: {target.get('cta', '')}
- Landing: {target.get('landing_page', '')}
- Dias rodando: {target.get('days_running', 0)}
- Curtidas: {target.get('likes', 0)}

JSON:
{{"niche":"string","target_audience":"string curta","strategy":"string curta","hook_type":"string","product_type":"string","copy_quality":"1-10","urgency_level":"1-10","emotion":"string","language":"string","summary":"resumo em 1 frase do que o anuncio vende e como"}}"""

        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.3,
        )
        text = response.choices[0].message.content.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        import json as json_lib
        analysis = json_lib.loads(text)

        # Salvar no ad
        target["ai_niche"] = analysis.get("niche", "")
        target["ai_target_audience"] = analysis.get("target_audience", "")
        target["ai_strategy"] = analysis.get("strategy", "")
        target["ai_hook_type"] = analysis.get("hook_type", "")
        target["ai_product_type"] = analysis.get("product_type", "")
        target["ai_copy_quality"] = analysis.get("copy_quality", 0)
        target["ai_urgency_level"] = analysis.get("urgency_level", 0)
        target["ai_emotion"] = analysis.get("emotion", "")
        target["ai_language"] = analysis.get("language", "")
        target["ai_summary"] = analysis.get("summary", "")

        # Salvar no arquivo
        files = sorted(glob.glob(f"{OUTPUT_DIR}/unified_*.json"), reverse=True)
        if files:
            with open(files[0], "r", encoding="utf-8") as f:
                all_ads = json.load(f)
            for j, a in enumerate(all_ads):
                if a.get("ad_id") == ad_id:
                    all_ads[j].update(target)
                    break
            with open(files[0], "w", encoding="utf-8") as f:
                json.dump(all_ads, f, ensure_ascii=False)

        return {
            "status": "analyzed",
            "ad_id": ad_id,
            **{k: v for k, v in analysis.items()},
        }

    except Exception as e:
        return {"error": str(e)}


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
