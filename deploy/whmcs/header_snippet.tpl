{* WHM Analytics Tracker - Add this at the end of header.tpl, before </head> *}

{* WHM Analytics Tracker *}
<script src="https://analytics.webhostmost.com/whm.js"></script>
<script>
whm('init', {
    siteId: 4,
    collectorUrl: 'https://analytics.webhostmost.com/collect',
    linkDomains: ['analytics.ignat.best'],  // For testing cross-domain back to front
    debug: true  // Remove in production
});

{* Set user ID if logged in *}
{if $loggedin}
whm('set', 'userId', 'client_{$clientsdetails.id}');
{if $clientsdetails.email}
whm('set', 'email', '{$clientsdetails.email|escape:'javascript'}');
{/if}
{/if}
</script>
