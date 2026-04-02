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

  const social1 = tabs.find(p => p.url.includes('social1'));
  const ws = new WebSocket(social1.webSocketDebuggerUrl);
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

  async function fetchAPI(path) {
    const r = await send('Runtime.evaluate', {
      expression: `(async () => { const r = await fetch("${path}"); return await r.text(); })()`,
      returnByValue: true,
      awaitPromise: true,
    });
    try { return JSON.parse(r.result.value); }
    catch { return null; }
  }

  ws.on('open', async () => {
    // Navigate to videos page
    console.log('Navigating to videos page...');
    await send('Page.navigate', { url: 'https://www.social1.ai/videos' });
    await new Promise(r => setTimeout(r, 6000));

    // Enable network to catch video requests
    await send('Network.enable');

    // Check what API the videos page calls
    const apiCalls = [];
    const netListener = (data) => {
      const msg = JSON.parse(data.toString());
      if (msg.method === 'Network.requestWillBeSent') {
        const req = msg.params.request;
        if (req.url.includes('/api/') || req.url.includes('tiktok') || req.url.includes('.mp4') || req.url.includes('video')) {
          if (!req.url.includes('posthog') && !req.url.includes('google')) {
            apiCalls.push({ method: req.method, url: req.url.substring(0, 200) });
          }
        }
      }
    };
    ws.on('message', netListener);

    // Check page for video elements
    const videoInfo = await send('Runtime.evaluate', {
      expression: `(function() {
        const videos = document.querySelectorAll('video');
        const iframes = document.querySelectorAll('iframe');
        const results = {
          videoTags: Array.from(videos).map(v => ({ src: v.src || v.querySelector('source')?.src || '', poster: v.poster || '' })),
          iframes: Array.from(iframes).map(f => ({ src: f.src })),
          // Look for any video URLs in the page
          s3Videos: [],
          tiktokEmbeds: [],
        };

        // Search for S3 or CDN video URLs
        const html = document.documentElement.innerHTML;
        const s3Matches = html.match(/https:\\/\\/[^"'\\s]*s3[^"'\\s]*\\.mp4/g) || [];
        const cdnMatches = html.match(/https:\\/\\/[^"'\\s]*cloudfront[^"'\\s]*\\.mp4/g) || [];
        const tiktokMatches = html.match(/https:\\/\\/[^"'\\s]*tiktok[^"'\\s]*embed[^"'\\s]*/g) || [];
        results.s3Videos = [...s3Matches, ...cdnMatches].slice(0, 5);
        results.tiktokEmbeds = tiktokMatches.slice(0, 5);

        return JSON.stringify(results, null, 2);
      })()`,
      returnByValue: true,
    });

    console.log('\n=== VIDEO ELEMENTS ===');
    console.log(videoInfo.result.value);

    // Wait a bit and check network calls
    await new Promise(r => setTimeout(r, 3000));

    // Try clicking on a video to see what happens
    await send('Runtime.evaluate', {
      expression: `(function() {
        const cards = document.querySelectorAll('[class*="video"], [class*="card"]');
        if (cards.length > 0) cards[0].click();
        return 'clicked ' + cards.length + ' elements';
      })()`,
      returnByValue: true,
    });

    await new Promise(r => setTimeout(r, 3000));

    // Check again for video elements after click
    const afterClick = await send('Runtime.evaluate', {
      expression: `(function() {
        const videos = document.querySelectorAll('video');
        const iframes = document.querySelectorAll('iframe');
        const html = document.documentElement.innerHTML;
        const s3 = html.match(/https:\\/\\/[^"'\\s]*\\.(mp4|webm)/g) || [];
        const embeds = html.match(/https:\\/\\/[^"'\\s]*tiktok[^"'\\s]*/g) || [];
        return JSON.stringify({
          videos: Array.from(videos).map(v => ({ src: v.src || v.currentSrc || '', poster: v.poster })),
          iframes: Array.from(iframes).map(f => f.src).filter(s => s),
          mediaUrls: [...new Set(s3)].slice(0, 5),
          tiktokUrls: [...new Set(embeds)].slice(0, 5),
        }, null, 2);
      })()`,
      returnByValue: true,
    });

    console.log('\n=== AFTER CLICK ===');
    console.log(afterClick.result.value);

    console.log('\n=== NETWORK CALLS ===');
    apiCalls.forEach(c => console.log(`  [${c.method}] ${c.url}`));

    ws.removeListener('message', netListener);
    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
