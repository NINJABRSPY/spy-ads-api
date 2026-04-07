"""
Feed SimilarWeb Server — Envia os top domínios dos ads para análise automática
Requer: similarweb_server.js rodando na porta 4000
Uso: python feed_similarweb.py
"""
import json
import gzip
import glob
import os
import time
import requests
from urllib.parse import urlparse
from collections import Counter

OUTPUT_DIR = "resultados"
SW_SERVER = "http://localhost:4000"
SW_KEY = "njspy_traffic_2026_x9k"
DELAY = 20  # segundos entre requests (SimilarWeb é lento)

# Domínios genéricos que não valem a pena analisar
SKIP_DOMAINS = {
    "facebook.com", "instagram.com", "tiktok.com", "youtube.com",
    "twitter.com", "google.com", "linkedin.com", "meta.com",
    "pinterest.com", "reddit.com", "t.co", "bit.ly",
    "linktr.ee", "linktree.com", "wa.me", "api.whatsapp.com",
    "fb.me", "m.me", "messenger.com",
    "adstransparency.google.com", "ad.doubleclick.net",
    "play.google.com", "apps.apple.com", "itunes.apple.com",
    "amazon.com", "amazon.com.br", "shopify.com",
    "hotmart.com", "kiwify.com.br", "monetizze.com.br",
    # Plataformas de pagamento
    "pay.hotmart.com", "go.hotmart.com", "pay.kiwify.com.br",
    "checkout.stripe.com", "paypal.com",
}


def load_ads():
    files = sorted(glob.glob(f"{OUTPUT_DIR}/unified_*.json.gz"), reverse=True)
    if files:
        with gzip.open(files[0], "rt", encoding="utf-8") as f:
            return json.load(f)
    files = sorted(glob.glob(f"{OUTPUT_DIR}/unified_*.json"), reverse=True)
    if files:
        with open(files[0], encoding="utf-8") as f:
            return json.load(f)
    return []


def load_already_analyzed():
    """Domínios já no SimilarWeb (cache + main file)"""
    analyzed = set()
    # Main file
    files = sorted(glob.glob(f"{OUTPUT_DIR}/similarweb_*.json"), reverse=True)
    files = [f for f in files if "cache" not in os.path.basename(f)]
    if files:
        with open(files[0], encoding="utf-8") as f:
            data = json.load(f)
        analyzed.update(data.get("domains", {}).keys())
    # Cache
    cache_file = os.path.join(OUTPUT_DIR, "similarweb_cache.json")
    if os.path.exists(cache_file):
        with open(cache_file, encoding="utf-8") as f:
            analyzed.update(json.load(f).keys())
    return analyzed


def extract_top_domains(ads, already_analyzed, max_domains=100):
    """Extrai os domínios mais importantes para analisar"""
    domain_stats = {}

    for ad in ads:
        lp = ad.get("landing_page", "") or ""
        if not lp or len(lp) < 10:
            continue
        try:
            host = urlparse(lp).hostname
            if not host:
                continue
            domain = host.replace("www.", "")
            # Clean subdomains for common patterns
            parts = domain.split(".")
            if len(parts) > 2:
                tld2 = ".".join(parts[-2:])
                tld3 = ".".join(parts[-3:])
                if tld2 in ("co.uk", "com.br", "com.au", "co.jp", "com.mx"):
                    domain = tld3
                else:
                    domain = tld2

            if domain in SKIP_DOMAINS or domain in already_analyzed:
                continue
            if len(domain) < 5:
                continue

            if domain not in domain_stats:
                domain_stats[domain] = {"ads": 0, "impressions": 0, "spend": 0, "sources": set()}
            domain_stats[domain]["ads"] += 1
            domain_stats[domain]["impressions"] += ad.get("impressions", 0) or 0
            domain_stats[domain]["spend"] += ad.get("estimated_spend", 0) or 0
            domain_stats[domain]["sources"].add(ad.get("source", ""))
        except:
            pass

    # Score: mais ads + mais impressões + mais sources = prioridade
    scored = []
    for domain, stats in domain_stats.items():
        score = stats["ads"] * 10 + stats["impressions"] / 1000 + len(stats["sources"]) * 50
        scored.append((domain, score, stats))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:max_domains]


def main():
    print("=" * 50)
    print("  Feed SimilarWeb — Análise automática de domínios")
    print("=" * 50)

    # Check server
    try:
        r = requests.get(f"{SW_SERVER}/health?key={SW_KEY}", timeout=5)
        info = r.json()
        print(f"\n  Server OK — {info.get('cached_domains', 0)} domínios em cache")
    except:
        print("\n  ERRO: SimilarWeb server não está rodando!")
        print("  Inicie com: node similarweb_server.js")
        return

    ads = load_ads()
    print(f"  Total ads carregados: {len(ads):,}")

    already = load_already_analyzed()
    print(f"  Domínios já analisados: {len(already)}")

    top_domains = extract_top_domains(ads, already)
    print(f"  Novos domínios para analisar: {len(top_domains)}")

    if not top_domains:
        print("\n  Nenhum domínio novo para analisar!")
        return

    print(f"\n  Top 10 prioridades:")
    for dom, score, stats in top_domains[:10]:
        print(f"    {dom} — {stats['ads']} ads, {stats['impressions']:,} imp, {len(stats['sources'])} sources")

    print(f"\n  Iniciando análise ({DELAY}s entre cada)...")
    print("-" * 50)

    success = 0
    errors = 0
    for i, (domain, score, stats) in enumerate(top_domains):
        print(f"\n  [{i+1}/{len(top_domains)}] {domain}...", end=" ", flush=True)
        try:
            r = requests.get(f"{SW_SERVER}/api/traffic/{domain}?key={SW_KEY}", timeout=45)
            data = r.json()
            if data.get("monthly_visits"):
                print(f"OK — {data['monthly_visits']:,} visits/month")
                success += 1
            elif data.get("error"):
                print(f"EMPTY — {data.get('error', 'no data')}")
                errors += 1
            else:
                print("EMPTY — no traffic data")
                errors += 1
        except Exception as e:
            print(f"ERROR — {e}")
            errors += 1

        if i < len(top_domains) - 1:
            time.sleep(DELAY)

    print(f"\n{'=' * 50}")
    print(f"  Concluído: {success} OK, {errors} sem dados")
    print(f"  Total domínios analisados: {len(already) + success}")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
