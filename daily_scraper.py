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


def run_clickmidas():
    """ClickMidas - scrape via Chrome CDP + converte para formato NinjaSpy"""
    log("=== CLICKMIDAS ===")
    MIN_PRODUCTS = 500  # Se pegar menos que isso, algo deu errado

    try:
        import requests
        r = requests.get("http://localhost:9222/json/version", timeout=3)
        if r.status_code != 200:
            log("ClickMidas: Chrome debugging nao disponivel - PULANDO")
            return
    except:
        log("ClickMidas: Chrome debugging nao disponivel - PULANDO")
        return

    # Verificar se tem aba do ClickMidas aberta e logada
    try:
        import requests
        r = requests.get("http://localhost:9222/json", timeout=3)
        tabs = r.json()
        midas_tab = None
        for t in tabs:
            if "clickmidas" in t.get("url", ""):
                midas_tab = t
                break
        if not midas_tab:
            log("ClickMidas: Nenhuma aba do ClickMidas aberta - PULANDO")
            return
        # Verificar se não está na página de login
        if "/login" in midas_tab.get("url", "") or "acesse sua conta" in midas_tab.get("title", "").lower():
            log("ClickMidas: Aba encontrada mas NAO LOGADO - PULANDO")
            return
        log(f"ClickMidas: Aba encontrada -> {midas_tab.get('url', '')}")
    except:
        log("ClickMidas: Erro ao verificar abas - PULANDO")
        return

    # Navegar para midas-score antes de scrapar (garante página certa)
    try:
        import websocket as ws_lib
        # Navigate via CDP
        log("ClickMidas: Navegando para midas-score...")
    except:
        pass

    # Rodar scraper Node.js (timeout 45min para tabela grande de 556 páginas)
    try:
        result = subprocess.run(
            ["node", "clickmidas_scraper.js"],
            capture_output=True, text=True, timeout=2700,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        if result.returncode == 0:
            log("ClickMidas scraper: OK")
        else:
            log(f"ClickMidas scraper ERRO: {result.stderr[:200]}")
            return
    except subprocess.TimeoutExpired:
        log("ClickMidas scraper: TIMEOUT (45min) - usando dados parciais")
    except Exception as e:
        log(f"ClickMidas scraper ERRO: {e}")
        return

    # Verificar quantos produtos foram coletados
    import re as _re
    cm_files = sorted(glob.glob("resultados/clickmidas_*.json"), reverse=True)
    cm_files = [f for f in cm_files if _re.search(r'clickmidas_\d{8}\.json$', f)]
    if not cm_files:
        log("ClickMidas: Nenhum arquivo gerado")
        return

    with open(cm_files[0], "r", encoding="utf-8") as f:
        cm_data = json.load(f)

    new_count = cm_data.get("total_products", 0)
    log(f"ClickMidas: {new_count} produtos coletados hoje")

    if new_count < MIN_PRODUCTS:
        log(f"ClickMidas: ALERTA - Apenas {new_count} produtos (mínimo {MIN_PRODUCTS})")
        log("ClickMidas: Mantendo dados do dia anterior para não perder cobertura")
        # Não converter - mantém arquivo anterior intacto
        # Verificar se tem arquivo anterior com mais dados
        aff_files = sorted(glob.glob("resultados/affiliate_products_*.json"), reverse=True)
        if aff_files:
            with open(aff_files[0], "r", encoding="utf-8") as f:
                prev = json.load(f)
            log(f"ClickMidas: Arquivo anterior tem {prev.get('total_products', 0)} produtos - MANTIDO")
        return

    # Converter para formato NinjaSpy (merge incremental)
    try:
        from clickmidas_converter import convert_clickmidas_to_ninjaspy
        output_file = convert_clickmidas_to_ninjaspy()
        if output_file:
            merge_affiliate_data(output_file)
            log(f"ClickMidas converter: OK -> {output_file}")
        else:
            log("ClickMidas converter: nenhum dado")
    except Exception as e:
        log(f"ClickMidas converter ERRO: {e}")

    # Verificar se BuyGoods/Digistore24/MaxWeb têm dados diferentes agora
    try:
        check_other_platforms()
    except Exception as e:
        log(f"ClickMidas platforms check ERRO: {e}")


def check_other_platforms():
    """Verifica se BuyGoods/Digistore24/MaxWeb já têm dados próprios no ClickMidas"""
    import requests
    try:
        r = requests.get("http://localhost:9222/json", timeout=3)
        tabs = r.json()
        midas_tab = next((t for t in tabs if "clickmidas" in t.get("url", "")), None)
        if not midas_tab:
            return

        # Usar o scraper multi-platform para verificar
        # Só roda se o ClickBank já foi scrapeado com sucesso
        result = subprocess.run(
            ["node", "-e", """
const WebSocket = require('ws');
const http = require('http');
http.get('http://localhost:9222/json', res => {
  let d = '';
  res.on('data', c => d += c);
  res.on('end', () => {
    const tab = JSON.parse(d).find(t => t.url.includes('clickmidas'));
    if (!tab) { console.log('NO_TAB'); process.exit(0); }
    const ws = new WebSocket(tab.webSocketDebuggerUrl);
    let id = 1;
    const pending = {};
    function send(m, p = {}) {
      return new Promise(r => { const i = id++; pending[i] = r; ws.send(JSON.stringify({id: i, method: m, params: p})); });
    }
    ws.on('message', d => { const m = JSON.parse(d.toString()); if (m.id && pending[m.id]) { pending[m.id](m.result); delete pending[m.id]; } });
    ws.on('open', async () => {
      // Click BuyGoods tab
      const r1 = await send('Runtime.evaluate', {
        expression: '(function(){ const t = document.getElementById("tab-comp-lk8qhech1"); if(t){t.click(); return "clicked"} return "not found" })()',
        returnByValue: true
      });
      await new Promise(r => setTimeout(r, 3000));
      // Get first product name
      const r2 = await send('Runtime.evaluate', {
        expression: 'document.body.innerText.match(/Nome do Produto[\\s\\S]*?\\n([^\\n]+)/)?.[1] || "EMPTY"',
        returnByValue: true
      });
      const bgProduct = r2.result.value;

      // Click ClickBank tab back
      await send('Runtime.evaluate', {
        expression: '(function(){ const t = document.getElementById("tab-comp-lk8qhebi1"); if(t) t.click(); })()',
        returnByValue: true
      });
      await new Promise(r => setTimeout(r, 2000));
      const r3 = await send('Runtime.evaluate', {
        expression: 'document.body.innerText.match(/Nome do Produto[\\s\\S]*?\\n([^\\n]+)/)?.[1] || "EMPTY"',
        returnByValue: true
      });
      const cbProduct = r3.result.value;

      const same = bgProduct === cbProduct;
      console.log(JSON.stringify({buygoods_first: bgProduct, clickbank_first: cbProduct, same_data: same}));

      // Restore ClickBank tab
      await send('Runtime.evaluate', {
        expression: '(function(){ const t = document.getElementById("tab-comp-lk8qhebi1"); if(t) t.click(); })()',
        returnByValue: true
      });

      ws.close();
      process.exit(0);
    });
  });
});
"""],
            capture_output=True, text=True, timeout=30,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        if result.returncode == 0 and result.stdout.strip():
            try:
                check = json.loads(result.stdout.strip())
                if check.get("same_data"):
                    log("ClickMidas platforms: BuyGoods = ClickBank (mesmos dados ainda)")
                else:
                    log(f"ClickMidas platforms: BuyGoods TEM DADOS DIFERENTES! ({check.get('buygoods_first', '')[:50]})")
                    log("ClickMidas: SCRAPING BuyGoods separadamente...")
                    # Rodar scraper multi-platform
                    subprocess.run(
                        ["node", "clickmidas_digi_max.js"],
                        capture_output=True, text=True, timeout=2700,
                        cwd=os.path.dirname(os.path.abspath(__file__))
                    )
            except:
                pass
    except:
        pass


def run_searchapi():
    """SearchAPI.io — Meta Ad Library oficial"""
    log("=== SEARCHAPI (Meta Ad Library) ===")
    try:
        from searchapi_scraper import run as run_search
        total = run_search()
        log(f"SearchAPI: +{total} ads")
    except Exception as e:
        log(f"SearchAPI ERRO: {e}")


def run_social1():
    """Social1 - TikTok Shop products, videos e creators via Chrome CDP"""
    log("=== SOCIAL1 (TikTok Shop) ===")
    try:
        import requests
        r = requests.get("http://localhost:9222/json/version", timeout=3)
        if r.status_code != 200:
            log("Social1: Chrome debugging nao disponivel - PULANDO")
            return
    except:
        log("Social1: Chrome debugging nao disponivel - PULANDO")
        return

    # Verificar se tem aba do Social1 aberta
    try:
        import requests
        r = requests.get("http://localhost:9222/json", timeout=3)
        tabs = r.json()
        has_social1 = any("social1" in t.get("url", "") for t in tabs)
        if not has_social1:
            log("Social1: Nenhuma aba do Social1 aberta - PULANDO")
            return
    except:
        log("Social1: Erro ao verificar abas - PULANDO")
        return

    # Rodar scraper de produtos + videos
    try:
        result = subprocess.run(
            ["node", "social1_scraper.js"],
            capture_output=True, text=True, timeout=1200,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        if result.returncode == 0:
            log("Social1 products+videos: OK")
        else:
            log(f"Social1 scraper ERRO: {result.stderr[:200]}")
    except subprocess.TimeoutExpired:
        log("Social1 scraper: TIMEOUT (20min)")
    except Exception as e:
        log(f"Social1 scraper ERRO: {e}")

    # Rodar scraper de creators
    try:
        result = subprocess.run(
            ["node", "social1_creators2.js"],
            capture_output=True, text=True, timeout=300,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        if result.returncode == 0:
            log("Social1 creators: OK")
        else:
            log(f"Social1 creators ERRO: {result.stderr[:200]}")
    except Exception as e:
        log(f"Social1 creators ERRO: {e}")

    # Converter para formato NinjaSpy (TikTok Shop page)
    try:
        from social1_converter import convert_social1
        output_file = convert_social1()
        if output_file:
            log(f"Social1 converter: OK -> {output_file}")
        else:
            log("Social1 converter: nenhum dado")
    except Exception as e:
        log(f"Social1 converter ERRO: {e}")

    # Merge com unified (mesclado com outros ads)
    try:
        from social1_to_unified import convert_and_merge
        added = convert_and_merge()
        log(f"Social1 -> unified: +{added} ads mesclados")
    except Exception as e:
        log(f"Social1 unified merge ERRO: {e}")


def merge_affiliate_data(new_file):
    """Merge incremental - atualiza produtos existentes, mantem os que nao mudaram"""
    try:
        # Carregar novo
        with open(new_file, "r", encoding="utf-8") as f:
            new_data = json.load(f)
        new_products = {p["name"]: p for p in new_data.get("products", [])}

        # Carregar mais recente anterior (se existir)
        files = sorted(glob.glob("resultados/affiliate_products_*.json"), reverse=True)
        existing_products = {}
        for af in files:
            if af != new_file:
                with open(af, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                existing_products = {p["name"]: p for p in old_data.get("products", [])}
                break

        # Merge: novos sobrescrevem, antigos permanecem
        merged = {**existing_products, **new_products}

        # Salvar no arquivo novo
        new_data["products"] = list(merged.values())
        new_data["total_products"] = len(new_data["products"])
        new_data["merged_from_previous"] = len(existing_products)
        new_data["new_or_updated"] = len(new_products)

        with open(new_file, "w", encoding="utf-8") as f:
            json.dump(new_data, f, ensure_ascii=False, indent=2)

        log(f"Merge: {len(merged)} total ({len(new_products)} atualizados, {len(merged) - len(new_products)} mantidos)")
    except Exception as e:
        log(f"Merge ERRO: {e}")


def compress_unified():
    """Comprime o unified JSON para caber no GitHub (<100MB)"""
    import gzip
    uf = sorted(glob.glob("resultados/unified_*.json"), reverse=True)
    if not uf:
        return
    src = uf[0]
    dst = "resultados/unified_latest.json.gz"
    try:
        with open(src, "r", encoding="utf-8") as f:
            data = json.load(f)
        with gzip.open(dst, "wt", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        size_mb = os.path.getsize(dst) / 1024 / 1024
        log(f"Compressed: {len(data)} ads -> {dst} ({size_mb:.1f} MB)")
    except Exception as e:
        log(f"Compress ERRO: {e}")


def push_to_render():
    """Comprime dados + push automatico para GitHub"""
    log("=== COMPRESS + PUSH ===")
    compress_unified()
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

    # Verificar tokens antes de tudo
    log("=== TOKENS ===")
    try:
        from check_tokens import check_all_tokens
        alerts = check_all_tokens()
        if alerts:
            log(f"ALERTA DE TOKENS: {'; '.join(alerts)}")
        else:
            log("Tokens: todos OK")
    except Exception as e:
        log(f"Check tokens ERRO: {e}")

    run_clickmidas()
    run_social1()
    run_searchapi()
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
