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
  const netCalls = [];

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
      if (!url.includes('google') && !url.includes('posthog') && !url.includes('woff') &&
          !url.includes('.css') && !url.includes('.js') && !url.includes('png') &&
          !url.includes('analytics') && !url.includes('_next/static')) {
        netCalls.push(url);
        console.log(`[NET] ${url.substring(0, 200)}`);
      }
    }
    if (msg.method === 'Network.responseReceived') {
      const resp = msg.params.response;
      if (resp.mimeType && (resp.mimeType.includes('video') || resp.mimeType.includes('mp4'))) {
        console.log(`[VIDEO FOUND] ${resp.url.substring(0, 200)} (${resp.mimeType})`);
      }
    }
  });

  async function ev(expr) {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    return r.result.value;
  }

  ws.on('open', async () => {
    await send('Network.enable');

    // Go to videos page
    console.log('Going to /videos...\n');
    await send('Page.navigate', { url: 'https://www.social1.ai/videos' });
    await new Promise(r => setTimeout(r, 6000));

    // Get all clickable elements on video cards
    const elements = await ev(`
      (function() {
        const all = document.querySelectorAll('*');
        const clickable = [];
        for (const el of all) {
          const rect = el.getBoundingClientRect();
          if (rect.height < 10 || rect.width < 10 || rect.y < 0 || rect.y > 2000) continue;
          const text = el.textContent.trim().substring(0, 50);
          const tag = el.tagName;
          const cls = (el.className || '').toString().substring(0, 60);
          // Look for video-related elements
          if (cls.includes('video') || cls.includes('Video') || cls.includes('play') ||
              cls.includes('Play') || cls.includes('card') || cls.includes('Card') ||
              tag === 'VIDEO' || tag === 'BUTTON') {
            clickable.push({ tag, cls, text, y: Math.round(rect.y), x: Math.round(rect.x), w: Math.round(rect.width), h: Math.round(rect.height) });
          }
        }
        return JSON.stringify(clickable.slice(0, 30));
      })()
    `);
    console.log('Video-related elements:');
    const els = JSON.parse(elements);
    els.forEach(e => console.log(`  [${e.tag}] y:${e.y} ${e.w}x${e.h} cls:"${e.cls}" text:"${e.text}"`));

    // Click on the first card-like element
    if (els.length > 0) {
      console.log(`\nClicking first video element...`);
      const clickResult = await ev(`
        (function() {
          const all = document.querySelectorAll('*');
          for (const el of all) {
            const cls = (el.className || '').toString();
            if (cls.includes('card') || cls.includes('Card') || cls.includes('video') || cls.includes('Video')) {
              const rect = el.getBoundingClientRect();
              if (rect.height > 50 && rect.width > 50 && rect.y > 100 && rect.y < 800) {
                el.click();
                return 'clicked: ' + el.tagName + ' ' + cls.substring(0, 60);
              }
            }
          }
          return 'nothing to click';
        })()
      `);
      console.log('Click result:', clickResult);

      // Wait and check for video/modal
      await new Promise(r => setTimeout(r, 4000));

      const afterClick = await ev(`
        (function() {
          const videos = document.querySelectorAll('video');
          const iframes = document.querySelectorAll('iframe');
          const modals = document.querySelectorAll('[class*="modal"], [class*="Modal"], [class*="dialog"], [class*="Dialog"], [class*="overlay"], [class*="Overlay"], [role="dialog"]');
          const html = document.documentElement.innerHTML;
          const videoUrls = html.match(/(?:src|href)="([^"]*(?:mp4|webm|video|stream|playback|media)[^"]*)"/gi) || [];

          return JSON.stringify({
            videos: Array.from(videos).map(v => ({ src: v.src || v.currentSrc, poster: v.poster })),
            iframes: Array.from(iframes).map(f => f.src),
            modals: modals.length,
            modalText: modals.length > 0 ? modals[0].textContent.substring(0, 200) : '',
            videoUrls: [...new Set(videoUrls)].slice(0, 10),
            newUrl: window.location.href,
          });
        })()
      `);
      console.log('\nAfter click:', afterClick);
    }

    console.log('\n=== ALL NETWORK CALLS ===');
    netCalls.forEach(c => console.log(`  ${c.substring(0, 200)}`));

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
