const WebSocket = require('ws');
const http = require('http');

async function main() {
  const tabs = await new Promise((resolve) => {
    http.get('http://localhost:9222/json', res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve(JSON.parse(d).filter(t => t.type === 'page')));
    });
  });

  const fm = tabs.find(p => p.url.includes('fastmoss.com'));
  const ws = new WebSocket(fm.webSocketDebuggerUrl);
  let msgId = 1;
  const pending = {};
  const apiCalls = [];

  function send(method, params = {}) {
    return new Promise((resolve) => {
      const id = msgId++;
      pending[id] = resolve;
      ws.send(JSON.stringify({ id, method, params }));
    });
  }

  ws.on('message', (data) => {
    const msg = JSON.parse(data.toString());
    if (msg.id && pending[msg.id]) { pending[msg.id](msg.result); delete pending[msg.id]; }

    if (msg.method === 'Network.requestWillBeSent') {
      const url = msg.params.request.url;
      if (url.includes('fastmoss.com/api/') &&
          !url.includes('notify') && !url.includes('userInfo') &&
          !url.includes('userPay') && !url.includes('getUserCards') &&
          !url.includes('user/user') && !url.includes('info/handle')) {
        apiCalls.push({
          method: msg.params.request.method,
          url,
          postData: msg.params.request.postData || null,
        });
        console.log(`[API] ${msg.params.request.method} ${url}`);
        if (msg.params.request.postData) console.log(`  Body: ${msg.params.request.postData.substring(0, 300)}`);
      }
    }
  });

  async function ev(expr) {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    return r.result.value;
  }

  ws.on('open', async () => {
    console.log('Connected!\n');
    await send('Network.enable');

    // Go to dashboard first
    await send('Page.navigate', { url: 'https://www.fastmoss.com/dashboard' });
    await new Promise(r => setTimeout(r, 8000));

    // Find all navigation links
    const navLinks = await ev(`
      (function() {
        const links = document.querySelectorAll('a[href]');
        const relevant = [];
        for (const a of links) {
          const href = a.href || '';
          const text = a.textContent.trim();
          if (href.includes('fastmoss.com') && text.length > 0 && text.length < 50 &&
              !href.includes('pricing') && !href.includes('account') && !href.includes('login')) {
            relevant.push({ text, href });
          }
        }
        return JSON.stringify([...new Map(relevant.map(r => [r.href, r])).values()].slice(0, 30));
      })()
    `);
    console.log('=== NAVIGATION LINKS ===');
    JSON.parse(navLinks).forEach(l => console.log(`  ${l.text} -> ${l.href}`));

    // Click on "Find Products" or "Top Selling Products" in sidebar
    console.log('\n=== CLICKING TOP SELLING PRODUCTS ===');
    const clickResult = await ev(`
      (function() {
        const links = document.querySelectorAll('a');
        for (const a of links) {
          const text = a.textContent.trim();
          if (text === 'Top Selling Products' || text === 'Find Products' || text.includes('Top-Selling')) {
            a.click();
            return 'clicked: ' + text + ' -> ' + a.href;
          }
        }
        // Try sidebar items
        const items = document.querySelectorAll('[class*="menu"] a, [class*="sidebar"] a, [class*="nav"] a, nav a');
        for (const a of items) {
          const text = a.textContent.trim();
          if (text.includes('Product') || text.includes('product')) {
            a.click();
            return 'clicked sidebar: ' + text + ' -> ' + a.href;
          }
        }
        return 'nothing found to click';
      })()
    `);
    console.log(clickResult);
    await new Promise(r => setTimeout(r, 8000));

    // Check URL and content after click
    const afterClick = await ev(`JSON.stringify({url: window.location.href, title: document.title, body: document.body.innerText.substring(0, 800)})`);
    const ac = JSON.parse(afterClick);
    console.log('URL:', ac.url);
    console.log('Content:', ac.body.substring(0, 500));

    // Now click on Creators
    console.log('\n=== CLICKING CREATORS ===');
    const clickCreator = await ev(`
      (function() {
        const links = document.querySelectorAll('a');
        for (const a of links) {
          const text = a.textContent.trim();
          if (text === 'Find Creators' || text === 'Creator' || text.includes('Top Ecommerce Creators')) {
            a.click();
            return 'clicked: ' + text + ' -> ' + a.href;
          }
        }
        return 'not found';
      })()
    `);
    console.log(clickCreator);
    await new Promise(r => setTimeout(r, 8000));

    const afterCreator = await ev(`JSON.stringify({url: window.location.href, body: document.body.innerText.substring(0, 500)})`);
    console.log('URL:', JSON.parse(afterCreator).url);
    console.log('Content:', JSON.parse(afterCreator).body.substring(0, 300));

    // Click on Shops
    console.log('\n=== CLICKING SHOPS ===');
    const clickShop = await ev(`
      (function() {
        const links = document.querySelectorAll('a');
        for (const a of links) {
          const text = a.textContent.trim();
          if (text === 'Find Shops' || text.includes('Top Selling TikTok Shops')) {
            a.click();
            return 'clicked: ' + text + ' -> ' + a.href;
          }
        }
        return 'not found';
      })()
    `);
    console.log(clickShop);
    await new Promise(r => setTimeout(r, 8000));

    const afterShop = await ev(`JSON.stringify({url: window.location.href, body: document.body.innerText.substring(0, 500)})`);
    console.log('URL:', JSON.parse(afterShop).url);
    console.log('Content:', JSON.parse(afterShop).body.substring(0, 300));

    // Summary
    console.log('\n\n=== ALL API ENDPOINTS DISCOVERED ===');
    const unique = [...new Set(apiCalls.map(a => `${a.method} ${a.url.split('?')[0]}`))];
    unique.forEach(u => console.log(`  ${u}`));
    console.log(`\nTotal: ${apiCalls.length} calls, ${unique.length} unique`);

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
