"""
PiPiAds Auto Scraper - API direta com paginacao
"""
import json, time, csv, glob, requests
from datetime import datetime
from pathlib import Path

TOKEN = "NjljNWQ5ZDhmZTJkN2YzOGZkZjcxNWQ3LTE3NzQ1NzUzMTI="
DEVICE_ID = "887465847"
COOKIE = "uid=NjljNWQ5ZDhmZTJkN2YzOGZkZjcxNWQ3LTI5NTc2OTM0; language=pt; langTip=1"

KEYWORDS = [
    # Ecommerce / Dropshipping
    "dropshipping", "winning product", "viral product", "trending product",
    "free shipping", "50% off", "buy now", "shop now", "order now",
    "tiktok made me buy it", "must have", "life hack",
    # Electronics
    "led lights", "posture corrector", "massage gun", "smart watch",
    "phone case", "wireless earbuds", "kitchen gadgets", "car accessories",
    "robot vacuum", "drone", "projector", "electric bike",
    # Health / Supplements (DR)
    "weight loss", "belly fat burner", "keto supplement",
    "blood sugar support", "prostate supplement",
    "testosterone booster", "joint pain relief",
    "brain supplement", "memory boost", "sleep aid",
    "gut health", "probiotic", "liver detox",
    "collagen", "anti aging", "hair growth serum",
    "teeth whitening", "nail fungus treatment",
    # Beauty
    "skincare", "face cream", "serum", "dark spots",
    "wrinkle cream", "vitamin c serum",
    # Home
    "home decor", "led mirror", "organizer", "wall art",
    "garden tools", "cleaning hack",
    # Fashion
    "hoodie", "sneakers", "dress", "activewear",
    "jewelry", "necklace", "sunglasses",
    # Pets
    "dog training", "pet toys", "cat", "dog food",
    # Gaming
    "gaming", "headset", "mouse pad",
    # BR específico
    "frete gratis", "compre agora", "oferta", "promocao",
    "emagrecer", "emagrecimento", "suplemento", "whey", "creatina", "colageno",
    "maquiagem", "protetor solar", "creme facial",
    "decoracao", "luminaria", "organizador",
    "roupa feminina", "tenis", "bolsa", "relogio",
    "cachorro", "gato", "brinquedo",
    "renda extra", "trabalhar em casa",
]

MAX_PAGES = 5  # 20 ads/pagina x 5 = 100 por keyword
DELAY = 2

HEADERS = {
    'access_token': TOKEN,
    'device_id': DEVICE_ID,
    'language_code': 'pt',
    'time_zone_id': 'America/Sao_Paulo',
    'timezone_offset': '180',
    'Content-Type': 'application/json',
    'Origin': 'https://www.pipiads.com',
    'Referer': 'https://www.pipiads.com/pt/ad-search',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Cookie': COOKIE,
}


def search(keyword, page=1):
    body = {
        'is_participle': False,
        'search_type': 1,
        'extend_keywords': [{'type': 1, 'keyword': keyword}],
        'sort': 999,
        'sort_type': 'desc',
        'current_page': page,
        'page_size': 20,
    }
    try:
        r = requests.post('https://www.pipiads.com/v3/api/search4/at/video/search',
                          headers=HEADERS, json=body, timeout=30)
        if r.status_code != 200:
            return []
        return r.json().get('result', {}).get('data', [])
    except:
        return []


def normalize(item, keyword):
    likes = int(item.get('digg_count', 0) or 0)
    comments = int(item.get('comment_count', 0) or 0)
    shares = int(item.get('share_count', 0) or 0)
    regions = item.get('fetch_region', [])
    if isinstance(regions, list):
        regions = ', '.join(regions)
    return {
        'ad_id': str(item.get('ad_id', '')),
        'source': 'pipiads', 'platform': 'tiktok',
        'advertiser': item.get('app_name', item.get('unique_id', '')),
        'advertiser_image': item.get('app_image', ''),
        'title': (item.get('desc', '') or '')[:200],
        'body': (item.get('desc', '') or '')[:200],
        'cta': item.get('button_text', ''),
        'image_url': item.get('cover', ''),
        'video_url': item.get('video_url', ''),
        'likes': likes, 'comments': comments, 'shares': shares,
        'impressions': int(item.get('play_count', 0) or 0),
        'total_engagement': likes + comments + shares,
        'days_running': int(item.get('put_days', 0) or 0),
        'heat': int(item.get('hot_value', 0) or 0),
        'ad_type': 'video',
        'video_duration': int(item.get('duration', 0) or 0),
        'country': regions, 'all_countries': regions,
        'channels': 'tiktok', 'has_media': True,
        'pipi_hook': item.get('ai_analysis_main_hook', ''),
        'pipi_script': (item.get('ai_analysis_script', '') or '')[:300],
        'pipi_tags': item.get('ai_analysis_tags', []),
        'pipi_has_presenter': item.get('ai_analysis_human_presenter', ''),
        'pipi_language': item.get('ai_analysis_language', ''),
        'pipi_cpm': item.get('min_cpm', 0),
        'pipi_cpa': item.get('min_cpa', 0),
        'estimated_spend': round((item.get('min_cpm', 0) or 0) * (item.get('play_count', 0) or 0) / 1000, 2) if item.get('min_cpm') else 0,
        'landing_page': item.get('link_url', '') or item.get('landing_page', '') or item.get('destination_url', '') or '',
        'search_keyword': keyword,
        'collected_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }


def run():
    print("=" * 60)
    print(f"  PiPiAds Auto Scraper")
    print(f"  {len(KEYWORDS)} keywords x {MAX_PAGES} paginas")
    print("=" * 60)

    all_ads = []
    seen = set()

    for idx, kw in enumerate(KEYWORDS):
        for page in range(1, MAX_PAGES + 1):
            items = search(kw, page)
            if not items:
                break

            new = 0
            for item in items:
                ad = normalize(item, kw)
                if ad['ad_id'] and ad['ad_id'] not in seen:
                    seen.add(ad['ad_id'])
                    all_ads.append(ad)
                    new += 1

            if page == 1:
                print(f'  [{idx+1:2d}/{len(KEYWORDS)}] {kw:25s} pg{page}: +{len(items)} ({new} novos, total: {len(all_ads)})')

            if len(items) < 20:
                break
            time.sleep(DELAY)

    if not all_ads:
        print('\nNenhum ad coletado!')
        return

    # Salvar
    Path('resultados').mkdir(exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M')
    with open(f'resultados/pipiads_auto_{ts}.json', 'w', encoding='utf-8') as f:
        json.dump(all_ads, f, ensure_ascii=False)

    # Merge
    uf = sorted(glob.glob('resultados/unified_*.json'), reverse=True)
    if uf:
        with open(uf[0], 'r', encoding='utf-8') as f:
            existing = json.load(f)
        ids = {a.get('ad_id', '') for a in existing if a.get('ad_id')}
        new = [a for a in all_ads if a['ad_id'] not in ids]
        with open(uf[0], 'w', encoding='utf-8') as f:
            json.dump(existing + new, f, ensure_ascii=False)
        print(f'\n{len(new)} novos -> unified (total: {len(existing)+len(new)})')

    wh = len([a for a in all_ads if a.get('pipi_hook')])
    wv = len([a for a in all_ads if a.get('video_url')])
    print(f'PiPiAds: {len(all_ads)} unicos | hooks: {wh} | videos: {wv}')

    # AI
    try:
        from ai_enricher import enrich_ads, AI_API_KEY
        if AI_API_KEY:
            print('\n--- AI Enrichment ---')
            enrich_ads(AI_API_KEY, max_ads=99999)
    except Exception as e:
        print(f'AI: {e}')


if __name__ == '__main__':
    run()
