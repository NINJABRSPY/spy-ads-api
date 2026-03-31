// Quick test - parse current page + try next page
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

    // Parse current page
    const result = await send('Runtime.evaluate', {
      expression: `
        (function() {
          const text = document.body.innerText;
          const lines = text.split('\\n').map(l => l.trim()).filter(l => l);
          const headerIdx = lines.findIndex(l => l.includes('Nome do Produto'));

          // Show lines around header to understand structure
          const context = lines.slice(Math.max(0, headerIdx - 2), headerIdx + 40);
          return JSON.stringify({
            url: window.location.href,
            headerIdx,
            linesAroundHeader: context,
            pageInfo: text.match(/Page \\d+ of \\d+/)?.[0] || 'not found'
          }, null, 2);
        })()
      `,
      returnByValue: true
    });

    const data = JSON.parse(result.result.value);
    console.log('URL:', data.url);
    console.log('Page:', data.pageInfo);
    console.log('\nLines around header:');
    data.linesAroundHeader.forEach((l, i) => console.log(`  ${i}: "${l}"`));

    // Now try clicking next page
    console.log('\n--- Trying to find pagination ---');
    const pagResult = await send('Runtime.evaluate', {
      expression: `
        (function() {
          // Look at ALL clickable elements near "Page X of Y"
          const allEls = document.querySelectorAll('button, a, [role="button"], span[tabindex], div[tabindex]');
          const pageRelated = [];
          for (const el of allEls) {
            const text = el.textContent.trim();
            const rect = el.getBoundingClientRect();
            if (rect.y > 500) { // likely near bottom where pagination is
              pageRelated.push({
                tag: el.tagName,
                text: text.substring(0, 50),
                class: (el.className || '').toString().substring(0, 60),
                y: Math.round(rect.y),
                ariaLabel: el.getAttribute('aria-label') || ''
              });
            }
          }
          return JSON.stringify(pageRelated.slice(0, 30), null, 2);
        })()
      `,
      returnByValue: true
    });

    console.log('Bottom elements:');
    console.log(pagResult.result.value);

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
