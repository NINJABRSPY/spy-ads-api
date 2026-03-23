"""
BigSpy Auto Scraper - NinjaBR
Coleta anuncios do BigSpy automaticamente por keyword e plataforma.
Salva em CSV e JSON. Roda agendado ou sob demanda.
"""

import json
import csv
import os
import time
import requests
from datetime import datetime, timedelta
from pathlib import Path

# ============================================================
# CONFIGURACAO
# ============================================================

CONFIG = {
    "jwt_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJsYW4iOiJlbiIsInZlciI6ImJzIiwidGltZXN0YW1wIjoxNzc0MjA5MzE4LCJleHBpcmUiOjE3NzQ0Njg1MTgsInVzZXJfaWQiOiJTRmRvUjF4U2Frdz0iLCJhcHBuYW1lIjoiQmlnU3B5IiwidXNlcl9uYW1lIjoiTmljIEtvIiwic3Vic2NyaXB0aW9uIjp7ImNvZGUiOiJiaWdzcHlfbW9udGhseV9wcm8yNSIsImFkc19wZXJtaXNzaW9uIjp7InNlYXJjaCI6MSwiZXhjbHVkZV9zZWFyY2giOjEsImZpbHRlciI6MSwicGFnZV9saW1pdCI6MCwicXVlcnlfbnVtIjoxMDAwLCJkb3dubG9hZF9udW0iOjI1MCwidmlkZW9fcmVjb2duaXplX2xpbWl0IjoxMDB9LCJuZXR3b3JrcyI6eyJmYWNlYm9vayI6MSwiaW5zdGFncmFtIjoxLCJ0d2l0dGVyIjoxLCJhZG1vYiI6MCwicGludGVyZXN0IjoxLCJ5YWhvbyI6MSwieW91dHViZSI6MCwidGlrdG9rIjoxLCJ1bml0eSI6MH0sInRyYWNrX3Blcm1pc3Npb24iOnsiZmVhdHVyZV9hZHMiOjEsInBlb3BsZV9hZHMiOjEsIm15X3RyYWNrIjoxLCJ0cmFja19udW0iOjI1MCwicGFnZV9hbmFseXNpcyI6MSwicGFnZV90cmFja19udW0iOjIwfSwibW9kdWxlX3Blcm1pc3Npb24iOnsicGFnZV9hbmFseXNpcyI6MSwiZmVhdHVyZV9hZHMiOjIsInBsYXlhYmxlIjowLCJhZHNweSI6MSwibGFuZGluZ19wYWdlIjoxLCJ0aWt0b2tfc2hvcCI6MSwidGlrdG9rX3N0b3JlX2NoYXJ0IjoxfSwidGVhbV9pbmZvIjp7ImlkIjowLCJuZXdfdGVhbV9wb3B1cCI6MCwidGVhbV9yZXF1ZXN0IjowfSwiaW5kdXN0cnlfaW5mbyI6eyJ0b3RhbF9pbmR1c3RyeV9jb3VudCI6MywicmVtYWluX2luZHVzdHJ5X2NvdW50IjozLCJwZXJtaXNzaW9uX2FwcF90eXBlIjpbMSwyLDNdLCJsYXN0X2FwcF90eXBlIjozfSwidXNlcl9zdGF0dXMiOjMsImlzX2FkbWluIjowfSwiY29tcGFueV9pZCI6MCwiZW1haWwiOiJrYXJhbmdhc2FtMTk4OEBnbWFpbC5jb20ifQ.1hl-HiUz75UHFeEwUbYNTXe0nCgF1MAu_8NSKQaTw8Y",
    "device_id": "bbe6b60cf45b3967acc419c461ddff0a",
    "delay_between_requests": 3,  # segundos entre requests
    "max_pages_per_keyword": 5,   # paginas por keyword (60 ads/pagina)
    "output_dir": "resultados",
}

# Keywords e plataformas para buscar
SEARCHES = [
    {"keyword": "dropshipping", "platform": "facebook"},
    {"keyword": "dropshipping", "platform": "tiktok"},
    {"keyword": "skincare", "platform": "facebook"},
    {"keyword": "skincare", "platform": "instagram"},
    {"keyword": "fitness", "platform": "facebook"},
    {"keyword": "ecommerce", "platform": "facebook"},
]

# ============================================================
# API CLIENT
# ============================================================

API_URL = "https://bigspy.com/napi/v1/creative/list"
COUNT_URL = "https://bigspy.com/napi/v1/creative/count"

def get_headers():
    return {
        "authorization": CONFIG["jwt_token"],
        "x-device-id": CONFIG["device_id"],
        "x-timezone": "-0300",
        "content-type": "application/json",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

def get_date_range(days_back=30):
    """Retorna timestamps Unix para os ultimos N dias"""
    now = datetime.now()
    end = int(now.timestamp())
    begin = int((now - timedelta(days=days_back)).timestamp())
    return begin, end

def search_ads(keyword, platform="facebook", page=1, page_size=60, days_back=30):
    """Busca anuncios no BigSpy"""
    seen_begin, seen_end = get_date_range(days_back)

    body = {
        "page": page,
        "keyword": [keyword],
        "search_type": 1,
        "sort_field": "-first_seen",
        "seen_begin": seen_begin,
        "seen_end": seen_end,
        "page_size": page_size,
        "app_type": "3",
        "platform": [platform],
        "is_first": page == 1,
    }

    try:
        response = requests.post(API_URL, headers=get_headers(), json=body, timeout=30)
        data = response.json()

        if data.get("id") != "SUCCESS":
            print(f"    [ERRO] {data.get('message', 'Erro desconhecido')}")
            return None, 0

        ads = data.get("data", {}).get("creative_list", [])
        remain = data.get("remain_req_count", "?")
        return ads, remain

    except Exception as e:
        print(f"    [ERRO] {e}")
        return None, 0

def count_ads(keyword, platform="facebook", days_back=30):
    """Conta total de anuncios para uma busca"""
    seen_begin, seen_end = get_date_range(days_back)

    body = {
        "page": 1,
        "keyword": [keyword],
        "search_type": 1,
        "sort_field": "-first_seen",
        "seen_begin": seen_begin,
        "seen_end": seen_end,
        "page_size": 60,
        "app_type": "3",
        "platform": [platform],
    }

    try:
        response = requests.post(COUNT_URL, headers=get_headers(), json=body, timeout=30)
        data = response.json()
        return data.get("data", {}).get("total_count", 0)
    except:
        return 0

# ============================================================
# DATA PROCESSING
# ============================================================

def flatten_ad(ad):
    """Converte um anuncio em dicionario plano para CSV"""
    # Extrair imagem e video
    image_url = ""
    video_url = ""
    if ad.get("resource_urls"):
        for res in ad["resource_urls"]:
            if res.get("image_url") and not image_url:
                image_url = res.get("image_url", "")
            if res.get("video_url") and not video_url:
                video_url = res.get("video_url", "")

    return {
        "ad_key": ad.get("ad_key", ""),
        "platform": ad.get("platform", ""),
        "page_name": ad.get("page_name", ""),
        "advertiser_name": ad.get("advertiser_name", ""),
        "title": ad.get("title", ""),
        "body": (ad.get("body", "") or "")[:500],
        "message": (ad.get("message", "") or "")[:500],
        "call_to_action": ad.get("call_to_action", ""),
        "first_seen": datetime.fromtimestamp(ad.get("first_seen", 0)).strftime("%Y-%m-%d") if ad.get("first_seen") else "",
        "last_seen": datetime.fromtimestamp(ad.get("last_seen", 0)).strftime("%Y-%m-%d") if ad.get("last_seen") else "",
        "days_count": ad.get("days_count", 0),
        "like_count": ad.get("like_count", 0),
        "comment_count": ad.get("comment_count", 0),
        "share_count": ad.get("share_count", 0),
        "impression": ad.get("impression", 0),
        "heat": ad.get("heat", 0),
        "ads_type": ad.get("ads_type", ""),
        "image_url": image_url,
        "video_url": video_url,
        "preview_img_url": ad.get("preview_img_url", ""),
        "video_duration": ad.get("video_duration", 0),
        "channels": ",".join(ad.get("fb_merge_channel", [])),
        "has_store_url": ad.get("has_store_url", False),
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "search_keyword": "",  # preenchido depois
    }

# ============================================================
# MAIN SCRAPER
# ============================================================

def run_scraper():
    print("=" * 60)
    print("  BigSpy Auto Scraper")
    print("  " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)

    # Criar diretorio de resultados
    output_dir = Path(CONFIG["output_dir"])
    output_dir.mkdir(exist_ok=True)

    all_ads = []
    total_queries_used = 0

    for search in SEARCHES:
        keyword = search["keyword"]
        platform = search["platform"]

        print(f"\n[BUSCA] '{keyword}' em {platform}")

        # Contar total
        total = count_ads(keyword, platform)
        total_queries_used += 1
        print(f"  Total encontrado: {total} anuncios")
        time.sleep(CONFIG["delay_between_requests"])

        # Paginar
        for page in range(1, CONFIG["max_pages_per_keyword"] + 1):
            print(f"  Pagina {page}...", end=" ")

            ads, remain = search_ads(keyword, platform, page=page)
            total_queries_used += 1

            if not ads:
                print("sem resultados")
                break

            print(f"{len(ads)} ads (creditos restantes: {remain})")

            # Processar cada anuncio
            for ad in ads:
                flat = flatten_ad(ad)
                flat["search_keyword"] = keyword
                all_ads.append(flat)

            # Parar se poucos resultados (ultima pagina)
            if len(ads) < 60:
                break

            time.sleep(CONFIG["delay_between_requests"])

    # ============================================================
    # SALVAR RESULTADOS
    # ============================================================

    if not all_ads:
        print("\nNenhum anuncio coletado.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    # Deduplicar por ad_key
    seen_keys = set()
    unique_ads = []
    for ad in all_ads:
        if ad["ad_key"] not in seen_keys:
            seen_keys.add(ad["ad_key"])
            unique_ads.append(ad)

    print(f"\n{'=' * 60}")
    print(f"  RESULTADOS")
    print(f"  Total coletado: {len(all_ads)} | Unicos: {len(unique_ads)}")
    print(f"  Queries usadas: {total_queries_used}")
    print(f"{'=' * 60}")

    # Salvar CSV
    csv_path = output_dir / f"bigspy_{timestamp}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=unique_ads[0].keys())
        writer.writeheader()
        writer.writerows(unique_ads)
    print(f"\n  CSV: {csv_path} ({len(unique_ads)} linhas)")

    # Salvar JSON
    json_path = output_dir / f"bigspy_{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(unique_ads, f, ensure_ascii=False, indent=2)
    print(f"  JSON: {json_path}")

    # Salvar resumo
    summary = {
        "scrape_date": datetime.now().isoformat(),
        "total_ads_collected": len(all_ads),
        "unique_ads": len(unique_ads),
        "queries_used": total_queries_used,
        "searches": SEARCHES,
        "by_platform": {},
        "by_keyword": {},
    }
    for ad in unique_ads:
        p = ad["platform"]
        k = ad["search_keyword"]
        summary["by_platform"][p] = summary["by_platform"].get(p, 0) + 1
        summary["by_keyword"][k] = summary["by_keyword"].get(k, 0) + 1

    summary_path = output_dir / f"resumo_{timestamp}.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"  Resumo: {summary_path}")

    # Mostrar distribuicao
    print(f"\n  Por plataforma:")
    for p, count in summary["by_platform"].items():
        print(f"    {p}: {count}")
    print(f"\n  Por keyword:")
    for k, count in summary["by_keyword"].items():
        print(f"    {k}: {count}")

    print(f"\n  Concluido!")

# ============================================================
# EXECUCAO
# ============================================================

if __name__ == "__main__":
    run_scraper()
