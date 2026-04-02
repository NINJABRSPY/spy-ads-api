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

  async function ev(expr) {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    return r.result.value;
  }

  ws.on('open', async () => {
    console.log('=== INVESTIGATING MAIN PAGE VIDEOS ===\n');

    // Enable network
    await send('Network.enable');
    const netCalls = [];
    ws.on('message', (data) => {
      const msg = JSON.parse(data.toString());
      if (msg.method === 'Network.requestWillBeSent') {
        const url = msg.params.request.url;
        if (url.includes('tiktok') || url.includes('.mp4') || url.includes('video') ||
            url.includes('stream') || url.includes('playback') || url.includes('bytedance') ||
            url.includes('muscdn')) {
          netCalls.push(url);
          console.log(`[NET] ${url.substring(0, 200)}`);
        }
      }
    });

    // Navigate to main page (where videos are)
    console.log('Going to / ...');
    await send('Page.navigate', { url: 'https://www.social1.ai/' });
    await new Promise(r => setTimeout(r, 6000));

    // Find all tiktokcdn images (thumbnails)
    const thumbs = await ev(`
      (function() {
        const imgs = document.querySelectorAll('img[src*="tiktokcdn"], img[alt*="Video"]');
        return JSON.stringify(Array.from(imgs).slice(0, 5).map(img => ({
          alt: img.alt,
          src: img.src.substring(0, 150),
          parent: img.parentElement?.className?.substring(0, 60) || '',
          grandparent: img.parentElement?.parentElement?.className?.substring(0, 60) || '',
          gpTag: img.parentElement?.parentElement?.tagName || '',
        })));
      })()
    `);
    console.log('\nTikTok thumbnails:', thumbs);

    // Click on first video thumbnail
    console.log('\nClicking first video...');
    const clickResult = await ev(`
      (function() {
        const imgs = document.querySelectorAll('img[alt*="Video"]');
        if (imgs.length === 0) return 'no video images found';
        // Click the parent container
        let el = imgs[0];
        for (let i = 0; i < 5; i++) {
          if (el.parentElement) el = el.parentElement;
          if (el.onclick || el.tagName === 'A' || el.getAttribute('role') === 'button') break;
        }
        el.click();
        return 'clicked: ' + el.tagName + ' ' + (el.className || '').toString().substring(0, 80);
      })()
    `);
    console.log('Click:', clickResult);

    await new Promise(r => setTimeout(r, 4000));

    // Check what appeared
    const afterClick = await ev(`
      (function() {
        const videos = document.querySelectorAll('video');
        const iframes = document.querySelectorAll('iframe');
        const modals = document.querySelectorAll('[class*="modal"], [class*="Modal"], [role="dialog"], [class*="overlay"], [class*="Overlay"], [class*="drawer"], [class*="Drawer"], [class*="sheet"], [class*="Sheet"]');

        const result = {
          url: window.location.href,
          videoElements: [],
          iframeElements: [],
          modals: modals.length,
        };

        videos.forEach(v => {
          result.videoElements.push({
            src: v.src || v.currentSrc || '',
            poster: v.poster || '',
            sources: Array.from(v.querySelectorAll('source')).map(s => ({ src: s.src, type: s.type })),
          });
        });

        iframes.forEach(f => result.iframeElements.push(f.src));

        // Search entire HTML for video URLs
        const html = document.documentElement.innerHTML;
        const tiktokVideoUrls = html.match(/https:\/\/[^"'\s]*tiktokcdn[^"'\s]*\.mp4[^"'\s]*/g) || [];
        const playUrls = html.match(/https:\/\/[^"'\s]*(?:playback|play|stream|video)[^"'\s]*\.mp4[^"'\s]*/g) || [];
        result.tiktokVideoUrls = [...new Set(tiktokVideoUrls)].slice(0, 5);
        result.playUrls = [...new Set(playUrls)].slice(0, 5);

        // Check for any new visible content
        if (modals.length > 0) {
          const modal = modals[0];
          result.modalHTML = modal.innerHTML.substring(0, 500);
          const modalVideos = modal.querySelectorAll('video');
          const modalIframes = modal.querySelectorAll('iframe');
          result.modalVideos = Array.from(modalVideos).map(v => v.src || v.currentSrc);
          result.modalIframes = Array.from(modalIframes).map(f => f.src);
        }

        return JSON.stringify(result, null, 2);
      })()
    `);
    console.log('\nAfter click:', afterClick);

    // Wait more and check network
    await new Promise(r => setTimeout(r, 3000));

    console.log('\n=== NETWORK CALLS (video-related) ===');
    netCalls.forEach(c => console.log(`  ${c.substring(0, 250)}`));

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
