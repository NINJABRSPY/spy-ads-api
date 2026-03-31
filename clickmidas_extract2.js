// Extract ClickMidas data - wait for full render
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
    console.log('Connected! Navigating to ClickMidas...\n');

    // Navigate
    await send('Page.navigate', { url: 'https://www.clickmidas.com.br/' });

    // Wait for full render (Wix sites are slow)
    console.log('Waiting 15s for full page render...');
    await new Promise(r => setTimeout(r, 15000));

    // Try to get page content with multiple strategies
    const result = await send('Runtime.evaluate', {
      expression: `
        (function() {
          const output = {};

          // Strategy 1: Get all text content organized by visible elements
          const allElements = document.querySelectorAll('*');
          const visibleTexts = [];
          for (const el of allElements) {
            if (el.children.length === 0 || el.tagName === 'SPAN' || el.tagName === 'P') {
              const text = el.textContent.trim();
              const rect = el.getBoundingClientRect();
              if (text && text.length > 1 && text.length < 500 && rect.height > 0 && rect.width > 0) {
                visibleTexts.push({
                  tag: el.tagName,
                  text: text.substring(0, 200),
                  x: Math.round(rect.x),
                  y: Math.round(rect.y),
                  className: (el.className || '').toString().substring(0, 80)
                });
              }
            }
          }
          output.visibleTexts = visibleTexts.slice(0, 300);

          // Strategy 2: Look for Wix Data Viewer / Table
          const wixTables = document.querySelectorAll('[data-testid*="table"], [data-testid*="grid"], [data-testid*="repeater"], [class*="table"], [class*="Table"]');
          output.wixTableElements = Array.from(wixTables).map(el => ({
            testid: el.getAttribute('data-testid'),
            className: (el.className || '').toString().substring(0, 100),
            childCount: el.children.length,
            innerHTML: el.innerHTML.substring(0, 500)
          }));

          // Strategy 3: Check for iframes that might contain the data
          output.iframes = Array.from(document.querySelectorAll('iframe')).map(f => ({
            src: f.src,
            id: f.id
          }));

          // Strategy 4: Get full body text to find data patterns
          output.bodyText = document.body.innerText.substring(0, 5000);

          // Strategy 5: Check document.querySelector for Wix specific containers
          const containers = document.querySelectorAll('[id*="comp-"], [id*="dataItem"]');
          output.wixContainers = Array.from(containers).slice(0, 30).map(el => ({
            id: el.id,
            tag: el.tagName,
            text: el.textContent.substring(0, 150).trim(),
            childCount: el.children.length
          }));

          return JSON.stringify(output, null, 2);
        })()
      `,
      returnByValue: true
    });

    const data = JSON.parse(result.result.value);

    console.log('=== BODY TEXT (first 3000 chars) ===');
    console.log(data.bodyText.substring(0, 3000));

    console.log('\n=== IFRAMES ===');
    console.log(JSON.stringify(data.iframes, null, 2));

    console.log('\n=== WIX TABLES ===');
    console.log(JSON.stringify(data.wixTableElements, null, 2));

    console.log('\n=== WIX CONTAINERS (with text) ===');
    data.wixContainers.filter(c => c.text.length > 5).forEach(c => {
      console.log(`[${c.id}] ${c.text.substring(0, 120)}`);
    });

    fs.writeFileSync('clickmidas_page_data.json', JSON.stringify(data, null, 2));
    console.log('\nFull data saved to clickmidas_page_data.json');

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
