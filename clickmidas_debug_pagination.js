const WebSocket = require('ws');
const http = require('http');

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
    console.log('Connected!\n');

    // Navigate to midas-score fresh
    await send('Page.navigate', { url: 'https://www.clickmidas.com.br/midas-score' });
    await new Promise(r => setTimeout(r, 8000));

    // Get detailed pagination info
    const result = await send('Runtime.evaluate', {
      expression: `
        (function() {
          // Find ALL pagination-related elements
          const allBtns = document.querySelectorAll('button');
          const pagBtns = [];
          for (const b of allBtns) {
            const label = b.getAttribute('aria-label') || '';
            const text = b.textContent.trim();
            const rect = b.getBoundingClientRect();
            const cls = (b.className || '').toString();
            if (label === 'Next' || label === 'Previous' || cls.includes('zqtXId') || cls.includes('paginat')) {
              pagBtns.push({
                label, text, y: Math.round(rect.y), x: Math.round(rect.x),
                w: Math.round(rect.width), h: Math.round(rect.height),
                class: cls.substring(0, 80),
                disabled: b.disabled,
                parent: b.parentElement ? b.parentElement.className.substring(0, 80) : '',
                grandparent: b.parentElement && b.parentElement.parentElement ? b.parentElement.parentElement.className.substring(0, 80) : ''
              });
            }
          }

          // Also look for page number indicators
          const pageTexts = [];
          const spans = document.querySelectorAll('span, div, p');
          for (const s of spans) {
            const t = s.textContent.trim();
            if (t.match(/^Page \\d+ of \\d+$/)) {
              const rect = s.getBoundingClientRect();
              pageTexts.push({ text: t, y: Math.round(rect.y), class: (s.className || '').toString().substring(0, 60) });
            }
          }

          // Look for page number links/buttons
          const pageNums = [];
          for (const b of allBtns) {
            const t = b.textContent.trim();
            if (t.match(/^\\d+$/) && parseInt(t) < 100) {
              const rect = b.getBoundingClientRect();
              if (rect.height > 0) {
                pageNums.push({
                  num: t, y: Math.round(rect.y), x: Math.round(rect.x),
                  class: (b.className || '').toString().substring(0, 60)
                });
              }
            }
          }

          return JSON.stringify({ pagBtns, pageTexts, pageNums }, null, 2);
        })()
      `,
      returnByValue: true
    });

    console.log(result.result.value);

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
