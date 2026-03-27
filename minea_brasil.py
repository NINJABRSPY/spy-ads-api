"""
Minea Scraper - Focado Brasil
Keywords em portugues via Meta Library
"""
from playwright.sync_api import sync_playwright
import json, time, csv, glob
from datetime import datetime
from pathlib import Path

KEYWORDS_BR = [
    "ganhar dinheiro", "renda extra", "trabalhar em casa", "marketing digital",
    "trafego pago", "afiliado", "infoproduto", "curso online",
    "mentoria", "coaching", "dropshipping brasil", "loja virtual",
    "emagrecimento", "dieta", "suplemento", "skincare",
    "cosmeticos", "cabelo", "academia", "whey protein",
    "pet shop", "cachorro", "moda feminina", "vestido",
    "investimento", "cripto", "concurso publico", "ingles",
    "decoracao", "cozinha", "advogado", "psicologo",
]

def normalize(item, keyword):
    ad = item.get("ad", {})
    brand = item.get("brand", {})
    shop = item.get("shop", {})
    cards = item.get("ad_cards", [])
    card = cards[0] if cards else {}
    mv = shop.get("monthly_visits", [])
    lv = mv[-1].get("visits", 0) if mv else 0
    return {
        "ad_id": item.get("id", ""),
        "source": "minea",
        "platform": "facebook",
        "advertiser": brand.get("name", ""),
        "advertiser_image": brand.get("logo_url", ""),
        "facebook_page_id": brand.get("page_id", ""),
        "title": card.get("title", ""),
        "body": (card.get("ad_copy", "") or card.get("description", "") or "")[:800],
        "cta": card.get("cta_text", ""),
        "landing_page": card.get("link_url", ""),
        "image_url": card.get("image_url", ""),
        "video_url": card.get("video_url", ""),
        "first_seen": ad.get("start_date", ""),
        "last_seen": ad.get("end_date", ""),
        "is_active": ad.get("is_active", False),
        "likes": 0, "comments": 0, "shares": 0, "impressions": 0,
        "total_engagement": 0,
        "days_running": ad.get("duration", 0) or 0,
        "heat": 0,
        "ad_type": ad.get("media_type", "image"),
        "video_duration": 0,
        "country": shop.get("country", "BR"),
        "channels": "facebook",
        "has_media": bool(card.get("image_url") or card.get("video_url")),
        "has_store": bool(shop.get("url")),
        "brand_active_ads": brand.get("active_ads", 0),
        "brand_total_ads": brand.get("total_ads", 0),
        "brand_estimated_audience": brand.get("page_estimated_audience", 0),
        "brand_estimated_spend": round(brand.get("page_estimated_spend", 0) or 0, 2),
        "store_url": shop.get("url", ""),
        "store_domain": shop.get("domain", ""),
        "store_created_at": shop.get("created_at", ""),
        "store_country": shop.get("country", ""),
        "store_products_listed": shop.get("products_listed", 0),
        "store_monthly_visits": lv,
        "store_daily_revenue": round(shop.get("daily_revenue", 0) or 0, 2),
        "total_cards": len(cards),
        "search_keyword": keyword,
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

def run():
    print("=" * 60)
    print("  Minea BRASIL Scraper")
    print(f"  {len(KEYWORDS_BR)} keywords")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp('http://localhost:9222')
        context = browser.contexts[0]
        page = context.pages[0]

        all_ads = []
        seen = set()

        for idx, kw in enumerate(KEYWORDS_BR):
            captured = []
            def on_resp(resp, c=captured):
                if 'searchAds' in resp.url:
                    try:
                        items = resp.json().get('json', {}).get('items', [])
                        c.extend(items)
                    except: pass

            page.on('response', on_resp)
            try:
                page.goto(f'https://app.minea.com/pt/ads/meta-library?sort_by=-publication_date&query={kw}',
                          timeout=20000, wait_until='domcontentloaded')
                time.sleep(5)
                for _ in range(5):
                    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    time.sleep(2)

                # Tambem buscar por duracao e gasto
                for sort in ['-duration', '-estimated_spend']:
                    page.goto(f'https://app.minea.com/pt/ads/meta-library?sort_by={sort}&query={kw}',
                              timeout=20000, wait_until='domcontentloaded')
                    time.sleep(4)
                    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    time.sleep(2)
            except: pass

            page.remove_listener('response', on_resp)

            for item in captured:
                aid = item.get('id', '')
                if aid and aid not in seen:
                    seen.add(aid)
                    all_ads.append(normalize(item, kw))

            if captured:
                print(f'  [{idx+1:2d}/{len(KEYWORDS_BR)}] {kw:25s} +{len(captured)} (uniq: {len(all_ads)})')
            time.sleep(1)

    # Salvar
    Path("resultados").mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")

    with open(f"resultados/minea_brasil_{ts}.json", "w", encoding="utf-8") as f:
        json.dump(all_ads, f, ensure_ascii=False, indent=2)

    if all_ads:
        with open(f"resultados/minea_brasil_{ts}.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=all_ads[0].keys(), extrasaction='ignore')
            w.writeheader()
            w.writerows(all_ads)

    # Merge
    uf = sorted(glob.glob("resultados/unified_*.json"), reverse=True)
    if uf:
        with open(uf[0], "r", encoding="utf-8") as f:
            existing = json.load(f)
        ids = {a.get("ad_id","") for a in existing if a.get("ad_id")}
        new = [a for a in all_ads if a["ad_id"] not in ids]
        with open(uf[0], "w", encoding="utf-8") as f:
            json.dump(existing + new, f, ensure_ascii=False, indent=2)
        print(f'\n{len(new)} novos -> unified (total: {len(existing)+len(new)})')

    print(f'\nMinea Brasil: {len(all_ads)} unicos')

    # AI
    try:
        from ai_enricher import enrich_ads, AI_API_KEY
        if AI_API_KEY:
            print("\n--- AI Enrichment ---")
            enrich_ads(AI_API_KEY, max_ads=99999)
    except Exception as e:
        print(f"AI: {e}")

if __name__ == "__main__":
    run()
