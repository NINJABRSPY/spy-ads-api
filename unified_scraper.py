"""
Unified Spy Ads Scraper
BigSpy + Adyntel + AdsParo combinados em um unico sistema.
Coleta, normaliza, deduplica e salva tudo junto.
"""

import json
import csv
import time
import requests
from datetime import datetime, timedelta
from pathlib import Path

from config import (
    BIGSPY_JWT, BIGSPY_DEVICE_ID, KEYWORDS, BIGSPY_PLATFORMS,
    COMPETITOR_DOMAINS, BIGSPY_MAX_PAGES, BIGSPY_DELAY, OUTPUT_DIR
)
from adyntel_client import (
    search_meta_by_keyword, search_meta_by_domain,
    search_google, search_linkedin, search_tiktok, get_domain_keywords,
    normalize_meta_keyword_ads, normalize_meta_domain_ads,
    normalize_google_ads, normalize_linkedin_ads, normalize_tiktok_ads,
)
from adsparo_client import search_ads as adsparo_search, normalize_adsparo_ad

# ============================================================
# BIGSPY CLIENT
# ============================================================

def bigspy_search(keyword, platform, page=1, page_size=60, days_back=30):
    now = datetime.now()
    body = {
        "page": page,
        "keyword": [keyword],
        "search_type": 1,
        "sort_field": "-first_seen",
        "seen_begin": int((now - timedelta(days=days_back)).timestamp()),
        "seen_end": int(now.timestamp()),
        "page_size": page_size,
        "app_type": "3",
        "platform": [platform],
        "is_first": page == 1,
    }
    headers = {
        "authorization": BIGSPY_JWT,
        "x-device-id": BIGSPY_DEVICE_ID,
        "x-timezone": "-0300",
        "content-type": "application/json",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    try:
        r = requests.post("https://bigspy.com/napi/v1/creative/list",
                          headers=headers, json=body, timeout=30)
        data = r.json()
        if data.get("id") != "SUCCESS":
            return [], data.get("remain_req_count", "?")
        ads = data.get("data", {}).get("creative_list", [])
        return ads, data.get("remain_req_count", "?")
    except Exception as e:
        print(f"    [ERRO BigSpy] {e}")
        return [], "?"


def normalize_bigspy_ad(ad, keyword):
    image_url = ""
    video_url = ""
    for res in (ad.get("resource_urls") or []):
        # Pegar imagem
        if res.get("image_url") and not image_url:
            image_url = res.get("image_url", "")
        # Pegar video (pode estar em qualquer type)
        if res.get("video_url") and not video_url:
            video_url = res.get("video_url", "")

    return {
        "ad_id": ad.get("ad_key", ""),
        "source": "bigspy",
        "platform": ad.get("platform", ""),
        "advertiser": ad.get("page_name", ad.get("advertiser_name", "")),
        "title": ad.get("title", ""),
        "body": (ad.get("body", "") or ad.get("message", "") or "")[:500],
        "cta": ad.get("call_to_action", ""),
        "landing_page": "",
        "image_url": image_url or ad.get("preview_img_url", ""),
        "video_url": video_url,
        "first_seen": datetime.fromtimestamp(ad["first_seen"]).strftime("%Y-%m-%d") if ad.get("first_seen") else "",
        "last_seen": datetime.fromtimestamp(ad["last_seen"]).strftime("%Y-%m-%d") if ad.get("last_seen") else "",
        "is_active": True,
        "likes": ad.get("like_count", 0),
        "comments": ad.get("comment_count", 0),
        "shares": ad.get("share_count", 0),
        "days_running": ad.get("days_count", 0),
        "impressions": ad.get("impression", 0),
        "heat": ad.get("heat", 0),
        "search_keyword": keyword,
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


# ============================================================
# MAIN SCRAPER
# ============================================================

def run():
    print("=" * 70)
    print("  UNIFIED SPY ADS SCRAPER")
    print("  BigSpy + Adyntel")
    print("  " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 70)

    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(exist_ok=True)

    all_ads = []
    keywords_data = []
    stats = {"bigspy": 0, "adsparo": 0, "adyntel_meta": 0, "adyntel_google": 0,
             "adyntel_linkedin": 0, "adyntel_tiktok": 0}

    # ========================================================
    # FASE 1: BigSpy - Busca por keyword em cada plataforma
    # ========================================================
    print("\n" + "=" * 70)
    print("  FASE 1: BigSpy")
    print("=" * 70)

    for keyword in KEYWORDS:
        for platform in BIGSPY_PLATFORMS:
            print(f"\n[BigSpy] '{keyword}' / {platform}")

            for page in range(1, BIGSPY_MAX_PAGES + 1):
                ads, remain = bigspy_search(keyword, platform, page=page)
                if not ads:
                    break

                for ad in ads:
                    all_ads.append(normalize_bigspy_ad(ad, keyword))
                    stats["bigspy"] += 1

                print(f"  pg{page}: {len(ads)} ads (restam: {remain})")

                if len(ads) < 60:
                    break
                time.sleep(BIGSPY_DELAY)

    # ========================================================
    # FASE 1.5: AdsParo - Busca por keyword
    # ========================================================
    print("\n" + "=" * 70)
    print("  FASE 1.5: AdsParo")
    print("=" * 70)

    for keyword in KEYWORDS:
        ads = adsparo_search(keyword)
        for ad in ads:
            all_ads.append(normalize_adsparo_ad(ad, keyword))
            stats["adsparo"] += 1

    # ========================================================
    # FASE 2: Adyntel - Busca por keyword (Meta + TikTok)
    # ========================================================
    print("\n" + "=" * 70)
    print("  FASE 2: Adyntel - Busca por keyword")
    print("=" * 70)

    for keyword in KEYWORDS[:5]:  # Top 5 keywords para nao gastar muitos creditos
        # Meta Ad Search
        data = search_meta_by_keyword(keyword, "ALL")
        if not data.get("error"):
            ads = normalize_meta_keyword_ads(data, keyword)
            all_ads.extend(ads)
            stats["adyntel_meta"] += len(ads)
            print(f"    Meta: {len(ads)} ads")

        # TikTok Search
        data = search_tiktok(keyword)
        if not data.get("error"):
            ads = normalize_tiktok_ads(data, keyword)
            all_ads.extend(ads)
            stats["adyntel_tiktok"] += len(ads)
            print(f"    TikTok: {len(ads)} ads")

    # ========================================================
    # FASE 3: Adyntel - Concorrentes por dominio
    # ========================================================
    print("\n" + "=" * 70)
    print("  FASE 3: Adyntel - Concorrentes por dominio")
    print("=" * 70)

    for domain in COMPETITOR_DOMAINS:
        print(f"\n[Dominio] {domain}")

        # Meta
        data = search_meta_by_domain(domain)
        if not data.get("error") and data.get("results"):
            ads = normalize_meta_domain_ads(data, domain)
            all_ads.extend(ads)
            stats["adyntel_meta"] += len(ads)
            print(f"    Meta: {len(ads)} ads")

        # Google
        data = search_google(domain)
        if not data.get("error") and data.get("ads"):
            ads = normalize_google_ads(data, domain)
            all_ads.extend(ads)
            stats["adyntel_google"] += len(ads)
            print(f"    Google: {len(ads)} ads")

        # LinkedIn
        data = search_linkedin(domain)
        if not data.get("error") and data.get("ads"):
            ads = normalize_linkedin_ads(data, domain)
            all_ads.extend(ads)
            stats["adyntel_linkedin"] += len(ads)
            print(f"    LinkedIn: {len(ads)} ads")

        # Keywords pagas vs organicas
        data = get_domain_keywords(domain)
        if not data.get("error"):
            keywords_data.append({
                "domain": domain,
                "data": data,
                "collected_at": datetime.now().isoformat(),
            })
            print(f"    Keywords: dados coletados")

    # ========================================================
    # FASE 4: Deduplicar e salvar
    # ========================================================
    print("\n" + "=" * 70)
    print("  FASE 4: Processando resultados")
    print("=" * 70)

    # Deduplicar por ad_id + source
    seen = set()
    unique_ads = []
    for ad in all_ads:
        key = f"{ad['source']}:{ad['ad_id']}"
        if key not in seen and ad['ad_id']:
            seen.add(key)
            unique_ads.append(ad)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    # CSV unificado
    if unique_ads:
        # Garantir que todos os ads tenham os mesmos campos
        all_keys = set()
        for ad in unique_ads:
            all_keys.update(ad.keys())
        all_keys = sorted(all_keys)

        csv_path = output_dir / f"unified_{timestamp}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction='ignore')
            writer.writeheader()
            for ad in unique_ads:
                row = {k: ad.get(k, "") for k in all_keys}
                writer.writerow(row)
        print(f"\n  CSV: {csv_path}")

    # JSON unificado
    json_path = output_dir / f"unified_{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(unique_ads, f, ensure_ascii=False, indent=2)
    print(f"  JSON: {json_path}")

    # Keywords separado
    if keywords_data:
        kw_path = output_dir / f"keywords_{timestamp}.json"
        with open(kw_path, "w", encoding="utf-8") as f:
            json.dump(keywords_data, f, ensure_ascii=False, indent=2)
        print(f"  Keywords: {kw_path}")

    # Resumo
    summary = {
        "scrape_date": datetime.now().isoformat(),
        "total_collected": len(all_ads),
        "unique_ads": len(unique_ads),
        "sources": stats,
        "keywords_searched": KEYWORDS,
        "competitors_analyzed": COMPETITOR_DOMAINS,
        "by_source": {},
        "by_platform": {},
        "by_keyword": {},
    }
    for ad in unique_ads:
        s = ad.get("source", "unknown")
        p = ad.get("platform", "unknown")
        k = ad.get("search_keyword", "unknown")
        summary["by_source"][s] = summary["by_source"].get(s, 0) + 1
        summary["by_platform"][p] = summary["by_platform"].get(p, 0) + 1
        summary["by_keyword"][k] = summary["by_keyword"].get(k, 0) + 1

    sum_path = output_dir / f"resumo_{timestamp}.json"
    with open(sum_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # Print final
    print(f"\n{'=' * 70}")
    print(f"  RESULTADO FINAL")
    print(f"{'=' * 70}")
    print(f"  Total coletado: {len(all_ads)}")
    print(f"  Unicos: {len(unique_ads)}")
    print(f"\n  Por fonte:")
    for s, c in summary["by_source"].items():
        print(f"    {s}: {c}")
    print(f"\n  Por plataforma:")
    for p, c in summary["by_platform"].items():
        print(f"    {p}: {c}")
    print(f"\n  Top keywords:")
    for k, c in sorted(summary["by_keyword"].items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"    {k}: {c}")

    print(f"\n  Arquivos em: {output_dir}/")
    print(f"  Concluido!")
    return unique_ads


if __name__ == "__main__":
    run()
