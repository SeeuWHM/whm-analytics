/**
 * WHM Analytics - Next.js Middleware Snippet
 * 
 * ЗАЧЕМ: Brave/Safari удаляют tracking параметры (msclkid, gclid, fbclid)
 * из URL ДО того, как JavaScript их увидит. Сервер Next.js получает
 * оригинальный URL с параметрами от пользователя. Этот код захватывает
 * их на сервере и ставит куки через Set-Cookie заголовки.
 * 
 * КАК ИСПОЛЬЗОВАТЬ:
 * Добавить этот код в вашу middleware функцию (proxy.ts / middleware.ts)
 * ПОСЛЕ создания NextResponse и ПЕРЕД return response.
 * 
 * Пример интеграции в proxy.ts:
 * 
 *   export async function proxy(request: NextRequest) {
 *     // ... existing code ...
 *     const response = NextResponse.next();
 *     // ... existing headers/cookies ...
 *     
 *     // === WHM Analytics: Capture tracking params server-side ===
 *     captureTrackingParams(request, response);
 *     
 *     return response;
 *   }
 */

// Tracking params that Brave/Safari strip from URLs
const TRACKING_PARAMS: Record<string, { cookie: string; maxAge: number }> = {
  msclkid:     { cookie: '_whm_mc', maxAge: 90 * 24 * 60 * 60 },  // Microsoft Click ID
  gclid:       { cookie: '_whm_gc', maxAge: 90 * 24 * 60 * 60 },  // Google Click ID
  fbclid:      { cookie: '_whm_fc_raw', maxAge: 90 * 24 * 60 * 60 },  // Facebook Click ID (raw)
  yclid:       { cookie: '_whm_yc', maxAge: 90 * 24 * 60 * 60 },  // Yandex Click ID
  utm_source:  { cookie: '_whm_us', maxAge: 90 * 24 * 60 * 60 },  // UTM Source
  utm_medium:  { cookie: '_whm_um', maxAge: 90 * 24 * 60 * 60 },  // UTM Medium
};

export function captureTrackingParams(request: NextRequest, response: NextResponse): void {
  const searchParams = request.nextUrl.searchParams;
  
  for (const [param, config] of Object.entries(TRACKING_PARAMS)) {
    const value = searchParams.get(param);
    if (value && value.length > 0 && value.length < 500) {
      response.cookies.set(config.cookie, value, {
        maxAge: config.maxAge,
        path: '/',
        sameSite: 'lax',
        secure: true,
        httpOnly: false,  // whm.js needs to read these
      });
    }
  }
}

/**
 * Минимальный вариант - просто скопировать в proxy.ts:
 * 
 * // WHM Analytics: Capture tracking params before Brave strips them
 * ['msclkid', 'gclid', 'fbclid', 'yclid', 'utm_source', 'utm_medium'].forEach(param => {
 *   const val = request.nextUrl.searchParams.get(param);
 *   if (val) {
 *     const cookieName = '_whm_' + param.replace('msclkid','mc').replace('gclid','gc')
 *       .replace('fbclid','fc_raw').replace('yclid','yc').replace('utm_source','us').replace('utm_medium','um');
 *     response.cookies.set(cookieName, val, { maxAge: 90*24*60*60, path: '/', sameSite: 'lax', secure: true, httpOnly: false });
 *   }
 * });
 */
