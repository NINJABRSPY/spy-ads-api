const fs = require('fs');
const calls = JSON.parse(fs.readFileSync('clickmidas_api_discovery.json', 'utf8'));

const skipPatterns = ['wix', 'frog.', 'panorama', 'sentry', 'parastorage', 'ecom', 'siteassets', 'google', 'facebook', 'analytics', 'clarity', 'hotjar', 'fonts', 'bolt-performance'];

const filtered = calls.filter(a => {
  return !skipPatterns.some(p => a.url.includes(p));
});

console.log('=== RELEVANT API CALLS ===\n');
const unique = [...new Set(filtered.map(a => a.method + ' ' + a.url.split('?')[0]))];
unique.forEach(u => console.log(u));

console.log('\n=== DETAILS ===\n');
filtered.forEach(a => {
  console.log(`[${a.method}] ${a.url}`);
  if (a.postData) console.log(`  Body: ${a.postData.substring(0, 300)}`);
  // Check for auth headers
  const authHeaders = ['authorization', 'token', 'x-api-key', 'cookie', 'x-token', 'access-token'];
  for (const h of authHeaders) {
    if (a.headers[h]) console.log(`  ${h}: ${a.headers[h].substring(0, 150)}`);
  }
  console.log('');
});
