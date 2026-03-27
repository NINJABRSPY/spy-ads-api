"""
Minea API Client
Coleta ads do Minea (Facebook, TikTok, Pinterest) + dados de loja + fornecedores
"""

import json
import time
import csv
import glob
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright


def scrape_minea_via_browser(keywords, max_scroll=20):
    """
    Conecta ao Chrome com debugging e coleta ads do Minea.
    Requer: Chrome aberto com --remote-debugging-port=9222 e logado no Minea.
    """
    print("=" * 60)
    print("  Minea Scraper")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp('http://localhost:9222')
        context = browser.contexts[0]

        # Verificar se Minea esta aberta
        minea_page = None
        for pg in context.pages:
            if 'minea' in pg.url:
                minea_page = pg
                break

        if not minea_page:
            minea_page = context.new_page()
            minea_page.goto('https://app.minea.com/ads/facebook', wait_until='networkidle', timeout=30000)
            time.sleep(3)

        print(f'Pagina: {minea_page.url[:60]}')

        all_ads = []
        api_data = []

        # Interceptar respostas da API
        def on_response(resp):
            url = resp.url
            if 'minea' in url and ('api' in url or 'graphql' in url or 'ads' in url):
                try:
                    ct = resp.headers.get('content-type', '')
                    if 'json' in ct:
                        data = resp.json()
                        api_data.append({'url': url, 'data': data})
                except:
                    pass

        minea_page.on('response', on_response)

        for kw in keywords:
            print(f'\n[Minea] Buscando: {kw}')

            # Navegar para busca
            try:
                # Tentar preencher campo de busca
                minea_page.fill('input[placeholder*="Search"], input[type="search"], input[name="search"]', kw, timeout=5000)
                time.sleep(1)
                minea_page.keyboard.press('Enter')
                time.sleep(5)
            except:
                # Se nao achar input, tentar navegar por URL
                try:
                    minea_page.goto(f'https://app.minea.com/ads/facebook?search={kw}', wait_until='networkidle', timeout=20000)
                    time.sleep(3)
                except:
                    print(f'  Nao conseguiu buscar {kw}')
                    continue

            # Scrollar para carregar mais
            print(f'  Scrollando...')
            last_count = len(api_data)
            for i in range(max_scroll):
                minea_page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                time.sleep(2)
                if len(api_data) > last_count:
                    print(f'    Scroll {i+1}: {len(api_data)} API responses')
                    last_count = len(api_data)
                elif i > 5:
                    break

            # Extrair do DOM tambem
            dom_ads = minea_page.evaluate("""() => {
                const cards = document.querySelectorAll('[class*="card"], [class*="ad-"], [class*="item"]');
                const results = [];
                cards.forEach(card => {
                    try {
                        const text = card.innerText || '';
                        if (text.length > 30) {
                            const imgs = card.querySelectorAll('img');
                            let imgUrl = '';
                            imgs.forEach(img => {
                                const src = img.src || '';
                                if (src && src.startsWith('http') && !src.includes('avatar') && !src.includes('logo'))
                                    imgUrl = imgUrl || src;
                            });
                            const videos = card.querySelectorAll('video');
                            let vidUrl = '';
                            videos.forEach(v => { vidUrl = vidUrl || v.src || ''; });

                            const links = card.querySelectorAll('a');
                            let link = '';
                            links.forEach(a => {
                                if (a.href && a.href.startsWith('http') && !a.href.includes('minea'))
                                    link = link || a.href;
                            });

                            results.push({
                                text: text.substring(0, 500),
                                image: imgUrl,
                                video: vidUrl,
                                link: link,
                            });
                        }
                    } catch(e) {}
                });
                return results;
            }""")

            print(f'  DOM cards: {len(dom_ads)}')
            print(f'  API responses: {len(api_data)}')

            # Processar dados da API
            for item in api_data:
                data = item['data']
                # Minea pode retornar em varios formatos
                ads_list = []
                if isinstance(data, list):
                    ads_list = data
                elif isinstance(data, dict):
                    for key in ['data', 'ads', 'results', 'items', 'edges', 'nodes']:
                        if key in data:
                            val = data[key]
                            if isinstance(val, list):
                                ads_list = val
                                break
                            elif isinstance(val, dict) and 'edges' in val:
                                ads_list = [e.get('node', e) for e in val['edges']]
                                break

                for ad in ads_list:
                    if isinstance(ad, dict):
                        all_ads.append(normalize_minea_ad(ad, kw))

            api_data.clear()
            time.sleep(2)

        minea_page.remove_listener('response', on_response)

    return all_ads


def normalize_minea_ad(ad, keyword):
    """Normaliza ad do Minea para schema unificado"""
    # Minea pode ter diferentes estruturas - tentar varias
    return {
        "ad_id": str(ad.get("id", ad.get("_id", ad.get("adId", "")))),
        "source": "minea",
        "platform": ad.get("platform", ad.get("network", "facebook")).lower(),
        "advertiser": ad.get("pageName", ad.get("page_name", ad.get("advertiser", ""))),
        "advertiser_image": ad.get("pageImage", ad.get("page_image", "")),
        "title": ad.get("title", ad.get("headline", "")),
        "body": (ad.get("body", ad.get("text", ad.get("description", ""))) or "")[:800],
        "cta": ad.get("cta", ad.get("callToAction", "")),
        "landing_page": ad.get("landingPage", ad.get("link", ad.get("url", ""))),
        "image_url": ad.get("image", ad.get("thumbnail", ad.get("mediaUrl", ""))),
        "video_url": ad.get("video", ad.get("videoUrl", "")),
        "first_seen": ad.get("firstSeen", ad.get("createdAt", ad.get("startDate", ""))),
        "last_seen": ad.get("lastSeen", ad.get("updatedAt", "")),
        "is_active": ad.get("isActive", ad.get("active", True)),
        "likes": int(ad.get("likes", ad.get("reactions", 0)) or 0),
        "comments": int(ad.get("comments", ad.get("commentCount", 0)) or 0),
        "shares": int(ad.get("shares", ad.get("shareCount", 0)) or 0),
        "impressions": int(ad.get("impressions", ad.get("reach", 0)) or 0),
        "total_engagement": int(ad.get("likes", 0) or 0) + int(ad.get("comments", 0) or 0) + int(ad.get("shares", 0) or 0),
        "days_running": int(ad.get("daysRunning", ad.get("duration", 0)) or 0),
        "heat": 0,
        "ad_type": "video" if ad.get("video", ad.get("videoUrl")) else "image",
        "video_duration": 0,
        "country": ad.get("country", ad.get("geo", "")),
        "all_countries": ad.get("countries", ""),
        "channels": ad.get("platform", "facebook"),
        # Minea exclusivos
        "store_url": ad.get("storeUrl", ad.get("shopUrl", "")),
        "store_platform": ad.get("storePlatform", ad.get("ecommerce", "")),
        "supplier_url": ad.get("supplierUrl", ad.get("aliexpressUrl", "")),
        "product_price": ad.get("price", ad.get("productPrice", "")),
        "spending": ad.get("spending", ad.get("adSpend", "")),
        "is_winning": ad.get("isWinning", ad.get("trending", False)),
        "has_media": True,
        "has_store": bool(ad.get("storeUrl", ad.get("shopUrl"))),
        "search_keyword": keyword,
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def save_and_merge(ads, prefix="minea"):
    """Salva resultados e merge com unified"""
    if not ads:
        print("Nenhum ad coletado!")
        return

    # Deduplicar
    seen = set()
    unique = []
    for ad in ads:
        key = ad['ad_id']
        if key and key not in seen:
            seen.add(key)
            unique.append(ad)

    Path("resultados").mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")

    # JSON
    json_path = f"resultados/{prefix}_{ts}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(unique, f, ensure_ascii=False, indent=2)

    # CSV
    csv_path = f"resultados/{prefix}_{ts}.csv"
    if unique:
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=unique[0].keys())
            w.writeheader()
            w.writerows(unique)

    # Merge com unified
    uf = sorted(glob.glob("resultados/unified_*.json"), reverse=True)
    if uf:
        with open(uf[0], "r", encoding="utf-8") as f:
            existing = json.load(f)
        ids = {a.get("ad_id","") for a in existing if a.get("ad_id")}
        new = [a for a in unique if a["ad_id"] not in ids]
        with open(uf[0], "w", encoding="utf-8") as f:
            json.dump(existing + new, f, ensure_ascii=False, indent=2)
        print(f"{len(new)} novos ads -> unified (total: {len(existing)+len(new)})")

    print(f"Total: {len(ads)} coletados, {len(unique)} unicos")
    print(f"Salvo: {json_path}")
    return unique


if __name__ == "__main__":
    KEYWORDS = [
        "dropshipping", "skincare", "fitness", "ecommerce",
        "pet shop", "jewelry", "gadgets", "home decor",
        "beauty", "clothing",
    ]

    ads = scrape_minea_via_browser(KEYWORDS, max_scroll=10)
    save_and_merge(ads)
