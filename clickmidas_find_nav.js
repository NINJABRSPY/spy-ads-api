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

    // Get ALL links on the page
    const result = await send('Runtime.evaluate', {
      expression: `
        (function() {
          const links = document.querySelectorAll('a');
          const all = [];
          for (const a of links) {
            const text = a.textContent.trim();
            const href = a.href || '';
            if (text && href && href.includes('clickmidas')) {
              all.push({ text: text.substring(0, 80), href });
            }
          }
          return JSON.stringify(all, null, 2);
        })()
      `,
      returnByValue: true
    });

    console.log('All ClickMidas links:');
    console.log(result.result.value);

    // Also look at the full body text to find where the platform names appear
    const bodyResult = await send('Runtime.evaluate', {
      expression: `
        (function() {
          const text = document.body.innerText;
          // Find lines containing platform names
          const lines = text.split('\\n');
          const relevant = lines.filter(l =>
            l.includes('BuyGoods') || l.includes('Digistore') || l.includes('MaxWeb') ||
            l.includes('Hotmart') || l.includes('ClickBank')
          );
          return JSON.stringify(relevant);
        })()
      `,
      returnByValue: true
    });

    console.log('\nLines with platform names:');
    console.log(bodyResult.result.value);

    // Check for the sidebar/hamburger menu - it might be hidden
    const menuResult = await send('Runtime.evaluate', {
      expression: `
        (function() {
          // Try to open hamburger menu first
          const hamburger = document.querySelector('[class*="hamburger"], [class*="Hamburger"], [aria-label*="menu"], [aria-label*="Menu"], button[class*="menu"]');
          if (hamburger) {
            hamburger.click();
            return 'clicked hamburger';
          }

          // Look for the MENU text element
          const allEls = document.querySelectorAll('*');
          for (const el of allEls) {
            if (el.textContent.trim() === '☰  MENU' || el.textContent.trim() === 'MENU') {
              el.click();
              return 'clicked MENU text: ' + el.tagName;
            }
          }
          return 'no menu found';
        })()
      `,
      returnByValue: true
    });

    console.log('\nMenu click:', menuResult.result.value);

    // Wait and check what appeared
    await new Promise(r => setTimeout(r, 2000));

    const afterMenuResult = await send('Runtime.evaluate', {
      expression: `
        (function() {
          const links = document.querySelectorAll('a');
          const all = [];
          for (const a of links) {
            const text = a.textContent.trim();
            const href = a.href || '';
            const rect = a.getBoundingClientRect();
            if (text && href && href.includes('clickmidas') && rect.height > 0) {
              all.push({ text: text.substring(0, 80), href, visible: rect.width > 0 });
            }
          }
          return JSON.stringify(all, null, 2);
        })()
      `,
      returnByValue: true
    });

    console.log('\nAfter menu click - visible links:');
    console.log(afterMenuResult.result.value);

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
