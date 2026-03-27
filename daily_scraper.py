"""
NinjaSpy - Scraper Diario
Roda BigSpy + PiPiAds via API direta (sem browser)
Minea precisa de Chrome com debugging aberto
AdsParo precisa de coleta manual (token expira em 5min)

Agendar via Windows Task Scheduler para rodar 1x/dia
"""
import json
import time
import glob
import subprocess
import sys
import os
from datetime import datetime
from pathlib import Path

LOG_FILE = "resultados/daily_log.txt"

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_bigspy():
    """BigSpy - via API direta (token dura 3 dias)"""
    log("=== BIGSPY ===")
    try:
        from scraper_brasil import run as run_br
        run_br()
        log("BigSpy BR: OK")
    except Exception as e:
        log(f"BigSpy BR ERRO: {e}")

    try:
        from unified_scraper import run as run_unified
        run_unified()
        log("BigSpy Global: OK")
    except Exception as e:
        log(f"BigSpy Global ERRO: {e}")


def run_pipiads():
    """PiPiAds - via API direta (token dura ~30 dias)"""
    log("=== PIPIADS ===")
    try:
        from pipi_auto import run as run_pipi
        run_pipi()
        log("PiPiAds: OK")
    except Exception as e:
        log(f"PiPiAds ERRO: {e}")


def run_minea():
    """Minea - precisa de Chrome com debugging aberto"""
    log("=== MINEA ===")
    try:
        import requests
        r = requests.get("http://localhost:9222/json/version", timeout=3)
        if r.status_code == 200:
            from minea_dropshipping import run as run_minea_drop
            run_minea_drop()
            log("Minea: OK")
        else:
            log("Minea: Chrome debugging nao disponivel - PULANDO")
    except:
        log("Minea: Chrome debugging nao disponivel - PULANDO")


def run_adyntel():
    """Adyntel - via API direta (sem expiracao)"""
    log("=== ADYNTEL ===")
    try:
        from adyntel_client import (
            search_meta_by_keyword, search_meta_by_domain,
            search_google, search_linkedin, get_domain_keywords,
            normalize_meta_keyword_ads, normalize_meta_domain_ads,
            normalize_google_ads, normalize_linkedin_ads,
        )
        from config import KEYWORDS, COMPETITOR_DOMAINS

        all_new = []

        # Busca por keyword (top 5)
        for kw in KEYWORDS[:5]:
            data = search_meta_by_keyword(kw, "ALL")
            if not data.get("error"):
                ads = normalize_meta_keyword_ads(data, kw)
                all_new.extend(ads)

        # Concorrentes por dominio
        for domain in COMPETITOR_DOMAINS:
            data = search_meta_by_domain(domain)
            if not data.get("error") and data.get("results"):
                all_new.extend(normalize_meta_domain_ads(data, domain))

            data = search_google(domain)
            if not data.get("error") and data.get("ads"):
                all_new.extend(normalize_google_ads(data, domain))

            data = search_linkedin(domain)
            if not data.get("error") and data.get("ads"):
                all_new.extend(normalize_linkedin_ads(data, domain))

        # Merge
        if all_new:
            uf = sorted(glob.glob("resultados/unified_*.json"), reverse=True)
            if uf:
                with open(uf[0], "r", encoding="utf-8") as f:
                    existing = json.load(f)
                ids = {a.get("ad_id", "") for a in existing if a.get("ad_id")}
                new = [a for a in all_new if a.get("ad_id") and a["ad_id"] not in ids]
                if new:
                    with open(uf[0], "w", encoding="utf-8") as f:
                        json.dump(existing + new, f, ensure_ascii=False)
                    log(f"Adyntel: +{len(new)} novos ads")
                else:
                    log("Adyntel: sem ads novos")
        log("Adyntel: OK")
    except Exception as e:
        log(f"Adyntel ERRO: {e}")


def push_to_render():
    """Push automatico para GitHub (Render faz redeploy)"""
    log("=== PUSH ===")
    try:
        os.system("git add -A")
        os.system('git commit -m "Daily scrape: ' + datetime.now().strftime("%Y-%m-%d") + '"')
        os.system("git push")
        log("Push: OK")
    except Exception as e:
        log(f"Push ERRO: {e}")


def count_total():
    uf = sorted(glob.glob("resultados/unified_*.json"), reverse=True)
    if uf:
        with open(uf[0], "r", encoding="utf-8") as f:
            ads = json.load(f)
        sources = {}
        for a in ads:
            s = a.get("source", "?")
            sources[s] = sources.get(s, 0) + 1
        log(f"Total: {len(ads)} ads | Fontes: {sources}")


def main():
    log("")
    log("=" * 60)
    log("  NinjaSpy Daily Scraper")
    log("=" * 60)

    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    start = time.time()

    run_bigspy()
    run_pipiads()
    run_minea()
    run_adyntel()

    count_total()
    push_to_render()

    elapsed = time.time() - start
    log(f"Concluido em {elapsed/60:.1f} minutos")
    log("=" * 60)


if __name__ == "__main__":
    main()
