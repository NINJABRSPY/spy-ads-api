"""Captura ads do PiPiAds da pagina ja scrollada"""
from playwright.sync_api import sync_playwright
import json, time, glob
from datetime import datetime
from pathlib import Path

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp('http://localhost:9222')
    context = browser.contexts[0]

    page = None
    for pg in context.pages:
        if 'pipiads' in pg.url:
            page = pg
            break

    if not page:
        print('PiPiAds nao encontrado')
        exit()

    print(f'URL: {page.url[:80]}')

    # Instalar interceptor
    page.evaluate("""() => {
        window._ppAds = [];
        const origSend = XMLHttpRequest.prototype.send;
        const origOpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function(m, url) {
            this._ppUrl = url;
            return origOpen.apply(this, arguments);
        };
        XMLHttpRequest.prototype.send = function(body) {
            this.addEventListener('load', function() {
                try {
                    if (this._ppUrl && this._ppUrl.includes('search')) {
                        var d = JSON.parse(this.responseText);
                        if (d.result && d.result.data) {
                            d.result.data.forEach(function(item) { window._ppAds.push(item); });
                        }
                    }
                } catch(e) {}
            });
            return origSend.apply(this, arguments);
        };
    }""")

    print('Interceptor OK. Scrollando mais...')
    for i in range(20):
        page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
        time.sleep(2)
        count = page.evaluate('window._ppAds.length')
        if i % 5 == 0:
            print(f'  Scroll {i+1}: {count} ads')

    total = page.evaluate('window._ppAds.length')
    print(f'\nTotal interceptado: {total}')

    if total > 0:
        raw = page.evaluate('JSON.stringify(window._ppAds)')
        items = json.loads(raw)

        all_ads = []
        seen = set()
        for item in items:
            aid = str(item.get('ad_id', ''))
            if not aid or aid in seen:
                continue
            seen.add(aid)
            likes = int(item.get('digg_count', 0) or 0)
            comments = int(item.get('comment_count', 0) or 0)
            shares = int(item.get('share_count', 0) or 0)
            regions = item.get('fetch_region', [])
            if isinstance(regions, list):
                regions = ', '.join(regions)

            all_ads.append({
                'ad_id': aid, 'source': 'pipiads', 'platform': 'tiktok',
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
                'search_keyword': 'pipiads_scroll',
                'collected_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            })

        print(f'Unicos: {len(all_ads)}')

        # Salvar
        ts = datetime.now().strftime('%Y%m%d_%H%M')
        Path('resultados').mkdir(exist_ok=True)
        with open(f'resultados/pipiads_final_{ts}.json', 'w', encoding='utf-8') as f:
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
            print(f'{len(new)} novos -> unified (total: {len(existing)+len(new)})')

        wh = len([a for a in all_ads if a.get('pipi_hook')])
        wv = len([a for a in all_ads if a.get('video_url')])
        print(f'Com hook: {wh} | Com video: {wv} | Com likes: {len([a for a in all_ads if a["likes"]>0])}')
        if all_ads:
            ad = all_ads[0]
            print(f'\nEx: {ad["advertiser"][:30]} | likes={ad["likes"]} | views={ad["impressions"]} | days={ad["days_running"]}')
            print(f'  Hook: {ad.get("pipi_hook","")[:80]}')
    else:
        print('Nenhum ad interceptado via scroll. Os dados ja estavam carregados antes.')
        print('Tente buscar outra keyword no PiPiAds e me avise.')
