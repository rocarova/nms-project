// Shared helpers. Times come from the server as ISO 8601 (UTC) and are shown in the viewer's local time.
window.NMS = {
    relativeTime(date) {
        const seconds = Math.round((Date.now() - date.getTime()) / 1000);
        if (seconds < 45) return 'just now';
        const minutes = Math.round(seconds / 60);
        if (minutes < 60) return `${minutes}m ago`;
        const hours = Math.round(minutes / 60);
        if (hours < 24) return `${hours}h ago`;
        const days = Math.round(hours / 24);
        if (days < 30) return `${days}d ago`;
        return date.toLocaleDateString();
    },

    formatDateTime(date) {
        return date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
    },

    // Fills every <time data-time="ISO" [data-format="relative"]> under root
    localizeTimes(root = document) {
        root.querySelectorAll('[data-time]').forEach(el => {
            const date = new Date(el.dataset.time);
            if (isNaN(date)) return;
            el.textContent = el.dataset.format === 'relative' ? NMS.relativeTime(date) : NMS.formatDateTime(date);
            el.title = date.toLocaleString();
        });
    },
};

document.addEventListener('DOMContentLoaded', () => NMS.localizeTimes());
