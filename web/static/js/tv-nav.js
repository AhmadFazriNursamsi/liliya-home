// =========================================================
// Liliya — Navigasi remote TV (tombol panah + OK)
// Hanya aktif di aplikasi Liliya untuk Android TV (user agent "LiliyaTV"); HP & browser tidak berubah.
// =========================================================

(() => {
    if (!/LiliyaTV/.test(navigator.userAgent)) return;
    document.documentElement.classList.add('tv-mode');

    const FOCUSABLE = 'a[href], button:not([disabled]), input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    const DIRS = { ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right' };

    const style = document.createElement('style');
    style.textContent = `
        .tv-mode :focus { outline: none; }
        .tv-mode :focus-visible, .tv-mode .tv-focus {
            outline: 4px solid var(--peach-strong, #F7996A) !important;
            outline-offset: 3px;
            box-shadow: 0 0 0 8px rgba(247, 153, 106, 0.25) !important;
            border-radius: 14px;
        }
        .tv-mode button.tv-focus, .tv-mode [tabindex].tv-focus { transform: scale(1.04); transition: transform .12s ease; }
    `;
    document.head.appendChild(style);

    function visible(el) {
        if (el.closest('[hidden], [inert]')) return false;
        const r = el.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) return false;
        const cs = getComputedStyle(el);
        return cs.visibility !== 'hidden' && cs.display !== 'none' && cs.pointerEvents !== 'none';
    }

    /** Dialog yang sedang terbuka mengurung fokus; selain itu seluruh halaman. */
    function scope() {
        const open = [...document.querySelectorAll('.modal, [role="dialog"]')].find(m => !m.hidden && visible(m));
        return open || document.body;
    }

    function candidates() {
        return [...scope().querySelectorAll(FOCUSABLE)].filter(visible);
    }

    function center(r) {
        return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    }

    /** Elemen terdekat ke arah tombol: jarak searah + hukuman untuk pergeseran menyamping. */
    function nearest(from, dir, list) {
        const a = from.getBoundingClientRect();
        const ca = center(a);
        let best = null, bestScore = Infinity;
        for (const el of list) {
            if (el === from || el.contains(from) || from.contains(el)) continue;
            const b = el.getBoundingClientRect();
            const cb = center(b);
            let main, side;
            if (dir === 'down') { if (b.top < a.bottom - 4) continue; main = b.top - a.bottom; side = Math.abs(cb.x - ca.x); }
            else if (dir === 'up') { if (b.bottom > a.top + 4) continue; main = a.top - b.bottom; side = Math.abs(cb.x - ca.x); }
            else if (dir === 'right') { if (b.left < a.right - 4) continue; main = b.left - a.right; side = Math.abs(cb.y - ca.y); }
            else { if (b.right > a.left + 4) continue; main = a.left - b.right; side = Math.abs(cb.y - ca.y); }
            const score = Math.max(main, 0) + side * 2;
            if (score < bestScore) { bestScore = score; best = el; }
        }
        return best;
    }

    function focus(el) {
        document.querySelectorAll('.tv-focus').forEach(x => x.classList.remove('tv-focus'));
        el.classList.add('tv-focus');
        el.focus({ preventScroll: true });
        el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
    }

    /** Fokus awal: tombol pertama di tab yang aktif, bukan bilah navigasi bawah. */
    function first(list) {
        return list.find(el => !el.closest('.bottom-nav')) || list[0];
    }

    document.addEventListener('keydown', (e) => {
        const dir = DIRS[e.key];
        if (!dir) return;
        const cur = document.activeElement;
        const typing = cur && (cur.tagName === 'TEXTAREA' || (cur.tagName === 'INPUT' && cur.type !== 'range'));
        // Kiri/kanan di kolom teks tetap menggeser kursor
        if (typing && (dir === 'left' || dir === 'right')) return;
        const list = candidates();
        if (!list.length) return;
        const from = cur && cur !== document.body && list.includes(cur) ? cur : null;
        const next = from ? nearest(from, dir, list) : first(list);
        if (next) {
            e.preventDefault();
            focus(next);
        } else if (dir === 'up' || dir === 'down') {
            // Tidak ada tombol lagi ke arah itu: biarkan halaman bergulir (mis. daftar panjang)
            return;
        } else {
            e.preventDefault();
        }
    }, true);

    // Elemen yang difokus lewat klik/skrip juga diberi sorotan
    document.addEventListener('focusin', (e) => {
        if (e.target instanceof Element && e.target.matches(FOCUSABLE)) {
            document.querySelectorAll('.tv-focus').forEach(x => x !== e.target && x.classList.remove('tv-focus'));
            e.target.classList.add('tv-focus');
        }
    });

    // Ganti tab atau tutup dialog: fokus pindah ke isi yang baru tampil agar remote tidak "hilang"
    let lastScope = null;
    let queued = false;
    new MutationObserver(() => {
        // Satu pemeriksaan per frame: TV ini hanya punya RAM 1 GB
        if (queued) return;
        queued = true;
        requestAnimationFrame(() => { queued = false; refocus(); });
    }).observe(document.body, { subtree: true, attributes: true, attributeFilter: ['hidden', 'class'] });

    function refocus() {
        const s = scope();
        const cur = document.activeElement;
        if (s !== lastScope || !cur || cur === document.body || !visible(cur)) {
            lastScope = s;
            const list = candidates();
            if (list.length && (!cur || cur === document.body || !list.includes(cur))) focus(first(list));
        }
    }

    window.addEventListener('load', () => {
        const list = candidates();
        if (list.length) focus(first(list));
    });
})();
