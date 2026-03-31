// Discover platform tabs in ClickMidas
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

    // Check current URL
    const urlR = await send('Runtime.evaluate', { expression: 'window.location.href', returnByValue: true });
    console.log('Current URL:', urlR.result.value);

    // Find platform navigation links/buttons
    const result = await send('Runtime.evaluate', {
      expression: `
        (function() {
          // Find all links and buttons with platform names
          const platforms = ['BuyGoods', 'Digistore', 'MaxWeb', 'Hotmart', 'ClickBank'];
          const found = [];

          // Check links
          const links = document.querySelectorAll('a');
          for (const a of links) {
            const text = a.textContent.trim();
            const href = a.href || '';
            for (const p of platforms) {
              if (text.toLowerCase().includes(p.toLowerCase()) || href.toLowerCase().includes(p.toLowerCase())) {
                found.push({ type: 'link', text: text.substring(0, 60), href, tag: 'A' });
              }
            }
          }

          // Check buttons
          const btns = document.querySelectorAll('button, [role="button"], [role="menuitem"]');
          for (const b of btns) {
            const text = b.textContent.trim();
            const label = b.getAttribute('aria-label') || '';
            for (const p of platforms) {
              if (text.toLowerCase().includes(p.toLowerCase()) || label.toLowerCase().includes(p.toLowerCase())) {
                const rect = b.getBoundingClientRect();
                found.push({
                  type: 'button', text: text.substring(0, 60),
                  label, tag: b.tagName, y: Math.round(rect.y), x: Math.round(rect.x),
                  class: (b.className || '').toString().substring(0, 80)
                });
              }
            }
          }

          // Check menu items - the sidebar menu from earlier discovery
          const menuItems = document.querySelectorAll('[data-testid*="menu"], nav a, nav button, [class*="menu"] a');
          for (const m of menuItems) {
            const text = m.textContent.trim();
            if (text.length < 50) {
              found.push({ type: 'menu', text, href: m.href || '', tag: m.tagName });
            }
          }

          return JSON.stringify(found, null, 2);
        })()
      `,
      returnByValue: true
    });

    console.log('Platform elements found:');
    console.log(result.result.value);

    // Also get the full nav/menu structure
    const navResult = await send('Runtime.evaluate', {
      expression: `
        (function() {
          // Get the sidebar menu text
          const nav = document.querySelector('nav') || document.querySelector('[class*="menu"]');
          if (nav) return nav.innerText.substring(0, 1000);

          // Try finding menu by looking at the body text for menu-like patterns
          const text = document.body.innerText;
          const menuSection = text.substring(0, 500);
          return menuSection;
        })()
      `,
      returnByValue: true
    });

    console.log('\nMenu/nav text:');
    console.log(navResult.result.value);

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
