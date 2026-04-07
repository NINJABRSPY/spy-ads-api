// ClickMidas Full Scraper - uses Wix table component IDs
const WebSocket = require('ws');
const http = require('http');
const fs = require('fs');

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

  ws.on('open', async () => {
    console.log('Connected to ClickMidas!\n');

    // Clear cache and hard reload to fix pagination issues
    console.log('Clearing cache + hard reload...');
    await send('Network.clearBrowserCache');
    await send('Network.setCacheDisabled', { cacheDisabled: true });

    // Navigate fresh to midas-score
    await send('Page.navigate', { url: 'https://www.clickmidas.com.br/midas-score' });
    await new Promise(r => setTimeout(r, 10000));

    // Re-enable cache after load
    await send('Network.setCacheDisabled', { cacheDisabled: false });

    // First, discover all tables and their data
    const discoverResult = await send('Runtime.evaluate', {
      expression: `
        (function() {
          const tables = document.querySelectorAll('.wixui-table');
          const result = [];
          for (const table of tables) {
            const id = table.id || table.parentElement?.id || 'unknown';
            const pagination = table.querySelector('.wixui-table__pagination');
            const pageText = pagination ? pagination.textContent.trim() : '';
            const rect = table.getBoundingClientRect();

            // Get headers
            const headers = Array.from(table.querySelectorAll('th, [role="columnheader"]'))
              .map(h => h.textContent.trim());

            // Get rows
            const rows = Array.from(table.querySelectorAll('tbody tr, [role="row"]'))
              .slice(0, 3) // just first 3 for preview
              .map(r => Array.from(r.querySelectorAll('td, [role="cell"]')).map(c => c.textContent.trim()));

            result.push({
              id, pageText, y: Math.round(rect.y), h: Math.round(rect.height),
              visible: rect.height > 0 && rect.width > 0,
              headers, sampleRows: rows
            });
          }
          return JSON.stringify(result, null, 2);
        })()
      `,
      returnByValue: true
    });

    console.log('=== DISCOVERED TABLES ===');
    console.log(discoverResult.result.value);
    const tables = JSON.parse(discoverResult.result.value);

    // Now scrape data from each table by clicking their specific Next buttons
    const allProducts = [];

    for (const table of tables) {
      if (!table.visible || !table.pageText) continue;

      const pageMatch = table.pageText.match(/Page (\d+) of (\d+)/);
      if (!pageMatch) continue;
      const totalPages = parseInt(pageMatch[2]);

      console.log(`\n=== TABLE ${table.id} (${totalPages} pages) ===`);
      console.log(`  Headers: ${table.headers.join(' | ')}`);

      // Extract all pages from this specific table
      let lastPageNum = 0;
      for (let page = 1; page <= totalPages; page++) {
        if (page > 1) {
          // Click Next for THIS specific table
          await send('Runtime.evaluate', {
            expression: `
              (function() {
                const table = document.getElementById('${table.id}') || document.querySelector('#${table.id}');
                if (!table) return 'table not found';
                const nextBtn = table.querySelector('button[aria-label="Next"]');
                if (nextBtn && !nextBtn.disabled) { nextBtn.click(); return 'ok'; }
                return 'no next button';
              })()
            `,
            returnByValue: true
          });
          await new Promise(r => setTimeout(r, 3000));
        }

        // Extract rows from this specific table
        const rowResult = await send('Runtime.evaluate', {
          expression: `
            (function() {
              const table = document.getElementById('${table.id}') || document.querySelector('#${table.id}');
              if (!table) return JSON.stringify({error: 'table not found'});

              const pageEl = table.querySelector('.wixui-table__pagination');
              const pageText = pageEl ? pageEl.textContent.trim() : '';
              const pageMatch = pageText.match(/Page (\\d+) of (\\d+)/);
              const currentPage = pageMatch ? parseInt(pageMatch[1]) : 0;

              const rows = [];
              const trs = table.querySelectorAll('tbody tr, [data-testid="table-row"]');
              for (const tr of trs) {
                const cells = Array.from(tr.querySelectorAll('td, [role="cell"]'))
                  .map(c => c.textContent.trim());
                if (cells.length >= 2) rows.push(cells);
              }

              return JSON.stringify({ currentPage, rowCount: rows.length, rows });
            })()
          `,
          returnByValue: true
        });

        let pd;
        try {
          pd = JSON.parse(rowResult.result.value);
        } catch {
          console.log(`  Parse error on page ${page}, skipping.`);
          continue;
        }

        if (!pd || !pd.rows || !Array.isArray(pd.rows)) {
          console.log(`  No rows on page ${page}, skipping.`);
          continue;
        }

        // Detect stuck pagination
        if (pd.currentPage === lastPageNum && page > 1) {
          console.log(`  Stuck at page ${pd.currentPage}, stopping table.`);
          break;
        }
        lastPageNum = pd.currentPage;

        // Parse rows into products based on table headers
        for (const cells of pd.rows) {
          if (cells.length >= 2 && cells[0]) {
            const product = { name: cells[0], table_id: table.id };

            // Map cells to headers dynamically
            const hdrs = table.headers;
            for (let h = 1; h < hdrs.length && h < cells.length; h++) {
              const header = hdrs[h].toLowerCase();
              const val = parseFloat(cells[h]) || 0;

              if (header === 'grav.' || header === 'temp.') product.gravity = val;
              else if (header.includes('1d')) product.gravity_1d = val;
              else if (header.includes('7d')) product.gravity_7d = val;
              else if (header.includes('30d')) product.gravity_30d = val;
              else if (header.includes('midas')) product.midas_score = val;
              else if (header.includes('visitas')) product.traffic = val;
              else if (header.includes('comiss')) product.max_commission = val;
              else if (header.includes('moeda')) product.currency = cells[h];
              else if (header.includes('avalia')) product.rating = val;
              else if (header.includes('nota')) product.overall_score = val;
            }

            allProducts.push(product);
          }
        }

        if (page % 5 === 0 || page === 1) {
          console.log(`  Page ${pd.currentPage}/${totalPages} - ${pd.rowCount} rows`);
        }
      }
    }

    // Deduplicate - merge ALL fields from all tables
    const productMap = {};
    for (const p of allProducts) {
      const tableId = p.table_id;
      delete p.table_id;

      if (!productMap[p.name]) {
        productMap[p.name] = { ...p, tables: [tableId] };
      } else {
        // Merge: keep non-zero/non-empty values from each table
        productMap[p.name].tables.push(tableId);
        for (const [k, v] of Object.entries(p)) {
          if (k === 'name') continue;
          if (v && v !== 0 && (!productMap[p.name][k] || productMap[p.name][k] === 0)) {
            productMap[p.name][k] = v;
          }
        }
      }
    }

    const finalProducts = Object.values(productMap);

    const output = {
      source: 'clickmidas',
      platform_source: 'clickbank',
      scraped_at: new Date().toISOString(),
      total_products: finalProducts.length,
      total_raw: allProducts.length,
      products: finalProducts
    };

    const filename = `resultados/clickmidas_${new Date().toISOString().slice(0, 10).replace(/-/g, '')}.json`;
    fs.writeFileSync(filename, JSON.stringify(output, null, 2));
    console.log(`\n========== DONE ==========`);
    console.log(`Total raw rows: ${allProducts.length}`);
    console.log(`Unique products: ${finalProducts.length}`);
    console.log(`Saved to: ${filename}`);

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
