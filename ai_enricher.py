"""
AI Ad Enricher - Usa DeepSeek/OpenRouter para analisar ads e gerar insights
Adiciona: nicho, publico-alvo, estrategia, score de qualidade, estimativas
"""
import json
import time
import glob
from datetime import datetime
from pathlib import Path
from openai import OpenAI

# Configuracao - trocar pela key real
AI_API_KEY = "sk-75b1ddd6be014170a52a790133025c07"
AI_BASE_URL = "https://api.deepseek.com"
AI_MODEL = "deepseek-chat"

# CPM medios por plataforma (dados de mercado 2025-2026)
CPM_BY_PLATFORM = {
    "facebook": 9.0,
    "instagram": 11.0,
    "tiktok": 6.5,
    "google": 7.0,
    "linkedin": 35.0,
}

# RPV medio por nicho (Revenue Per Visit)
RPV_BY_NICHE = {
    "ecommerce": 2.50,
    "dropshipping": 1.80,
    "saas": 5.00,
    "infoproduto": 3.50,
    "cosmeticos": 3.00,
    "suplementos": 4.00,
    "moda": 2.20,
    "pet": 2.80,
    "fitness": 3.20,
    "default": 2.50,
}


def estimate_ad_spend(ad):
    """Estima gasto do anuncio baseado em metricas disponiveis"""
    platform = ad.get("platform", "facebook")
    cpm = CPM_BY_PLATFORM.get(platform, 9.0)
    impressions = ad.get("impressions", 0) or 0
    days = ad.get("days_running", 0) or 0

    if impressions > 0:
        # Formula: spend = impressions / 1000 * CPM
        estimated = (impressions / 1000) * cpm
        return round(estimated, 2)
    elif days > 0:
        # Se nao tem impressoes, estimar por duracao
        # Anunciante medio gasta ~$30-50/dia
        daily_budget = 30 if days < 30 else 50
        return round(days * daily_budget, 2)

    return 0


def estimate_revenue(ad):
    """Estima receita baseado em dados disponiveis"""
    # Se ja tem dados da Minea, usar
    if ad.get("store_daily_revenue", 0) > 0:
        return ad["store_daily_revenue"]

    # Se tem visitas mensais
    visits = ad.get("store_monthly_visits", 0)
    if visits > 0:
        keyword = ad.get("search_keyword", "").lower()
        rpv = RPV_BY_NICHE.get("default", 2.50)
        for niche, rate in RPV_BY_NICHE.items():
            if niche in keyword:
                rpv = rate
                break
        return round((visits / 30) * rpv, 2)

    return 0


def ai_analyze_ad(client, ad):
    """Usa IA para analisar um anuncio e gerar insights"""
    body = ad.get("body", "") or ""
    title = ad.get("title", "") or ""
    cta = ad.get("cta", "") or ""
    advertiser = ad.get("advertiser", "") or ""
    platform = ad.get("platform", "") or ""
    days = ad.get("days_running", 0) or 0
    likes = ad.get("likes", 0) or 0
    landing = ad.get("landing_page", "") or ""

    if not body and not title:
        return {}

    prompt = f"""Analise este anúncio e retorne APENAS um JSON com os campos abaixo. Não invente dados - baseie-se apenas no que está no anúncio.

Anúncio:
- Anunciante: {advertiser}
- Plataforma: {platform}
- Título: {title}
- Copy: {body[:400]}
- CTA: {cta}
- Landing page: {landing}
- Dias rodando: {days}
- Curtidas: {likes}

Retorne JSON:
{{
  "niche": "string - nicho do anúncio (ex: ecommerce, saúde, educação, finanças, beleza, pet, fitness, tecnologia, alimentação, moda, serviços)",
  "target_audience": "string curta - público-alvo provável (ex: mulheres 25-45 interessadas em skincare)",
  "strategy": "string curta - estratégia usada (ex: oferta direta, conteúdo educativo, prova social, escassez, storytelling)",
  "hook_type": "string - tipo de gancho (ex: pergunta, estatística, dor, curiosidade, benefício)",
  "product_type": "string - tipo de produto (ex: físico, digital, serviço, SaaS, curso)",
  "copy_quality": "number 1-10 - qualidade do copy",
  "urgency_level": "number 1-10 - nível de urgência/escassez",
  "emotion": "string - emoção principal (ex: medo, desejo, curiosidade, confiança, exclusividade)",
  "language": "string - idioma do anúncio (pt, en, es, etc)"
}}

APENAS o JSON, sem explicação."""

    try:
        response = client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.3,
        )
        text = response.choices[0].message.content.strip()
        # Limpar markdown se houver
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        return {"error": str(e)}


def enrich_ads(api_key, max_ads=100):
    """Enriquece ads com estimativas e analise de IA"""
    print("=" * 60)
    print("  AI Ad Enricher")
    print("=" * 60)

    # Carregar ads
    uf = sorted(glob.glob("resultados/unified_*.json"), reverse=True)
    if not uf:
        print("Nenhum arquivo unified encontrado!")
        return

    with open(uf[0], "r", encoding="utf-8") as f:
        ads = json.load(f)

    print(f"Total ads: {len(ads)}")

    # Configurar AI client
    client = None
    if api_key:
        client = OpenAI(api_key=api_key, base_url=AI_BASE_URL)
        print(f"AI: {AI_MODEL} via {AI_BASE_URL}")

    enriched_count = 0
    ai_count = 0

    for i, ad in enumerate(ads):
        # 1. Estimar gasto se nao tem
        if not ad.get("estimated_spend"):
            spend = estimate_ad_spend(ad)
            if spend > 0:
                ad["estimated_spend"] = spend
                ad["spend_source"] = "ninjaspy_estimate"

        # 2. Estimar receita se nao tem
        if not ad.get("estimated_daily_revenue"):
            rev = estimate_revenue(ad)
            if rev > 0:
                ad["estimated_daily_revenue"] = rev
                ad["revenue_source"] = "ninjaspy_estimate"
            elif ad.get("store_daily_revenue", 0) > 0:
                ad["estimated_daily_revenue"] = ad["store_daily_revenue"]
                ad["revenue_source"] = "minea"

        # 3. Calcular score de potencial
        likes = int(ad.get("likes", 0) or 0)
        comments = int(ad.get("comments", 0) or 0)
        days = int(ad.get("days_running", 0) or 0)
        impressions = int(ad.get("impressions", 0) or 0)

        score = 0
        if days > 30: score += 3
        elif days > 7: score += 2
        elif days > 0: score += 1
        if likes > 1000: score += 3
        elif likes > 100: score += 2
        elif likes > 10: score += 1
        if impressions > 100000: score += 3
        elif impressions > 10000: score += 2
        elif impressions > 1000: score += 1
        if ad.get("video_url"): score += 1

        ad["potential_score"] = min(score, 10)

        enriched_count += 1

        # 4. AI analysis (apenas para top ads e com limite)
        if client and ai_count < max_ads and ad.get("body") and len(ad["body"]) > 30:
            if ad.get("potential_score", 0) >= 3 or ad.get("days_running", 0) > 7:
                if not ad.get("ai_niche"):
                    analysis = ai_analyze_ad(client, ad)
                    if analysis and not analysis.get("error"):
                        ad["ai_niche"] = analysis.get("niche", "")
                        ad["ai_target_audience"] = analysis.get("target_audience", "")
                        ad["ai_strategy"] = analysis.get("strategy", "")
                        ad["ai_hook_type"] = analysis.get("hook_type", "")
                        ad["ai_product_type"] = analysis.get("product_type", "")
                        ad["ai_copy_quality"] = analysis.get("copy_quality", 0)
                        ad["ai_urgency_level"] = analysis.get("urgency_level", 0)
                        ad["ai_emotion"] = analysis.get("emotion", "")
                        ad["ai_language"] = analysis.get("language", "")
                        ai_count += 1

                        if ai_count % 10 == 0:
                            print(f"  AI analisou {ai_count}/{max_ads} ads...")

                    time.sleep(0.5)  # Rate limit

    # Salvar
    with open(uf[0], "w", encoding="utf-8") as f:
        json.dump(ads, f, ensure_ascii=False, indent=2)

    print(f"\nEnriquecidos: {enriched_count}")
    print(f"AI analisados: {ai_count}")
    print(f"Salvo: {uf[0]}")

    # Stats
    with_spend = len([a for a in ads if a.get("estimated_spend", 0) > 0])
    with_rev = len([a for a in ads if a.get("estimated_daily_revenue", 0) > 0])
    with_score = len([a for a in ads if a.get("potential_score", 0) >= 5])
    with_ai = len([a for a in ads if a.get("ai_niche")])
    print(f"\n  Com gasto estimado: {with_spend}")
    print(f"  Com receita estimada: {with_rev}")
    print(f"  Score >= 5 (alto potencial): {with_score}")
    print(f"  Com analise IA: {with_ai}")


if __name__ == "__main__":
    import sys
    key = sys.argv[1] if len(sys.argv) > 1 else ""
    enrich_ads(key, max_ads=200)
