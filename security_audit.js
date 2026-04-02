// NinjaSpy Hub Security Audit
const WebSocket = require('ws');
const http = require('http');
const https = require('https');
const fs = require('fs');

async function getWsUrl() {
  return new Promise((resolve, reject) => {
    http.get('http://localhost:9222/json', res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => {
        const pages = JSON.parse(d).filter(t => t.type === 'page');
        const hub = pages.find(p => p.url.includes('ninjabrhub'));
        if (hub) resolve(hub.webSocketDebuggerUrl);
        else reject('NinjaBR Hub tab not found');
      });
    });
  });
}

async function main() {
  const wsUrl = await getWsUrl();
  const ws = new WebSocket(wsUrl);
  let msgId = 1;
  const pending = {};
  const findings = [];

  function send(method, params = {}) {
    return new Promise((resolve) => {
      const id = msgId++;
      pending[id] = resolve;
      ws.send(JSON.stringify({ id, method, params }));
    });
  }

  ws.on('message', (data) => {
    const msg = JSON.parse(data.toString());
    if (msg.id && pending[msg.id]) {
      pending[msg.id](msg.result);
      delete pending[msg.id];
    }
  });

  async function ev(expr) {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true });
    return r.result.value;
  }

  ws.on('open', async () => {
    console.log('=== NINJASPY HUB SECURITY AUDIT ===\n');

    // 1. Basic info
    const url = await ev('window.location.href');
    const title = await ev('document.title');
    console.log('URL:', url);
    console.log('Title:', title);

    // 2. Check cookies
    console.log('\n--- COOKIES ---');
    const cookies = await ev(`
      document.cookie.split(';').map(c => {
        const parts = c.trim().split('=');
        return parts[0];
      }).join(', ')
    `);
    console.log('Cookie names:', cookies || '(none visible from JS)');

    // Get all cookies via CDP
    const cdpCookies = await send('Network.getCookies', { urls: [url] });
    if (cdpCookies && cdpCookies.cookies) {
      for (const c of cdpCookies.cookies) {
        const issues = [];
        if (!c.httpOnly) issues.push('NOT HttpOnly');
        if (!c.secure) issues.push('NOT Secure');
        if (c.sameSite === 'None' || !c.sameSite) issues.push('SameSite=' + (c.sameSite || 'not set'));
        const severity = issues.length > 1 ? '⚠️' : issues.length === 1 ? '⚡' : '✅';
        console.log(`  ${severity} ${c.name}: ${issues.length > 0 ? issues.join(', ') : 'OK'} (domain: ${c.domain}, expires: ${c.expires > 0 ? new Date(c.expires * 1000).toISOString() : 'session'})`);
        if (issues.length > 0) {
          findings.push({ type: 'cookie', severity: issues.length > 1 ? 'high' : 'medium', detail: `Cookie "${c.name}": ${issues.join(', ')}` });
        }
      }
    }

    // 3. Check localStorage/sessionStorage for sensitive data
    console.log('\n--- LOCAL STORAGE ---');
    const lsKeys = await ev(`Object.keys(localStorage).join(', ')`);
    console.log('Keys:', lsKeys || '(empty)');

    const lsSensitive = await ev(`
      (function() {
        const sensitive = [];
        for (let i = 0; i < localStorage.length; i++) {
          const key = localStorage.key(i);
          const val = localStorage.getItem(key);
          if (val && (val.includes('eyJ') || val.includes('token') || val.includes('password') || val.includes('secret') || val.includes('key') || val.length > 500)) {
            sensitive.push({ key, preview: val.substring(0, 100), length: val.length });
          }
        }
        return JSON.stringify(sensitive);
      })()
    `);
    const sensitiveItems = JSON.parse(lsSensitive);
    if (sensitiveItems.length > 0) {
      console.log('⚠️ Sensitive data in localStorage:');
      sensitiveItems.forEach(s => {
        console.log(`  - ${s.key}: ${s.preview}... (${s.length} chars)`);
        findings.push({ type: 'storage', severity: 'high', detail: `Sensitive data in localStorage: "${s.key}" (${s.length} chars)` });
      });
    }

    // Session storage
    const ssKeys = await ev(`Object.keys(sessionStorage).join(', ')`);
    console.log('Session Storage keys:', ssKeys || '(empty)');

    // 4. Check for exposed credentials in page source
    console.log('\n--- EXPOSED CREDENTIALS ---');
    const exposed = await ev(`
      (function() {
        const html = document.documentElement.outerHTML;
        const issues = [];
        // Check for hardcoded keys/tokens in HTML
        if (html.match(/api[_-]?key['"\\s]*[:=]['"\\s]*[a-zA-Z0-9_-]{20,}/i)) issues.push('API key in HTML');
        if (html.match(/token['"\\s]*[:=]['"\\s]*eyJ/i)) issues.push('JWT token in HTML');
        if (html.match(/password['"\\s]*[:=]['"\\s]*[^'"]{3,}/i)) issues.push('Password in HTML');
        if (html.match(/secret['"\\s]*[:=]['"\\s]*[a-zA-Z0-9]{10,}/i)) issues.push('Secret in HTML');
        if (html.match(/supabase[a-zA-Z]*['"\\s]*[:=]['"\\s]*eyJ/i)) issues.push('Supabase key in HTML');
        if (html.match(/firebase/i)) issues.push('Firebase reference detected');

        // Check meta tags
        const metas = document.querySelectorAll('meta');
        metas.forEach(m => {
          const content = m.getAttribute('content') || '';
          if (content.match(/^(sk-|pk_|rk_|whsec_)/)) issues.push('API key in meta tag: ' + content.substring(0, 20));
        });

        // Check for .env exposure
        if (html.match(/NEXT_PUBLIC_|REACT_APP_|VITE_/)) issues.push('Environment variable prefix found in client');

        return JSON.stringify(issues);
      })()
    `);
    const exposedIssues = JSON.parse(exposed);
    if (exposedIssues.length > 0) {
      exposedIssues.forEach(i => {
        console.log(`  ⚠️ ${i}`);
        findings.push({ type: 'exposed', severity: 'critical', detail: i });
      });
    } else {
      console.log('  ✅ No obvious credentials in HTML');
    }

    // 5. Check security headers
    console.log('\n--- SECURITY HEADERS ---');
    const headerChecks = [
      'Content-Security-Policy',
      'X-Frame-Options',
      'X-Content-Type-Options',
      'Strict-Transport-Security',
      'X-XSS-Protection',
      'Referrer-Policy',
      'Permissions-Policy',
    ];

    // 6. Check forms for CSRF protection
    console.log('\n--- FORMS & INPUTS ---');
    const forms = await ev(`
      (function() {
        const forms = document.querySelectorAll('form');
        const inputs = document.querySelectorAll('input');
        const result = {
          formCount: forms.length,
          passwordInputs: 0,
          csrfTokens: 0,
          autocompleteOff: 0,
          hiddenInputs: [],
        };
        inputs.forEach(i => {
          if (i.type === 'password') {
            result.passwordInputs++;
            if (i.autocomplete !== 'off' && i.autocomplete !== 'new-password') {
              result.autocompleteOff++;
            }
          }
          if (i.type === 'hidden') {
            result.hiddenInputs.push({ name: i.name, hasValue: !!i.value });
          }
          if (i.name && i.name.match(/csrf|_token|authenticity/i)) result.csrfTokens++;
        });
        return JSON.stringify(result);
      })()
    `);
    const formData = JSON.parse(forms);
    console.log(`  Forms: ${formData.formCount}`);
    console.log(`  Password inputs: ${formData.passwordInputs}`);
    console.log(`  CSRF tokens found: ${formData.csrfTokens}`);
    if (formData.passwordInputs > 0 && formData.csrfTokens === 0) {
      console.log('  ⚠️ No CSRF token detected on login form');
      findings.push({ type: 'csrf', severity: 'medium', detail: 'No CSRF token on form with password input' });
    }

    // 7. Check for XSS vectors
    console.log('\n--- XSS VECTORS ---');
    const xss = await ev(`
      (function() {
        const issues = [];
        // Check for innerHTML usage in scripts
        const scripts = document.querySelectorAll('script');
        let inlineScripts = 0;
        scripts.forEach(s => {
          if (s.textContent && !s.src) inlineScripts++;
        });
        issues.push('Inline scripts: ' + inlineScripts);

        // Check for dangerous event handlers
        const allEls = document.querySelectorAll('*');
        let onHandlers = 0;
        allEls.forEach(el => {
          const attrs = el.attributes;
          for (let i = 0; i < attrs.length; i++) {
            if (attrs[i].name.startsWith('on') && attrs[i].name !== 'on') onHandlers++;
          }
        });
        issues.push('Inline event handlers: ' + onHandlers);

        // Check for eval usage
        const allScriptText = Array.from(scripts).map(s => s.textContent).join(' ');
        if (allScriptText.includes('eval(')) issues.push('eval() detected in scripts');
        if (allScriptText.includes('innerHTML')) issues.push('innerHTML usage in scripts');
        if (allScriptText.includes('document.write')) issues.push('document.write detected');

        return JSON.stringify(issues);
      })()
    `);
    JSON.parse(xss).forEach(i => console.log(`  ${i}`));

    // 8. Check third-party scripts
    console.log('\n--- THIRD-PARTY SCRIPTS ---');
    const thirdParty = await ev(`
      (function() {
        const scripts = document.querySelectorAll('script[src]');
        const domains = new Set();
        const list = [];
        scripts.forEach(s => {
          try {
            const u = new URL(s.src);
            if (!u.hostname.includes('ninjabrhub')) {
              domains.add(u.hostname);
              list.push(u.hostname + u.pathname.substring(0, 50));
            }
          } catch(e) {}
        });
        return JSON.stringify({ count: domains.size, domains: [...domains], scripts: list.slice(0, 20) });
      })()
    `);
    const tp = JSON.parse(thirdParty);
    console.log(`  ${tp.count} external domains:`);
    tp.domains.forEach(d => console.log(`    - ${d}`));

    // 9. Check iframe embedding
    console.log('\n--- IFRAMES ---');
    const iframes = await ev(`
      (function() {
        return JSON.stringify(Array.from(document.querySelectorAll('iframe')).map(f => ({
          src: f.src,
          sandbox: f.sandbox ? f.sandbox.toString() : 'none',
          allow: f.allow || 'none'
        })));
      })()
    `);
    const iframeList = JSON.parse(iframes);
    console.log(`  ${iframeList.length} iframes`);
    iframeList.forEach(f => {
      console.log(`  - ${f.src.substring(0, 80)}`);
      if (f.sandbox === 'none') {
        console.log('    ⚠️ No sandbox attribute');
        findings.push({ type: 'iframe', severity: 'medium', detail: `Iframe without sandbox: ${f.src.substring(0, 60)}` });
      }
    });

    // 10. Check auth state
    console.log('\n--- AUTH STATE ---');
    const auth = await ev(`
      (function() {
        const result = {};
        // Check for Supabase
        try {
          const sbKeys = Object.keys(localStorage).filter(k => k.includes('supabase') || k.includes('sb-'));
          result.supabase = sbKeys.length > 0 ? sbKeys : [];
        } catch(e) {}
        // Check for Firebase
        try {
          const fbKeys = Object.keys(localStorage).filter(k => k.includes('firebase'));
          result.firebase = fbKeys.length > 0 ? fbKeys : [];
        } catch(e) {}
        // Check for any auth tokens
        try {
          const authKeys = Object.keys(localStorage).filter(k =>
            k.includes('auth') || k.includes('token') || k.includes('session') || k.includes('user')
          );
          result.authKeys = authKeys;
          // Check values for JWTs
          authKeys.forEach(k => {
            const v = localStorage.getItem(k);
            if (v && v.includes('eyJ')) {
              result.hasJWT = true;
              result.jwtKey = k;
              result.jwtPreview = v.substring(0, 80);
            }
          });
        } catch(e) {}
        return JSON.stringify(result);
      })()
    `);
    console.log(auth);

    // 11. Navigate to check login page specifically
    console.log('\n--- NAVIGATING TO AUTH PAGE ---');
    await send('Page.navigate', { url: 'https://ninjabrhub.io/auth' });
    await new Promise(r => setTimeout(r, 5000));

    const authPage = await ev(`
      (function() {
        const result = {
          url: window.location.href,
          isHTTPS: window.location.protocol === 'https:',
          forms: [],
          passwordFields: [],
        };

        // Check password fields
        document.querySelectorAll('input[type="password"]').forEach(i => {
          result.passwordFields.push({
            name: i.name || i.id || 'unnamed',
            autocomplete: i.autocomplete || 'not set',
            hasPattern: !!i.pattern,
            minLength: i.minLength || 0,
          });
        });

        // Check email fields
        document.querySelectorAll('input[type="email"], input[type="text"]').forEach(i => {
          result.forms.push({
            type: i.type,
            name: i.name || i.id || 'unnamed',
            placeholder: i.placeholder || '',
          });
        });

        // Check for rate limiting hints
        const bodyText = document.body.innerText.substring(0, 2000);
        result.hasRateLimit = bodyText.includes('rate') || bodyText.includes('attempts') || bodyText.includes('tentativas');
        result.hasCaptcha = !!document.querySelector('[class*="captcha"], [class*="recaptcha"], [data-sitekey]');
        result.pageText = bodyText.substring(0, 500);

        return JSON.stringify(result);
      })()
    `);
    const authInfo = JSON.parse(authPage);
    console.log('HTTPS:', authInfo.isHTTPS ? '✅' : '❌ NOT HTTPS');
    console.log('Password fields:', authInfo.passwordFields.length);
    authInfo.passwordFields.forEach(p => {
      console.log(`  - ${p.name}: autocomplete=${p.autocomplete}, minLength=${p.minLength}`);
      if (p.minLength < 8) {
        findings.push({ type: 'auth', severity: 'medium', detail: `Password field "${p.name}" has no minimum length requirement` });
      }
    });
    console.log('CAPTCHA:', authInfo.hasCaptcha ? '✅' : '⚠️ No CAPTCHA detected');
    if (!authInfo.hasCaptcha) {
      findings.push({ type: 'auth', severity: 'medium', detail: 'No CAPTCHA on login page - vulnerable to brute force' });
    }
    console.log('Rate limiting:', authInfo.hasRateLimit ? '✅' : '⚠️ No rate limit detected');
    console.log('Page text:', authInfo.pageText.substring(0, 200));

    // SUMMARY
    console.log('\n\n' + '='.repeat(60));
    console.log('  SECURITY AUDIT SUMMARY');
    console.log('='.repeat(60));

    const critical = findings.filter(f => f.severity === 'critical');
    const high = findings.filter(f => f.severity === 'high');
    const medium = findings.filter(f => f.severity === 'medium');

    console.log(`\n  🔴 Critical: ${critical.length}`);
    critical.forEach(f => console.log(`     - ${f.detail}`));
    console.log(`  🟠 High: ${high.length}`);
    high.forEach(f => console.log(`     - ${f.detail}`));
    console.log(`  🟡 Medium: ${medium.length}`);
    medium.forEach(f => console.log(`     - ${f.detail}`));

    // Save report
    const report = { timestamp: new Date().toISOString(), url: 'ninjabrhub.io', findings };
    fs.writeFileSync('security_audit_report.json', JSON.stringify(report, null, 2));

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
