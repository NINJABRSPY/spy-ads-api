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
    const r = await send('Runtime.evaluate', {
      expression: expr, returnByValue: true, awaitPromise: true,
    });
    return r.result.value;
  }

  ws.on('open', async () => {
    console.log('=== DEEP VIDEO INVESTIGATION ===\n');

    // Enable network monitoring
    await send('Network.enable');
    const netCalls = [];
    ws.on('message', (data) => {
      const msg = JSON.parse(data.toString());
      if (msg.method === 'Network.requestWillBeSent') {
        const url = msg.params.request.url;
        if (url.includes('.mp4') || url.includes('video') || url.includes('media') ||
            url.includes('stream') || url.includes('cloudfront') || url.includes('s3.') ||
            url.includes('tiktok') || url.includes('bytedance') || url.includes('muscdn')) {
          netCalls.push(url);
          console.log(`  [NET] ${url.substring(0, 200)}`);
        }
      }
      if (msg.method === 'Network.responseReceived') {
        const resp = msg.params.response;
        if (resp.mimeType && resp.mimeType.includes('video')) {
          console.log(`  [VIDEO RESPONSE] ${resp.url.substring(0, 200)} (${resp.mimeType})`);
          netCalls.push('VIDEO:' + resp.url);
        }
      }
    });

    // Navigate to videos page
    console.log('Navigating to /videos...');
    await send('Page.navigate', { url: 'https://www.social1.ai/videos' });
    await new Promise(r => setTimeout(r, 6000));

    // Get video API response to find video IDs
    const videoData = await ev(`
      (async () => {
        const r = await fetch('/api/videos/getTopVideos?limit=3&offset=0&days=1&region=uk');
        const d = await r.json();
        return JSON.stringify(d.results?.slice(0, 3).map(v => ({
          id: v.video_id,
          handle: v.handle,
          desc: (v.description || '').substring(0, 50)
        })));
      })()
    `);
    console.log('\nFirst 3 videos:', videoData);

    // Try navigating to a specific video page
    const videos = JSON.parse(videoData);
    if (videos.length > 0) {
      const vid = videos[0];
      console.log(`\nNavigating to video: ${vid.id}...`);

      // Try different URL patterns
      const urls = [
        `https://www.social1.ai/video/${vid.id}`,
        `https://www.social1.ai/videos/${vid.id}`,
        `https://www.social1.ai/v/${vid.id}`,
      ];

      for (const url of urls) {
        console.log(`\nTrying: ${url}`);
        await send('Page.navigate', { url });
        await new Promise(r => setTimeout(r, 4000));

        const pageInfo = await ev(`
          (function() {
            const videos = document.querySelectorAll('video');
            const iframes = document.querySelectorAll('iframe');
            const html = document.documentElement.innerHTML;

            // Find ALL src attributes that could be videos
            const srcMatch = html.match(/src="[^"]*(?:mp4|video|stream|media|cloudfront|bytedance|muscdn|tiktok)[^"]*"/gi) || [];

            // Find video poster/thumbnail
            const posterMatch = html.match(/poster="[^"]*"/gi) || [];

            return JSON.stringify({
              url: window.location.href,
              title: document.title,
              videoElements: videos.length,
              iframeElements: iframes.length,
              videoSrc: Array.from(videos).map(v => ({
                src: v.src || v.currentSrc || '',
                poster: v.poster || '',
                sources: Array.from(v.querySelectorAll('source')).map(s => s.src)
              })),
              iframeSrc: Array.from(iframes).map(f => f.src),
              srcMatches: srcMatch.slice(0, 10),
              posterMatches: posterMatch.slice(0, 5),
              bodyText: document.body.innerText.substring(0, 500),
            });
          })()
        `);
        const info = JSON.parse(pageInfo);
        console.log(`  URL: ${info.url}`);
        console.log(`  Videos: ${info.videoElements}, Iframes: ${info.iframeElements}`);
        if (info.videoSrc.length) console.log(`  Video sources:`, JSON.stringify(info.videoSrc));
        if (info.iframeSrc.length) console.log(`  Iframe sources:`, JSON.stringify(info.iframeSrc));
        if (info.srcMatches.length) console.log(`  SRC matches:`, JSON.stringify(info.srcMatches));
        if (info.bodyText) console.log(`  Body: ${info.bodyText.substring(0, 200)}`);

        if (info.videoElements > 0 || info.iframeElements > 0) break;
      }
    }

    // Also check: does the videos page have hover-to-play or click-to-play?
    console.log('\n\nChecking videos list page for play mechanism...');
    await send('Page.navigate', { url: 'https://www.social1.ai/videos' });
    await new Promise(r => setTimeout(r, 5000));

    // Hover over first card
    const hoverResult = await ev(`
      (function() {
        // Find video cards/items
        const items = document.querySelectorAll('[class*="card"], [class*="video"], [class*="item"], a[href*="video"]');
        const links = [];
        items.forEach(el => {
          const href = el.getAttribute('href') || '';
          if (href) links.push(href);
        });

        // Also check for data attributes
        const dataItems = document.querySelectorAll('[data-video-id], [data-id], [data-video]');
        const dataAttrs = Array.from(dataItems).map(el => ({
          tag: el.tagName,
          videoId: el.getAttribute('data-video-id') || el.getAttribute('data-id') || '',
          href: el.getAttribute('href') || '',
        }));

        return JSON.stringify({ links: [...new Set(links)].slice(0, 10), dataAttrs: dataAttrs.slice(0, 5) });
      })()
    `);
    console.log('Links/data:', hoverResult);

    console.log('\n\n=== NETWORK CALLS WITH VIDEO CONTENT ===');
    netCalls.forEach(c => console.log(`  ${c.substring(0, 200)}`));

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
