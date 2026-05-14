/**
 * WHM Analytics Tracker v2.3.0
 * 
 * Server-side first трекер с cross-domain tracking.
 * Генерирует FBP/FBC для Meta Conversions API БЕЗ Facebook Pixel.
 * Передаёт attribution params между доменами.
 * 
 * v2.3.0: Brave-safe ad params: _mc={msclkid}, _gc={gclid} bypass Brave URL stripping.
 *         Configure in Microsoft/Google Ads tracking templates.
 *         Removed redundant fbp server cookie (whm.js generates it client-side).
 * v2.2.0: Server-side cookies: collector sets cookies via Set-Cookie headers,
 *         bypassing Brave/Safari document.cookie blocking.
 *         Uses credentials:'same-origin' for same-origin proxy endpoints.
 *         Reads _whm_* server-set cookies as fallback.
 * v2.1.0: Robust msclkid: early URL capture + URLSearchParams + try-catch
 * v2.0.9: URL cleanup for msclkid/gclid/yclid/fbclid
 * v2.0.4: Added dimension30 (fbc_action) for action-level fbc storage
 * v2.0.3: FBC logic fix - new fbclid overwrites cookie, same fbclid keeps original timestamp
 * v2.0.2: CRITICAL FIX - Don't override existing _fbc cookie (Meta CAPI requirement)
 * 
 * begin_checkout срабатывает при КЛИКЕ на внешние ссылки ведущие на:
 *   1. /store/* (Buy Now / Try Now для hosting)
 *   2. cart.php?a=add&domain=register (Buy Domain)
 *   3. cart.php?a=add&domain=transfer (Transfer Domain)
 * 
 * Scroll tracking: отправляет ОДНО событие с MAX порогом при уходе со страницы
 * Consent mode: whm('denied') / whm('granted') для GDPR compliance
 * 
 * Отправляется ПЕРЕД редиректом на client area.
 */
(function(window, document, undefined) {
  'use strict';

  // === EARLY URL CAPTURE ===
  // Capture search string IMMEDIATELY at script load time,
  // BEFORE Next.js hydration or any router can modify the URL.
  var INITIAL_SEARCH = window.location.search || '';
  var INITIAL_HREF = window.location.href || '';

  var VERSION = '2.3.0';  // Brave-safe ad params (_mc/_gc) + server cookies
  var STORAGE_KEY = '_whm_vid';
  var STORAGE_SESSION = '_whm_sid';
  var URL_PARAM = '_whm_vid';
  // Short params for cross-domain attribution
  var URL_GCLID = '_wg';   // gclid
  var URL_FBC = '_wf';     // fbc  
  var URL_FBP = '_wp';     // fbp
  var URL_MSCLKID = '_wm'; // msclkid (Microsoft Click ID)
  var URL_SOURCE = '_ws';  // utm_source
  var URL_MEDIUM = '_wu';  // utm_medium (renamed from _wm)
  // Brave-safe ad platform params (use in tracking templates)
  // Microsoft Ads final URL suffix: _mc={msclkid}
  // Google Ads final URL suffix: _gc={gclid}
  var URL_SAFE_MC = '_mc';  // msclkid (Brave doesn't strip this)
  var URL_SAFE_GC = '_gc';  // gclid (Brave doesn't strip this)
  var DEFAULT_ENDPOINT = '/collect';
  var CROSS_LINK_CLASS = 'whm-cross';
  
  // Facebook cookie settings
  var FB_COOKIE_DAYS = 90;  // FB рекомендует 90 дней
  // Microsoft cookie settings
  var MS_COOKIE_DAYS = 90;  // Microsoft recommends 90 days
  
  // Server-set cookie names (generic names to bypass Brave/Safari filter lists)
  // Collector sets these via Set-Cookie headers in HTTP response
  var SERVER_COOKIE_MC = '_whm_mc';  // msclkid
  var SERVER_COOKIE_GC = '_whm_gc';  // gclid
  var SERVER_COOKIE_FC = '_whm_fc';  // fbc
  var SERVER_COOKIE_FP = '_whm_fp';  // fbp
  var SERVER_COOKIE_YC = '_whm_yc';  // yclid
  var SERVER_COOKIE_US = '_whm_us';  // utm_source
  var SERVER_COOKIE_UM = '_whm_um';  // utm_medium
  
  var CONSENT_KEY = 'whm_consent';  // Ключ для сохранения consent
  
  var config = {
    siteId: null,
    endpoint: DEFAULT_ENDPOINT,
    debug: false,
    autoPageview: true,
    respectDNT: false,
    linkDomains: [],
    consentDenied: false  // Флаг отказа от трекинга
  };
  
  var state = {
    initialized: false,
    visitorId: null,
    sessionId: null,
    userId: null,
    dimensions: {},
    trackingParams: {}
  };

  // Utils
  function generateId(len) {
    len = len || 16;
    var c = 'abcdef0123456789', r = '';
    if (window.crypto && window.crypto.getRandomValues) {
      var a = new Uint8Array(len);
      window.crypto.getRandomValues(a);
      for (var i = 0; i < len; i++) r += c[a[i] % 16];
    } else {
      for (var j = 0; j < len; j++) r += c[Math.floor(Math.random() * 16)];
    }
    return r;
  }

  function sha256(str) {
    if (!window.crypto || !window.crypto.subtle) return Promise.resolve(null);
    var buf = new TextEncoder().encode(str.toLowerCase().trim());
    return window.crypto.subtle.digest('SHA-256', buf).then(function(h) {
      var hex = [], v = new DataView(h);
      for (var i = 0; i < v.byteLength; i++) hex.push(v.getUint8(i).toString(16).padStart(2, '0'));
      return hex.join('');
    });
  }

  function getCookie(n) {
    var m = document.cookie.match(new RegExp('(^| )' + n + '=([^;]+)'));
    return m ? decodeURIComponent(m[2]) : null;
  }

  function setCookie(n, v, days) {
    days = days || 365;
    var dt = new Date(); 
    dt.setTime(dt.getTime() + days * 86400000);
    // Для FB кук важно: SameSite=Lax, Secure на HTTPS
    var secure = window.location.protocol === 'https:' ? ';Secure' : '';
    document.cookie = n + '=' + encodeURIComponent(v) + 
      ';expires=' + dt.toUTCString() + 
      ';path=/' + 
      ';SameSite=Lax' + secure;
  }

  function getUrlParam(n) {
    // Method 1: URLSearchParams (most reliable)
    try {
      var sp = new URLSearchParams(window.location.search);
      if (sp.has(n)) return sp.get(n);
    } catch(e) {}
    // Method 2: Regex on current href
    var m = new RegExp('[?&]' + n + '=([^&#]*)').exec(window.location.href);
    if (m) return decodeURIComponent(m[1]);
    // Method 3: Regex on INITIAL href (captured before hydration)
    var m2 = new RegExp('[?&]' + n + '=([^&#]*)').exec(INITIAL_HREF);
    return m2 ? decodeURIComponent(m2[1]) : null;
  }

  var storage = {
    get: function(k) {
      // Cookie first (readable by both JS and PHP), then localStorage
      var cv = getCookie(k);
      if (cv) return cv;
      try { return localStorage.getItem(k); } catch(e) { return null; }
    },
    set: function(k, v, d) {
      d = d || 365;
      // Always set cookie (so PHP/server can read it)
      var dt = new Date(); dt.setTime(dt.getTime() + d * 86400000);
      document.cookie = k + '=' + encodeURIComponent(v) + ';expires=' + dt.toUTCString() + ';path=/;SameSite=Lax' + (location.protocol === 'https:' ? ';Secure' : '');
      // Also set localStorage (backup, survives cookie clearing)
      try { localStorage.setItem(k, v); } catch(e) {}
    }
  };

  // ==========================================================================
  // FACEBOOK BROWSER ID (FBP) & CLICK ID (FBC) GENERATION
  // Формат по документации Meta Conversions API:
  // _fbp: fb.1.{timestamp_ms}.{random_number}
  // _fbc: fb.1.{timestamp_ms}.{fbclid}
  // ==========================================================================

  function generateFbpRandomPart() {
    // FB Pixel генерирует 10-значный random number
    // Мы делаем так же для совместимости
    return Math.floor(Math.random() * 9000000000) + 1000000000;
  }

  function getOrCreateFbp() {
    // 1. Проверяем существующую куку _fbp
    var existing = getCookie('_fbp');
    if (existing && /^fb\.\d+\.\d+\.\d+$/.test(existing)) {
      log('FBP from cookie:', existing);
      return existing;
    }
    
    // 1.5 Server-set cookie fallback (Brave bypass)
    var serverFbp = getCookie(SERVER_COOKIE_FP);
    if (serverFbp && /^fb\.\d+\.\d+\.\d+$/.test(serverFbp)) {
      log('FBP from server cookie:', serverFbp);
      setCookie('_fbp', serverFbp, FB_COOKIE_DAYS);  // try to replicate
      return serverFbp;
    }
    
    // 2. Проверяем cross-domain параметр
    // Support both short (_wp) and long (_fbp) param names
    var urlFbp = getUrlParam(URL_FBP) || getUrlParam('_fbp');
    if (urlFbp && /^fb\.\d+\.\d+\.\d+$/.test(urlFbp)) {
      setCookie('_fbp', urlFbp, FB_COOKIE_DAYS);
      log('FBP from cross-domain URL:', urlFbp);
      return urlFbp;
    }
    
    // 3. Генерируем новый FBP
    // Формат: fb.{subdomainIndex}.{creationTime}.{randomNumber}
    // subdomainIndex=1 для основного домена (example.com)
    var fbp = 'fb.1.' + Date.now() + '.' + generateFbpRandomPart();
    setCookie('_fbp', fbp, FB_COOKIE_DAYS);
    log('FBP generated:', fbp);
    return fbp;
  }

  function getOrCreateFbc() {
    // Get fbclid from URL (new click from FB ad)
    var fbclid = getUrlParam('fbclid');
    
    // Check existing _fbc cookie
    var existing = getCookie('_fbc');
    
    if (fbclid) {
      // User clicked a FB ad link with fbclid in URL
      
      if (existing && existing.indexOf('.' + fbclid) !== -1) {
        // Existing cookie contains the SAME fbclid - don't modify timestamp
        log('FBC from cookie (same fbclid, keeping timestamp):', existing);
        return existing;
      }
      
      // New fbclid (different from cookie or no cookie) - create new fbc
      var fbc = 'fb.1.' + Date.now() + '.' + fbclid;
      setCookie('_fbc', fbc, FB_COOKIE_DAYS);
      log('FBC created from new fbclid:', fbc);
      return fbc;
    }
    
    // No fbclid in URL - use existing cookie if valid
    if (existing && /^fb\.\d+\.\d+\..+$/.test(existing)) {
      log('FBC from cookie:', existing);
      return existing;
    }
    
    // Check cross-domain parameter (full fbc value)
    // Support both short (_wf) and long (_fbc) param names
    var urlFbc = getUrlParam(URL_FBC) || getUrlParam('_fbc');
    if (urlFbc && /^fb\.\d+\.\d+\..+$/.test(urlFbc)) {
      setCookie('_fbc', urlFbc, FB_COOKIE_DAYS);
      log('FBC from cross-domain URL:', urlFbc);
      return urlFbc;
    }
    
    // 5. Server-set cookie fallback (Brave bypass)
    var serverFbc = getCookie(SERVER_COOKIE_FC);
    if (serverFbc && /^fb\.\d+\.\d+\..+$/.test(serverFbc)) {
      log('FBC from server cookie:', serverFbc);
      setCookie('_fbc', serverFbc, FB_COOKIE_DAYS);  // try to replicate
      return serverFbc;
    }
    
    // No FBC available
    return null;
  }

  // ==========================================================================
  // MICROSOFT CLICK ID (MSCLKID) HANDLING
  // Microsoft Ads adds msclkid to URL when user clicks on Bing ad
  // We store it in cookie for attribution tracking
  // ==========================================================================
  
  function getOrCreateMsclkid() {
    try {
      // Get msclkid from URL (new click from Microsoft/Bing ad)
      var msclkid = getUrlParam('msclkid');
      
      // Brave-safe param: _mc={msclkid} in Microsoft Ads tracking template
      // Brave strips 'msclkid' but leaves '_mc' intact
      if (!msclkid) {
        msclkid = getUrlParam(URL_SAFE_MC);
        if (msclkid) log('MSCLKID from _mc (Brave-safe):', msclkid);
      }
      
      // Fallback: try INITIAL_SEARCH directly (before any router changes)
      if (!msclkid && INITIAL_SEARCH) {
        try {
          var isp = new URLSearchParams(INITIAL_SEARCH);
          msclkid = isp.get('msclkid') || isp.get(URL_SAFE_MC) || null;
          if (msclkid) log('MSCLKID from INITIAL_SEARCH:', msclkid);
        } catch(e2) {
          var im = /[?&](?:msclkid|_mc)=([^&#]*)/.exec(INITIAL_SEARCH);
          if (im) { msclkid = decodeURIComponent(im[1]); log('MSCLKID from INITIAL regex:', msclkid); }
        }
      }
      
      // Check existing _msclkid cookie
      var existing = getCookie('_msclkid');
      
      if (msclkid) {
        // User clicked a Microsoft ad link with msclkid in URL
        if (existing === msclkid) {
          log('MSCLKID from cookie (same):', existing);
          return existing;
        }
        
        // New msclkid - save to cookie
        setCookie('_msclkid', msclkid, MS_COOKIE_DAYS);
        log('MSCLKID saved from URL:', msclkid);
        return msclkid;
      }
      
      // No msclkid in URL - use existing cookie if valid
      if (existing) {
        log('MSCLKID from cookie:', existing);
        return existing;
      }
      
      // Check cross-domain parameter
      var urlMsclkid = getUrlParam(URL_MSCLKID) || getUrlParam('_msclkid');
      if (urlMsclkid) {
        setCookie('_msclkid', urlMsclkid, MS_COOKIE_DAYS);
        log('MSCLKID from cross-domain URL:', urlMsclkid);
        return urlMsclkid;
      }
      
      // Server-set cookie fallback (Brave bypass)
      var serverMc = getCookie(SERVER_COOKIE_MC);
      if (serverMc) {
        log('MSCLKID from server cookie:', serverMc);
        setCookie('_msclkid', serverMc, MS_COOKIE_DAYS);  // try to replicate
        return serverMc;
      }
      
      // No MSCLKID available
      return null;
    } catch(e) {
      log('MSCLKID ERROR:', e.message || e);
      return null;
    }
  }

  function log() {
    if (config.debug && console) {
      var a = [].slice.call(arguments);
      a.unshift('[WHM]');
      console.log.apply(console, a);
    }
  }

  // Visitor ID (with cross-domain support)
  function getOrCreateVisitorId() {
    // 1. Check URL for cross-domain visitor_id
    var urlVid = getUrlParam(URL_PARAM);
    if (urlVid && /^[a-fA-F0-9]{16,32}$/.test(urlVid)) {
      var vid = urlVid.toLowerCase();
      storage.set(STORAGE_KEY, vid, 730);
      log('Visitor ID from URL (cross-domain):', vid);
      return vid;
    }
    // 2. Check localStorage
    var stored = storage.get(STORAGE_KEY);
    if (stored && stored.length >= 16) return stored;
    // 3. Generate new
    var newVid = generateId(16);
    storage.set(STORAGE_KEY, newVid, 730);
    log('New visitor ID:', newVid);
    return newVid;
  }

  function getSessionId() {
    var sid; try { sid = sessionStorage.getItem(STORAGE_SESSION); } catch(e) {}
    if (!sid) { sid = generateId(8); try { sessionStorage.setItem(STORAGE_SESSION, sid); } catch(e) {} }
    return sid;
  }

  // Clean tracking params from URL after they're processed (UX improvement)
  function cleanUrlParams() {
    if (!window.history || !history.replaceState) return;
    
    var url = new URL(window.location.href);
    var paramsToRemove = [
      // Cross-domain short params (наши)
      '_whm_vid', '_wf', '_wp', '_wg', '_wm', '_ws', '_wu',
      // Cross-domain long aliases
      '_fbc', '_fbp', '_msclkid',
      // Brave-safe ad platform params
      '_mc', '_gc',
      // Original click IDs from ad platforms (очищаем после считывания)
      'msclkid', 'gclid', 'yclid', 'fbclid',
      // UTM parameters (уже считаны в dimensions)
      'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'
    ];
    var removed = false;
    
    paramsToRemove.forEach(function(param) {
      if (url.searchParams.has(param)) {
        url.searchParams.delete(param);
        removed = true;
      }
    });
    
    if (removed) {
      // Keep the URL clean but preserve other params
      var cleanUrl = url.pathname + (url.search || '') + (url.hash || '');
      history.replaceState(null, '', cleanUrl);
      log('Cleaned tracking params from URL');
    }
  }

  // Cross-domain tracking with attribution
  function getUrlWithVisitorId(url) {
    if (!state.visitorId) return url;
    if (url.indexOf(URL_PARAM + '=') !== -1) return url;
    
    var params = URL_PARAM + '=' + state.visitorId;
    
    // Add attribution params (short names to keep URL clean)
    var d = state.dimensions;
    if (d.dimension3) params += '&' + URL_GCLID + '=' + encodeURIComponent(d.dimension3);  // gclid
    if (d.dimension1) {
      var encoded = encodeURIComponent(d.dimension1);
      params += '&' + URL_FBC + '=' + encoded;
      // DEBUG: Log FBC before cross-domain transfer
      log('FBC CROSS-DOMAIN DEBUG:', {
        original: d.dimension1,
        originalLen: d.dimension1.length,
        encoded: encoded,
        encodedLen: encoded.length
      });
    }
    if (d.dimension2) params += '&' + URL_FBP + '=' + encodeURIComponent(d.dimension2);    // fbp
    if (d.dimension27) params += '&' + URL_MSCLKID + '=' + encodeURIComponent(d.dimension27); // msclkid
    if (d.dimension8) params += '&' + URL_SOURCE + '=' + encodeURIComponent(d.dimension8); // utm_source
    if (d.dimension9) params += '&' + URL_MEDIUM + '=' + encodeURIComponent(d.dimension9); // utm_medium
    if (d.dimension10) params += '&_wc=' + encodeURIComponent(d.dimension10); // utm_campaign
    
    return url + (url.indexOf('?') === -1 ? '?' : '&') + params;
  }

  function setupCrossLinkHandler() {
    // Handle links with .whm-cross class OR linkDomains
    // ALSO: отправляем begin_checkout для ссылок на /store/* или cart.php?domain=
    document.addEventListener('click', function(e) {
      if(config.debug)console.log('[WHM DEBUG] Click detected on:', e.target.tagName, e.target);
      
      var link = e.target.closest ? e.target.closest('a') : null;
      if (!link) {
        var el = e.target;
        while (el && el.tagName !== 'A') el = el.parentElement;
        link = el;
      }
      
      if(config.debug)console.log('[WHM DEBUG] Found link:', link ? link.href : 'NO LINK');
      
      if (!link || !link.href) {
        if(config.debug)console.log('[WHM DEBUG] No link or href, skipping');
        return;
      }
      if (!state.visitorId) {
        if(config.debug)console.log('[WHM DEBUG] No visitorId, skipping');
        return;
      }
      
      var hasClass = link.classList && link.classList.contains(CROSS_LINK_CLASS);
      var isExternal = false;
      var matchDomain = false;

      try {
        var u = new URL(link.href, window.location.origin);
        if(config.debug)console.log('[WHM DEBUG] Parsed URL:', u.hostname, u.pathname);
        
        if (u.searchParams.has(URL_PARAM)) {
          if(config.debug)console.log('[WHM DEBUG] Already has vid param, skipping');
          return;
        }
        
        isExternal = u.hostname !== window.location.hostname;
        if(config.debug)console.log('[WHM DEBUG] isExternal:', isExternal, 'current:', window.location.hostname);
        
        if (config.linkDomains.length) {
          matchDomain = config.linkDomains.some(function(d) {
            return u.hostname === d || u.hostname.endsWith('.' + d);
          });
        }
        if(config.debug)console.log('[WHM DEBUG] matchDomain:', matchDomain, 'linkDomains:', config.linkDomains);
        if(config.debug)console.log('[WHM DEBUG] hasClass:', hasClass);
        
        // Add vid if: external domain match OR has whm-cross class
        if ((isExternal && matchDomain) || hasClass) {
          if(config.debug)console.log('[WHM DEBUG] ✓ Will handle this link!');
          
          // ==========================================================
          // BEGIN_CHECKOUT: проверяем и отправляем ПЕРЕД редиректом
          // ==========================================================
          var checkoutData = detectBeginCheckout(u);
          if(config.debug)console.log('[WHM DEBUG] checkoutData:', checkoutData);
          
          if (checkoutData) {
            if(config.debug)console.log('[WHM DEBUG] 🛒 Sending begin_checkout:', checkoutData.checkout_type);
            track('begin_checkout', checkoutData);
            if(config.debug)console.log('[WHM DEBUG] ✓ track() called');
          }
          
          e.preventDefault();
          var newUrl = getUrlWithVisitorId(link.href);
          if(config.debug)console.log('[WHM DEBUG] Redirecting to:', newUrl);
          window.location.href = newUrl;
        } else {
          if(config.debug)console.log('[WHM DEBUG] ✗ Not handling - not external+matchDomain and no class');
        }
      } catch(err) {
        if(config.debug)console.log('[WHM DEBUG] Error:', err);
        // Relative URL with class
        if (hasClass) {
          e.preventDefault();
          var newUrl = getUrlWithVisitorId(link.href);
          if(config.debug)console.log('[WHM DEBUG] Class redirect:', newUrl);
          window.location.href = newUrl;
        }
      }
    }, true);
  }
  
  // Вспомогательная функция для определения begin_checkout
  function detectBeginCheckout(u) {
    var pathname = u.pathname;
    
    // СЛУЧАЙ 1: Ссылка на /store/* (Buy Now / Try Now для hosting)
    var storeMatch = pathname.match(/\/store\/([^\/]+)(?:\/([^\/\?]+))?/);
    if (storeMatch) {
      return {
        product_group: storeMatch[1],
        product_name: storeMatch[2] || storeMatch[1],
        checkout_type: 'hosting',
        page_path: window.location.pathname,
        target_url: u.href
      };
    }
    
    // СЛУЧАЙ 2: cart.php?a=add&domain=register (Buy Domain)
    // СЛУЧАЙ 3: cart.php?a=add&domain=transfer (Transfer Domain)
    if (pathname.indexOf('cart.php') !== -1) {
      var a = u.searchParams.get('a');
      var domain = u.searchParams.get('domain');
      
      if (a === 'add' && (domain === 'register' || domain === 'transfer')) {
        return {
          product_group: 'domains',
          product_name: 'domain_' + domain,
          checkout_type: 'domain_' + domain,
          page_path: window.location.pathname,
          target_url: u.href
        };
      }
    }
    
    return null;
  }

  function setupLinkDomains(domains) {
    if (!domains || !domains.length) return;
    config.linkDomains = domains;
    log('Cross-domain linking for:', domains);
  }

  // Data collection
  function collectParams() {
    var p = {};
    ['utm_source','utm_medium','utm_campaign','utm_term','utm_content'].forEach(function(k) {
      var v = getUrlParam(k); if (v) p[k] = v;
    });
    var gclid = getUrlParam('gclid') || getUrlParam(URL_SAFE_GC); if (gclid) p.gclid = gclid;
    var yclid = getUrlParam('yclid'); if (yclid) p.yclid = yclid;
    
    // ==========================================================================
    // FBP и FBC - ГЕНЕРИРУЕМ САМИ (без Facebook Pixel!)
    // Это критично для Meta Conversions API
    // ==========================================================================
    
    // FBC (Click ID) - из fbclid в URL или из куки
    var fbc = getOrCreateFbc();
    if (fbc) p.fbc = fbc;
    
    // FBP (Browser ID) - ВСЕГДА создаём/читаем
    var fbp = getOrCreateFbp();
    if (fbp) p.fbp = fbp;
    
    // ==========================================================================
    // MSCLKID - Microsoft Click ID (для Microsoft Ads / Bing)
    // ==========================================================================
    var msclkid = getOrCreateMsclkid();
    if (msclkid) p.msclkid = msclkid;
    
    // Read cross-domain attribution params (short names)
    var wg = getUrlParam(URL_GCLID); if (wg && !p.gclid) p.gclid = wg;
    var wf = getUrlParam(URL_FBC); if (wf && !p.fbc) p.fbc = wf;
    var wp = getUrlParam(URL_FBP); if (wp && !p.fbp) p.fbp = wp;
    var wms = getUrlParam(URL_MSCLKID); if (wms && !p.msclkid) p.msclkid = wms;
    var ws = getUrlParam(URL_SOURCE); if (ws && !p.utm_source) p.utm_source = ws;
    var wu = getUrlParam(URL_MEDIUM); if (wu && !p.utm_medium) p.utm_medium = wu;
    var wc = getUrlParam('_wc'); if (wc && !p.utm_campaign) p.utm_campaign = wc;
    
    // Server-set cookie fallbacks (Brave/Safari bypass)
    // These cookies are set by the collector via Set-Cookie headers
    if (!p.msclkid) { var smc = getCookie(SERVER_COOKIE_MC); if (smc) { p.msclkid = smc; log('msclkid from server cookie'); } }
    if (!p.gclid)   { var sgc = getCookie(SERVER_COOKIE_GC); if (sgc) { p.gclid = sgc; log('gclid from server cookie'); } }
    if (!p.fbc)     { var sfc = getCookie(SERVER_COOKIE_FC); if (sfc) { p.fbc = sfc; log('fbc from server cookie'); } }
    if (!p.fbp)     { var sfp = getCookie(SERVER_COOKIE_FP); if (sfp) { p.fbp = sfp; log('fbp from server cookie'); } }
    if (!p.yclid)   { var syc = getCookie(SERVER_COOKIE_YC); if (syc) { p.yclid = syc; log('yclid from server cookie'); } }
    if (!p.utm_source) { var sus = getCookie(SERVER_COOKIE_US); if (sus) { p.utm_source = sus; log('utm_source from server cookie'); } }
    if (!p.utm_medium) { var sum = getCookie(SERVER_COOKIE_UM); if (sum) { p.utm_medium = sum; log('utm_medium from server cookie'); } }
    
    return p;
  }
  
  // Apply server-stored params from collector response
  // The collector returns stored tracking params in the response body
  // This updates dimensions for subsequent events in the same session
  function applyServerStored(stored) {
    if (!stored || typeof stored !== 'object') return;
    var updated = false;
    for (var dimKey in stored) {
      if (stored.hasOwnProperty(dimKey) && stored[dimKey] && !state.dimensions[dimKey]) {
        state.dimensions[dimKey] = stored[dimKey];
        updated = true;
        log('Server restored:', dimKey, '=', stored[dimKey]);
      }
    }
    if (updated) log('Dimensions updated from server store');
  }

  function collectPage() {
    // Clean URL from cross-domain tracking params
    var url = window.location.href
      .replace(new RegExp('[?&]' + URL_PARAM + '=[^&]*'), '')
      .replace(new RegExp('[?&]' + URL_GCLID + '=[^&]*'), '')
      .replace(new RegExp('[?&]' + URL_FBC + '=[^&]*'), '')
      .replace(new RegExp('[?&]' + URL_FBP + '=[^&]*'), '')
      .replace(new RegExp('[?&]' + URL_MSCLKID + '=[^&]*'), '')
      .replace(new RegExp('[?&]' + URL_SAFE_MC + '=[^&]*'), '')
      .replace(new RegExp('[?&]' + URL_SAFE_GC + '=[^&]*'), '')
      .replace(new RegExp('[?&]' + URL_SOURCE + '=[^&]*'), '')
      .replace(new RegExp('[?&]' + URL_MEDIUM + '=[^&]*'), '')
      .replace(/[?&]$/, '');
    return {
      url: url,
      referrer: document.referrer || null,
      title: document.title || null,
      screen_width: screen ? screen.width : null,
      screen_height: screen ? screen.height : null
    };
  }

  // Send
  function send(data, cb) {
    var payload = Object.assign({}, data, {
      site_id: config.siteId,
      visitor_id: state.visitorId,
      _t: Date.now()
    });
    if (state.userId) payload.user_id = state.userId;
    Object.keys(state.dimensions).forEach(function(k) { payload[k] = state.dimensions[k]; });
    
    var body = JSON.stringify(payload);
    log('Sending:', payload);

    // sendBeacon disabled - CORS issues with Cloudflare
    // if (navigator.sendBeacon) {
    //   var blob = new Blob([body], {type: 'application/json'});
    //   if (navigator.sendBeacon(config.endpoint, blob)) { log('Sent (beacon)'); if (cb) cb(true); return; }
    // }
    if (fetch) {
      // Determine credentials mode: same-origin for proxied /t/collect, cors for absolute URLs
      var isSameOrigin = config.endpoint.charAt(0) === '/';
      var fetchOpts = {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: body,
        keepalive: true,
        credentials: isSameOrigin ? 'same-origin' : 'omit',
        mode: isSameOrigin ? 'same-origin' : 'cors'
      };
      fetch(config.endpoint, fetchOpts)
        .then(function(r) {
          log('Sent (fetch):', r.status);
          // Read server-stored params from response (Brave bypass)
          if (r.ok) {
            r.clone().json().then(function(resp) {
              if (resp && resp.stored) {
                applyServerStored(resp.stored);
              }
            }).catch(function(){});
          }
          if (cb) cb(r.ok);
        })
        .catch(function() { if (cb) cb(false); });
      return;
    }
    var xhr = new XMLHttpRequest();
    xhr.open('POST', config.endpoint, true);
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.onreadystatechange = function() { if (xhr.readyState === 4) { log('Sent (xhr):', xhr.status); if (cb) cb(xhr.status >= 200 && xhr.status < 300); } };
    xhr.send(body);
  }

  // Consent Management
  function setConsentDenied() {
    config.consentDenied = true;
    state.initialized = false;  // Останавливаем трекинг
    scrollState.enabled = false; // Останавливаем scroll tracking
    try {
      localStorage.setItem(CONSENT_KEY, 'denied');
    } catch (e) {
      setCookie(CONSENT_KEY, 'denied', 365);
    }
    log('Consent DENIED - tracking disabled');
  }
  
  function setConsentGranted() {
    config.consentDenied = false;
    try {
      localStorage.setItem(CONSENT_KEY, 'granted');
    } catch (e) {
      setCookie(CONSENT_KEY, 'granted', 365);
    }
    log('Consent GRANTED - tracking enabled');
  }
  
  function checkSavedConsent() {
    // Проверяем сохранённый consent при загрузке
    try {
      var saved = localStorage.getItem(CONSENT_KEY);
      if (saved === 'denied') {
        config.consentDenied = true;
        return 'denied';
      }
    } catch (e) {
      var cookie = getCookie(CONSENT_KEY);
      if (cookie === 'denied') {
        config.consentDenied = true;
        return 'denied';
      }
    }
    return 'granted';
  }

  // Public API
  function init(siteId, opts) {
    if (state.initialized) return;
    
    // Support: whm('init', 3, {...}) or whm('init', {siteId: 3, ...})
    if (typeof siteId === 'object') {
      opts = siteId;
      siteId = opts.siteId;
    }
    
    if (!siteId) { 
      console.error('[WHM] siteId required'); 
      return; 
    }
    
    opts = opts || {};
    config.siteId = siteId;
    
    // Support both 'endpoint' and 'collectorUrl'
    if (opts.endpoint || opts.collectorUrl) config.endpoint = opts.endpoint || opts.collectorUrl;
    if (opts.autoPageview === false) config.autoPageview = false;
    if (opts.respectDNT && navigator.doNotTrack === '1') { log('DNT enabled'); return; }

    state.visitorId = getOrCreateVisitorId();
    state.sessionId = getSessionId();
    state.trackingParams = collectParams();
    
    var p = state.trackingParams;
    if (p.fbc) {
      state.dimensions.dimension1 = p.fbc;   // Visit-level (will be overwritten)
      state.dimensions.dimension30 = p.fbc;  // Action-level (preserved per event)
    }
    if (p.fbp) state.dimensions.dimension2 = p.fbp;
    if (p.gclid) state.dimensions.dimension3 = p.gclid;
    if (p.yclid) state.dimensions.dimension4 = p.yclid;
    if (p.msclkid) state.dimensions.dimension27 = p.msclkid;  // Microsoft Click ID
    if (p.utm_source) state.dimensions.dimension8 = p.utm_source;
    if (p.utm_medium) state.dimensions.dimension9 = p.utm_medium;
    if (p.utm_campaign) state.dimensions.dimension10 = p.utm_campaign;
    if (p.utm_content) state.dimensions.dimension11 = p.utm_content;
    if (p.utm_term) state.dimensions.dimension12 = p.utm_term;

    // DEBUG: log URL state for msclkid diagnostics (temporary)
    if (INITIAL_SEARCH && (INITIAL_SEARCH.indexOf('msclkid') !== -1 || INITIAL_SEARCH.indexOf('gclid') !== -1)) {
      log('DEBUG URL state:', {
        initial_search: INITIAL_SEARCH,
        current_search: window.location.search,
        msclkid_result: p.msclkid || 'NULL',
        dim27: state.dimensions.dimension27 || 'NULL'
      });
    }

    setupCrossLinkHandler();
    setupSpaTracking();
    
    // Fetch config from server (linkDomains, trackScroll)
    var configUrl = config.endpoint.replace('/collect', '') + '/config/' + siteId;
    
    fetch(configUrl)
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(cfg) {
        if (cfg) {
          log('Config loaded:', cfg);
          // Server config, но opts имеют приоритет
          if (opts.linkDomains === undefined && cfg.linkDomains) {
            setupLinkDomains(cfg.linkDomains);
          } else if (opts.linkDomains) {
            setupLinkDomains(opts.linkDomains);
          }
          if (opts.trackScroll === undefined && cfg.trackScroll === false) {
            log('Scroll disabled by server');
          } else if (opts.trackScroll !== false) {
            setupScrollTracking();
          }
        } else {
          // Fallback на opts
          if (opts.linkDomains) setupLinkDomains(opts.linkDomains);
          if (opts.trackScroll !== false) setupScrollTracking();
        }
      })
      .catch(function() {
        // Fallback на opts
        if (opts.linkDomains) setupLinkDomains(opts.linkDomains);
        if (opts.trackScroll !== false) setupScrollTracking();
      });

    // Auto-detect WHMCS user_id from server-set cookie (set by ClientAreaPage PHP hook when logged in)
    var whmUid = getCookie('_whm_uid');
    if (whmUid && /^[0-9]+$/.test(whmUid)) {
      state.userId = whmUid;
      state.dimensions.dimension7 = whmUid;
      log('User ID from _whm_uid cookie:', whmUid);
    }

    state.initialized = true;
    log('Initialized site', siteId, 'visitor:', state.visitorId);
    
    // Clean tracking params from URL after processing (better UX)
    cleanUrlParams();
    
    if (config.autoPageview) track('pageview');
  }

  function track(type, data) {
    if (!state.initialized) return;
    if (config.consentDenied) { log('Tracking blocked (consent denied)'); return; }
    data = data || {};
    var payload = collectPage();
    
    if (type === 'event') {
      payload.event_type = 'event';
      if (data.category) payload.event_category = data.category;
      if (data.action) payload.event_action = data.action;
      if (data.name) payload.event_name = data.name;
      if (data.value) payload.event_value = data.value;
    } else if (type === 'ecommerce') {
      payload.event_type = 'ecommerce';
      if (data.orderId) payload.order_id = data.orderId;
      if (data.revenue) payload.revenue = data.revenue;
      if (data.items) payload.items = JSON.stringify(data.items);
    } else if (type === 'goal') {
      payload.event_type = 'goal';
      if (data.goalId) payload.goal_id = data.goalId;
      if (data.revenue) payload.revenue = data.revenue;
    } else if (type === 'begin_checkout') {
      // begin_checkout отправляем как event_type='event' для валидации коллектора
      payload.event_type = 'event';
      payload.event_category = 'ecommerce';
      payload.event_action = 'begin_checkout';
      if (data.product_group) payload.event_name = data.product_group + (data.product_name ? '/' + data.product_name : '');
      if (data.checkout_type) payload.checkout_type = data.checkout_type;
      if (data.page_path) payload.checkout_path = data.page_path;
      if (data.target_url) payload.target_url = data.target_url;
    } else if (type === 'scroll') {
      // Scroll depth event
      // ВАЖНО: Для scroll нужно отправлять URL страницы где был scroll,
      // а не текущий URL (при SPA навигации они могут различаться)
      payload.event_type = 'event';
      payload.event_category = 'engagement';
      payload.event_action = 'scroll';
      payload.event_name = data.percent_scrolled + '%';
      payload.percent_scrolled = data.percent_scrolled;
      if (data.page_path) {
        // Подменяем URL на тот где был scroll
        var origin = window.location.origin;
        payload.url = origin + data.page_path;
        payload.scroll_page_path = data.page_path;
      }
    } else {
      // default: pageview
      payload.event_type = 'pageview';
    }
    send(payload);
  }

  function set(key, val) {
    if (!state.initialized && key !== 'debug') return;
    if (key === 'userId') { state.userId = val; state.dimensions.dimension7 = val; log('User ID:', val); }
    else if (key === 'email') { sha256(val).then(function(h) { if (h) { state.dimensions.dimension5 = h; log('Email hash set'); } }); }
    else if (key === 'phone') { sha256(String(val).replace(/\D/g,'')).then(function(h) { if (h) { state.dimensions.dimension6 = h; log('Phone hash set'); } }); }
    else if (key === 'debug') config.debug = !!val;
    else if (/^dimension\d+$/.test(key)) { state.dimensions[key] = val; log('Dim:', key, val); }
  }

  // ==========================================================================
  // SCROLL DEPTH TRACKING
  // Отслеживает глубину прокрутки страницы
  // Отправляет ОДНО событие с максимальным достигнутым порогом при уходе со страницы
  // ==========================================================================
  
  var scrollState = {
    thresholds: [25, 50, 75, 90],
    maxReached: {},    // { url: maxPercent } - максимальный достигнутый порог
    sent: {},          // { url: true } - уже отправлено для этого URL
    enabled: false,
    debounceTimer: null,
    scrollContainer: null  // Кастомный скролл-контейнер для SPA
  };
  
  // Определяем скролл-контейнер (для SPA может быть не window)
  function findScrollContainer() {
    // Проверяем популярные ID скролл-контейнеров для SPA
    var containerIds = ['main-content', 'main', 'content', 'app', 'root'];
    for (var i = 0; i < containerIds.length; i++) {
      var el = document.getElementById(containerIds[i]);
      if (el) {
        var style = window.getComputedStyle(el);
        var overflow = style.overflow || style.overflowY;
        if (overflow === 'auto' || overflow === 'scroll') {
          log('Found scroll container: #' + containerIds[i]);
          return el;
        }
      }
    }
    return null; // Используем window по умолчанию
  }
  
  function getScrollPercent() {
    var container = scrollState.scrollContainer;
    
    if (container) {
      // Кастомный скролл-контейнер (SPA)
      var scrollTop = container.scrollTop;
      var scrollHeight = container.scrollHeight;
      var clientHeight = container.clientHeight;
      var maxScroll = scrollHeight - clientHeight;
      
      if (maxScroll <= 0) return 100;
      return Math.min(100, Math.round((scrollTop / maxScroll) * 100));
    }
    
    // Стандартный window скролл
    var docHeight = Math.max(
      document.body.scrollHeight || 0,
      document.body.offsetHeight || 0,
      document.documentElement.scrollHeight || 0,
      document.documentElement.offsetHeight || 0,
      document.documentElement.clientHeight || 0
    );
    var viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    var scrollTop = window.pageYOffset || document.documentElement.scrollTop || document.body.scrollTop || 0;
    
    // Максимально возможная прокрутка
    var maxScroll = docHeight - viewportHeight;
    if (maxScroll <= 0) return 100; // Страница полностью видна
    
    return Math.min(100, Math.round((scrollTop / maxScroll) * 100));
  }
  
  function checkScrollThresholds() {
    if (!state.initialized || !scrollState.enabled) return;
    
    var currentUrl = window.location.pathname;
    var percent = getScrollPercent();
    
    // Находим максимальный достигнутый порог
    var maxThreshold = 0;
    for (var i = 0; i < scrollState.thresholds.length; i++) {
      var threshold = scrollState.thresholds[i];
      if (percent >= threshold) {
        maxThreshold = threshold;
      }
    }
    
    // Обновляем максимум для текущего URL (если больше предыдущего)
    if (!scrollState.maxReached[currentUrl] || maxThreshold > scrollState.maxReached[currentUrl]) {
      scrollState.maxReached[currentUrl] = maxThreshold;
      log('Scroll depth updated:', maxThreshold + '% on', currentUrl);
    }
  }
  
  // Отправляет scroll событие для страницы (вызывается при уходе)
  function sendScrollEvent(url) {
    if (!scrollState.enabled) return;
    if (scrollState.sent[url]) return;  // Уже отправлено
    
    var maxPercent = scrollState.maxReached[url];
    if (!maxPercent || maxPercent < 25) return;  // Не достигли даже 25%
    
    scrollState.sent[url] = true;
    
    // Получаем последнюю часть URL (после последнего /)
    var pageName = url.split('/').filter(Boolean).pop() || 'home';
    
    log('Sending scroll event:', maxPercent + '% for', url, '(' + pageName + ')');
    
    track('scroll', {
      percent_scrolled: maxPercent,
      page_path: url,
      page_name: pageName  // Последняя часть URL для идентификации страницы
    });
  }
  
  function onScroll() {
    // Debounce: проверяем не чаще чем раз в 100ms
    if (scrollState.debounceTimer) {
      clearTimeout(scrollState.debounceTimer);
    }
    scrollState.debounceTimer = setTimeout(checkScrollThresholds, 100);
  }
  
  function setupScrollTracking() {
    scrollState.enabled = true;
    
    // Находим скролл-контейнер (для SPA может быть не window)
    scrollState.scrollContainer = findScrollContainer();
    
    // Слушаем scroll на нужном элементе
    if (scrollState.scrollContainer) {
      scrollState.scrollContainer.addEventListener('scroll', onScroll, { passive: true });
      log('Scroll tracking on custom container');
    } else {
      window.addEventListener('scroll', onScroll, { passive: true });
      log('Scroll tracking on window');
    }
    
    // Отправляем scroll при уходе со страницы
    window.addEventListener('beforeunload', function() {
      var currentUrl = window.location.pathname;
      sendScrollEvent(currentUrl);
    });
    
    // Для Safari/iOS - visibilitychange более надёжен
    document.addEventListener('visibilitychange', function() {
      if (document.visibilityState === 'hidden') {
        var currentUrl = window.location.pathname;
        sendScrollEvent(currentUrl);
      }
    });
    
    // Проверяем начальное состояние (страница может быть уже прокручена)
    // Задержка 1.5s чтобы pageview точно ушёл первым
    setTimeout(checkScrollThresholds, 500);
    
    log('Scroll tracking enabled (thresholds:', scrollState.thresholds.join('%, ') + '%, send on leave)');
  }
  
  function resetScrollTracking() {
    // При навигации в SPA - отправляем scroll для предыдущей страницы и сбрасываем
    var currentUrl = window.location.pathname;
    
    // Отправляем scroll для всех URL кроме текущего (они закончились)
    for (var url in scrollState.maxReached) {
      if (url !== currentUrl && !scrollState.sent[url]) {
        sendScrollEvent(url);
      }
    }
    
    // Сбрасываем для нового URL
    scrollState.maxReached[currentUrl] = 0;
    scrollState.sent[currentUrl] = false;
    
    log('Scroll tracking reset for:', currentUrl);
  }

  // ==========================================================================
  // SPA NAVIGATION TRACKING
  // Отслеживает изменения URL в Single Page Applications
  // ==========================================================================
  
  var lastTrackedUrl = null;
  
  function trackSpaNavigation() {
    var currentUrl = window.location.href;
    
    // Не трекаем если URL не изменился (или только hash изменился)
    if (lastTrackedUrl === currentUrl) return;
    
    // Не трекаем изменения только хэша (scroll anchors)
    if (lastTrackedUrl) {
      var lastPath = lastTrackedUrl.split('#')[0].split('?')[0];
      var currentPath = currentUrl.split('#')[0].split('?')[0];
      if (lastPath === currentPath) {
        log('SPA: Hash-only change, skipping');
        return;
      }
    }
    
    lastTrackedUrl = currentUrl;
    
    if (state.initialized) {
      log('SPA: Navigation detected, tracking pageview:', currentUrl);
      resetScrollTracking();  // Reset scroll tracking for new page
      track('pageview');
    }
  }
  
  function setupSpaTracking() {
    // Сохраняем начальный URL
    lastTrackedUrl = window.location.href;
    
    // Перехватываем pushState
    var originalPushState = history.pushState;
    history.pushState = function() {
      originalPushState.apply(this, arguments);
      log('SPA: pushState called');
      // Небольшая задержка чтобы URL обновился
      setTimeout(trackSpaNavigation, 10);
    };
    
    // Перехватываем replaceState
    var originalReplaceState = history.replaceState;
    history.replaceState = function() {
      originalReplaceState.apply(this, arguments);
      log('SPA: replaceState called');
      setTimeout(trackSpaNavigation, 10);
    };
    
    // Слушаем popstate (кнопки назад/вперёд)
    window.addEventListener('popstate', function() {
      log('SPA: popstate event');
      setTimeout(trackSpaNavigation, 10);
    });
    
    log('SPA tracking enabled');
  }

  function whm() {
    var args = [].slice.call(arguments), cmd = args.shift();
    if (cmd === 'init') return init.apply(null, args);
    if (cmd === 'track') return track.apply(null, args);
    if (cmd === 'event') return track('event', {category: args[0], action: args[1], name: args[2], value: args[3]});
    if (cmd === 'pageview') return track('pageview');
    if (cmd === 'conversion' || cmd === 'goal') return track('goal', args[0]);
    if (cmd === 'set') return set.apply(null, args);
    if (cmd === 'getVisitorId') return state.visitorId;
    if (cmd === 'getUrl') return getUrlWithVisitorId(args[0]);
    if (cmd === 'linkDomains') return setupLinkDomains(args[0]);
    if (cmd === 'denied') return setConsentDenied();
    if (cmd === 'granted') return setConsentGranted();
    if (cmd === 'getConsent') return config.consentDenied ? 'denied' : 'granted';
    log('Unknown:', cmd);
  }

  whm.VERSION = VERSION;
  var old = window.whm;
  window.whm = whm;
  if (old && old.q) old.q.forEach(function(a) { whm.apply(null, a); });
  log('WHM Tracker v' + VERSION);

})(window, document);
