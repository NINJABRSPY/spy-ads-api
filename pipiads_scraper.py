"""
PiPiAds Scraper - TikTok Ads + TikTok Shop + Rankings
Requer Chrome com --remote-debugging-port=9222 e logado no PiPiAds
"""
from playwright.sync_api import sync_playwright
import json, time, csv, glob
from datetime import datetime
from pathlib import Path

KEYWORDS = [
    # Produtos dropshipping winners
    "dropshipping", "winning product", "viral product", "trending",
    "free shipping", "50% off", "buy now", "shop now", "order now",
    # Produtos populares TikTok
    "led lights", "posture corrector", "massage gun", "smart watch",
    "phone case", "wireless earbuds", "car accessories", "kitchen gadgets",
    "pet toys", "dog", "cat", "baby",
    "yoga mat", "water bottle", "backpack",
    "sunglasses", "jewelry", "necklace", "ring",
    "skincare", "face cream", "serum", "hair growth", "teeth whitening",
    "home decor", "led mirror", "organizer",
    "gaming", "mouse pad", "headset",
    "hoodie", "sneakers", "dress",
    # Nichos BR
    "frete gratis", "compre agora", "oferta",
    "creme facial", "maquiagem", "protetor solar",
    "suplemento", "whey", "creatina",
    "decoracao", "organizador", "luminaria",
    "roupa feminina", "tenis", "bolsa",
]


def normalize(ad, keyword, platform="tiktok"):
    """Normaliza ad do PiPiAds para schema unificado"""
    return {
        "ad_id": str(ad.get("id", ad.get("ad_id", ad.get("adId", "")))),
        "source": "pipiads",
        "platform": platform,
        "advertiser": ad.get("advertiser_name", ad.get("advertiserName", ad.get("name", ""))),
        "advertiser_image": ad.get("advertiser_logo", ad.get("logo", "")),
        "title": ad.get("title", ad.get("headline", ad.get("ad_title", ""))),
        "body": (ad.get("text", ad.get("body", ad.get("description", ad.get("ad_text", "")))) or "")[:200],
        "cta": ad.get("cta", ad.get("call_to_action", "")),
        "landing_page": ad.get("landing_page", ad.get("url", ad.get("link", ""))),
        "image_url": ad.get("thumbnail", ad.get("cover", ad.get("image", ad.get("preview", "")))),
        "video_url": ad.get("video_url", ad.get("videoUrl", ad.get("video", ""))),
        "first_seen": ad.get("first_seen", ad.get("firstSeen", ad.get("created_at", ad.get("start_date", "")))),
        "last_seen": ad.get("last_seen", ad.get("lastSeen", ad.get("updated_at", ad.get("end_date", "")))),
        "is_active": ad.get("is_active", ad.get("isActive", ad.get("status", "") == "active")),
        "likes": int(ad.get("likes", ad.get("like_count", ad.get("digg_count", 0))) or 0),
        "comments": int(ad.get("comments", ad.get("comment_count", 0)) or 0),
        "shares": int(ad.get("shares", ad.get("share_count", 0)) or 0),
        "impressions": int(ad.get("impressions", ad.get("views", ad.get("play_count", 0))) or 0),
        "total_engagement": int(ad.get("likes", 0) or 0) + int(ad.get("comments", 0) or 0) + int(ad.get("shares", 0) or 0),
        "days_running": int(ad.get("duration", ad.get("days_running", ad.get("days", 0))) or 0),
        "ad_type": "video" if ad.get("video_url", ad.get("videoUrl", ad.get("video"))) else "image",
        "country": ad.get("country", ad.get("region", "")),
        "channels": "tiktok",
        "has_media": True,
        "has_store": bool(ad.get("shop_url", ad.get("store_url", ad.get("landing_page", "")))),
        # PiPiAds exclusivos
        "pipi_ad_spend": ad.get("spend", ad.get("cost", ad.get("estimated_spend", 0))),
        "pipi_ctr": ad.get("ctr", 0),
        "pipi_cvr": ad.get("cvr", ad.get("conversion_rate", 0)),
        "pipi_category": ad.get("category", ad.get("industry", "")),
        "pipi_product_name": ad.get("product_name", ad.get("productName", "")),
        "pipi_product_price": ad.get("product_price", ad.get("price", "")),
        "pipi_shop_name": ad.get("shop_name", ad.get("shopName", "")),
        "pipi_ranking": ad.get("rank", ad.get("ranking", 0)),
        "search_keyword": keyword,
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def run():
    print("=" * 60)
    print("  PiPiAds Scraper")
    print(f"  {len(KEYWORDS)} keywords")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp('http://localhost:9222')
        context = browser.contexts[0]

        # Verificar se PiPiAds esta aberta
        page = None
        for pg in context.pages:
            if 'pipiads' in pg.url:
                page = pg
                break

        if not page:
            page = context.new_page()
            page.goto('https://www.pipiads.com/ad-search', wait_until='domcontentloaded', timeout=30000)
            time.sleep(5)

        print(f'URL: {page.url[:60]}')

        all_ads = []
        seen = set()
        api_patterns = ['api', 'search', 'ads', 'query', 'graphql', 'rpc']

        # Descobrir API primeiro
        discovered_apis = []
        def on_response_discover(resp):
            url = resp.url
            ct = resp.headers.get('content-type', '')
            if 'json' in ct and any(p in url for p in api_patterns) and 'pipiads' in url:
                try:
                    data = resp.json()
                    size = len(json.dumps(data))
                    if size > 200:
                        discovered_apis.append({'url': url, 'size': size, 'data': data})
                        path = url.split('pipiads.com')[1][:60] if 'pipiads.com' in url else url[:60]
                        print(f'  [API] {size:6d} bytes | {path}')
                except:
                    pass

        page.on('response', on_response_discover)

        # Navegar pelas paginas principais
        pages_to_visit = [
            ('Ad Search', 'https://www.pipiads.com/ad-search'),
            ('TikTok Ads', 'https://www.pipiads.com/ad-search?platform=tiktok'),
            ('Facebook Ads', 'https://www.pipiads.com/ad-search?platform=facebook'),
            ('Product Search', 'https://www.pipiads.com/product-search'),
            ('Ad Ranking', 'https://www.pipiads.com/ad-ranking'),
            ('TikTok Shop', 'https://www.pipiads.com/tiktok-shop'),
        ]

        print('\nFase 1: Descobrindo APIs...')
        for name, url in pages_to_visit:
            try:
                print(f'\n  -> {name}...')
                page.goto(url, timeout=20000, wait_until='domcontentloaded')
                time.sleep(5)
                for _ in range(3):
                    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    time.sleep(2)
            except:
                pass

        page.remove_listener('response', on_response_discover)

        print(f'\nAPIs descobertas: {len(discovered_apis)}')

        # Salvar APIs descobertas
        with open('resultados/pipiads_apis.json', 'w', encoding='utf-8') as f:
            json.dump([{'url': a['url'], 'size': a['size']} for a in discovered_apis], f, indent=2)

        # Analisar dados capturados
        for api in discovered_apis:
            data = api['data']
            if isinstance(data, dict):
                for key in ['data', 'list', 'items', 'ads', 'results', 'records']:
                    items = data.get(key, [])
                    if isinstance(items, list) and len(items) > 0:
                        print(f'\n  Encontrado {len(items)} items em "{key}"')
                        if isinstance(items[0], dict):
                            print(f'  Campos: {list(items[0].keys())[:15]}')
                        for item in items:
                            if isinstance(item, dict):
                                ad = normalize(item, 'discovery')
                                aid = ad['ad_id']
                                if aid and aid not in seen:
                                    seen.add(aid)
                                    all_ads.append(ad)

        print(f'\nFase 1 completa: {len(all_ads)} ads da descoberta')

        # Fase 2: Buscar por keywords
        print('\nFase 2: Buscando por keywords...')

        for idx, kw in enumerate(KEYWORDS):
            captured = []
            def on_resp(resp, c=captured):
                url = resp.url
                ct = resp.headers.get('content-type', '')
                if 'json' in ct and 'pipiads' in url:
                    try:
                        data = resp.json()
                        if isinstance(data, dict):
                            for key in ['data', 'list', 'items', 'ads', 'results']:
                                items = data.get(key, [])
                                if isinstance(items, list):
                                    c.extend([i for i in items if isinstance(i, dict)])
                    except:
                        pass

            page.on('response', on_resp)
            try:
                page.goto(f'https://www.pipiads.com/ad-search?keyword={kw}&platform=tiktok',
                          timeout=20000, wait_until='domcontentloaded')
                time.sleep(5)
                for _ in range(5):
                    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    time.sleep(2)
            except:
                pass

            page.remove_listener('response', on_resp)

            for item in captured:
                ad = normalize(item, kw)
                aid = ad['ad_id']
                if aid and aid not in seen:
                    seen.add(aid)
                    all_ads.append(ad)

            if captured:
                print(f'  [{idx+1:2d}/{len(KEYWORDS)}] {kw:25s} +{len(captured)} (uniq: {len(all_ads)})')

            time.sleep(1)

    # Salvar
    Path("resultados").mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")

    with open(f"resultados/pipiads_{ts}.json", "w", encoding="utf-8") as f:
        json.dump(all_ads, f, ensure_ascii=False)
    print(f'\nSalvo: resultados/pipiads_{ts}.json')

    # Merge com unified
    uf = sorted(glob.glob("resultados/unified_*.json"), reverse=True)
    if uf:
        with open(uf[0], "r", encoding="utf-8") as f:
            existing = json.load(f)
        ids = {a.get("ad_id", "") for a in existing if a.get("ad_id")}
        new = [a for a in all_ads if a.get("ad_id") and a["ad_id"] not in ids]
        with open(uf[0], "w", encoding="utf-8") as f:
            json.dump(existing + new, f, ensure_ascii=False)
        print(f'{len(new)} novos -> unified (total: {len(existing)+len(new)})')

    print(f'\nPiPiAds: {len(all_ads)} unicos')

    # AI Enrichment
    try:
        from ai_enricher import enrich_ads, AI_API_KEY
        if AI_API_KEY:
            print("\n--- AI Enrichment ---")
            enrich_ads(AI_API_KEY, max_ads=99999)
    except Exception as e:
        print(f"AI: {e}")


if __name__ == "__main__":
    run()
