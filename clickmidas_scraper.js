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

    // Navigate to midas-score page
    await send('Page.navigate', { url: 'https://www.clickmidas.com.br/midas-score' });
    await new Promise(r => setTimeout(r, 8000));

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

        const pd = JSON.parse(rowResult.result.value);

        // Detect stuck pagination
        if (pd.currentPage === lastPageNum && page > 1) {
          console.log(`  Stuck at page ${pd.currentPage}, stopping table.`);
          break;
        }
        lastPageNum = pd.currentPage;

        // Parse rows into products
        for (const cells of pd.rows) {
          if (cells.length >= 6) {
            allProducts.push({
              name: cells[0],
              gravity: parseFloat(cells[1]) || 0,
              gravity_1d: parseFloat(cells[2]) || 0,
              gravity_7d: parseFloat(cells[3]) || 0,
              gravity_30d: parseFloat(cells[4]) || 0,
              midas_score: parseFloat(cells[5]) || 0,
              table_id: table.id
            });
          }
        }

        if (page % 5 === 0 || page === 1) {
          console.log(`  Page ${pd.currentPage}/${totalPages} - ${pd.rowCount} rows`);
        }
      }
    }

    // Deduplicate
    const productMap = {};
    for (const p of allProducts) {
      if (!productMap[p.name]) {
        productMap[p.name] = {
          name: p.name,
          gravity: p.gravity,
          gravity_1d: p.gravity_1d,
          gravity_7d: p.gravity_7d,
          gravity_30d: p.gravity_30d,
          midas_score: p.midas_score,
          tables: [p.table_id]
        };
      } else {
        productMap[p.name].tables.push(p.table_id);
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
