const WebSocket = require('ws');
const http = require('http');
const fs = require('fs');

async function main() {
  const tabs = await new Promise((resolve) => {
    http.get('http://localhost:9222/json', res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve(JSON.parse(d).filter(t => t.type === 'page')));
    });
  });

  const fm = tabs.find(p => p.url.includes('fastmoss.com'));
  if (!fm) { console.log('FastMoss tab not found'); process.exit(1); }

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
      const req = msg.params.request;
      const url = req.url;
      if (url.includes('/api/') || url.includes('/v1/') || url.includes('/v2/') ||
          (url.includes('fastmoss') && !url.match(/\.(js|css|png|jpg|svg|woff|ico|gif|webp)(\?|$)/) &&
           !url.includes('google') && !url.includes('analytics') && !url.includes('clarity') &&
           !url.includes('facebook') && !url.includes('sentry') && !url.includes('posthog'))) {
        apiCalls.push({
          url, method: req.method,
          headers: req.headers,
          postData: req.postData || null,
        });
        console.log(`[${req.method}] ${url.substring(0, 200)}`);
        if (req.postData) console.log(`  Body: ${req.postData.substring(0, 300)}`);
      }
    }
  });

  ws.on('open', async () => {
    console.log('Connected to FastMoss!\n');
    await send('Network.enable');

    // Get current page content
    const body = await send('Runtime.evaluate', {
      expression: 'document.body.innerText.substring(0, 3000)',
      returnByValue: true
    });
    console.log('=== DASHBOARD CONTENT ===');
    console.log(body.result.value.substring(0, 1500));

    // Reload to capture all API calls
    console.log('\n=== RELOADING ===\n');
    await send('Page.reload');
    await new Promise(r => setTimeout(r, 8000));

    // Navigate to products page
    console.log('\n=== NAVIGATING TO PRODUCTS ===\n');
    await send('Page.navigate', { url: 'https://www.fastmoss.com/products' });
    await new Promise(r => setTimeout(r, 8000));

    // Navigate to creators
    console.log('\n=== NAVIGATING TO CREATORS ===\n');
    await send('Page.navigate', { url: 'https://www.fastmoss.com/creators' });
    await new Promise(r => setTimeout(r, 6000));

    // Navigate to videos
    console.log('\n=== NAVIGATING TO VIDEOS ===\n');
    await send('Page.navigate', { url: 'https://www.fastmoss.com/videos' });
    await new Promise(r => setTimeout(r, 6000));

    // Navigate to shops
    console.log('\n=== NAVIGATING TO SHOPS ===\n');
    await send('Page.navigate', { url: 'https://www.fastmoss.com/shops' });
    await new Promise(r => setTimeout(r, 6000));

    // Navigate to ads
    console.log('\n=== NAVIGATING TO ADS ===\n');
    await send('Page.navigate', { url: 'https://www.fastmoss.com/ads' });
    await new Promise(r => setTimeout(r, 6000));

    // Summary
    console.log('\n\n=== API CALLS SUMMARY ===');
    const unique = [...new Set(apiCalls.map(a => `${a.method} ${a.url.split('?')[0]}`))];
    unique.forEach(u => console.log(`  ${u}`));

    console.log(`\nTotal calls: ${apiCalls.length}`);
    console.log(`Unique endpoints: ${unique.length}`);

    fs.writeFileSync('fastmoss_api_discovery.json', JSON.stringify(apiCalls, null, 2));
    console.log('Saved to fastmoss_api_discovery.json');

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
