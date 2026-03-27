"""
Minea MAX Dropshipping Scraper
Foco total em produtos e nichos de dropshipping
"""
from playwright.sync_api import sync_playwright
import json, time, csv, glob
from datetime import datetime
from pathlib import Path

# Keywords massivas de dropshipping/e-commerce
KEYWORDS = [
    # Produtos winners classicos
    "free shipping", "50% off", "buy now", "limited time", "order now",
    "shop now", "get yours", "best seller", "trending product", "viral product",
    # Nichos dropshipping
    "dropshipping", "shopify store", "online store", "ecommerce",
    "winning product", "product review", "unboxing",
    # Produtos populares
    "led lights", "posture corrector", "massage gun", "smart watch",
    "phone case", "wireless earbuds", "car accessories", "kitchen gadgets",
    "pet toys", "dog leash", "cat bed", "baby monitor",
    "yoga mat", "resistance bands", "water bottle", "backpack",
    "sunglasses", "jewelry", "necklace", "ring", "bracelet",
    "skincare", "face cream", "serum", "hair growth", "teeth whitening",
    "home decor", "wall art", "led mirror", "organizer", "storage",
    "camping gear", "fishing", "hiking", "outdoor",
    "gaming accessories", "mouse pad", "keyboard", "headset",
    "clothes", "hoodie", "sneakers", "dress", "activewear",
    # Nichos BR
    "frete gratis", "compre agora", "oferta", "promocao",
    "produto viral", "tendencia", "lançamento",
    "creme facial", "protetor solar", "maquiagem",
    "decoracao casa", "organizador", "luminaria",
    "roupa feminina", "tenis", "bolsa", "relogio",
    "suplemento", "whey", "creatina", "colageno",
    "brinquedo", "cachorro", "gato", "pet",
    # High ticket
    "electric bike", "standing desk", "air purifier", "robot vacuum",
    "projector", "drone", "3d printer", "espresso machine",
]

SORTS = ["-publication_date", "-duration", "-estimated_spend"]

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
        "body": (card.get("ad_copy", "") or card.get("description", "") or "")[:200],
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
        "ad_type": ad.get("media_type", "image"),
        "country": shop.get("country", ""),
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
    print("  Minea DROPSHIPPING MAX Scraper")
    print(f"  {len(KEYWORDS)} keywords x {len(SORTS)} sorts")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp('http://localhost:9222')
        context = browser.contexts[0]
        page = context.pages[0]

        all_ads = []
        seen = set()

        for idx, kw in enumerate(KEYWORDS):
            for sort in SORTS:
                captured = []
                def on_resp(resp, c=captured):
                    if 'searchAds' in resp.url:
                        try:
                            items = resp.json().get('json', {}).get('items', [])
                            c.extend(items)
                        except: pass

                page.on('response', on_resp)
                try:
                    page.goto(f'https://app.minea.com/pt/ads/meta-library?sort_by={sort}&query={kw}',
                              timeout=20000, wait_until='domcontentloaded')
                    time.sleep(4)
                    for _ in range(5):
                        page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                        time.sleep(2)
                except: pass

                page.remove_listener('response', on_resp)

                for item in captured:
                    aid = item.get('id', '')
                    if aid and aid not in seen:
                        seen.add(aid)
                        all_ads.append(normalize(item, kw))

                sn = sort.replace('-','')[:8]
                if captured:
                    print(f'  [{idx+1:2d}/{len(KEYWORDS)}] {kw:25s} {sn:8s} +{len(captured):2d} (uniq: {len(all_ads)})')
                time.sleep(1)

    # Salvar
    Path("resultados").mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")

    with open(f"resultados/minea_drop_{ts}.json", "w", encoding="utf-8") as f:
        json.dump(all_ads, f, ensure_ascii=False)

    # Merge com unified
    uf = sorted(glob.glob("resultados/unified_*.json"), reverse=True)
    if uf:
        with open(uf[0], "r", encoding="utf-8") as f:
            existing = json.load(f)
        ids = {a.get("ad_id","") for a in existing if a.get("ad_id")}
        new = [a for a in all_ads if a["ad_id"] not in ids]
        with open(uf[0], "w", encoding="utf-8") as f:
            json.dump(existing + new, f, ensure_ascii=False)
        print(f'\n{len(new)} novos -> unified (total: {len(existing)+len(new)})')

    ws = len([a for a in all_ads if a.get('store_url')])
    wr = len([a for a in all_ads if (a.get('store_daily_revenue') or 0) > 0])
    print(f'Minea Drop: {len(all_ads)} unicos | Com loja: {ws} | Com receita: {wr}')

    # AI Enrichment
    try:
        from ai_enricher import enrich_ads, AI_API_KEY
        if AI_API_KEY:
            print("\n--- AI Enrichment (novos ads) ---")
            enrich_ads(AI_API_KEY, max_ads=99999)
    except Exception as e:
        print(f"AI: {e}")

if __name__ == "__main__":
    run()
