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

  const social1 = tabs.find(p => p.url.includes('social1'));
  if (!social1) { console.log('Social1 tab not found'); process.exit(1); }

  const ws = new WebSocket(social1.webSocketDebuggerUrl);
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

    // Capture network requests
    if (msg.method === 'Network.requestWillBeSent') {
      const req = msg.params.request;
      const url = req.url;
      if (!url.match(/\.(js|css|png|jpg|svg|woff|ico|gif|ttf|webp)(\?|$)/) &&
          !url.includes('google') && !url.includes('analytics') &&
          !url.includes('facebook') && !url.includes('clarity') &&
          !url.includes('fonts') && !url.includes('hotjar') &&
          !url.includes('sentry')) {
        apiCalls.push({
          url, method: req.method,
          headers: req.headers,
          postData: req.postData || null,
        });
        console.log(`[${req.method}] ${url}`);
        if (req.postData) console.log(`  Body: ${req.postData.substring(0, 300)}`);
        // Auth headers
        ['authorization', 'token', 'x-api-key', 'cookie', 'x-token'].forEach(h => {
          if (req.headers[h]) console.log(`  ${h}: ${req.headers[h].substring(0, 120)}`);
        });
      }
    }
  });

  ws.on('open', async () => {
    console.log('Connected to Social1!\n');

    // Enable network
    await send('Network.enable');

    // Get current page content
    const bodyText = await send('Runtime.evaluate', {
      expression: 'document.body.innerText.substring(0, 5000)',
      returnByValue: true
    });
    console.log('=== PAGE CONTENT ===');
    console.log(bodyText.result.value.substring(0, 2000));

    // Reload to capture fresh API calls
    console.log('\n=== RELOADING PAGE ===\n');
    await send('Page.reload');

    // Wait for requests
    await new Promise(r => setTimeout(r, 8000));

    // Get page content after load
    const bodyText2 = await send('Runtime.evaluate', {
      expression: 'document.body.innerText.substring(0, 3000)',
      returnByValue: true
    });
    console.log('\n=== PAGE CONTENT AFTER RELOAD ===');
    console.log(bodyText2.result.value.substring(0, 1500));

    // Summary
    console.log('\n=== API CALLS SUMMARY ===');
    const unique = [...new Set(apiCalls.map(a => `${a.method} ${a.url.split('?')[0]}`))];
    unique.forEach(u => console.log(`  ${u}`));

    fs.writeFileSync('social1_api_discovery.json', JSON.stringify(apiCalls, null, 2));
    console.log(`\nSaved ${apiCalls.length} calls to social1_api_discovery.json`);

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
