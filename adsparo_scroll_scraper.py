"""
AdsParo Scroll Scraper - Extrai todos os ads carregados na pagina via DOM
Requer Chrome com debugging aberto e AdsParo logado com busca feita.
"""
from playwright.sync_api import sync_playwright
import json, time, glob
from datetime import datetime
from pathlib import Path

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp('http://localhost:9222')
    context = browser.contexts[0]
    page = context.pages[0]

    print(f'Pagina: {page.url[:60]}')

    # Scrollar para carregar tudo
    print('Scrollando...')
    last = 0
    for i in range(50):
        page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
        time.sleep(1.5)
        count = page.evaluate('document.querySelectorAll(".as-card").length')
        if count > last:
            print(f'  Scroll {i+1}: {count} cards')
            last = count
        elif i > 3 and count == last:
            break

    print(f'\nExtraindo {last} cards...')

    ads = page.evaluate("""() => {
        const cards = document.querySelectorAll('.as-card');
        const results = [];
        cards.forEach(card => {
            try {
                const text = card.innerText || '';
                const lines = text.split('\\n').filter(l => l.trim());
                const imgs = card.querySelectorAll('img');
                let adImg = '', pageImg = '';
                imgs.forEach(img => {
                    const src = img.src || img.dataset.src || '';
                    if (src.includes('page_img')) pageImg = src;
                    else if (src.includes('images/') || src.includes('digitalocean')) adImg = src;
                });
                let advertiser = lines[0] || '';
                let lastSeen = '', totalAds = '', countries = '', dateFound = '', desc = '';
                lines.forEach(line => {
                    if (line.includes('Last Seen:')) lastSeen = line.replace('Last Seen:','').trim();
                    if (line.match(/^\\d+ ads/)) totalAds = line.trim();
                    if (line.includes('Countries:')) countries = line.replace('Countries:','').trim();
                    if (line.includes('Found:')) dateFound = line.replace('Found:','').trim();
                    if (line.length > 50 && !line.includes('Last Seen') && !line.includes('Countries') && !line.includes('Found:') && !line.includes('Page on'))
                        desc = line;
                });
                if (advertiser && advertiser !== 'Categories' && advertiser.length > 1)
                    results.push({a:advertiser, ls:lastSeen, ta:totalAds, c:countries, df:dateFound, d:desc.substring(0,500), i:adImg||pageImg, pi:pageImg, vid:''});
            } catch(e) {}
        });
        return results;
    }""")

    print(f'Extraidos: {len(ads)}')

    # Deduplicar e normalizar
    seen = set()
    normalized = []
    for ad in ads:
        key = ad['a'] + (ad.get('d',''))[:50]
        if key in seen or not ad['a']:
            continue
        seen.add(key)
        total = 0
        try:
            total = int(''.join(c for c in ad.get('ta','') if c.isdigit()) or '0')
        except:
            pass
        normalized.append({
            'ad_id': str(abs(hash(key)))[:12],
            'source': 'adsparo',
            'platform': 'facebook',
            'advertiser': ad['a'],
            'title': ad['a'],
            'body': ad.get('d','')[:800],
            'cta': '',
            'landing_page': '',
            'image_url': ad.get('i',''),
            'video_url': '',
            'first_seen': ad.get('df',''),
            'last_seen': ad.get('ls',''),
            'is_active': True,
            'likes': 0, 'comments': 0, 'shares': 0, 'impressions': 0,
            'days_running': 0,
            'total_ads_from_advertiser': total,
            'country': ad.get('c',''),
            'all_countries': ad.get('c',''),
            'ad_type': 'image',
            'has_media': bool(ad.get('i')),
            'total_engagement': 0, 'heat': 0, 'video_duration': 0,
            'channels': 'facebook', 'has_store': False,
            'search_keyword': 'adsparo_scroll',
            'collected_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        })

    print(f'Unicos: {len(normalized)}')

    # Salvar
    Path('resultados').mkdir(exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M')
    with open(f'resultados/adsparo_scroll_{ts}.json', 'w', encoding='utf-8') as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)

    # Merge
    uf = sorted(glob.glob('resultados/unified_*.json'), reverse=True)
    if uf:
        with open(uf[0], 'r', encoding='utf-8') as f:
            existing = json.load(f)
        ids = {a['ad_id'] for a in existing}
        new = [a for a in normalized if a['ad_id'] not in ids]
        with open(uf[0], 'w', encoding='utf-8') as f:
            json.dump(existing + new, f, ensure_ascii=False, indent=2)
        print(f'{len(new)} novos ads -> unified (total: {len(existing)+len(new)})')

    print(f'\nSalvo: resultados/adsparo_scroll_{ts}.json')
    for ad in normalized[:3]:
        print(f'  {ad["advertiser"][:30]} | ads={ad["total_ads_from_advertiser"]} | img={"SIM" if ad["image_url"] else "NAO"}')
