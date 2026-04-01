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
# CACHE - Carrega dados uma vez, nao a cada request
# ============================================================
_cache = {"ads": None, "loaded_at": None, "file": None}

def load_latest_data():
    """Carrega dados com cache em memoria - so rele se arquivo mudou"""
    files = sorted(glob.glob(f"{OUTPUT_DIR}/unified_*.json"), reverse=True)
    if not files:
        return []

    latest = files[0]
    file_mtime = os.path.getmtime(latest)

    # Usar cache se mesmo arquivo e nao mudou
    if _cache["ads"] is not None and _cache["file"] == latest and _cache["loaded_at"] == file_mtime:
        return _cache["ads"]

    # Carregar e filtrar
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

# Campos essenciais para listagem (reduz payload ~80%)
COMPACT_FIELDS = [
    "ad_id", "source", "platform", "advertiser", "advertiser_image",
    "title", "body", "cta", "landing_page", "image_url", "video_url",
    "ad_type", "first_seen", "last_seen", "days_running",
    "likes", "comments", "shares", "impressions", "total_engagement",
    "heat", "potential_score", "estimated_spend", "country",
    "ai_niche", "ai_strategy", "ai_copy_quality", "ai_emotion",
    "also_on", "has_store", "store_daily_revenue", "search_keyword",
]

@app.get("/api/ads")
def list_ads(
    platform: str = Query(None, description="facebook, instagram, tiktok, google, linkedin"),
    source: str = Query(None, description="bigspy, adyntel_meta, adyntel_google, adyntel_linkedin, adyntel_tiktok"),
    keyword: str = Query(None, description="Filtrar por keyword de busca"),
    search: str = Query(None, description="Buscar no texto/titulo do anuncio"),
    niche: str = Query(None, description="Filtrar por nicho IA"),
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
        ads = [a for a in ads if
               sl in (a.get("title", "") or "").lower() or
               sl in (a.get("body", "") or "").lower() or
               sl in (a.get("advertiser", "") or "").lower()]
    if niche:
        ads = [a for a in ads if niche.lower() in (a.get("ai_niche", "") or "").lower()]
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
# HOOK BANK — Banco de ganchos validados
# ============================================================

def _build_hook_bank():
    """Extrai e classifica hooks (primeira frase) de todos os ads"""
    ads = load_latest_data()
    hooks = []
    seen = set()

    for ad in ads:
        body = (ad.get("body") or "").strip()
        title = (ad.get("title") or "").strip()

        # O hook e a primeira frase do body ou o titulo
        text = body or title
        if not text or len(text) < 10:
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

        "total_monitored": {
            "ads": len(ads),
            "affiliates": len(affiliates),
            "sources": 7,
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
# UNCLOAK ENGINE — Revelar criativos escondidos
# ============================================================

def _build_uncloak_data():
    """Cruza todas as fontes para encontrar criativos escondidos atras de catalogos"""
    ads = load_latest_data()

    # Agrupar por advertiser
    by_advertiser = {}
    for ad in ads:
        name = (ad.get("advertiser") or "").lower().strip()
        if not name or len(name) < 3:
            continue
        if name not in by_advertiser:
            by_advertiser[name] = []
        by_advertiser[name].append(ad)

    # Agrupar por dominio da landing page
    by_domain = {}
    for ad in ads:
        lp = ad.get("landing_page", "") or ""
        if not lp or len(lp) < 10:
            continue
        try:
            from urllib.parse import urlparse
            domain = urlparse(lp).hostname
            if domain:
                domain = domain.replace("www.", "")
                if domain not in by_domain:
                    by_domain[domain] = []
                by_domain[domain].append(ad)
        except:
            pass

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

        if not signals or cloaking_score < 20:
            continue

        cloaking_score = min(100, cloaking_score)

        # Selecionar ads representativos
        top_images = sorted(image_ads, key=lambda x: x.get("impressions", 0) or 0, reverse=True)[:3]
        top_videos = sorted(video_ads, key=lambda x: x.get("impressions", 0) or 0, reverse=True)[:3]

        # Domains
        domains = list(set(
            d.replace("www.", "") for a in ad_list
            for d in [urlparse(a.get("landing_page", "")).hostname or ""]
            if d and d != ""
        ))

        results.append({
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
        })

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
               any(q_lower in d for d in r.get("domains", []))]

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

    return {
        "total_products": len(products),
        "total_videos": len(videos),
        "total_creators": len(creators),
        "total_gmv": round(total_gmv, 2),
        "total_units_sold": total_units,
        "total_video_views": total_views,
        "videos_with_insights": with_insights,
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
# RUN
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"\n  Spy Ads API rodando em http://localhost:{port}")
    print(f"  Docs interativos em http://localhost:{port}/docs\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
