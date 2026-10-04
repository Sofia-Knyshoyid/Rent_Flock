// RentFlock - minimal frontend helpers

document.addEventListener('DOMContentLoaded', () => {
    // Auto-dismiss flash messages after 4s
    const flashes = document.querySelectorAll('.flash');
    flashes.forEach(el => {
        setTimeout(() => {
            el.style.transition = 'opacity 0.4s';
            el.style.opacity = '0';
            setTimeout(() => el.remove(), 400);
        }, 4000);
    });

    // Highlight checked checklist items
    document.querySelectorAll('.check-item input[type="checkbox"]').forEach(cb => {
        cb.addEventListener('change', () => {
            if (cb.checked) {
                cb.parentElement.style.background = 'rgba(124, 58, 237, 0.25)';
            } else {
                cb.parentElement.style.background = '';
            }
        });
        // Init state
        if (cb.checked) {
            cb.parentElement.style.background = 'rgba(124, 58, 237, 0.25)';
        }
    });
});