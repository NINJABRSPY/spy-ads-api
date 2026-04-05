// SimilarWeb On-Demand Server
// Roda localmente, recebe pedidos de domínio, scrapa via Chrome CDP, retorna dados
const http = require('http');
const WebSocket = require('ws');
const fs = require('fs');
const path = require('path');

const PORT = 4000;
const CACHE_FILE = path.join(__dirname, 'resultados', 'similarweb_cache.json');
const CACHE_TTL = 24 * 60 * 60 * 1000; // 24h cache
const WAIT_TIME = 15000;

// Load cache
let cache = {};
try { cache = JSON.parse(fs.readFileSync(CACHE_FILE, 'utf8')); } catch {}

function saveCache() {
  fs.writeFileSync(CACHE_FILE, JSON.stringify(cache, null, 2));
}

async function getSwTab() {
  return new Promise((resolve, reject) => {
    http.get('http://localhost:9222/json', res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => {
        const tab = JSON.parse(d).find(t => t.url.includes('similarweb'));
        if (tab) resolve(tab.webSocketDebuggerUrl);
        else reject('SimilarWeb tab not found');
      });
    }).on('error', reject);
  });
}

async function scrapeDomain(domain) {
  const wsUrl = await getSwTab();
  const ws = new WebSocket(wsUrl);
  let msgId = 1;
  const pending = {};

  function send(m, p = {}) {
    return new Promise(r => {
      const i = msgId++;
      pending[i] = r;
      ws.send(JSON.stringify({ id: i, method: m, params: p }));
    });
  }

  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      ws.close();
      reject('Timeout');
    }, 30000);

    ws.on('message', d => {
      const msg = JSON.parse(d.toString());
      if (msg.id && pending[msg.id]) { pending[msg.id](msg.result); delete pending[msg.id]; }
    });

    ws.on('open', async () => {
      try {
        await send('Network.enable');

        const domainData = {};
        const pendingBodies = [];

        const listener = (data) => {
          const msg = JSON.parse(data.toString());
          if (msg.method === 'Network.responseReceived') {
            const url = msg.params.response.url;
            if (url.includes('widgetApi') || (url.includes('/api/') && url.includes('WebsiteOverview'))) {
              pendingBodies.push({
                endpoint: url.split('?')[0].replace('https://pro.similarweb.com/', ''),
                reqId: msg.params.requestId
              });
            }
          }
        };
        ws.on('message', listener);

        const navUrl = `https://pro.similarweb.com/#/digitalsuite/websiteanalysis/overview/website-performance/*/999/3m?webSource=Total&key=${domain}`;
        await send('Page.navigate', { url: navUrl });
        await new Promise(r => setTimeout(r, WAIT_TIME));

        ws.removeListener('message', listener);

        for (const { endpoint, reqId } of pendingBodies) {
          try {
            const body = await send('Network.getResponseBody', { requestId: reqId });
            if (body && body.body) {
              try { domainData[endpoint] = JSON.parse(body.body); } catch {}
            }
          } catch {}
        }

        const metrics = extractMetrics(domainData, domain);
        clearTimeout(timeout);
        ws.close();
        resolve(metrics);
      } catch (e) {
        clearTimeout(timeout);
        ws.close();
        reject(e);
      }
    });

    ws.on('error', (e) => {
      clearTimeout(timeout);
      reject(e);
    });
  });
}

function extractMetrics(data, domain) {
  const metrics = { domain, scraped_at: new Date().toISOString() };

  const header = data['api/WebsiteOverview/getheader'];
  if (header && header[domain]) {
    const h = header[domain];
    metrics.title = h.title || '';
    metrics.icon = h.icon || '';
    metrics.global_rank = h.globalRanking || 0;
    metrics.country_rank = h.highestTrafficCountryRanking || 0;
    metrics.employee_range = h.employeeRange || '';
  }

  const visits = data['widgetApi/WebsiteOverview/EngagementVisits/SingleMetric'];
  if (visits?.Data?.[domain]) {
    const v = visits.Data[domain];
    metrics.monthly_visits = Math.round(v.TotalVisits || 0);
    metrics.visits_change = Math.round((v.Change || 0) * 100) / 100;
    metrics.visits_trend = v.Trend || [];
  }

  const engagement = data['widgetApi/WebsiteOverview/EngagementOverview/Table'];
  if (engagement?.Data?.[0]) {
    const e = engagement.Data[0];
    metrics.bounce_rate = Math.round((e.BounceRate || 0) * 1000) / 10;
    metrics.avg_duration = Math.round(e.AvgVisitDuration || 0);
    metrics.pages_per_visit = Math.round((e.PagesPerVisit || 0) * 100) / 100;
    metrics.unique_users = Math.round(e.UniqueUsers || 0);
  }

  const devices = data['widgetApi/WebsiteOverview/EngagementDesktopVsMobileVisits/PieChart'];
  if (devices?.Data?.[domain]) {
    const d = devices.Data[domain];
    const total = (d.Desktop || 0) + (d['Mobile Web'] || 0);
    metrics.desktop_pct = total > 0 ? Math.round((d.Desktop || 0) / total * 100) : 0;
    metrics.mobile_pct = total > 0 ? Math.round((d['Mobile Web'] || 0) / total * 100) : 0;
  }

  const ranks = data['widgetApi/WebsiteOverview/WebRanks/SingleMetric'];
  if (ranks?.Data?.[domain]) {
    const r = ranks.Data[domain];
    metrics.global_rank = r.GlobalRank?.Value || metrics.global_rank;
    metrics.global_rank_trend = (r.GlobalRank?.Trend || []).map(t => ({ month: t.Key, rank: t.Value }));
  }

  const sources = data['widgetApi/MarketingMixTotal/TrafficSourcesOverview/PieChart'];
  if (sources?.Data?.Total?.[domain]) {
    const s = sources.Data.Total[domain];
    const total = Object.values(s).reduce((a, b) => a + b, 0);
    metrics.traffic_sources = {
      direct: Math.round(s.Direct || 0),
      direct_pct: total > 0 ? Math.round((s.Direct || 0) / total * 100) : 0,
      organic_search: Math.round(s['Organic Search'] || 0),
      organic_pct: total > 0 ? Math.round((s['Organic Search'] || 0) / total * 100) : 0,
      paid_search: Math.round(s['Paid Search'] || 0),
      paid_pct: total > 0 ? Math.round((s['Paid Search'] || 0) / total * 100) : 0,
      social: Math.round(s.Social || 0),
      social_pct: total > 0 ? Math.round(s.Social / total * 100) : 0,
      referrals: Math.round(s.Referrals || 0),
      display_ads: Math.round(s['Display Ads'] || 0),
      email: Math.round(s.Email || 0),
    };
  }

  const geo = data['widgetApi/WebsiteGeography/Geography/Table'];
  if (geo?.Data) {
    metrics.geography = geo.Data.map(g => ({
      country_code: g.Country,
      share: Math.round((g.Share || 0) * 1000) / 10,
    })).slice(0, 5);
    if (geo.Filters?.country) {
      metrics.geography_names = geo.Filters.country.map(c => ({ code: c.id, name: c.text }));
    }
  }

  const graph = data['widgetApi/WebsiteOverview/EngagementVisits/Graph'];
  if (graph?.Data?.[domain]?.Total?.[0]) {
    metrics.visits_graph = graph.Data[domain].Total[0].map(p => ({
      month: p.Key,
      visits: Math.round(p.Value),
    }));
  }

  const refs = data['widgetApi/WebsiteOverviewDesktop/TopReferrals/Table'];
  if (refs?.Data) {
    metrics.top_referrals = refs.Data.map(r => ({
      domain: r.Domain,
      share: Math.round((r.Share || 0) * 1000) / 10,
      change: Math.round((r.Change || 0) * 100) / 100,
    })).slice(0, 10);
  }

  const social = data['widgetApi/WebsiteOverviewDesktop/TrafficSourcesSocial/PieChart'];
  if (social?.Data?.[domain]) {
    metrics.social_breakdown = {};
    for (const [platform, info] of Object.entries(social.Data[domain])) {
      metrics.social_breakdown[platform] = Math.round((info.Share || 0) * 1000) / 10;
    }
  }

  const branded = data['widgetApi/TrafficSourcesSearchV2/BrandedKeywords/WebsitePerformance/PieChart'];
  if (branded?.Data?.[domain]) {
    const b = branded.Data[domain];
    const total = (b.Branded || 0) + (b.NoneBranded || 0);
    metrics.branded_search = {
      branded_pct: total > 0 ? Math.round((b.Branded || 0) / total * 100) : 0,
      non_branded_pct: total > 0 ? Math.round((b.NoneBranded || 0) / total * 100) : 0,
    };
  }

  const keywords = data['widgetApi/SearchKeywordsV2/WebsitePerformance/Table'];
  if (keywords?.Data) {
    metrics.top_keywords = keywords.Data.map(k => ({
      keyword: k.SearchTerm,
      share: Math.round((k.TotalShare || 0) * 1000) / 10,
      visits: Math.round(k.TotalVisits || 0),
    })).slice(0, 10);
  }

  const adIntel = data['api/AdIntelligence/Advertiser/Publishers/breakdown'];
  if (adIntel?.records) {
    metrics.ad_publishers = adIntel.records.map(r => ({
      domain: r.entity,
      impressions_share: Math.round((r.impressionsShare || 0) * 1000) / 10,
      category: r.category,
    })).slice(0, 10);
  }

  const refCats = data['widgetApi/WebsiteOverviewDesktop/TopReferringCategories/Table'];
  if (refCats?.Data) {
    metrics.referral_categories = refCats.Data.map(c => ({
      category: c.Category?.replace(/_/g, ' '),
      share: Math.round((c.Share || 0) * 1000) / 10,
    })).slice(0, 5);
  }

  const destRefs = data['widgetApi/WebsiteOverviewDesktop/TrafficDestinationReferrals/Table'];
  if (destRefs?.Data) {
    metrics.exit_destinations = destRefs.Data.map(d => ({
      domain: d.Domain,
      share: Math.round((d.Share || 0) * 1000) / 10,
    })).slice(0, 5);
  }

  const competitors = data['widgetApi/WebsiteOverview/EngagementVisits/Table'];
  if (competitors?.Data) {
    metrics.competitors = competitors.Data.filter(c => c.Domain !== domain).map(c => ({
      domain: c.Domain,
      visits: Math.round(c.TotalVisits || 0),
      change: Math.round((c.Change || 0) * 100) / 100,
    })).slice(0, 5);
  }

  return (metrics.monthly_visits || metrics.global_rank) ? metrics : null;
}

// Security
const ALLOWED_ORIGINS = [
  'https://ninjabrhub.io',
  'https://www.ninjabrhub.io',
  'http://localhost:5173',  // Lovable dev
  'http://localhost:3000',  // Local dev
];
const API_SECRET = 'njspy_traffic_2026_x9k';  // Token de seguranca

// HTTP Server
const server = http.createServer(async (req, res) => {
  const origin = req.headers.origin || req.headers.referer || '';
  const isAllowed = ALLOWED_ORIGINS.some(o => origin.startsWith(o));
  const hasToken = new URL(req.url, `http://localhost:${PORT}`).searchParams.get('key') === API_SECRET;

  // CORS — só para origens permitidas
  if (isAllowed) {
    res.setHeader('Access-Control-Allow-Origin', origin);
  } else if (hasToken) {
    res.setHeader('Access-Control-Allow-Origin', '*');
  } else {
    // Bloquear requests de origens desconhecidas sem token
    res.writeHead(403, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Acesso negado. Origem nao autorizada.' }));
    console.log(`[BLOCKED] Origin: ${origin} | IP: ${req.socket.remoteAddress}`);
    return;
  }
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }

  const url = new URL(req.url, `http://localhost:${PORT}`);

  // GET /api/traffic/domain.com
  if (url.pathname.startsWith('/api/traffic/')) {
    const domain = url.pathname.replace('/api/traffic/', '').replace(/\/$/, '');
    if (!domain) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Domain required' }));
      return;
    }

    // Check cache
    if (cache[domain] && (Date.now() - new Date(cache[domain].scraped_at).getTime()) < CACHE_TTL) {
      console.log(`[CACHE] ${domain}`);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ cached: true, ...cache[domain] }));
      return;
    }

    // Scrape on-demand
    console.log(`[SCRAPING] ${domain}...`);
    try {
      const data = await scrapeDomain(domain);
      if (data) {
        cache[domain] = data;
        saveCache();

        // Also update the main similarweb file for Render
        try {
          const swFile = path.join(__dirname, 'resultados', 'similarweb_20260403.json');
          const existing = JSON.parse(fs.readFileSync(swFile, 'utf8'));
          existing.domains[domain] = data;
          existing.total_domains = Object.keys(existing.domains).length;
          fs.writeFileSync(swFile, JSON.stringify(existing, null, 2));
        } catch {}

        console.log(`[OK] ${domain} — ${data.monthly_visits?.toLocaleString()} visits`);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ cached: false, ...data }));
      } else {
        console.log(`[EMPTY] ${domain} — no data`);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ domain, error: 'No data available for this domain', cached: false }));
      }
    } catch (e) {
      console.log(`[ERROR] ${domain} — ${e}`);
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ domain, error: String(e) }));
    }
    return;
  }

  // GET /api/traffic — list cached
  if (url.pathname === '/api/traffic') {
    const items = Object.values(cache).sort((a, b) => (b.monthly_visits || 0) - (a.monthly_visits || 0));
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ total: items.length, data: items }));
    return;
  }

  // Health
  if (url.pathname === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'ok', cached_domains: Object.keys(cache).length }));
    return;
  }

  res.writeHead(404, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ error: 'Not found' }));
});

server.listen(PORT, () => {
  console.log(`\n  SimilarWeb On-Demand Server`);
  console.log(`  http://localhost:${PORT}/api/traffic/{domain}`);
  console.log(`  ${Object.keys(cache).length} domains cached\n`);
});
