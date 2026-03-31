// Script para descobrir APIs do ClickMidas via Chrome DevTools Protocol
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
  console.log('Connecting to:', wsUrl);

  const ws = new WebSocket(wsUrl);
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

    // Handle responses
    if (msg.id && pending[msg.id]) {
      pending[msg.id](msg.result);
      delete pending[msg.id];
    }

    // Capture network events
    if (msg.method === 'Network.requestWillBeSent') {
      const req = msg.params.request;
      const url = req.url;
      // Filter for API calls (not static assets)
      if (!url.match(/\.(js|css|png|jpg|svg|woff|ico|gif)(\?|$)/) &&
          !url.includes('google') && !url.includes('analytics') &&
          !url.includes('facebook') && !url.includes('hotjar') &&
          !url.includes('clarity') && !url.includes('fonts')) {
        apiCalls.push({
          url: url,
          method: req.method,
          headers: req.headers,
          postData: req.postData || null,
          timestamp: new Date().toISOString()
        });
        console.log(`[${req.method}] ${url}`);
        if (req.postData) console.log(`  Body: ${req.postData.substring(0, 200)}`);
        // Print interesting headers
        const interestingHeaders = ['authorization', 'token', 'x-api-key', 'cookie', 'x-token', 'access-token'];
        for (const h of interestingHeaders) {
          if (req.headers[h]) console.log(`  ${h}: ${req.headers[h].substring(0, 100)}`);
        }
      }
    }

    if (msg.method === 'Network.responseReceived') {
      const resp = msg.params.response;
      const url = resp.url;
      if (!url.match(/\.(js|css|png|jpg|svg|woff|ico|gif)(\?|$)/) &&
          !url.includes('google') && !url.includes('analytics') &&
          !url.includes('facebook') && !url.includes('hotjar') &&
          !url.includes('clarity') && !url.includes('fonts')) {
        console.log(`  Response: ${resp.status} ${resp.mimeType} <- ${url.substring(0, 100)}`);
      }
    }
  });

  ws.on('open', async () => {
    console.log('Connected! Enabling network monitoring...\n');

    // Enable network monitoring
    await send('Network.enable');

    // Get current URL
    const result = await send('Runtime.evaluate', {
      expression: 'window.location.href'
    });
    console.log('Current page:', result.result.value);

    // Now navigate to different pages to capture API calls
    const pages = [
      'https://www.clickmidas.com.br/',
      'https://www.clickmidas.com.br/midas-score',
    ];

    console.log('\n--- Reloading current page to capture API calls ---\n');

    // Reload page to capture fresh API calls
    await send('Page.reload');

    // Wait for page to load and capture requests
    setTimeout(async () => {
      // Try navigating to main page
      console.log('\n--- Navigating to main page ---\n');
      await send('Page.navigate', { url: 'https://www.clickmidas.com.br/' });

      setTimeout(async () => {
        // Navigate to midas-score
        console.log('\n--- Navigating to midas-score ---\n');
        await send('Page.navigate', { url: 'https://www.clickmidas.com.br/midas-score' });

        setTimeout(async () => {
          // Navigate to keywords page
          console.log('\n--- Navigating to keywords ---\n');
          await send('Page.navigate', { url: 'https://www.clickmidas.com.br/keywords' });

          setTimeout(() => {
            console.log('\n\n========== SUMMARY ==========');
            console.log(`Total API calls captured: ${apiCalls.length}`);
            console.log('\nUnique endpoints:');
            const unique = [...new Set(apiCalls.map(a => `${a.method} ${a.url.split('?')[0]}`))];
            unique.forEach(u => console.log(`  ${u}`));

            // Save to file
            const fs = require('fs');
            fs.writeFileSync('clickmidas_api_discovery.json', JSON.stringify(apiCalls, null, 2));
            console.log('\nSaved to clickmidas_api_discovery.json');

            ws.close();
            process.exit(0);
          }, 8000);
        }, 8000);
      }, 8000);
    }, 8000);
  });
}

main().catch(console.error);
