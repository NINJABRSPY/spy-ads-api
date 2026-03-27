"""
AdsParo Quick Scraper - Roda em menos de 3 minutos
Uso: python adsparo_quick.py [token]
"""
import sys
import json
import csv
import time
import requests
from datetime import datetime
from pathlib import Path

if len(sys.argv) < 2:
    print("Uso: python adsparo_quick.py [cole o token JWT aqui]")
    sys.exit(1)

TOKEN = sys.argv[1]
KEYWORDS = ["dropshipping", "skincare", "fitness", "ecommerce", "marketing digital",
            "curso online", "suplementos", "moda feminina", "pet shop", "infoproduto"]

print("=" * 50)
print("  AdsParo Quick Scraper")
print("  Token expira em ~5 min - correndo!")
print("=" * 50)

all_ads = []

for kw in KEYWORDS:
    print(f"  [{KEYWORDS.index(kw)+1}/{len(KEYWORDS)}] '{kw}'...", end=" ")

    try:
        r = requests.post("https://adsparo.com/api/ad/read.php",
            headers={"authorization": TOKEN, "content-type": "application/x-www-form-urlencoded"},
            data={"searchtext": kw, "sortdir": "desc", "minads": "1", "maxads": "200",
                  "searchby": "", "sortby": "", "country": "", "language": "", "tld": "",
                  "startdate": "", "enddate": "", "ad_pl_id": "", "type": ""},
            timeout=20)

        if r.status_code == 401:
            print("TOKEN EXPIRADO!")
            break

        data = r.json()
        ads = data.get("ads", [])
        print(f"{len(ads)} ads")

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
                "also_on_twitter": ad.get("a_twitter", False),
                "search_keyword": kw,
                "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
        time.sleep(1)
    except Exception as e:
        print(f"ERRO: {e}")

if not all_ads:
    print("\nNenhum ad coletado!")
    sys.exit(1)

# Deduplicar
seen = set()
unique = []
for ad in all_ads:
    if ad["ad_id"] not in seen:
        seen.add(ad["ad_id"])
        unique.append(ad)

# Salvar
ts = datetime.now().strftime("%Y%m%d_%H%M")
Path("resultados").mkdir(exist_ok=True)

json_path = f"resultados/adsparo_{ts}.json"
with open(json_path, "w", encoding="utf-8") as f:
    json.dump(unique, f, ensure_ascii=False, indent=2)

csv_path = f"resultados/adsparo_{ts}.csv"
with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=unique[0].keys())
    w.writeheader()
    w.writerows(unique)

# Tambem adicionar ao unified mais recente
import glob
unified_files = sorted(glob.glob("resultados/unified_*.json"), reverse=True)
if unified_files:
    with open(unified_files[0], "r", encoding="utf-8") as f:
        existing = json.load(f)
    existing_ids = {a.get("ad_id","") for a in existing if a.get("ad_id")}
    new_ads = [a for a in unique if a["ad_id"] not in existing_ids]
    combined = existing + new_ads
    with open(unified_files[0], "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)
    print(f"\n  {len(new_ads)} novos ads adicionados ao unified")

print(f"\n{'=' * 50}")
print(f"  Total: {len(all_ads)} coletados, {len(unique)} unicos")
print(f"  CSV: {csv_path}")
print(f"  JSON: {json_path}")
print(f"{'=' * 50}")
