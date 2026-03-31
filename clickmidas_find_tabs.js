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

    // Find elements containing exactly "BuyGoods", "Digistore24", "MaxWeb"
    const result = await send('Runtime.evaluate', {
      expression: `
        (function() {
          const targets = ['ClickBank', 'BuyGoods', 'Hotmart', 'Digistore24', 'MaxWeb'];
          const found = [];

          const allEls = document.querySelectorAll('*');
          for (const el of allEls) {
            // Only check leaf-ish elements or those with short text
            const text = el.textContent.trim();
            if (targets.includes(text)) {
              const rect = el.getBoundingClientRect();
              found.push({
                text,
                tag: el.tagName,
                id: el.id || '',
                class: (el.className || '').toString().substring(0, 100),
                role: el.getAttribute('role') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                tabindex: el.getAttribute('tabindex'),
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                w: Math.round(rect.width),
                h: Math.round(rect.height),
                visible: rect.height > 0 && rect.width > 0,
                clickable: el.onclick !== null || el.tagName === 'BUTTON' || el.tagName === 'A' || el.getAttribute('role') === 'button' || el.getAttribute('tabindex') !== null,
                parentTag: el.parentElement ? el.parentElement.tagName : '',
                parentClass: el.parentElement ? (el.parentElement.className || '').toString().substring(0, 80) : '',
                parentRole: el.parentElement ? (el.parentElement.getAttribute('role') || '') : ''
              });
            }
          }
          return JSON.stringify(found, null, 2);
        })()
      `,
      returnByValue: true
    });

    console.log('Platform tab elements:');
    console.log(result.result.value);

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
