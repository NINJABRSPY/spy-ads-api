// ClickMidas Scraper for Digistore24 + MaxWeb
// Navigates fresh for each platform to avoid stale pagination
const WebSocket = require('ws');
const http = require('http');
const fs = require('fs');

const PLATFORMS = [
  { name: 'digistore24', tabId: 'tab-comp-lqo80sdn' },
  { name: 'maxweb', tabId: 'tab-comp-lx0k7b52' },
];

async function getWsUrl() {
  return new Promise((resolve, reject) => {
    http.get('http://localhost:9222/json', res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => {
        const pages = JSON.parse(d).filter(t => t.type === 'page');
        const midas = pages.find(p => p.url.includes('clickmidas'));
        if (midas) resolve(midas.webSocketDebuggerUrl);
        else reject('ClickMidas tab not found');
      });
    });
  });
}

async function main() {
  const wsUrl = await getWsUrl();
  const ws = new WebSocket(wsUrl);
  let msgId = 1;
  const pending = {};

  function send(method, params = {}) {
    return new Promise((resolve) => {
      const id = msgId++;
      pending[id] = resolve;
      ws.send(JSON.stringify({ id, method, params }));
    });
  }

  ws.on('message', (data) => {
    const msg = JSON.parse(data.toString());
    if (msg.id && pending[msg.id]) {
      pending[msg.id](msg.result);
      delete pending[msg.id];
    }
  });

  async function ev(expr) {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true });
    return r.result.value;
  }

  async function evJSON(expr) {
    return JSON.parse(await ev(expr));
  }

  ws.on('open', async () => {
    console.log('Connected!\n');

    for (const platform of PLATFORMS) {
      console.log(`\n${'='.repeat(50)}`);
      console.log(`  ${platform.name.toUpperCase()}`);
      console.log(`${'='.repeat(50)}`);

      // FRESH page load to reset all pagination
      console.log('Loading fresh page...');
      await send('Page.navigate', { url: 'https://www.clickmidas.com.br/midas-score' });
      await new Promise(r => setTimeout(r, 10000));

      // Click the platform tab
      const clickResult = await ev(`
        (function() {
          const tab = document.getElementById('${platform.tabId}');
          if (tab) { tab.click(); return 'clicked ' + tab.textContent.trim(); }
          return 'not found';
        })()
      `);
      console.log(`Tab: ${clickResult}`);
      await new Promise(r => setTimeout(r, 6000));

      // Discover visible tables
      const tables = await evJSON(`
        (function() {
          const tables = document.querySelectorAll('.wixui-table');
          const result = [];
          for (const table of tables) {
            const id = table.id || 'unknown';
            const rect = table.getBoundingClientRect();
            if (rect.height <= 0 || rect.width <= 0) continue;

            const pagination = table.querySelector('.wixui-table__pagination');
            const pageText = pagination ? pagination.textContent.trim() : '';
            const pageMatch = pageText.match(/Page (\\d+) of (\\d+)/);

            const headers = Array.from(table.querySelectorAll('th, [role="columnheader"]'))
              .map(h => h.textContent.trim()).filter(h => h);

            if (headers.length > 0 && pageMatch) {
              result.push({
                id,
                currentPage: parseInt(pageMatch[1]),
                totalPages: parseInt(pageMatch[2]),
                headers,
                y: Math.round(rect.y)
              });
            }
          }
          return JSON.stringify(result);
        })()
      `);

      console.log(`Found ${tables.length} tables:`);
      tables.forEach(t => console.log(`  [${t.id}] pg ${t.currentPage}/${t.totalPages} | ${t.headers.join(' | ')}`));

      const allProducts = [];

      for (const table of tables) {
        console.log(`\n--- ${table.id} (${table.totalPages} pages, headers: ${table.headers.length}) ---`);

        let lastPageNum = 0;
        for (let page = 1; page <= table.totalPages; page++) {
          if (page > 1) {
            await ev(`
              (function() {
                const t = document.getElementById('${table.id}');
                if (!t) return 'no table';
                const btn = t.querySelector('button[aria-label="Next"]');
                if (btn && !btn.disabled) { btn.click(); return 'ok'; }
                return 'no btn';
              })()
            `);
            await new Promise(r => setTimeout(r, 3000));
          }

          const pageData = await evJSON(`
            (function() {
              const t = document.getElementById('${table.id}');
              if (!t) return JSON.stringify({currentPage: 0, rows: []});
              const pe = t.querySelector('.wixui-table__pagination');
              const pt = pe ? pe.textContent.trim() : '';
              const pm = pt.match(/Page (\\d+) of (\\d+)/);
              const cp = pm ? parseInt(pm[1]) : 0;
              const rows = [];
              const trs = t.querySelectorAll('tbody tr');
              for (const tr of trs) {
                const cells = Array.from(tr.querySelectorAll('td')).map(c => c.textContent.trim());
                if (cells.length >= 2 && cells[0]) rows.push(cells);
              }
              return JSON.stringify({ currentPage: cp, rowCount: rows.length, rows });
            })()
          `);

          if (pageData.currentPage === lastPageNum && page > 1) {
            console.log(`  Stuck at page ${pageData.currentPage}, stopping.`);
            break;
          }
          lastPageNum = pageData.currentPage;

          for (const cells of pageData.rows) {
            const product = {};
            table.headers.forEach((h, i) => {
              if (i < cells.length) product[h] = cells[i];
            });
            allProducts.push(product);
          }

          if (page % 10 === 0 || page === 1 || page === table.totalPages) {
            console.log(`  Page ${pageData.currentPage}/${table.totalPages} - ${pageData.rowCount} rows (total: ${allProducts.length})`);
          }
        }
      }

      // Normalize
      const normalized = allProducts.map(p => {
        const result = { name: p['Nome do Produto'] || '' };
        for (const [key, val] of Object.entries(p)) {
          if (key === 'Nome do Produto') continue;
          const kl = key.toLowerCase();
          if (kl.includes('grav') && kl.includes('1d')) result.gravity_1d = parseFloat(val) || 0;
          else if (kl.includes('grav') && kl.includes('7d')) result.gravity_7d = parseFloat(val) || 0;
          else if (kl.includes('grav') && kl.includes('30d')) result.gravity_30d = parseFloat(val) || 0;
          else if (kl === 'grav.' || kl === 'gravidade') result.gravity = parseFloat(val) || 0;
          else if (kl.includes('temp') && kl.includes('1d')) result.gravity_1d = parseFloat(val) || 0;
          else if (kl.includes('temp') && kl.includes('7d')) result.gravity_7d = parseFloat(val) || 0;
          else if (kl.includes('temp') && kl.includes('30d')) result.gravity_30d = parseFloat(val) || 0;
          else if (kl === 'temp.') result.gravity = parseFloat(val) || 0;
          else if (kl.includes('midas')) result.midas_score = parseFloat(val) || 0;
          else if (kl.includes('visitas')) result.traffic = parseFloat(val) || 0;
          else if (kl.includes('comiss')) result.max_commission = parseFloat(val) || 0;
          else if (kl.includes('moeda')) result.currency = val;
          else if (kl.includes('avalia')) result.rating = parseFloat(val) || 0;
          else if (kl.includes('nota')) result.overall_score = parseFloat(val) || 0;
        }
        return result;
      }).filter(p => p.name);

      // Deduplicate
      const uniqueMap = {};
      for (const p of normalized) {
        if (!uniqueMap[p.name]) {
          uniqueMap[p.name] = p;
        } else {
          for (const [k, v] of Object.entries(p)) {
            if (v && v !== 0 && !uniqueMap[p.name][k]) uniqueMap[p.name][k] = v;
          }
        }
      }
      const unique = Object.values(uniqueMap);

      const output = {
        source: 'clickmidas',
        platform_source: platform.name,
        scraped_at: new Date().toISOString(),
        total_products: unique.length,
        total_raw: normalized.length,
        products: unique
      };

      const filename = `resultados/clickmidas_${platform.name}_${new Date().toISOString().slice(0, 10).replace(/-/g, '')}.json`;
      fs.writeFileSync(filename, JSON.stringify(output, null, 2));
      console.log(`\nSaved: ${unique.length} unique products -> ${filename}`);
    }

    console.log('\n=== ALL DONE ===');
    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
