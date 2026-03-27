"""
Minea Scraper - Coleta via browser conectado
Requer Chrome com --remote-debugging-port=9222 e logado na Minea
"""
from playwright.sync_api import sync_playwright
import json, time, csv, glob
from datetime import datetime
from pathlib import Path

KEYWORDS = [
    "dropshipping", "skincare", "fitness", "ecommerce", "marketing digital",
    "curso online", "suplementos", "moda feminina", "pet shop", "infoproduto",
    "afiliado", "renda extra", "trafego pago", "loja virtual", "cosmeticos",
    "coaching", "mentoria", "investimentos", "saude", "emagrecimento",
]

def run():
    print("=" * 60)
    print("  Minea Scraper")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp('http://localhost:9222')
        context = browser.contexts[0]
        page = context.pages[0]

        print(f'URL: {page.url[:60]}')

        all_ads = []

        for idx, kw in enumerate(KEYWORDS):
            print(f'\n[{idx+1}/{len(KEYWORDS)}] "{kw}"...', end=' ', flush=True)

            captured = []
            def on_response(resp):
                if 'searchAds' in resp.url:
                    try:
                        data = resp.json()
                        items = data.get('json', {}).get('items', [])
                        captured.extend(items)
                    except:
                        pass

            page.on('response', on_response)

            try:
                page.goto(f'https://app.minea.com/pt/ads/meta-library?sort_by=-publication_date&query={kw}',
                          timeout=30000)
                time.sleep(5)

                # Scroll para carregar mais
                for _ in range(5):
                    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    time.sleep(3)

            except Exception as e:
                print(f'ERRO: {str(e)[:40]}')

            page.remove_listener('response', on_response)

            # Normalizar
            for item in captured:
                ad = normalize_minea(item, kw)
                if ad:
                    all_ads.append(ad)

            unique_new = len(set(a.get('ad_id','') for a in all_ads))
            print(f'{len(captured)} ads (total unico: {unique_new})')
            time.sleep(2)

    # Deduplicar
    seen = set()
    unique = []
    for ad in all_ads:
        if ad['ad_id'] and ad['ad_id'] not in seen:
            seen.add(ad['ad_id'])
            unique.append(ad)

    # Salvar
    Path("resultados").mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")

    with open(f"resultados/minea_{ts}.json", "w", encoding="utf-8") as f:
        json.dump(unique, f, ensure_ascii=False, indent=2)

    if unique:
        with open(f"resultados/minea_{ts}.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=unique[0].keys(), extrasaction='ignore')
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
        print(f'\n{len(new)} novos -> unified (total: {len(existing)+len(new)})')

    print(f'\nTotal: {len(all_ads)} coletados, {len(unique)} unicos')
    print(f'Salvo: resultados/minea_{ts}.json')


def normalize_minea(item, keyword):
    """Normaliza ad do Minea para schema unificado"""
    ad = item.get("ad", {})
    brand = item.get("brand", {})
    shop = item.get("shop", {})
    preview = item.get("preview", {})
    cards = item.get("ad_cards", [])

    # Pegar primeiro card
    card = cards[0] if cards else {}

    # Calcular receita mensal da loja
    monthly_visits = shop.get("monthly_visits", [])
    latest_visits = monthly_visits[-1].get("visits", 0) if monthly_visits else 0

    # Dias rodando
    days = ad.get("duration", 0) or 0

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
        "likes": 0,
        "comments": 0,
        "shares": 0,
        "impressions": 0,
        "total_engagement": 0,
        "days_running": days,
        "heat": 0,
        "ad_type": ad.get("media_type", "image"),
        "video_duration": 0,
        "country": shop.get("country", ""),
        "channels": "facebook",
        "has_media": bool(card.get("image_url") or card.get("video_url")),
        "has_store": bool(shop.get("url")),
        # EXCLUSIVOS MINEA - Dados do anunciante
        "brand_active_ads": brand.get("active_ads", 0),
        "brand_total_ads": brand.get("total_ads", 0),
        "brand_estimated_audience": brand.get("page_estimated_audience", 0),
        "brand_estimated_spend": brand.get("page_estimated_spend", 0),
        # EXCLUSIVOS MINEA - Dados da loja
        "store_url": shop.get("url", ""),
        "store_domain": shop.get("domain", ""),
        "store_created_at": shop.get("created_at", ""),
        "store_country": shop.get("country", ""),
        "store_products_listed": shop.get("products_listed", 0),
        "store_monthly_visits": latest_visits,
        "store_daily_revenue": shop.get("daily_revenue", 0),
        # Multiplos cards (carrossel)
        "total_cards": len(cards),
        "all_card_titles": " | ".join([c.get("title", "") for c in cards[:5]]),
        "search_keyword": keyword,
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


if __name__ == "__main__":
    run()
