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
  });

  async function ev(expr) {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    return r.result.value;
  }

  ws.on('open', async () => {
    console.log('Connected!\n');
    await send('Network.enable');

    const allApiCalls = [];
    ws.on('message', (data) => {
      const msg = JSON.parse(data.toString());
      if (msg.method === 'Network.requestWillBeSent') {
        const url = msg.params.request.url;
        if (url.includes('fastmoss.com/api/') && !url.includes('notify') && !url.includes('userInfo') && !url.includes('userPay')) {
          allApiCalls.push({
            method: msg.params.request.method,
            url,
            postData: msg.params.request.postData || null,
          });
          console.log(`[API] ${msg.params.request.method} ${url.substring(0, 200)}`);
          if (msg.params.request.postData) console.log(`  Body: ${msg.params.request.postData.substring(0, 200)}`);
        }
      }
    });

    // Pages to visit with longer wait
    const pages = [
      { name: 'Products - Top Selling', url: 'https://www.fastmoss.com/products/top-selling' },
      { name: 'Products - Most Promoted', url: 'https://www.fastmoss.com/products/most-promoted' },
      { name: 'Creators - Top', url: 'https://www.fastmoss.com/creators' },
      { name: 'Videos', url: 'https://www.fastmoss.com/videos' },
      { name: 'Shops - Top', url: 'https://www.fastmoss.com/shops/top-selling' },
      { name: 'Ads', url: 'https://www.fastmoss.com/ads' },
      { name: 'LIVE', url: 'https://www.fastmoss.com/live' },
    ];

    for (const page of pages) {
      console.log(`\n=== ${page.name} ===`);
      await send('Page.navigate', { url: page.url });
      await new Promise(r => setTimeout(r, 10000));

      // Get page content
      const body = await ev('document.body.innerText.substring(0, 1500)');
      console.log('Content:', body.substring(0, 500));
    }

    // Also try direct API calls we can guess
    console.log('\n=== TESTING DIRECT API ENDPOINTS ===\n');
    const testEndpoints = [
      '/api/product/index/topSelling?page=1&pagesize=20&country=US&_time=' + Math.floor(Date.now()/1000),
      '/api/product/index/mostPromoted?page=1&pagesize=20&country=US&_time=' + Math.floor(Date.now()/1000),
      '/api/author/index/topCreator?page=1&pagesize=20&country=US&_time=' + Math.floor(Date.now()/1000),
      '/api/shop/index/topSelling?page=1&pagesize=20&country=US&_time=' + Math.floor(Date.now()/1000),
      '/api/video/index/list?page=1&pagesize=20&country=US&_time=' + Math.floor(Date.now()/1000),
      '/api/ads/index/list?page=1&pagesize=20&country=US&_time=' + Math.floor(Date.now()/1000),
    ];

    for (const ep of testEndpoints) {
      const result = await ev(`
        (async () => {
          try {
            const r = await fetch("${ep}");
            const t = await r.text();
            return t.substring(0, 500);
          } catch(e) { return 'ERROR: ' + e.message; }
        })()
      `);
      console.log(`[TEST] ${ep.split('?')[0]}`);
      console.log(`  -> ${result?.substring(0, 300)}\n`);
    }

    console.log('\n=== ALL FASTMOSS API CALLS CAPTURED ===');
    const unique = [...new Set(allApiCalls.map(a => `${a.method} ${a.url.split('?')[0]}`))];
    unique.forEach(u => console.log(`  ${u}`));

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
