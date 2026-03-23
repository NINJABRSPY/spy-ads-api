"""
AdsParo Browser Scraper
Usa Playwright para executar as buscas DENTRO do browser (bypassa Cloudflare).
Voce faz login manualmente, depois ele coleta tudo automatico.
"""
import json
import csv
import time
import os
import shutil
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

TEMP_PROFILE = os.path.expanduser("~/chrome_adsparo")
OUTPUT_DIR = "resultados"

KEYWORDS = ["dropshipping", "skincare", "fitness", "ecommerce", "marketing digital",
            "curso online", "suplementos", "moda feminina", "pet shop", "infoproduto"]

all_ads = []

def main():
    print("=" * 60)
    print("  AdsParo Browser Scraper")
    print("=" * 60)

    if os.path.exists(TEMP_PROFILE):
        shutil.rmtree(TEMP_PROFILE, ignore_errors=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=TEMP_PROFILE,
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            viewport={"width": 1280, "height": 900},
            ignore_default_args=["--enable-automation"],
        )

        page = context.pages[0] if context.pages else context.new_page()

        # Login
        print("\n[1] Abrindo pagina de login...")
        page.goto("https://adsparo.com/overview/login.php", wait_until="networkidle", timeout=30000)

        print()
        print("=" * 60)
        print("  FACA LOGIN NO ADSPARO!")
        print("  Aguardando ate 90 segundos...")
        print("=" * 60)

        for i in range(120):
            time.sleep(1)
            url = page.url
            title = page.title()
            # Detectar login por mudanca de URL OU presenca de elementos da pagina logada
            if 'overview/index' in url or 'dashboard' in url or 'ads/index' in url:
                print(f"\n  Login OK via URL! ({url[:50]})")
                break
            # Verificar se tem elemento que so aparece logado
            try:
                logged = page.query_selector(".user-menu, .sidebar, .navbar-user, .dashboard, #main-content")
                if logged:
                    print(f"\n  Login OK via elemento! ({url[:50]})")
                    break
            except:
                pass
            if i % 15 == 0 and i > 0:
                print(f"  Aguardando... URL: {url[:40]} ({120-i}s)")

        # Mesmo se nao detectou, tenta continuar (usuario pode ter logado)
        print(f"  URL atual: {page.url[:60]}")
        print("  Continuando com coleta...")

        time.sleep(2)

        # Coletar ads usando JavaScript dentro do browser
        print(f"\n[2] Coletando {len(KEYWORDS)} keywords...")

        for idx, kw in enumerate(KEYWORDS):
            print(f"  [{idx+1}/{len(KEYWORDS)}] '{kw}'...", end=" ", flush=True)

            try:
                # Executar fetch DENTRO do browser (usa cookies/sessao do usuario)
                result = page.evaluate("""async (keyword) => {
                    try {
                        const formData = new URLSearchParams();
                        formData.append('searchby', '');
                        formData.append('searchtext', keyword);
                        formData.append('sortby', '');
                        formData.append('sortdir', 'desc');
                        formData.append('minads', '1');
                        formData.append('maxads', '200');
                        formData.append('country', '');
                        formData.append('language', '');
                        formData.append('tld', '');
                        formData.append('startdate', '');
                        formData.append('enddate', '');
                        formData.append('ad_pl_id', '');
                        formData.append('type', '');

                        const token = localStorage.getItem('token') ||
                                      sessionStorage.getItem('token') ||
                                      document.cookie.match(/token=([^;]+)/)?.[1] || '';

                        const resp = await fetch('https://adsparo.com/api/ad/read.php', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                                'Authorization': token,
                                'X-Requested-With': 'XMLHttpRequest',
                            },
                            body: formData.toString(),
                            credentials: 'include',
                        });
                        const data = await resp.json();
                        return {status: resp.status, ads: data.ads || [], count: (data.ads || []).length};
                    } catch(e) {
                        return {status: 0, error: e.message, ads: []};
                    }
                }""", kw)

                if result.get("status") == 401:
                    # Token no localStorage pode estar errado - tentar sem ele
                    result = page.evaluate("""async (keyword) => {
                        try {
                            const formData = new URLSearchParams();
                            formData.append('searchby', '');
                            formData.append('searchtext', keyword);
                            formData.append('sortby', '');
                            formData.append('sortdir', 'desc');
                            formData.append('minads', '1');
                            formData.append('maxads', '200');
                            formData.append('country', '');
                            formData.append('language', '');
                            formData.append('tld', '');
                            formData.append('startdate', '');
                            formData.append('enddate', '');
                            formData.append('ad_pl_id', '');
                            formData.append('type', '');

                            // Pegar token do header das requests anteriores
                            const resp = await fetch('/api/ad/read.php', {
                                method: 'POST',
                                headers: {
                                    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                                    'X-Requested-With': 'XMLHttpRequest',
                                },
                                body: formData.toString(),
                                credentials: 'include',
                            });
                            const data = await resp.json();
                            return {status: resp.status, ads: data.ads || [], count: (data.ads || []).length};
                        } catch(e) {
                            return {status: 0, error: e.message, ads: []};
                        }
                    }""", kw)

                ads = result.get("ads", [])
                print(f"{len(ads)} ads (HTTP {result.get('status', '?')})")

                for ad in ads:
                    all_ads.append({
                        "ad_id": str(ad.get("id", "")),
                        "source": "adsparo",
                        "platform": "facebook",
                        "advertiser": ad.get("p_title", ""),
                        "title": ad.get("p_title", ""),
                        "body": (ad.get("description", "") or "")[:500],
                        "cta": "",
                        "landing_page": ad.get("cta_link", ""),
                        "image_url": ad.get("thumbnail", ""),
                        "video_url": ad.get("video_link", ""),
                        "first_seen": ad.get("date_found", ""),
                        "last_seen": ad.get("date_updated", ""),
                        "is_active": True,
                        "likes": 0, "comments": 0, "shares": 0,
                        "total_ads": ad.get("totalads", 0),
                        "country": ad.get("country", ""),
                        "all_countries": ad.get("all_countries", ""),
                        "also_on_tiktok": ad.get("a_tiktok", False),
                        "search_keyword": kw,
                        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    })

                time.sleep(2)

            except Exception as e:
                print(f"ERRO: {e}")

        context.close()

    if not all_ads:
        print("\nNenhum ad coletado!")
        return

    # Deduplicar
    seen = set()
    unique = [a for a in all_ads if a["ad_id"] not in seen and not seen.add(a["ad_id"])]

    # Salvar
    Path(OUTPUT_DIR).mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")

    json_path = f"{OUTPUT_DIR}/adsparo_{ts}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(unique, f, ensure_ascii=False, indent=2)

    csv_path = f"{OUTPUT_DIR}/adsparo_{ts}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=unique[0].keys())
        w.writeheader()
        w.writerows(unique)

    # Adicionar ao unified
    import glob
    unified = sorted(glob.glob(f"{OUTPUT_DIR}/unified_*.json"), reverse=True)
    if unified:
        with open(unified[0], "r", encoding="utf-8") as f:
            existing = json.load(f)
        ids = {a["ad_id"] for a in existing}
        new = [a for a in unique if a["ad_id"] not in ids]
        combined = existing + new
        with open(unified[0], "w", encoding="utf-8") as f:
            json.dump(combined, f, ensure_ascii=False, indent=2)
        print(f"\n  {len(new)} novos ads adicionados ao unified")

    print(f"\n{'=' * 60}")
    print(f"  Total: {len(all_ads)} coletados, {len(unique)} unicos")
    print(f"  CSV: {csv_path}")
    print(f"  JSON: {json_path}")
    print(f"{'=' * 60}")

    # Cleanup
    shutil.rmtree(TEMP_PROFILE, ignore_errors=True)

if __name__ == "__main__":
    main()
