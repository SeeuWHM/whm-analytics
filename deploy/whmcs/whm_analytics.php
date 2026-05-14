<?php
/**
 * WHM Analytics Hook for WHMCS
 * 
 * Отправляет server-side события в WHM Analytics Collector.
 * Файл: /includes/hooks/whm_analytics.php
 * 
 * События:
 * - purchase (InvoicePaid) → ecommerce событие
 * - sign_up (ClientAdd) → регистрация
 * - login (ClientLogin) → вход
 */

use WHMCS\Database\Capsule;

/* ================= НАСТРОЙКИ ================= */
const WHM_COLLECTOR_URL = 'https://analytics.webhostmost.com/collect';
const WHM_SITE_ID = 4;  // staging.whmtest.com
const WHM_CLIENT_BASE_URL = 'https://staging.whmtest.com';

/* ================= ЛОГИ ================= */
define('WHM_LOG_DIR', dirname(__DIR__, 2) . '/custom_logs/whm-analytics');
define('WHM_LOG_FILE', WHM_LOG_DIR . '/whm-analytics.log');

function whm_analytics_log(string $msg): void {
    static $init = false;
    if (!$init) {
        if (!is_dir(WHM_LOG_DIR)) @mkdir(WHM_LOG_DIR, 0755, true);
        $init = true;
    }
    // Rotate if > 10MB
    if (@filesize(WHM_LOG_FILE) > 10 * 1024 * 1024) {
        @rename(WHM_LOG_FILE, WHM_LOG_FILE . '.' . date('Ymd_His'));
    }
    $line = '[' . date('c') . '] ' . $msg . PHP_EOL;
    @file_put_contents(WHM_LOG_FILE, $line, FILE_APPEND | LOCK_EX);
}

/* ================= HTTP ================= */
function whm_analytics_send(array $payload): bool {
    $json = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    
    $ch = curl_init(WHM_COLLECTOR_URL);
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HTTPHEADER => [
            'Content-Type: application/json',
            'X-Forwarded-For: ' . whm_analytics_client_ip(),
            'User-Agent: ' . ($_SERVER['HTTP_USER_AGENT'] ?? 'WHMCS-Hook/1.0'),
        ],
        CURLOPT_POSTFIELDS => $json,
        CURLOPT_TIMEOUT => 5,
        CURLOPT_CONNECTTIMEOUT => 3,
    ]);
    
    $response = curl_exec($ch);
    $httpCode = curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    $error = curl_error($ch);
    curl_close($ch);
    
    $ok = ($httpCode >= 200 && $httpCode < 300);
    
    whm_analytics_log(sprintf(
        'POST %s code=%d ok=%d payload=%s response=%s error=%s',
        WHM_COLLECTOR_URL,
        $httpCode,
        $ok ? 1 : 0,
        substr($json, 0, 500),
        substr($response ?: '', 0, 200),
        $error ?: '-'
    ));
    
    return $ok;
}

/* ================= UTILS ================= */
function whm_analytics_client_ip(): string {
    return $_SERVER['HTTP_CF_CONNECTING_IP'] 
        ?? explode(',', $_SERVER['HTTP_X_FORWARDED_FOR'] ?? '')[0] 
        ?? $_SERVER['REMOTE_ADDR'] 
        ?? '127.0.0.1';
}

function whm_analytics_visitor_id(): ?string {
    // 1. Check URL param (from cross-domain)
    if (!empty($_GET['_whm_vid']) && preg_match('/^[a-f0-9]{16,32}$/i', $_GET['_whm_vid'])) {
        return strtolower($_GET['_whm_vid']);
    }
    // 2. Check localStorage cookie fallback (set by JS)
    if (!empty($_COOKIE['_whm_vid']) && preg_match('/^[a-f0-9]{16,32}$/i', $_COOKIE['_whm_vid'])) {
        return strtolower($_COOKIE['_whm_vid']);
    }
    return null;
}

function whm_analytics_generate_vid(string $userId): string {
    return substr(hash('sha256', 'whmcs|' . $userId . '|' . time()), 0, 16);
}

function whm_analytics_currency_to_usd(float $amount, int $currencyId): float {
    try {
        $from = Capsule::table('tblcurrencies')->where('id', $currencyId)->first();
        $usd = Capsule::table('tblcurrencies')->where('code', 'USD')->first();
        if (!$from || !$usd) return round($amount, 2);
        $baseAmount = $from->rate > 0 ? ($amount / (float)$from->rate) : $amount;
        return round($baseAmount * (float)$usd->rate, 2);
    } catch (\Throwable $e) {
        return round($amount, 2);
    }
}

function whm_analytics_currency_code(int $currencyId): string {
    try {
        $row = Capsule::table('tblcurrencies')->where('id', $currencyId)->first();
        return $row ? (string)$row->code : 'USD';
    } catch (\Throwable $e) {
        return 'USD';
    }
}

/* ================= DEDUPLICATION ================= */
function whm_analytics_ensure_table(): void {
    try {
        $schema = Capsule::schema();
        if (!$schema->hasTable('mod_whm_analytics_sent')) {
            $schema->create('mod_whm_analytics_sent', function($t) {
                $t->increments('id');
                $t->string('event_type', 50);
                $t->string('event_id', 100)->unique();
                $t->timestamp('created_at')->useCurrent();
            });
            whm_analytics_log('Created table mod_whm_analytics_sent');
        }
    } catch (\Throwable $e) {
        whm_analytics_log('ensure_table error: ' . $e->getMessage());
    }
}

function whm_analytics_is_sent(string $type, string $id): bool {
    try {
        whm_analytics_ensure_table();
        $key = $type . ':' . $id;
        return Capsule::table('mod_whm_analytics_sent')->where('event_id', $key)->exists();
    } catch (\Throwable $e) {
        return false;
    }
}

function whm_analytics_mark_sent(string $type, string $id): void {
    try {
        whm_analytics_ensure_table();
        $key = $type . ':' . $id;
        Capsule::table('mod_whm_analytics_sent')->insertOrIgnore([
            'event_type' => $type,
            'event_id' => $key,
        ]);
    } catch (\Throwable $e) {
        whm_analytics_log('mark_sent error: ' . $e->getMessage());
    }
}

/* ================= HOOKS ================= */

/**
 * InvoicePaid → Ecommerce Purchase
 */
add_hook('InvoicePaid', 1, function(array $vars) {
    try {
        $invoiceId = (int)($vars['invoiceid'] ?? 0);
        if (!$invoiceId) return;
        
        // Dedupe
        if (whm_analytics_is_sent('purchase', (string)$invoiceId)) {
            whm_analytics_log("InvoicePaid: already sent invoice={$invoiceId}");
            return;
        }
        
        $invoice = Capsule::table('tblinvoices')->where('id', $invoiceId)->first();
        if (!$invoice || strtolower($invoice->status) !== 'paid') return;
        
        $userId = (int)$invoice->userid;
        $client = Capsule::table('tblclients')->where('id', $userId)->first();
        $currencyId = (int)($client->currency ?? 0);
        
        // Items
        $items = [];
        $lines = Capsule::table('tblinvoiceitems')->where('invoiceid', $invoiceId)->get();
        foreach ($lines as $line) {
            $items[] = [
                'sku' => (string)($line->relid ?: 'line-' . $line->id),
                'name' => (string)$line->description,
                'price' => whm_analytics_currency_to_usd((float)$line->amount, $currencyId),
                'quantity' => max(1, (int)$line->qty),
            ];
        }
        
        $total = (float)$invoice->total;
        $totalUsd = whm_analytics_currency_to_usd($total, $currencyId);
        
        // Get visitor_id (might have been passed)
        $visitorId = whm_analytics_visitor_id() ?? whm_analytics_generate_vid((string)$userId);
        
        $payload = [
            'site_id' => WHM_SITE_ID,
            'visitor_id' => $visitorId,
            'event' => [
                'type' => 'ecommerce',
                'action' => 'purchase',
                'transaction_id' => 'INV-' . $invoiceId,
                'revenue' => $totalUsd,
                'currency' => 'USD',
                'items' => $items,
            ],
            'page' => [
                'url' => WHM_CLIENT_BASE_URL . '/viewinvoice.php?id=' . $invoiceId,
            ],
            'user_id' => 'client_' . $userId,
            'email' => $client->email ?? null,
            'dimensions' => [
                'dimension1' => whm_analytics_currency_code($currencyId),  // orig_currency
                'dimension2' => (string)$total,  // orig_value
            ],
        ];
        
        if (whm_analytics_send($payload)) {
            whm_analytics_mark_sent('purchase', (string)$invoiceId);
            whm_analytics_log("InvoicePaid OK: invoice={$invoiceId} user={$userId} total={$totalUsd} USD");
        }
        
    } catch (\Throwable $e) {
        whm_analytics_log('InvoicePaid ERROR: ' . $e->getMessage());
    }
});

/**
 * ClientAdd → Sign Up
 */
add_hook('ClientAdd', 1, function(array $vars) {
    try {
        $userId = (int)($vars['userid'] ?? 0);
        if (!$userId) return;
        
        // Dedupe
        if (whm_analytics_is_sent('signup', (string)$userId)) {
            whm_analytics_log("ClientAdd: already sent user={$userId}");
            return;
        }
        
        $client = Capsule::table('tblclients')->where('id', $userId)->first();
        $visitorId = whm_analytics_visitor_id() ?? whm_analytics_generate_vid((string)$userId);
        
        $payload = [
            'site_id' => WHM_SITE_ID,
            'visitor_id' => $visitorId,
            'event' => [
                'type' => 'custom',
                'category' => 'auth',
                'action' => 'sign_up',
                'label' => 'whmcs_registration',
            ],
            'page' => [
                'url' => WHM_CLIENT_BASE_URL . '/register.php',
            ],
            'user_id' => 'client_' . $userId,
            'email' => $client->email ?? null,
        ];
        
        if (whm_analytics_send($payload)) {
            whm_analytics_mark_sent('signup', (string)$userId);
            whm_analytics_log("ClientAdd OK: user={$userId}");
        }
        
    } catch (\Throwable $e) {
        whm_analytics_log('ClientAdd ERROR: ' . $e->getMessage());
    }
});

/**
 * ClientLogin → Login event
 */
add_hook('ClientLogin', 1, function(array $vars) {
    try {
        $userId = (int)($vars['userid'] ?? 0);
        if (!$userId) return;
        
        // Don't dedupe logins - track each one
        $client = Capsule::table('tblclients')->where('id', $userId)->first();
        $visitorId = whm_analytics_visitor_id() ?? whm_analytics_generate_vid((string)$userId);
        
        $payload = [
            'site_id' => WHM_SITE_ID,
            'visitor_id' => $visitorId,
            'event' => [
                'type' => 'custom',
                'category' => 'auth',
                'action' => 'login',
                'label' => 'whmcs_login',
            ],
            'page' => [
                'url' => WHM_CLIENT_BASE_URL . '/clientarea.php',
            ],
            'user_id' => 'client_' . $userId,
            'email' => $client->email ?? null,
        ];
        
        whm_analytics_send($payload);
        whm_analytics_log("ClientLogin OK: user={$userId}");
        
    } catch (\Throwable $e) {
        whm_analytics_log('ClientLogin ERROR: ' . $e->getMessage());
    }
});

/**
 * AfterShoppingCartCheckout → начало заказа (trial или pending)
 */
add_hook('AfterShoppingCartCheckout', 1, function(array $vars) {
    try {
        $orderId = (int)($vars['OrderID'] ?? $vars['orderId'] ?? 0);
        if (!$orderId) return;
        
        // Dedupe
        if (whm_analytics_is_sent('checkout', (string)$orderId)) {
            whm_analytics_log("Checkout: already sent order={$orderId}");
            return;
        }
        
        $order = Capsule::table('tblorders')->where('id', $orderId)->first();
        $userId = (int)($order->userid ?? $_SESSION['uid'] ?? 0);
        $invoiceId = (int)($order->invoiceid ?? 0);
        
        // Check if invoice is already paid (will be handled by InvoicePaid)
        if ($invoiceId) {
            $invoice = Capsule::table('tblinvoices')->where('id', $invoiceId)->first();
            if ($invoice && strtolower($invoice->status) === 'paid' && (float)$invoice->total > 0) {
                whm_analytics_log("Checkout: invoice {$invoiceId} paid with amount, skipping (InvoicePaid will handle)");
                return;
            }
        }
        
        $visitorId = whm_analytics_visitor_id() ?? whm_analytics_generate_vid((string)$userId ?: (string)$orderId);
        $client = $userId ? Capsule::table('tblclients')->where('id', $userId)->first() : null;
        
        // Check for trial (zero invoice)
        $isTrial = !$invoiceId || (
            $invoiceId && 
            ($invoice = Capsule::table('tblinvoices')->where('id', $invoiceId)->first()) &&
            (float)$invoice->total == 0
        );
        
        $payload = [
            'site_id' => WHM_SITE_ID,
            'visitor_id' => $visitorId,
            'event' => [
                'type' => 'custom',
                'category' => 'ecommerce',
                'action' => $isTrial ? 'trial_started' : 'begin_checkout',
                'label' => 'order_' . $orderId,
            ],
            'page' => [
                'url' => WHM_CLIENT_BASE_URL . '/cart.php?a=complete',
            ],
            'user_id' => $userId ? 'client_' . $userId : null,
            'email' => $client->email ?? null,
        ];
        
        if (whm_analytics_send($payload)) {
            whm_analytics_mark_sent('checkout', (string)$orderId);
            whm_analytics_log("Checkout OK: order={$orderId} user={$userId} trial=" . ($isTrial ? 'yes' : 'no'));
        }
        
    } catch (\Throwable $e) {
        whm_analytics_log('Checkout ERROR: ' . $e->getMessage());
    }
});

whm_analytics_log('WHM Analytics hook loaded');
