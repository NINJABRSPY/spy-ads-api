"""
BigSpy + Minea - Scraper focado no mercado BRASILEIRO
Keywords em portugues + filtros BR
"""
import json, csv, time, glob, requests
from datetime import datetime, timedelta
from pathlib import Path
from config import BIGSPY_JWT, BIGSPY_DEVICE_ID

# Keywords BR
KEYWORDS_BR = [
    # Infoprodutos
    "ganhar dinheiro", "renda extra", "trabalhar em casa", "marketing digital",
    "trafego pago", "afiliado hotmart", "infoproduto", "curso online",
    "mentoria", "coaching", "lançamento digital",
    # E-commerce
    "dropshipping brasil", "loja virtual", "shopee", "mercado livre",
    "shopify brasil", "ecommerce brasil",
    # Saude/Beleza
    "emagrecimento", "emagrecer rapido", "dieta", "suplemento",
    "skincare brasil", "cosmeticos", "cabelo", "unhas",
    # Fitness
    "academia", "treino em casa", "whey protein", "creatina",
    # Pet
    "pet shop", "racao", "cachorro", "gato",
    # Moda
    "moda feminina", "roupa feminina", "vestido", "tenis",
    # Financeiro
    "investimento", "renda fixa", "cripto", "bitcoin brasil",
    # Educacao
    "concurso publico", "enem", "ingles online", "programacao",
    # Casa
    "decoracao", "organizacao", "moveis", "cozinha",
    # Servicos
    "advogado", "contabilidade", "dentista", "psicologo online",
]

PLATFORMS = ["facebook", "instagram", "tiktok"]
MAX_PAGES = 5

def bigspy_search(keyword, platform, page=1):
    now = datetime.now()
    body = {
        "page": page,
        "keyword": [keyword],
        "search_type": 1,
        "sort_field": "-first_seen",
        "seen_begin": int((now - timedelta(days=30)).timestamp()),
        "seen_end": int(now.timestamp()),
        "page_size": 60,
        "app_type": "3",
        "platform": [platform],
        "is_first": page == 1,
    }
    headers = {
        "authorization": BIGSPY_JWT,
        "x-device-id": BIGSPY_DEVICE_ID,
        "x-timezone": "-0300",
        "content-type": "application/json",
        "user-agent": "Mozilla/5.0",
    }
    try:
        r = requests.post("https://bigspy.com/napi/v1/creative/list",
                          headers=headers, json=body, timeout=30)
        data = r.json()
        if data.get("id") != "SUCCESS":
            return [], data.get("remain_req_count", "?")
        ads = data.get("data", {}).get("creative_list", [])
        return ads, data.get("remain_req_count", "?")
    except Exception as e:
        return [], "?"

def normalize(ad, keyword):
    image_url = ""
    video_url = ""
    for res in (ad.get("resource_urls") or []):
        if res.get("image_url") and not image_url:
            image_url = res.get("image_url", "")
        if res.get("video_url") and not video_url:
            video_url = res.get("video_url", "")
    channels = ad.get("fb_merge_channel", [])
    if isinstance(channels, list):
        channels = ", ".join(channels)
    return {
        "ad_id": ad.get("ad_key", ""),
        "source": "bigspy",
        "platform": ad.get("platform", ""),
        "advertiser": ad.get("page_name", ad.get("advertiser_name", "")),
        "title": ad.get("title", ""),
        "body": (ad.get("body", "") or ad.get("message", "") or "")[:800],
        "cta": ad.get("call_to_action", ""),
        "landing_page": ad.get("link_url", "") or ad.get("landing_page_url", "") or ad.get("destination_url", "") or ad.get("website_url", "") or "",
        "image_url": image_url or ad.get("preview_img_url", ""),
        "video_url": video_url,
        "first_seen": datetime.fromtimestamp(ad["first_seen"]).strftime("%Y-%m-%d") if ad.get("first_seen") else "",
        "last_seen": datetime.fromtimestamp(ad["last_seen"]).strftime("%Y-%m-%d") if ad.get("last_seen") else "",
        "is_active": True,
        "likes": int(ad.get("like_count", 0) or 0),
        "comments": int(ad.get("comment_count", 0) or 0),
        "shares": int(ad.get("share_count", 0) or 0),
        "impressions": int(ad.get("impression", 0) or 0),
        "total_engagement": int(ad.get("like_count", 0) or 0) + int(ad.get("comment_count", 0) or 0) + int(ad.get("share_count", 0) or 0),
        "days_running": int(ad.get("days_count", 0) or 0),
        "heat": int(ad.get("heat", 0) or 0),
        "ad_type": "video" if video_url else "image",
        "video_duration": int(ad.get("video_duration", 0) or 0),
        "channels": channels,
        "has_store": bool(ad.get("has_store_url", False)),
        "has_media": bool(image_url or video_url),
        "country": "BR",
        "search_keyword": keyword,
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

def run():
    print("=" * 60)
    print("  BigSpy BRASIL Scraper")
    print(f"  {len(KEYWORDS_BR)} keywords x {len(PLATFORMS)} plataformas x {MAX_PAGES} paginas")
    print("=" * 60)

    all_ads = []
    seen = set()

    for idx, kw in enumerate(KEYWORDS_BR):
        for plat in PLATFORMS:
            for page in range(1, MAX_PAGES + 1):
                ads, remain = bigspy_search(kw, plat, page)
                if not ads:
                    break
                for ad in ads:
                    n = normalize(ad, kw)
                    if n["ad_id"] not in seen:
                        seen.add(n["ad_id"])
                        all_ads.append(n)
                if page == 1:
                    print(f'  [{idx+1:2d}/{len(KEYWORDS_BR)}] {kw:25s} {plat:10s} {len(ads)} ads (restam: {remain})')
                if len(ads) < 60:
                    break
                time.sleep(3)

    # Salvar
    Path("resultados").mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")

    with open(f"resultados/bigspy_brasil_{ts}.json", "w", encoding="utf-8") as f:
        json.dump(all_ads, f, ensure_ascii=False, indent=2)

    if all_ads:
        with open(f"resultados/bigspy_brasil_{ts}.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=all_ads[0].keys(), extrasaction='ignore')
            w.writeheader()
            w.writerows(all_ads)

    # Merge com unified
    uf = sorted(glob.glob("resultados/unified_*.json"), reverse=True)
    if uf:
        with open(uf[0], "r", encoding="utf-8") as f:
            existing = json.load(f)
        ids = {a.get("ad_id","") for a in existing if a.get("ad_id")}
        new = [a for a in all_ads if a["ad_id"] not in ids]
        with open(uf[0], "w", encoding="utf-8") as f:
            json.dump(existing + new, f, ensure_ascii=False, indent=2)
        print(f'\n{len(new)} novos -> unified (total: {len(existing)+len(new)})')

    print(f'\nBrasil: {len(all_ads)} unicos')
    print(f'Salvo: resultados/bigspy_brasil_{ts}.json')

    # AI Enrichment automatico
    print("\n--- AI Enrichment ---")
    try:
        from ai_enricher import enrich_ads, AI_API_KEY
        if AI_API_KEY:
            enrich_ads(AI_API_KEY, max_ads=99999)
    except Exception as e:
        print(f"AI erro: {e}")

if __name__ == "__main__":
    run()
