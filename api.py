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

def _ai_call(prompt, max_tokens=800):
    client = _get_ai_client()
    r = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens, temperature=0.3,
    )
    text = r.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
    import json as jl
    return jl.loads(text)


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

        analysis = _ai_call(f"""Voce e um estrategista de marketing senior. Analise este anuncio em profundidade.
Retorne APENAS JSON valido.

ANUNCIO:
- Anunciante: {target.get('advertiser', '')}
- Plataforma: {target.get('platform', '')}
- Titulo: {title}
- Copy: {body[:600]}
- CTA: {target.get('cta', '')}
- Landing page: {target.get('landing_page', '')}
- Tipo: {target.get('ad_type', '')}
- Dias rodando: {target.get('days_running', 0)}
- Curtidas: {target.get('likes', 0)}
- Comentarios: {target.get('comments', 0)}
- Impressoes: {target.get('impressions', 0)}
- Paises: {target.get('all_countries', target.get('country', ''))}

JSON:
{{
  "niche": "nicho especifico",
  "target_audience": "descricao detalhada do publico-alvo",
  "persona": {{
    "gender": "homem/mulher/ambos",
    "age_range": "25-34",
    "interests": ["interesse1", "interesse2"],
    "pain_points": ["dor1", "dor2"],
    "income_level": "baixa/media/alta",
    "profile": "descricao em 1 frase da persona"
  }},
  "creative_brief": {{
    "objective": "objetivo da campanha",
    "angle": "angulo de abordagem",
    "value_proposition": "proposta de valor",
    "differentiator": "diferencial competitivo"
  }},
  "psychology": {{
    "triggers": ["gatilho1", "gatilho2"],
    "copy_framework": "PAS/AIDA/BAB/FAB/outro",
    "hook_type": "pergunta/estatistica/dor/curiosidade/beneficio",
    "emotion": "emocao principal",
    "urgency_level": 7,
    "social_proof_used": true
  }},
  "strategy": "descricao da estrategia em 2 frases",
  "product_type": "fisico/digital/servico/SaaS/curso",
  "copy_quality": 8,
  "estimated_ai_prompts": {{
    "image_prompt": "prompt provavel se a imagem foi gerada por IA",
    "copy_prompt": "prompt provavel para gerar copy similar"
  }},
  "recommendations": ["recomendacao1 para melhorar", "recomendacao2"],
  "summary": "resumo executivo em 2 frases",
  "language": "pt/en/es"
}}""")

        # Salvar todos os campos
        updates = {
            "ai_niche": analysis.get("niche", ""),
            "ai_target_audience": analysis.get("target_audience", ""),
            "ai_strategy": analysis.get("strategy", ""),
            "ai_hook_type": analysis.get("psychology", {}).get("hook_type", ""),
            "ai_product_type": analysis.get("product_type", ""),
            "ai_copy_quality": analysis.get("copy_quality", 0),
            "ai_urgency_level": analysis.get("psychology", {}).get("urgency_level", 0),
            "ai_emotion": analysis.get("psychology", {}).get("emotion", ""),
            "ai_language": analysis.get("language", ""),
            "ai_summary": analysis.get("summary", ""),
            "ai_persona": analysis.get("persona", {}),
            "ai_creative_brief": analysis.get("creative_brief", {}),
            "ai_psychology": analysis.get("psychology", {}),
            "ai_prompts": analysis.get("estimated_ai_prompts", {}),
            "ai_recommendations": analysis.get("recommendations", []),
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
