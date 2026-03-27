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
    """Normaliza ad do PiPiAds para schema unificado - campos reais mapeados"""
    from datetime import datetime as dt

    # Converter timestamps Unix
    first_seen = ""
    last_seen = ""
    try:
        if ad.get("found_time"):
            first_seen = dt.fromtimestamp(ad["found_time"]).strftime("%Y-%m-%d")
        if ad.get("last_put_time"):
            last_seen = dt.fromtimestamp(ad["last_put_time"]).strftime("%Y-%m-%d")
    except: pass

    # Regioes
    regions = ad.get("fetch_region", [])
    if isinstance(regions, list):
        regions = ", ".join(regions)

    likes = int(ad.get("digg_count", 0) or 0)
    comments = int(ad.get("comment_count", 0) or 0)
    shares = int(ad.get("share_count", 0) or 0)

    return {
        "ad_id": str(ad.get("ad_id", "")),
        "source": "pipiads",
        "platform": "tiktok",
        "advertiser": ad.get("app_name", ad.get("unique_id", "")),
        "advertiser_image": ad.get("app_image", ""),
        "title": ad.get("app_title", ad.get("desc", ""))[:200] if ad.get("app_title") or ad.get("desc") else "",
        "body": (ad.get("desc", ad.get("app_title", "")) or "")[:200],
        "cta": ad.get("button_text", ""),
        "landing_page": "",
        "image_url": ad.get("cover", ""),
        "video_url": ad.get("video_url", ""),
        "first_seen": first_seen,
        "last_seen": last_seen,
        "is_active": True,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "impressions": int(ad.get("play_count", 0) or 0),
        "total_engagement": likes + comments + shares,
        "days_running": int(ad.get("put_days", 0) or 0),
        "heat": int(ad.get("hot_value", 0) or 0),
        "ad_type": "video",
        "video_duration": int(ad.get("duration", 0) or 0),
        "country": regions,
        "all_countries": regions,
        "channels": "tiktok",
        "has_media": True,
        "has_store": bool(ad.get("shop_id") or ad.get("store_id")),
        # EXCLUSIVOS PIPIADS
        "pipi_hook": ad.get("ai_analysis_main_hook", ""),
        "pipi_script": (ad.get("ai_analysis_script", "") or "")[:500],
        "pipi_tags": ad.get("ai_analysis_tags", []),
        "pipi_has_presenter": ad.get("ai_analysis_human_presenter", ""),
        "pipi_language": ad.get("ai_analysis_language", ""),
        "pipi_cpm": ad.get("min_cpm", 0),
        "pipi_cpa": ad.get("min_cpa", 0),
        "pipi_digg_play_rate": ad.get("digg_play_rate", 0),
        "pipi_share_play_rate": ad.get("share_play_rate", 0),
        "pipi_score": ad.get("_score", 0),
        "pipi_video_id": ad.get("video_id", ""),
        "estimated_spend": round((ad.get("min_cpm", 0) or 0) * (ad.get("play_count", 0) or 0) / 1000, 2) if ad.get("min_cpm") and ad.get("play_count") else 0,
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

        # Fase 2: Buscar por keywords usando interceptor JS
        print('\nFase 2: Buscando por keywords...')

        for idx, kw in enumerate(KEYWORDS):
            try:
                # Instalar interceptor
                page.evaluate('''() => { window._pipiSearch = []; }''')
                page.evaluate('''() => {
                    const origXHR = XMLHttpRequest.prototype.send;
                    XMLHttpRequest.prototype.send = function(body) {
                        this.addEventListener('load', function() {
                            if (this._url && this._url.includes('search')) {
                                try {
                                    const d = JSON.parse(this.responseText);
                                    if (d.result && d.result.data) {
                                        d.result.data.forEach(item => window._pipiSearch.push(item));
                                    }
                                } catch(e) {}
                            }
                        });
                        return origXHR.apply(this, arguments);
                    };
                }''')

                page.goto(f'https://www.pipiads.com/ad-search?keyword={kw}&platform=1&search_type=1',
                          timeout=20000, wait_until='domcontentloaded')
                time.sleep(6)

                for _ in range(3):
                    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    time.sleep(3)

                # Pegar dados capturados
                raw = page.evaluate('JSON.stringify(window._pipiSearch)')
                items = json.loads(raw) if raw else []

                for item in items:
                    ad = normalize(item, kw)
                    aid = ad['ad_id']
                    if aid and aid not in seen:
                        seen.add(aid)
                        all_ads.append(ad)

                if items:
                    print(f'  [{idx+1:2d}/{len(KEYWORDS)}] {kw:25s} +{len(items)} (uniq: {len(all_ads)})')

            except Exception as e:
                pass

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
