/* AI Provocateurs — Minimal JS */

// Auto-scroll event list when new events arrive
document.addEventListener('htmx:sseMessage', function() {
    const eventList = document.getElementById('event-list');
    if (eventList) {
        eventList.scrollTop = eventList.scrollHeight;
    }
});

// Auto-resize report iframe to fit its content
window.addEventListener('load', function() {
    const iframe = document.getElementById('report-frame');
    if (iframe) {
        iframe.addEventListener('load', function() {
            try {
                const body = iframe.contentDocument.body;
                iframe.style.height = body.scrollHeight + 40 + 'px';
            } catch(e) {
                iframe.style.height = '800px';
            }
        });
    }
});
