// Extract ClickMidas data from DOM via Chrome DevTools Protocol
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
  console.log('Connecting to:', wsUrl);

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
    console.log('Connected!\n');

    // First, let's understand the page structure
    // Navigate to the main page (top geral)
    console.log('=== Navigating to main page ===');
    await send('Page.navigate', { url: 'https://www.clickmidas.com.br/' });
    await new Promise(r => setTimeout(r, 6000));

    // Extract page structure - find tables, iframes, or data elements
    const structureResult = await send('Runtime.evaluate', {
      expression: `
        (function() {
          const info = {
            url: window.location.href,
            title: document.title,
            iframes: Array.from(document.querySelectorAll('iframe')).map(f => ({src: f.src, id: f.id, name: f.name})),
            tables: document.querySelectorAll('table').length,
            tableData: [],
            divWithData: [],
            allText: '',
          };

          // Check for tables
          document.querySelectorAll('table').forEach((table, i) => {
            const headers = Array.from(table.querySelectorAll('th')).map(th => th.textContent.trim());
            const rowCount = table.querySelectorAll('tr').length;
            info.tableData.push({index: i, headers, rowCount});
          });

          // Check for Wix repeaters or data grids
          const repeaters = document.querySelectorAll('[data-mesh-id], [data-testid], .comp-wrapper, [id*="comp-"]');
          info.repeaterCount = repeaters.length;

          // Look for any grid/table-like structures
          const gridElements = document.querySelectorAll('[role="grid"], [role="table"], [class*="table"], [class*="grid"], [class*="Table"], [class*="Grid"]');
          info.gridElements = Array.from(gridElements).map(el => ({
            tag: el.tagName,
            className: el.className.substring(0, 100),
            childCount: el.children.length,
            role: el.getAttribute('role')
          }));

          // Get all visible text to understand page content
          const body = document.body;
          const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
          const texts = [];
          let count = 0;
          while (walker.nextNode() && count < 200) {
            const text = walker.currentNode.textContent.trim();
            if (text.length > 2 && text.length < 200) {
              texts.push(text);
              count++;
            }
          }
          info.visibleTexts = texts;

          return JSON.stringify(info, null, 2);
        })()
      `,
      returnByValue: true
    });

    console.log('Page structure:');
    console.log(structureResult.result.value);

    // Save result
    fs.writeFileSync('clickmidas_structure.json', structureResult.result.value);
    console.log('\nSaved to clickmidas_structure.json');

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
