// =========================================================
// BUDI — Tab Navigasi, Saklar Smart Home Manual & CCTV
// =========================================================

const BudiHome = (() => {
    const appContainer = document.querySelector('.app-container');
    const homeGrid = document.getElementById('home-rooms');
    const homeSummary = document.getElementById('home-summary');
    const cctvRoot = document.getElementById('panel-cctv');

    const SWITCHABLE = new Set(['light', 'fan', 'ac', 'switch', 'plug', 'socket']);
    const DAYS_SHORT = ["Min", "Sen", "Sel", "Rab", "Kam", "Jum", "Sab"];
    const MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"];

    const devices = new Map();
    const pending = new Map();

    function esc(s) {
        return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    async function api(path, opts = {}) {
        const res = await fetch(path, { credentials: 'same-origin', ...opts });
        if (res.status === 401) {
            location.href = '/login?next=' + encodeURIComponent(location.pathname + location.hash);
            throw new Error('Unauthorized');
        }
        if (!res.ok) {
            let msg = res.status >= 500 ? `Server tidak merespons dengan benar (kode ${res.status}). Coba lagi sebentar.` : `Error ${res.status}`;
            try { const j = await res.json(); if (j.error) msg = j.error; } catch (e) {}
            throw new Error(msg);
        }
        return res.json();
    }

    // ---------- Tabs ----------
    function setTab(tab) {
        if (!['assistant', 'home', 'cctv', 'router', 'docs'].includes(tab)) tab = 'assistant';
        if (appContainer.dataset.tab !== tab) window.scrollTo(0, 0);
        appContainer.dataset.tab = tab;
        document.querySelectorAll('.tab-btn[data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
        try { localStorage.setItem('budi_tab', tab); } catch (e) {}
        if (tab === 'home') loadDevices();
        if (tab === 'cctv') Cctv.show(); else Cctv.hide();
        if (tab === 'router') Router.show();
        if (tab === 'docs') Docs.show(); else Docs.hide();
    }

    document.querySelectorAll('.tab-btn[data-tab]').forEach(b => b.addEventListener('click', () => setTab(b.dataset.tab)));

    // ---------- Smart home ----------
    function isOn(d) { return String(d.state).toLowerCase() === 'on'; }

    function describe(d) {
        switch (d.type) {
            case 'light': return isOn(d) ? `Menyala · ${d.brightness ?? 100}%` : 'Mati';
            case 'ac': return isOn(d) ? `Menyala · ${d.temperature ?? 24}°C` : 'Mati';
            case 'fan': return isOn(d) ? `Menyala · Kecepatan ${d.speed ?? 1}` : 'Mati';
            case 'sensor_climate': return `${d.temperature ?? '--'}°C · ${d.humidity ?? '--'}%`;
            case 'sensor_contact': return d.is_open ? 'Terbuka' : 'Tertutup';
            case 'sensor_motion': return d.motion_detected ? 'Ada gerakan!' : 'Tidak ada gerakan';
            case 'sensor_gas': return `${d.status ?? 'Aman'} · ${d.ppm ?? 0} ppm`;
            default: return d.state ? (isOn(d) ? 'Menyala' : 'Mati') : '';
        }
    }

    function isAlert(d) {
        return (d.type === 'sensor_motion' && d.motion_detected)
            || (d.type === 'sensor_contact' && d.is_open)
            || (d.type === 'sensor_gas' && (d.ppm ?? 0) > 100);
    }

    function cardHtml(d) {
        const switchable = SWITCHABLE.has(d.type) || (d.state !== undefined && !String(d.type).startsWith('sensor'));
        const on = switchable && isOn(d);
        const cls = ['dev-card', switchable ? 'switchable' : '', on ? 'is-on' : '', isAlert(d) ? 'alert' : '', pending.has(d.id) ? 'pending' : '', d.online === false ? 'offline' : ''].join(' ');
        let extra = '';
        if (d.type === 'light' && on) {
            extra = `<div class="dev-extra"><i class="fa-solid fa-sun"></i><input type="range" min="5" max="100" step="5" value="${Number(d.brightness ?? 100)}" data-act="brightness" aria-label="Kecerahan"></div>`;
        } else if (d.type === 'ac' && on) {
            extra = `<div class="dev-extra"><button class="step-btn" data-act="temp" data-delta="-1">−</button><span class="step-val">${Number(d.temperature ?? 24)}°C</span><button class="step-btn" data-act="temp" data-delta="1">+</button></div>`;
        } else if (d.type === 'fan' && on) {
            extra = `<div class="dev-extra"><button class="step-btn" data-act="speed" data-delta="-1">−</button><span class="step-val">${Number(d.speed ?? 1)}</span><button class="step-btn" data-act="speed" data-delta="1">+</button></div>`;
        }
        return `<div class="${cls}" data-id="${esc(d.id)}" ${switchable ? 'role="switch" aria-checked="' + on + '" tabindex="0"' : ''}>
            <div class="dev-top">
                <div class="dev-icon"><i class="fa-solid ${esc(d.icon || 'fa-plug')}"></i></div>
                ${switchable ? '<span class="switch"></span>' : ''}
            </div>
            <div class="dev-bottom">
                <div><div class="dev-room">${esc(d.room || '')}</div><div class="dev-name">${esc(d.name || d.id)}</div><div class="dev-state">${esc(describe(d))}${d.online === false ? ' <span class="dev-badge">Offline</span>' : ''}</div></div>
                <button class="dev-edit" data-act="edit" aria-label="Edit ${esc(d.name || d.id)}" title="Edit perangkat"><i class="fa-solid fa-pen"></i></button>
            </div>
            ${extra}
        </div>`;
    }

    function renderDevices() {
        if (!homeGrid) return;
        const list = [...devices.values()];
        if (!list.length) {
            homeGrid.innerHTML = '<div class="empty-note">Belum ada perangkat. Tekan <strong>+ Tambah</strong> untuk menambahkan lampu dari Smart Life.</div>';
            if (homeSummary) homeSummary.textContent = 'Belum ada perangkat';
            return;
        }
        // Satu grid untuk semua perangkat (diurutkan per ruangan); nama ruangan tampil di kartu
        const sorted = [...list].sort((a, b) => (a.room || '').localeCompare(b.room || '') || (a.name || '').localeCompare(b.name || ''));
        homeGrid.innerHTML = `<div class="device-grid">${sorted.map(cardHtml).join('')}</div>`;

        const lights = list.filter(d => d.type === 'light');
        const onCount = lights.filter(isOn).length;
        if (homeSummary) homeSummary.textContent = `${onCount} dari ${lights.length} lampu menyala`;
    }

    async function setDevice(id, updates) {
        const d = devices.get(id);
        if (!d) return;
        const before = { ...d };
        Object.assign(d, updates);
        pending.set(id, Date.now());
        renderDevices();
        try {
            const res = await api(`/api/device/${encodeURIComponent(id)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updates)
            });
            if (res.device) devices.set(id, res.device);
        } catch (e) {
            devices.set(id, before);
            toast(`Gagal mengubah ${before.name || id}`);
        } finally {
            pending.delete(id);
            renderDevices();
        }
    }

    async function setAllLights(on) {
        const targets = [...devices.values()].filter(d => d.type === 'light' && isOn(d) !== on);
        await Promise.all(targets.map(d => setDevice(d.id, { state: on ? 'on' : 'off' })));
    }

    if (homeGrid) {
        homeGrid.addEventListener('click', (e) => {
            const editBtn = e.target.closest('[data-act="edit"]');
            if (editBtn) {
                const d = devices.get(editBtn.closest('.dev-card').dataset.id);
                if (d) DeviceEditor.openEdit(d);
                return;
            }
            const card = e.target.closest('.dev-card.switchable');
            if (!card) return;
            const id = card.dataset.id;
            const d = devices.get(id);
            if (!d || pending.has(id)) return;
            const btn = e.target.closest('[data-act]');
            if (btn && btn.dataset.act === 'temp') {
                const t = Math.max(16, Math.min(30, Number(d.temperature ?? 24) + Number(btn.dataset.delta)));
                return setDevice(id, { temperature: t });
            }
            if (btn && btn.dataset.act === 'speed') {
                const s = Math.max(1, Math.min(3, Number(d.speed ?? 1) + Number(btn.dataset.delta)));
                return setDevice(id, { speed: s });
            }
            if (e.target.closest('.dev-extra')) return;
            if (navigator.vibrate) navigator.vibrate(15);
            setDevice(id, { state: isOn(d) ? 'off' : 'on' });
        });
        homeGrid.addEventListener('change', (e) => {
            if (e.target.dataset.act !== 'brightness') return;
            const id = e.target.closest('.dev-card').dataset.id;
            setDevice(id, { state: 'on', brightness: Number(e.target.value) });
        });
        homeGrid.addEventListener('keydown', (e) => {
            if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('switchable')) {
                e.preventDefault();
                e.target.click();
            }
        });
    }

    document.getElementById('btn-all-on')?.addEventListener('click', () => setAllLights(true));
    document.getElementById('btn-all-off')?.addEventListener('click', () => setAllLights(false));

    function replaceDevices(list) {
        const next = new Map();
        list.forEach(d => next.set(d.id, pending.has(d.id) && devices.has(d.id) ? devices.get(d.id) : d));
        devices.clear();
        next.forEach((d, id) => devices.set(id, d));
        renderDevices();
    }

    async function loadDevices() {
        try {
            replaceDevices(await api('/api/devices'));
        } catch (e) {
            if (homeGrid && !devices.size) homeGrid.innerHTML = '<div class="empty-note">Gagal memuat perangkat. Periksa koneksi ke BUDI.</div>';
        }
    }

    // Event realtime dari WebSocket BUDI (dipanggil dari handleServerEvent)
    function onEvent(data) {
        if (data.type === 'smart_home_sync' && Array.isArray(data.devices)) {
            replaceDevices(data.devices);
        } else if (data.type === 'smart_home_device_update' && data.device) {
            if (!pending.has(data.device.id)) {
                devices.set(data.device.id, data.device);
                renderDevices();
            }
        }
    }

    // ---------- Tambah / Edit / Hapus perangkat ----------
    const DeviceEditor = (() => {
        const modal = document.getElementById('device-modal');
        const card = modal.querySelector('.modal-card');
        const TYPE_LABELS = { light: 'Lampu', switch: 'Saklar / Stopkontak', fan: 'Kipas', ac: 'AC' };
        const TYPE_ICONS = { light: 'fa-lightbulb', switch: 'fa-plug', fan: 'fa-fan', ac: 'fa-snowflake' };
        let lastFocus = null;

        function open(html) {
            if (modal.hidden) lastFocus = document.activeElement;
            card.innerHTML = html;
            modal.hidden = false;
            document.body.classList.add('modal-open');
            (card.querySelector('[autofocus]') || card.querySelector('button, input, select'))?.focus();
        }

        function close() {
            modal.hidden = true;
            card.innerHTML = '';
            document.body.classList.remove('modal-open');
            lastFocus?.focus?.();
        }

        modal.addEventListener('click', (e) => {
            if (e.target === modal || e.target.closest('[data-close]')) close();
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !modal.hidden) close();
        });

        function rooms() {
            return [...new Set([...devices.values()].map(d => d.room).filter(Boolean))].sort();
        }

        // Langkah 1: pilih perangkat dari akun Smart Life
        async function openPicker(onPick, { allowManual = true, currentTuyaId = '' } = {}) {
            open(`<h3>Pilih Perangkat Smart Life</h3><div class="modal-loading"><i class="fa-solid fa-spinner fa-spin"></i> Mengambil daftar perangkat dari Smart Life...</div>`);
            let data;
            try {
                data = await api('/api/tuya/devices');
            } catch (e) {
                data = { enabled: true, error: e.message, devices: [] };
            }
            if (modal.hidden) return;
            const list = data.devices || [];
            let body;
            if (!data.enabled) {
                body = '<div class="empty-note">Integrasi Tuya / Smart Life belum aktif di config.yaml.</div>';
            } else if (data.error) {
                body = `<div class="form-error">Gagal mengambil perangkat: ${esc(data.error)}</div>`;
            } else if (!list.length) {
                body = '<div class="empty-note">Belum ada perangkat di akun Smart Life.</div>';
            } else {
                body = `<div class="tuya-list">${list.map(t => {
                    const used = t.used_by && t.tuya_id !== currentTuyaId;
                    return `<button type="button" class="tuya-item" data-tuya="${esc(t.tuya_id)}" ${used ? 'disabled' : ''}>
                        <span class="tuya-icon"><i class="fa-solid ${TYPE_ICONS[t.suggested_type] || 'fa-plug'}"></i></span>
                        <span class="tuya-info"><strong>${esc(t.name)}</strong>
                            <small>${t.online ? '<span class="dot-online"></span>Online' : '<span class="dot-offline"></span>Offline'}${t.via_gateway ? ' · lewat gateway' : ''}${used ? ` · dipakai "${esc(t.used_by)}"` : ''}</small></span>
                        ${used ? '' : '<i class="fa-solid fa-chevron-right"></i>'}
                    </button>`;
                }).join('')}</div>`;
            }
            open(`<h3>Pilih Perangkat Smart Life</h3>
                <p class="modal-sub">Perangkat harus sudah di-pairing di aplikasi Smart Life. Lampu Bluetooth perlu terhubung ke gateway agar bisa dikontrol dari internet.</p>
                ${body}
                <div class="modal-actions">
                    ${allowManual ? '<button type="button" class="pill-btn" data-act="manual"><i class="fa-solid fa-pen-ruler"></i> Tanpa Smart Life</button>' : ''}
                    <button type="button" class="pill-btn" data-act="reload"><i class="fa-solid fa-rotate"></i> Muat ulang</button>
                    <button type="button" class="pill-btn" data-close>Batal</button>
                </div>`);
            card.querySelector('[data-act="reload"]').addEventListener('click', () => openPicker(onPick, { allowManual, currentTuyaId }));
            card.querySelector('[data-act="manual"]')?.addEventListener('click', () => onPick(null));
            card.querySelectorAll('.tuya-item:not([disabled])').forEach(b => b.addEventListener('click', () => {
                onPick(list.find(t => t.tuya_id === b.dataset.tuya));
            }));
        }

        // Langkah 2: form nama, ruangan, jenis & nama panggilan suara
        function openForm(mode, draft) {
            const title = mode === 'add' ? 'Tambah Perangkat' : 'Edit Perangkat';
            const typeOptions = Object.entries(TYPE_LABELS).map(([v, l]) =>
                `<option value="${v}" ${draft.type === v ? 'selected' : ''}>${l}</option>`).join('');
            const aliases = (draft.aliases || []).filter(a => a !== String(draft.name || '').toLowerCase()).join(', ');
            open(`<h3>${title}</h3>
                <form class="device-form" novalidate>
                    <label>Nama perangkat
                        <input name="name" maxlength="40" required autofocus value="${esc(draft.name || '')}" placeholder="mis. Lampu Dapur">
                    </label>
                    <label>Ruangan
                        <input name="room" maxlength="30" required list="room-options" value="${esc(draft.room || '')}" placeholder="mis. Dapur">
                        <datalist id="room-options">${rooms().map(r => `<option value="${esc(r)}">`).join('')}</datalist>
                    </label>
                    <label>Jenis
                        <select name="type">${typeOptions}</select>
                    </label>
                    <label>Nama panggilan untuk perintah suara <small>(opsional, pisahkan dengan koma)</small>
                        <input name="aliases" maxlength="200" value="${esc(aliases)}" placeholder="mis. lampu masak, lampu belakang">
                    </label>
                    <div class="tuya-link">
                        <i class="fa-solid fa-link"></i>
                        <span>${draft.tuya_id ? `Smart Life: <strong>${esc(draft.tuya_name || draft.tuya_id)}</strong>` : 'Tidak terhubung ke Smart Life (perangkat virtual)'}</span>
                        <button type="button" class="pill-btn" data-act="relink">${draft.tuya_id ? 'Ganti' : 'Hubungkan'}</button>
                        ${draft.tuya_id ? '<button type="button" class="pill-btn" data-act="unlink">Lepas</button>' : ''}
                    </div>
                    <div class="form-error" hidden></div>
                    <div class="modal-actions">
                        ${mode === 'edit' ? '<button type="button" class="pill-btn danger" data-act="delete"><i class="fa-solid fa-trash"></i> Hapus</button><span class="spacer"></span>' : ''}
                        <button type="button" class="pill-btn" data-close>Batal</button>
                        <button type="submit" class="pill-btn active"><i class="fa-solid fa-check"></i> Simpan</button>
                    </div>
                    <div class="delete-confirm" hidden>
                        <p>Hapus <strong>${esc(draft.name || '')}</strong> dari BUDI? Perangkat tetap ada di Smart Life dan bisa ditambahkan lagi kapan saja.</p>
                        <div class="modal-actions">
                            <button type="button" class="pill-btn" data-act="cancel-delete">Batal</button>
                            <button type="button" class="pill-btn danger" data-act="confirm-delete"><i class="fa-solid fa-trash"></i> Ya, hapus</button>
                        </div>
                    </div>
                </form>`);

            const form = card.querySelector('form');
            const errorBox = form.querySelector('.form-error');
            const field = (n) => form.elements[n].value;
            const readForm = () => ({
                ...draft,
                name: field('name').trim(),
                room: field('room').trim(),
                type: field('type'),
                aliases: field('aliases').split(',').map(a => a.trim()).filter(Boolean),
            });
            const showError = (msg) => { errorBox.textContent = msg; errorBox.hidden = false; };

            form.querySelector('[data-act="relink"]').addEventListener('click', () => {
                const current = readForm();
                openPicker((t) => openForm(mode, t
                    ? { ...current, tuya_id: t.tuya_id, tuya_name: t.name }
                    : { ...current, tuya_id: '', tuya_name: '' }), { currentTuyaId: draft.tuya_id });
            });
            form.querySelector('[data-act="unlink"]')?.addEventListener('click', () => openForm(mode, { ...readForm(), tuya_id: '', tuya_name: '' }));

            const confirmBox = form.querySelector('.delete-confirm');
            form.querySelector('[data-act="delete"]')?.addEventListener('click', () => { confirmBox.hidden = false; });
            form.querySelector('[data-act="cancel-delete"]')?.addEventListener('click', () => { confirmBox.hidden = true; });
            form.querySelector('[data-act="confirm-delete"]')?.addEventListener('click', async (e) => {
                e.currentTarget.disabled = true;
                try {
                    await api(`/api/devices/${encodeURIComponent(draft.id)}`, { method: 'DELETE' });
                    devices.delete(draft.id);
                    renderDevices();
                    close();
                    toast(`${draft.name} dihapus`);
                } catch (err) {
                    e.currentTarget.disabled = false;
                    confirmBox.hidden = true;
                    showError(err.message);
                }
            });

            form.addEventListener('submit', async (e) => {
                e.preventDefault();
                const data = readForm();
                if (!data.name) return showError('Nama perangkat wajib diisi.');
                if (!data.room) return showError('Ruangan wajib diisi.');
                const submit = form.querySelector('[type="submit"]');
                submit.disabled = true;
                const payload = { name: data.name, room: data.room, type: data.type, aliases: data.aliases, tuya_id: data.tuya_id || '' };
                try {
                    const res = mode === 'add'
                        ? await api('/api/devices', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
                        : await api(`/api/devices/${encodeURIComponent(draft.id)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
                    devices.set(res.device.id, res.device);
                    renderDevices();
                    close();
                    toast(mode === 'add' ? `${res.device.name} ditambahkan` : 'Perubahan disimpan');
                } catch (err) {
                    submit.disabled = false;
                    showError(err.message);
                }
            });
        }

        function openAdd() {
            openPicker((t) => openForm('add', t
                ? { type: t.suggested_type, tuya_id: t.tuya_id, tuya_name: t.name, room: '' }
                : { type: 'light', tuya_id: '', room: '' }));
        }

        async function openEdit(d) {
            const draft = { ...d, tuya_name: '' };
            openForm('edit', draft);
            if (d.tuya_id) {
                // Tampilkan nama perangkat Smart Life (bukan hanya ID) bila tersedia
                try {
                    const data = await api('/api/tuya/devices');
                    const t = (data.devices || []).find(x => x.tuya_id === d.tuya_id);
                    const nameEl = card.querySelector('.tuya-link strong');
                    if (t && nameEl && !modal.hidden) {
                        draft.tuya_name = t.name;
                        nameEl.textContent = t.name;
                    }
                } catch (e) {}
            }
        }

        document.getElementById('btn-add-device')?.addEventListener('click', openAdd);
        return { openAdd, openEdit, open, close };
    })();


    // ---------- Toast ----------
    let toastTimer = null;
    function toast(msg) {
        let el = document.getElementById('budi-toast');
        if (!el) {
            el = document.createElement('div');
            el.id = 'budi-toast';
            el.className = 'toast';
            el.setAttribute('role', 'status');
            document.body.appendChild(el);
        }
        el.textContent = msg;
        el.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { el.hidden = true; }, 3000);
    }

    // ---------- CCTV ----------
    const Cctv = (() => {
        let cameras = [];
        let cam = null;
        let quality = 'sd';
        let soundOn = false;
        let mode = 'live';
        let segments = [];
        let currentFile = null;
        let playerLoaded = null;
        let liveEl = null;
        let motionOnly = false;
        let motionEnabled = false;

        const stage = document.getElementById('cctv-stage');
        const badge = document.getElementById('cctv-badge');
        const camRow = document.getElementById('cctv-cameras');
        const dateRow = document.getElementById('cctv-dates');
        const hoursBox = document.getElementById('cctv-segments');
        const meta = document.getElementById('cctv-playback-meta');
        const ptzPad = document.getElementById('ptz-pad');
        const btnLive = document.getElementById('btn-cctv-live');
        const btnQuality = document.getElementById('btn-cctv-quality');
        const btnSound = document.getElementById('btn-cctv-sound');
        const motionTools = document.getElementById('cctv-motion-tools');
        const motionSummary = document.getElementById('cctv-motion-summary');
        const btnMotion = document.getElementById('btn-cctv-motion');

        const pad2 = n => String(n).padStart(2, '0');
        const fmtClock = ts => { const d = new Date(ts * 1000); return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`; };
        const fmtOffset = s => `${pad2(Math.floor(s / 60))}:${pad2(Math.floor(s % 60))}`;
        const hasMotion = s => (s.motion || []).length > 0;

        // Live view dimulai tanpa suara (seperti aplikasi Tapo) agar tidak terjadi feedback/denging
        // saat HP berada di dekat kamera. Suara bisa dinyalakan lewat tombol.
        function applySound() {
            const v = liveEl && liveEl.video;
            if (v) v.muted = !soundOn;
            btnSound.innerHTML = soundOn ? '<i class="fa-solid fa-volume-high"></i> Suara' : '<i class="fa-solid fa-volume-xmark"></i> Bisu';
            btnSound.classList.toggle('active', soundOn);
        }
        const btnFull = document.getElementById('btn-cctv-full');

        function loadPlayer() {
            if (!playerLoaded) {
                playerLoaded = import('/cctv/live/video-stream.js').catch(err => {
                    playerLoaded = null;
                    throw err;
                });
            }
            return playerLoaded;
        }

        function setBadge(text, playback) {
            badge.innerHTML = `<span class="dot"></span>${esc(text)}`;
            badge.classList.toggle('playback', !!playback);
        }

        function clearStage() {
            if (liveEl) { liveEl.remove(); liveEl = null; }
            stage.querySelectorAll('video.playback').forEach(v => { v.pause(); v.removeAttribute('src'); v.load(); v.remove(); });
            stage.querySelector('.empty-note')?.remove();
        }

        async function startLive() {
            if (!cam) return;
            mode = 'live';
            currentFile = null;
            clearStage();
            setBadge(quality === 'hd' ? 'LIVE · HD' : 'LIVE', false);
            ptzPad.hidden = !cam.ptz;
            btnLive.classList.add('active');
            btnQuality.hidden = false;
            btnSound.hidden = false;
            btnQuality.innerHTML = quality === 'hd' ? '<i class="fa-solid fa-film"></i> HD' : '<i class="fa-solid fa-gauge-simple"></i> Hemat';
            renderSegments();
            try {
                await loadPlayer();
            } catch (e) {
                stage.insertAdjacentHTML('beforeend', '<div class="empty-note">Gagal memuat player live. Pastikan service go2rtc berjalan.</div>');
                return;
            }
            liveEl = document.createElement('video-stream');
            liveEl.mode = 'mse';
            liveEl.background = false;
            stage.prepend(liveEl);
            applySound();
            liveEl.src = `/cctv/live/api/ws?src=${encodeURIComponent(quality === 'hd' ? cam.hd_stream : cam.live_stream)}`;
        }

        function playSegment(file) {
            const seg = segments.find(s => s.file === file);
            if (!seg || seg.recording) return;
            mode = 'playback';
            currentFile = file;
            clearStage();
            ptzPad.hidden = true;
            btnLive.classList.remove('active');
            btnQuality.hidden = true;
            btnSound.hidden = true;
            setBadge(`REKAMAN · ${seg.date} ${seg.start.slice(0, 5)}`, true);
            const v = document.createElement('video');
            v.className = 'playback';
            v.controls = true;
            v.playsInline = true;
            v.autoplay = true;
            v.preload = 'metadata';
            v.src = `/cctv/rec/${encodeURIComponent(cam.id)}/${encodeURIComponent(file)}`;
            const playNext = () => {
                const idx = segments.findIndex(s => s.file === file);
                const next = segments.slice(idx + 1).find(s => !motionOnly || hasMotion(s));
                if (next && !next.recording) playSegment(next.file);
            };
            // Mode "Hanya gerakan": langsung lompat ke gerakan pertama di segmen ini
            if (motionOnly && hasMotion(seg)) {
                v.addEventListener('loadedmetadata', () => { v.currentTime = Math.max(0, seg.motion[0].t - 3); }, { once: true });
            }
            v.addEventListener('ended', playNext);
            v.addEventListener('error', () => {
                toast(`Rekaman ${seg.start.slice(0, 5)} tidak bisa diputar, lanjut ke berikutnya.`);
                playNext();
            });
            stage.prepend(v);
            renderSegments();
            const end = new Date(seg.end_ts * 1000);
            meta.innerHTML = `<div class="playback-row"><span>Memutar <strong>${esc(seg.start.slice(0, 5))} – ${String(end.getHours()).padStart(2, '0')}:${String(end.getMinutes()).padStart(2, '0')}</strong> · ${(seg.size / 1048576).toFixed(0)} MB</span>
                <a class="pill-btn" href="${v.src}?download=1" download><i class="fa-solid fa-download"></i> Unduh</a></div>
                ${renderMotionTrack(seg)}`;
            meta.hidden = false;

            const track = meta.querySelector('.motion-track');
            if (track) {
                const cursor = track.querySelector('.motion-cursor');
                const total = () => (isFinite(v.duration) && v.duration > 0 ? v.duration : Math.max(1, seg.end_ts - seg.start_ts));
                v.addEventListener('timeupdate', () => { cursor.style.left = `${Math.min(100, v.currentTime / total() * 100)}%`; });
                track.addEventListener('click', e => {
                    if (e.target.closest('[data-seek]')) return;
                    const r = track.getBoundingClientRect();
                    v.currentTime = (e.clientX - r.left) / r.width * total();
                });
            }
            meta.querySelectorAll('[data-seek]').forEach(b => b.addEventListener('click', () => {
                v.currentTime = Math.max(0, Number(b.dataset.seek) - 3);
                v.play().catch(() => {});
            }));
        }

        // Garis waktu segmen dengan tanda oranye di detik terjadinya gerakan
        function renderMotionTrack(seg) {
            if (!motionEnabled) return '';
            const events = seg.motion || [];
            if (!events.length) return '<div class="motion-none"><i class="fa-regular fa-circle-check"></i> Tidak ada gerakan di rekaman ini</div>';
            const total = Math.max(1, seg.end_ts - seg.start_ts);
            const marks = events.map(m => `<button class="motion-mark ${m.people ? 'people' : ''}" data-seek="${m.t}"
                style="left:${(m.t / total * 100).toFixed(2)}%;width:${(m.d / total * 100).toFixed(2)}%"
                title="${fmtClock(seg.start_ts + m.t)}" aria-label="Gerakan pukul ${fmtClock(seg.start_ts + m.t)}"></button>`).join('');
            const chips = events.map(m => `<button class="pill-btn motion-chip ${m.people ? 'people' : ''}" data-seek="${m.t}">
                <i class="fa-solid ${m.people ? 'fa-person' : 'fa-person-running'}"></i>
                <strong>${fmtClock(seg.start_ts + m.t)}</strong><span>menit ${fmtOffset(m.t)}${m.d > 2 ? ` · ${m.d} dtk` : ''}</span></button>`).join('');
            return `<div class="motion-track" title="Ketuk untuk lompat"><div class="motion-cursor"></div>${marks}</div>
                <div class="motion-head"><i class="fa-solid fa-person-running"></i> ${events.length} gerakan terdeteksi · ketuk untuk lompat</div>
                <div class="pill-row motion-list">${chips}</div>`;
        }

        // Notifikasi gerakan ke HP (FCM): sakelar global + tombol tes
        const pushEl = document.getElementById('cctv-push');
        async function loadPush() {
            let p;
            try { p = await api('/api/push/settings'); } catch (e) { pushEl.hidden = true; return; }
            if (!p.enabled) { pushEl.hidden = true; return; }
            pushEl.innerHTML = `<i class="fa-solid ${p.motion ? 'fa-bell' : 'fa-bell-slash'}"></i>
                <span>Notifikasi gerakan ke HP · <strong>${p.motion ? 'aktif' : 'mati'}</strong><small>${p.devices} HP terdaftar</small></span>
                <button class="pill-btn icon" data-push="test" title="Kirim notifikasi tes" aria-label="Kirim notifikasi tes"><i class="fa-solid fa-paper-plane"></i></button>
                <button class="switch ${p.motion ? 'on' : ''}" data-push="toggle" role="switch" aria-checked="${p.motion}" aria-label="Notifikasi gerakan"><span></span></button>`;
            pushEl.classList.toggle('off', !p.motion);
            pushEl.hidden = false;
        }
        pushEl.addEventListener('click', async (e) => {
            const b = e.target.closest('[data-push]');
            if (!b) return;
            b.disabled = true;
            try {
                if (b.dataset.push === 'toggle') {
                    const on = b.getAttribute('aria-checked') !== 'true';
                    await api('/api/push/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ motion: on }) });
                    toast(on ? 'Notifikasi gerakan dinyalakan' : 'Notifikasi gerakan dimatikan');
                    loadPush();
                } else {
                    const r = await api('/api/push/test', { method: 'POST' });
                    toast(r.sent ? `Notifikasi tes dikirim ke ${r.sent} HP` : 'Belum ada HP terdaftar. Buka aplikasi Liliya di HP dulu.');
                }
            } catch (err) { toast(err.message); }
            b.disabled = false;
        });

        const backupEl = document.getElementById('cctv-backup');
        const DRIVE_WARN_BYTES = 200 * 1024 ** 3;

        function fmtBytes(b) {
            if (b >= 1024 ** 4) return (b / 1024 ** 4).toFixed(2) + ' TB';
            if (b >= 1024 ** 3) return (b / 1024 ** 3).toFixed(1) + ' GB';
            return Math.round(b / 1024 ** 2) + ' MB';
        }

        async function loadBackup() {
            let b;
            try { b = await api('/api/cctv/backup'); } catch (e) { return; }
            if (!b.enabled) { backupEl.hidden = true; return; }
            const last = b.last_success ? new Date(b.last_success * 1000) : null;
            const lastText = last ? `${String(last.getHours()).padStart(2, '0')}:${String(last.getMinutes()).padStart(2, '0')}` : 'belum pernah';
            const stale = !last || Date.now() / 1000 - b.last_success > 3 * 3600;
            const lowSpace = b.drive_free != null && b.drive_free < DRIVE_WARN_BYTES;
            let html = `<i class="fa-brands fa-google-drive"></i> Cadangan 720p ke Google Drive · terakhir ${esc(lastText)}`;
            if (b.pending_files) html += ` · ${b.pending_files} antre (${fmtBytes(b.pending_bytes)})`;
            if (b.drive_free != null) html += ` · sisa ${fmtBytes(b.drive_free)}`;
            if (lowSpace) html += `<br><strong>⚠️ Google Drive hampir penuh!</strong> Hapus rekaman lama di folder CCTV atau tambah kuota, agar upload tidak berhenti.`;
            else if (stale && b.error) html += `<br><strong>⚠️ Upload gagal:</strong> ${esc(b.error.slice(0, 160))}`;
            backupEl.innerHTML = html;
            backupEl.classList.toggle('warn', lowSpace || (stale && !!b.error));
            backupEl.hidden = false;
        }

        function dateLabel(iso) {
            const d = new Date(iso + 'T00:00:00');
            const today = new Date(); today.setHours(0, 0, 0, 0);
            const diff = Math.round((today - d) / 86400000);
            if (diff === 0) return 'Hari ini';
            if (diff === 1) return 'Kemarin';
            return `${DAYS_SHORT[d.getDay()]}, ${d.getDate()} ${MONTHS_SHORT[d.getMonth()]}`;
        }

        async function loadRecordings(date) {
            if (!cam) return;
            try {
                const data = await api(`/api/cctv/${encodeURIComponent(cam.id)}/recordings${date ? '?date=' + encodeURIComponent(date) : ''}`);
                segments = data.segments || [];
                motionEnabled = !!data.motion_enabled;
                dateRow.innerHTML = (data.dates || []).map(dt =>
                    `<button class="pill-btn ${dt === data.date ? 'active' : ''}" data-date="${esc(dt)}">${esc(dateLabel(dt))}</button>`).join('')
                    || '<span class="sub">Belum ada rekaman.</span>';
                renderSegments();
            } catch (e) {
                hoursBox.innerHTML = '<div class="empty-note">Gagal memuat daftar rekaman.</div>';
            }
        }

        function renderSegments() {
            const withMotion = segments.filter(hasMotion);
            motionTools.hidden = !motionEnabled || !segments.length;
            const totalEvents = withMotion.reduce((n, s) => n + s.motion.length, 0);
            motionSummary.textContent = totalEvents ? `${totalEvents} gerakan di ${withMotion.length} rekaman` : 'Belum ada gerakan hari ini';
            btnMotion.classList.toggle('active', motionOnly);
            if (!segments.length) {
                hoursBox.innerHTML = '<div class="empty-note">Belum ada rekaman untuk tanggal ini.</div>';
                return;
            }
            const shown = motionOnly && motionEnabled ? withMotion : segments;
            if (!shown.length) {
                hoursBox.innerHTML = '<div class="empty-note">Tidak ada gerakan terdeteksi pada tanggal ini.</div>';
                return;
            }
            const byHour = new Map();
            shown.forEach(s => {
                const h = s.start.slice(0, 2);
                if (!byHour.has(h)) byHour.set(h, []);
                byHour.get(h).push(s);
            });
            hoursBox.innerHTML = [...byHour.entries()].sort((a, b) => b[0].localeCompare(a[0])).map(([h, list]) => `
                <div class="segment-hour"><span class="hour-label">${h}:00</span>
                    <div class="pill-row">${list.map(s => `<button class="pill-btn segment-btn ${s.file === currentFile ? 'active' : ''} ${s.recording ? 'recording' : ''}" data-file="${esc(s.file)}" ${s.recording ? 'disabled title="Sedang direkam"' : ''}>
                        ${s.recording ? '<i class="fa-solid fa-circle" style="color:var(--accent-red);font-size:.55rem"></i>' : ''}${esc(s.start.slice(0, 5))}${hasMotion(s) ? `<span class="motion-count ${s.motion.some(m => m.people) ? 'people' : ''}" title="${s.motion.length} gerakan"><i class="fa-solid fa-person-running"></i>${s.motion.length}</span>` : ''}</button>`).join('')}
                    </div></div>`).join('');
        }

        // ---------- Kontrol kamera Tapo (privasi, deteksi, mode malam, sirene) ----------
        const ctrlEl = document.getElementById('cctv-controls');
        let ctrl = null;
        let sirenTimer = null;
        const SENS = [['low', 'Rendah'], ['normal', 'Normal'], ['high', 'Tinggi']];
        const NIGHT = [['auto', 'Otomatis'], ['on', 'Inframerah'], ['off', 'Mati']];
        const seg = (key, opts, value, disabled) => `<div class="seg-ctl" role="radiogroup">${opts.map(([v, l]) =>
            `<button data-ctl="${key}" data-val="${v}" class="${v === value ? 'on' : ''}" role="radio" aria-checked="${v === value}" ${disabled ? 'disabled' : ''}>${l}</button>`).join('')}</div>`;
        const sw = (key, on, label) => `<button class="switch ${on ? 'on' : ''}" data-ctl="${key}" role="switch" aria-checked="${on}" aria-label="${label}"><span></span></button>`;

        function renderControls() {
            if (!ctrl) return;
            if (ctrl.error) {
                ctrlEl.innerHTML = `<div class="ctl-error"><i class="fa-solid fa-triangle-exclamation"></i><span>${esc(ctrl.error)}</span>
                    <button class="pill-btn icon" data-ctl="reload" aria-label="Coba lagi"><i class="fa-solid fa-rotate"></i></button></div>`;
                return;
            }
            const off = ctrl.privacy;   // saat mode privasi, kamera tidur: pengaturan lain tidak berguna
            ctrlEl.innerHTML = `
                <div class="ctl-row ${ctrl.privacy ? 'warn' : ''}">
                    <i class="fa-solid ${ctrl.privacy ? 'fa-eye-slash' : 'fa-eye'}"></i>
                    <span><strong>Mode privasi</strong><small>${ctrl.privacy ? 'Kamera tidur: tidak ada live, rekaman, maupun deteksi' : 'Kamera aktif merekam'}</small></span>
                    ${sw('privacy', ctrl.privacy, 'Mode privasi')}
                </div>
                <div class="ctl-row ${off ? 'dim' : ''}">
                    <i class="fa-solid fa-person-running"></i>
                    <span><strong>Deteksi gerakan</strong><small>${ctrl.motion ? 'Notifikasi & penanda gerakan aktif' : 'Mati: tidak ada notifikasi gerakan'}</small></span>
                    ${sw('motion', ctrl.motion, 'Deteksi gerakan')}
                </div>
                ${ctrl.motion ? `<div class="ctl-sub ${off ? 'dim' : ''}"><small>Sensitivitas</small>${seg('sensitivity', SENS, ctrl.sensitivity, off)}</div>` : ''}
                ${ctrl.person === null ? '' : `<div class="ctl-row ${off ? 'dim' : ''}">
                    <i class="fa-solid fa-user"></i>
                    <span><strong>Deteksi orang</strong><small>Tandai khusus bila yang bergerak adalah orang</small></span>
                    ${sw('person', ctrl.person, 'Deteksi orang')}
                </div>`}
                <div class="ctl-row ${off ? 'dim' : ''}">
                    <i class="fa-solid fa-moon"></i>
                    <span><strong>Mode malam</strong><small>Inframerah = gambar hitam-putih saat gelap</small></span>
                </div>
                <div class="ctl-sub ${off ? 'dim' : ''}">${seg('night', NIGHT, ctrl.night, off)}</div>
                <div class="ctl-row ${off ? 'dim' : ''}">
                    <i class="fa-solid fa-arrows-up-down"></i>
                    <span><strong>Balik gambar</strong><small>Nyalakan bila kamera dipasang menggantung di plafon</small></span>
                    ${sw('flip', ctrl.flip, 'Balik gambar')}
                </div>
                <button class="siren-btn ${ctrl.siren ? 'on' : ''}" data-ctl="siren" ${off ? 'disabled' : ''}>
                    <i class="fa-solid fa-bullhorn"></i>
                    <span>${ctrl.siren ? 'Sirene berbunyi · ketuk untuk hentikan' : 'Bunyikan sirene'}</span>
                </button>`;
        }

        async function loadControls() {
            if (!cam) return;
            try {
                ctrl = await api(`/api/cctv/${encodeURIComponent(cam.id)}/controls`);
            } catch (e) {
                if (e.message === 'Error 404') { ctrlEl.hidden = true; return; }   // kamera tanpa kontrol Tapo
                ctrl = { error: e.message };
            }
            renderControls();
            ctrlEl.hidden = false;
        }

        async function saveControl(body, btn) {
            ctrlEl.querySelectorAll('button').forEach(b => { b.disabled = true; });
            if (btn) btn.classList.add('busy');
            try {
                ctrl = await api(`/api/cctv/${encodeURIComponent(cam.id)}/controls`, {
                    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
                });
                if ('flip' in body) toast(body.flip ? 'Gambar dibalik' : 'Gambar kembali normal');
                if ('privacy' in body) {
                    toast(body.privacy ? 'Mode privasi aktif: kamera tidur' : 'Kamera aktif kembali');
                    if (mode === 'live') setTimeout(startLive, body.privacy ? 0 : 2500);
                }
            } catch (e) {
                toast(e.message);
            }
            renderControls();
        }

        async function toggleSiren(btn) {
            const on = !ctrl.siren;
            if (on && btn.dataset.confirm !== '1') {
                // Ketuk dua kali agar sirene tidak terbunyi karena tidak sengaja tersentuh
                btn.dataset.confirm = '1';
                btn.classList.add('armed');
                btn.querySelector('span').textContent = 'Ketuk sekali lagi untuk membunyikan';
                setTimeout(() => { if (btn.isConnected && btn.dataset.confirm === '1') renderControls(); }, 4000);
                return;
            }
            btn.disabled = true;
            try {
                const r = await api(`/api/cctv/${encodeURIComponent(cam.id)}/siren`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ on })
                });
                ctrl.siren = r.siren;
                clearTimeout(sirenTimer);
                if (r.siren) sirenTimer = setTimeout(() => { if (ctrl) { ctrl.siren = false; renderControls(); } }, r.max_seconds * 1000);
                toast(r.siren ? `Sirene berbunyi (berhenti sendiri setelah ${r.max_seconds} detik)` : 'Sirene dihentikan');
            } catch (e) {
                toast(e.message);
            }
            renderControls();
        }

        ctrlEl.addEventListener('click', e => {
            const b = e.target.closest('[data-ctl]');
            if (!b || b.disabled || !ctrl) return;
            const key = b.dataset.ctl;
            if (key === 'reload') return loadControls();
            if (key === 'siren') return toggleSiren(b);
            if (b.dataset.val) {
                if (ctrl[key] !== b.dataset.val) saveControl({ [key]: b.dataset.val }, b);
                return;
            }
            saveControl({ [key]: !ctrl[key] }, b);
        });

        async function ptz(direction) {
            if (!cam) return;
            if (navigator.vibrate) navigator.vibrate(10);
            // Gambar dibalik (kamera menggantung): motor tilt tidak ikut terbalik, jadi atas/bawah ditukar
            if (ctrl && ctrl.flip && (direction === 'up' || direction === 'down')) direction = direction === 'up' ? 'down' : 'up';
            try {
                await api(`/api/cctv/${encodeURIComponent(cam.id)}/ptz`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ direction, step: 0.08 })
                });
            } catch (e) {
                toast('Gagal menggerakkan kamera');
            }
        }

        function selectCamera(id) {
            cam = cameras.find(c => c.id === id) || cameras[0];
            camRow.innerHTML = cameras.length > 1 ? cameras.map(c =>
                `<button class="pill-btn ${c.id === cam.id ? 'active' : ''}" data-cam="${esc(c.id)}"><i class="fa-solid fa-video"></i> ${esc(c.name)}</button>`).join('') : '';
            document.getElementById('cctv-title').textContent = cam.name;
            meta.hidden = true;
            startLive();
            loadRecordings();
            loadBackup();
            loadControls();
        }

        camRow.addEventListener('click', e => { const b = e.target.closest('[data-cam]'); if (b) selectCamera(b.dataset.cam); });
        dateRow.addEventListener('click', e => { const b = e.target.closest('[data-date]'); if (b) loadRecordings(b.dataset.date); });
        hoursBox.addEventListener('click', e => { const b = e.target.closest('[data-file]'); if (b && !b.disabled) playSegment(b.dataset.file); });
        btnMotion.addEventListener('click', () => { motionOnly = !motionOnly; renderSegments(); });
        ptzPad.addEventListener('click', e => { const b = e.target.closest('[data-dir]'); if (b) ptz(b.dataset.dir); });
        btnLive.addEventListener('click', () => { meta.hidden = true; startLive(); });
        btnQuality.addEventListener('click', () => { quality = quality === 'hd' ? 'sd' : 'hd'; startLive(); });
        btnSound.addEventListener('click', () => {
            soundOn = !soundOn;
            applySound();
            if (soundOn) toast('Suara aktif. Jauhkan HP dari kamera agar tidak berdenging.');
        });
        btnFull.addEventListener('click', () => {
            const target = stage.querySelector('video') || stage;
            const req = target.requestFullscreen || target.webkitRequestFullscreen || target.webkitEnterFullscreen;
            if (req) req.call(target);
        });
        document.getElementById('btn-cctv-refresh').addEventListener('click', () => {
            loadRecordings(dateRow.querySelector('.active')?.dataset.date);
            loadBackup();
        });

        let initialized = false;
        async function show() {
            if (!initialized) {
                try {
                    const data = await api('/api/cctv/cameras');
                    cameras = data.cameras || [];
                } catch (e) {
                    cameras = [];
                }
                if (!cameras.length) {
                    cctvRoot.querySelector('.cctv-body').innerHTML = '<div class="empty-note">CCTV belum dikonfigurasi di config.yaml.</div>';
                    return;
                }
                initialized = true;
                selectCamera(cameras[0].id);
            } else if (mode === 'live' && !liveEl) {
                startLive();
            } else {
                loadRecordings(dateRow.querySelector('.active')?.dataset.date);
            }
            if (initialized) { loadBackup(); loadPush(); loadControls(); }
        }

        function hide() {
            // Hentikan live stream & playback saat tab ditinggalkan agar hemat kuota & CPU
            if (liveEl) { liveEl.remove(); liveEl = null; }
            stage?.querySelectorAll('video.playback').forEach(v => v.pause());
        }

        return { show, hide };
    })();

    // ---------- Router (ZTE) ----------
    const Router = (() => {
        const root = document.getElementById('panel-router');
        const statusBox = document.getElementById('router-status');
        const listBox = document.getElementById('router-devices');
        const summary = document.getElementById('router-summary');
        const ICONS = {
            phone: ['fa-mobile-screen', 'HP'], laptop: ['fa-laptop', 'Laptop / PC'], tv: ['fa-tv', 'TV'],
            camera: ['fa-video', 'Kamera'], server: ['fa-server', 'Server / STB'], smart: ['fa-plug', 'Perangkat pintar'],
            router: ['fa-wifi', 'Router / Repeater'], other: ['fa-circle-question', 'Lainnya']
        };
        let data = null;
        let loading = false;

        function guessIcon(c) {
            const h = (c.hostname || '').toLowerCase();
            if (c.icon) return c.icon;
            if (/redmi|galaxy|iphone|oppo|vivo|realme|android|poco|xiaomi|infinix|samsung|pixel/.test(h)) return 'phone';
            if (/aml|box|armbian|raspberry|server/.test(h)) return 'server';
            if (/desktop|laptop|pc|macbook|thinkpad/.test(h)) return 'laptop';
            if (/tv|chromecast|roku/.test(h)) return 'tv';
            if (/tapo|cam|ipc/.test(h)) return 'camera';
            if (/^(c4:82:e1|d8:1f:12|10:d5:61|50:8a:06|68:57:2d|a0:92:08|38:1f:8d|84:e3:42)/i.test(c.mac)) return 'smart';
            return 'other';
        }
        const displayName = c => c.name || c.hostname || (guessIcon(c) === 'smart' ? 'Perangkat Tuya' : 'Tanpa nama');
        const ipNum = ip => String(ip).split('.').reduce((n, x) => n * 256 + Number(x), 0);

        function fmtDuration(sec) {
            if (sec == null) return '-';
            const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
            return d ? `${d} hari ${h} jam` : h ? `${h} jam ${m} mnt` : `${m} mnt`;
        }

        function renderStatus() {
            const w = data.wan, o = data.optical;
            statusBox.innerHTML = `
                <div class="rt-stat ${w.connected ? 'ok' : 'bad'}"><i class="fa-solid fa-globe"></i>
                    <div><strong>${w.connected ? 'Internet terhubung' : 'Internet terputus'}</strong>
                    <small>${w.connected ? `Online ${fmtDuration(w.online_seconds)}` : esc(w.status || 'Tidak ada koneksi')}</small></div></div>
                <div class="rt-stat ${o.quality === 'baik' ? 'ok' : o.quality ? 'warn' : ''}"><i class="fa-solid fa-signal"></i>
                    <div><strong>Sinyal fiber ${o.quality ? esc(o.quality) : '-'}</strong>
                    <small>${o.rx_dbm != null ? `${o.rx_dbm} dBm` : 'tidak terbaca'}</small></div></div>
                <div class="rt-stat"><i class="fa-solid fa-network-wired"></i>
                    <div><strong>${data.clients.length} perangkat</strong>
                    <small>${data.reservations.length} IP terkunci${(data.blocked || []).length ? ` · ${data.blocked.length} diblokir` : ''}</small></div></div>`;
            const time = new Date(data.fetched_at * 1000).toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' });
            summary.textContent = `${data.device.model || 'Router'} · diperbarui ${time}`;
        }

        function deviceRow(c, offline) {
            const icon = guessIcon(c);
            const tags = [];
            if (!offline) tags.push(`<span class="rt-tag">${c.connection === 'lan' ? 'Kabel' : 'WiFi'}</span>`);
            if (c.reserved_ip) tags.push('<span class="rt-tag lock"><i class="fa-solid fa-lock"></i> IP terkunci</span>');
            if (offline) tags.push('<span class="rt-tag off">Offline</span>');
            if (c.blocked) tags.push('<span class="rt-tag blocked"><i class="fa-solid fa-ban"></i> Diblokir</span>');
            return `<button class="rt-device ${offline ? 'offline' : ''} ${c.blocked ? 'blocked' : ''}" data-mac="${esc(c.mac)}">
                <span class="rt-icon"><i class="fa-solid ${ICONS[icon][0]}"></i></span>
                <span class="rt-info"><strong>${esc(displayName(c))}</strong>
                    <small><span class="mono">${esc(c.reserved_ip || c.ip)}</span> ${tags.join(' ')}</small></span>
                <i class="fa-solid fa-chevron-right"></i></button>`;
        }

        function renderDevices() {
            const online = [...data.clients].sort((a, b) => ipNum(a.ip) - ipNum(b.ip));
            const blocked = (data.blocked_devices || []).filter(b => !b.online);
            const offline = data.reservations.filter(r => !r.online && !r.blocked);
            listBox.innerHTML = online.map(c => deviceRow(c, false)).join('')
                + (blocked.length ? `<div class="rt-section">Diblokir</div>${blocked.map(b => deviceRow({ ...b, blocked: true, reserved_ip: b.ip }, true)).join('')}` : '')
                + (offline.length ? `<div class="rt-section">IP terkunci · sedang offline</div>${offline.map(r => deviceRow({ ...r, reserved_ip: r.ip }, true)).join('')}` : '');
        }

        async function load(refresh) {
            if (loading) return;
            loading = true;
            root.classList.add('is-loading');
            try {
                const res = await api('/api/router/status' + (refresh ? '?refresh=1' : ''));
                if (!res.enabled) {
                    statusBox.innerHTML = '';
                    listBox.innerHTML = '<div class="empty-note">Router belum dikonfigurasi di config.yaml (bagian router).</div>';
                    return;
                }
                data = res;
                renderStatus();
                renderDevices();
            } catch (e) {
                if (!data) { statusBox.innerHTML = ''; listBox.innerHTML = ''; }
                listBox.querySelector('.form-error')?.remove();
                listBox.insertAdjacentHTML('afterbegin', `<div class="form-error">${esc(e.message)}</div>`);
            } finally {
                loading = false;
                root.classList.remove('is-loading');
            }
        }

        function findDevice(mac) {
            const c = data.clients.find(x => x.mac === mac);
            if (c) return { ...c, online: true };
            const r = data.reservations.find(x => x.mac === mac);
            if (r) return { ...r, reserved_ip: r.ip, online: false, connection: '' };
            const b = (data.blocked_devices || []).find(x => x.mac === mac);
            return b ? { ...b, blocked: true, reserved_ip: null, online: false, connection: '' } : null;
        }

        function openDevice(mac) {
            const c = findDevice(mac);
            if (!c) return;
            const icon = guessIcon(c);
            const iconOptions = Object.entries(ICONS).map(([k, [, label]]) =>
                `<option value="${k}" ${icon === k ? 'selected' : ''}>${label}</option>`).join('');
            const randomWarn = c.random_mac && c.connection === 'wifi'
                ? '<div class="rt-note"><i class="fa-solid fa-shuffle"></i> Perangkat ini memakai <strong>MAC acak (privasi)</strong>. Agar IP terkunci tetap berlaku, ubah pengaturan WiFi rumah di perangkat tersebut ke "MAC perangkat" / matikan "MAC acak".</div>' : '';
            const lockBlock = c.reserved_ip
                ? `<div class="rt-lock on"><i class="fa-solid fa-lock"></i><div><strong>IP terkunci di ${esc(c.reserved_ip)}</strong><small>Router selalu memberi IP ini ke perangkat ini.</small></div>
                    <button type="button" class="pill-btn danger" data-act="unlock">Lepas</button></div>`
                : `<div class="rt-lock"><i class="fa-solid fa-lock-open"></i><div><strong>IP belum dikunci</strong><small>IP bisa berubah saat perangkat restart.</small></div></div>
                    <label>Kunci ke IP <input name="ip" class="mono" inputmode="decimal" value="${esc(c.ip)}" placeholder="192.168.1.x"></label>
                    <button type="button" class="pill-btn primary" data-act="lock"><i class="fa-solid fa-lock"></i> Kunci IP</button>`;
            const where = c.online ? ` · ${esc(c.ip)} · ${c.connection === 'lan' ? 'Kabel ' + esc(c.port) : 'WiFi'}` : ' · offline';
            DeviceEditor.open(`<h3>${esc(displayName(c))}</h3>
                <p class="modal-sub mono">${esc(c.mac)}${where}${c.hostname ? ` · ${esc(c.hostname)}` : ''}</p>
                <form class="device-form" novalidate>
                    <label>Nama <input name="name" maxlength="40" value="${esc(c.name || '')}" placeholder="${esc(c.hostname || 'mis. HP Ibu')}"></label>
                    <label>Jenis <select name="icon">${iconOptions}</select></label>
                    <button type="button" class="pill-btn" data-act="save"><i class="fa-solid fa-check"></i> Simpan nama</button>
                    <div class="rt-divider"></div>
                    ${lockBlock}
                    ${randomWarn}
                    <div class="rt-divider"></div>
                    ${c.blocked
                        ? `<div class="rt-lock blocked"><i class="fa-solid fa-ban"></i><div><strong>Internet diblokir</strong><small>Perangkat ini tidak bisa memakai internet & jaringan rumah.</small></div>
                            <button type="button" class="pill-btn primary" data-act="unblock">Buka blokir</button></div>`
                        : `<button type="button" class="pill-btn danger" data-act="block"><i class="fa-solid fa-ban"></i> Blokir perangkat ini</button>`}
                    <div class="form-error" hidden></div>
                </form>
                <div class="modal-actions"><span class="spacer"></span><button type="button" class="pill-btn" data-close>Tutup</button></div>`);
            const form = document.querySelector('#device-modal .device-form');
            const errBox = form.querySelector('.form-error');
            const fail = (btn, html, msg) => { errBox.textContent = msg; errBox.hidden = false; btn.disabled = false; btn.innerHTML = html; };
            const working = (btn, html) => { const old = btn.innerHTML; btn.disabled = true; btn.innerHTML = html; return old; };

            wireBlock(c, form, fail, working);
            form.querySelector('[data-act="save"]').addEventListener('click', async (e) => {
                const btn = e.currentTarget, old = working(btn, '<i class="fa-solid fa-spinner fa-spin"></i>');
                try {
                    await api(`/api/router/devices/${encodeURIComponent(c.mac)}`, {
                        method: 'PUT', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ name: form.elements.name.value, icon: form.elements.icon.value })
                    });
                    toast('Nama perangkat disimpan');
                    DeviceEditor.close();
                    load(false);
                } catch (err) { fail(btn, old, err.message); }
            });
            form.querySelector('[data-act="lock"]')?.addEventListener('click', async (e) => {
                const ip = form.elements.ip.value.trim();
                const btn = e.currentTarget, old = working(btn, '<i class="fa-solid fa-spinner fa-spin"></i> Menyimpan ke router...');
                try {
                    await api('/api/router/reservations', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mac: c.mac, ip })
                    });
                    toast(`IP ${ip} dikunci`);
                    DeviceEditor.close();
                    load(true);
                } catch (err) { fail(btn, old, err.message); }
            });
            form.querySelector('[data-act="unlock"]')?.addEventListener('click', async (e) => {
                const btn = e.currentTarget, old = working(btn, '<i class="fa-solid fa-spinner fa-spin"></i>');
                try {
                    await api(`/api/router/reservations/${encodeURIComponent(c.mac)}`, { method: 'DELETE' });
                    toast('Kunci IP dilepas');
                    DeviceEditor.close();
                    load(true);
                } catch (err) { fail(btn, old, err.message); }
            });
        }

        function wireBlock(c, form, fail, working) {
            form.querySelector('[data-act="block"]')?.addEventListener('click', async (e) => {
                const btn = e.currentTarget;
                if (btn.dataset.confirm !== '1') {
                    btn.dataset.confirm = '1';
                    btn.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i> Yakin? Ketuk lagi untuk blokir';
                    return;
                }
                const old = working(btn, '<i class="fa-solid fa-spinner fa-spin"></i> Memblokir...');
                try {
                    await api(`/api/router/block/${encodeURIComponent(c.mac)}`, { method: 'POST' });
                    toast(`${displayName(c)} diblokir`);
                    DeviceEditor.close();
                    load(true);
                } catch (err) { btn.dataset.confirm = ''; fail(btn, '<i class="fa-solid fa-ban"></i> Blokir perangkat ini', err.message); }
            });
            form.querySelector('[data-act="unblock"]')?.addEventListener('click', async (e) => {
                const btn = e.currentTarget, old = working(btn, '<i class="fa-solid fa-spinner fa-spin"></i>');
                try {
                    await api(`/api/router/block/${encodeURIComponent(c.mac)}`, { method: 'DELETE' });
                    toast(`Blokir ${displayName(c)} dibuka`);
                    DeviceEditor.close();
                    load(true);
                } catch (err) { fail(btn, old, err.message); }
            });
        }

        function confirmReboot() {
            DeviceEditor.open(`<h3>Restart router?</h3>
                <p class="modal-sub">Internet dan WiFi di rumah akan mati sekitar 1–2 menit. Liliya dari luar rumah juga ikut terputus sampai router menyala lagi.</p>
                <div class="modal-actions"><span class="spacer"></span>
                    <button type="button" class="pill-btn" data-close>Batal</button>
                    <button type="button" class="pill-btn danger" data-act="reboot"><i class="fa-solid fa-power-off"></i> Restart</button></div>`);
            document.querySelector('#device-modal [data-act="reboot"]').addEventListener('click', async (e) => {
                e.currentTarget.disabled = true;
                try {
                    await api('/api/router/reboot', { method: 'POST' });
                    toast('Router sedang restart, tunggu ±2 menit');
                } catch (err) { toast(err.message); }
                DeviceEditor.close();
            });
        }


        // ---------- WiFi (wajib sandi aplikasi untuk melihat / mengubah) ----------
        const wifiBox = document.getElementById('router-wifi');
        let wifi = null;

        async function loadWifi() {
            try {
                wifi = await api('/api/router/wifi');
            } catch (e) {
                wifiBox.innerHTML = `<div class="form-error">WiFi: ${esc(e.message)}</div>`;
                return;
            }
            wifiBox.innerHTML = `
                <div class="rt-wifi-head">
                    <span class="rt-icon"><i class="fa-solid fa-wifi"></i></span>
                    <div class="rt-info"><strong>${esc(wifi.ssid)}</strong>
                        <small>${esc(wifi.security || 'Terbuka')} · ${esc(wifi.band)}${wifi.hidden ? ' · <span class="rt-tag">Tersembunyi</span>' : ''} · ${wifi.connected} perangkat</small></div>
                </div>
                <div class="pill-row">
                    <button class="pill-btn" data-wifi="reveal"><i class="fa-solid fa-eye"></i> Lihat password</button>
                    <button class="pill-btn" data-wifi="edit"><i class="fa-solid fa-pen"></i> Ubah WiFi</button>
                </div>`;
        }

        const appPasswordField = `<label>Sandi aplikasi Liliya
            <input name="app_password" type="password" autocomplete="current-password" required placeholder="Sandi untuk masuk Liliya"></label>`;

        function openReveal() {
            DeviceEditor.open(`<h3>Lihat password WiFi</h3>
                <p class="modal-sub">Demi keamanan, masukkan sandi aplikasi Liliya dulu.</p>
                <form class="device-form" novalidate>
                    ${appPasswordField}
                    <div class="rt-secret" hidden></div>
                    <div class="form-error" hidden></div>
                </form>
                <div class="modal-actions"><span class="spacer"></span>
                    <button type="button" class="pill-btn" data-close>Tutup</button>
                    <button type="button" class="pill-btn primary" data-act="go"><i class="fa-solid fa-eye"></i> Tampilkan</button></div>`);
            const card = document.querySelector('#device-modal .modal-card');
            const form = card.querySelector('form');
            const err = form.querySelector('.form-error');
            const go = async () => {
                const btn = card.querySelector('[data-act="go"]');
                btn.disabled = true;
                err.hidden = true;
                try {
                    const r = await api('/api/router/wifi/reveal', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ app_password: form.elements.app_password.value })
                    });
                    const box = form.querySelector('.rt-secret');
                    box.innerHTML = `<small>Password WiFi "${esc(wifi?.ssid || '')}"</small><strong class="mono">${esc(r.password)}</strong>
                        <button type="button" class="pill-btn" data-act="copy"><i class="fa-regular fa-copy"></i> Salin</button>`;
                    box.hidden = false;
                    form.elements.app_password.closest('label').hidden = true;
                    btn.hidden = true;
                    box.querySelector('[data-act="copy"]').addEventListener('click', () => {
                        navigator.clipboard?.writeText(r.password).then(() => toast('Password disalin'), () => toast('Gagal menyalin'));
                    });
                } catch (e) { err.textContent = e.message; err.hidden = false; btn.disabled = false; }
            };
            card.querySelector('[data-act="go"]').addEventListener('click', go);
            form.addEventListener('submit', (e) => { e.preventDefault(); go(); });
        }

        function openEditWifi() {
            DeviceEditor.open(`<h3>Ubah WiFi</h3>
                <form class="device-form" novalidate>
                    <label>Nama WiFi <input name="ssid" maxlength="32" value="${esc(wifi?.ssid || '')}"></label>
                    <label>Password baru <small>(kosongkan jika tidak diganti)</small>
                        <span class="rt-pass"><input name="password" type="password" maxlength="63" autocomplete="new-password" placeholder="Minimal 8 karakter">
                        <button type="button" class="pill-btn icon" data-act="toggle" aria-label="Tampilkan password"><i class="fa-solid fa-eye"></i></button></span></label>
                    <label class="rt-check"><input name="hidden" type="checkbox" ${wifi?.hidden ? 'checked' : ''}> Sembunyikan nama WiFi (tidak muncul di daftar WiFi)</label>
                    <div class="rt-note"><i class="fa-solid fa-triangle-exclamation"></i> Setelah disimpan, <strong>semua perangkat WiFi terputus</strong> beberapa detik. Jika nama atau password diganti, HP, laptop, kamera WiFi & <strong>gateway Tuya</strong> harus disambungkan ulang (gateway Tuya biasanya perlu pairing ulang di Smart Life).</div>
                    ${appPasswordField}
                    <div class="form-error" hidden></div>
                </form>
                <div class="modal-actions"><span class="spacer"></span>
                    <button type="button" class="pill-btn" data-close>Batal</button>
                    <button type="button" class="pill-btn primary" data-act="save"><i class="fa-solid fa-check"></i> Simpan</button></div>`);
            const card = document.querySelector('#device-modal .modal-card');
            const form = card.querySelector('form');
            const err = form.querySelector('.form-error');
            const fail = (msg) => { err.textContent = msg; err.hidden = false; };
            card.querySelector('[data-act="toggle"]').addEventListener('click', (e) => {
                const input = form.elements.password;
                input.type = input.type === 'password' ? 'text' : 'password';
                e.currentTarget.innerHTML = `<i class="fa-solid ${input.type === 'password' ? 'fa-eye' : 'fa-eye-slash'}"></i>`;
            });
            card.querySelector('[data-act="save"]').addEventListener('click', async (e) => {
                const btn = e.currentTarget;
                err.hidden = true;
                const body = { app_password: form.elements.app_password.value };
                const ssid = form.elements.ssid.value.trim();
                const password = form.elements.password.value;
                const hidden = form.elements.hidden.checked;
                if (ssid !== wifi.ssid) body.ssid = ssid;
                if (password) body.password = password;
                if (hidden !== wifi.hidden) body.hidden = hidden;
                if (!body.ssid && !body.password && body.hidden === undefined) return fail('Belum ada yang diubah.');
                if (body.ssid !== undefined && !ssid) return fail('Nama WiFi tidak boleh kosong.');
                if (body.password && (password.length < 8 || /\s/.test(password))) return fail('Password minimal 8 karakter, tanpa spasi.');
                if (!body.app_password) return fail('Masukkan sandi aplikasi Liliya.');
                btn.disabled = true;
                btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Menyimpan ke router...';
                try {
                    await api('/api/router/wifi', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
                    DeviceEditor.close();
                    toast('WiFi diperbarui. Sambungkan ulang perangkat bila diminta.');
                    loadWifi();
                } catch (e2) {
                    if (e2 instanceof TypeError) {
                        // Koneksi HP ikut terputus karena WiFi router restart: perubahan kemungkinan besar sudah tersimpan
                        DeviceEditor.close();
                        toast('Koneksi terputus saat WiFi restart. Sambungkan ulang ke WiFi, lalu cek lagi.');
                        return;
                    }
                    fail(e2.message);
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fa-solid fa-check"></i> Simpan';
                }
            });
        }

        wifiBox.addEventListener('click', e => {
            const b = e.target.closest('[data-wifi]');
            if (!b || !wifi) return;
            if (b.dataset.wifi === 'reveal') openReveal(); else openEditWifi();
        });

        listBox.addEventListener('click', e => { const b = e.target.closest('[data-mac]'); if (b && data) openDevice(b.dataset.mac); });
        document.getElementById('btn-router-refresh').addEventListener('click', () => { load(true).then(loadWifi); });
        document.getElementById('btn-router-reboot').addEventListener('click', confirmReboot);

        return { show: () => load(false).then(() => { if (!wifi) loadWifi(); }) };
    })();

    // ---------- Dokumen (Word / Excel / PowerPoint / PDF tanpa iklan) ----------
    const Docs = (() => {
        const listBox = document.getElementById('docs-list');
        const summary = document.getElementById('docs-summary');
        const fileInput = document.getElementById('docs-file');
        const progress = document.getElementById('docs-progress');
        const KIND = { word: 'Word', sheet: 'Excel', slides: 'PowerPoint', pdf: 'PDF' };
        let docs = [];
        let pollTimer = null;
        let visible = false;

        const fmtSize = b => b >= 1048576 ? (b / 1048576).toFixed(1).replace('.', ',') + ' MB' : Math.max(1, Math.round(b / 1024)) + ' KB';
        function fmtWhen(ts) {
            const d = new Date(ts * 1000), now = new Date();
            const time = d.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' });
            if (d.toDateString() === now.toDateString()) return `Hari ini ${time}`;
            const y = new Date(now); y.setDate(now.getDate() - 1);
            if (d.toDateString() === y.toDateString()) return `Kemarin ${time}`;
            return `${d.getDate()} ${MONTHS_SHORT[d.getMonth()]} ${time}`;
        }

        function render() {
            if (!docs.length) {
                listBox.innerHTML = `<div class="empty-note"><i class="fa-regular fa-folder-open"></i><br>Belum ada dokumen.<br>Ketuk <b>Buka file</b>, atau buka dari WhatsApp lewat aplikasi Liliya.</div>`;
                return;
            }
            listBox.innerHTML = docs.map(d => {
                const tag = d.status === 'converting' ? '<span class="rt-tag busy"><i class="fa-solid fa-spinner fa-spin"></i> Menyiapkan</span>'
                    : d.status === 'error' ? '<span class="rt-tag off">Gagal</span>' : '';
                return `<div class="doc-row">
                    <a class="rt-device" href="/docs/${encodeURIComponent(d.id)}">
                        <span class="doc-ico ${esc(d.kind)}">${esc(d.ext.toUpperCase().slice(0, 4))}</span>
                        <span class="rt-info"><strong>${esc(d.name)}</strong>
                            <small>${KIND[d.kind] || 'Dokumen'} · ${fmtSize(d.size)} · ${fmtWhen(d.opened)} ${tag}</small></span>
                        <i class="fa-solid fa-chevron-right"></i></a>
                    <button class="doc-del" data-del="${esc(d.id)}" title="Hapus" aria-label="Hapus ${esc(d.name)}"><i class="fa-regular fa-trash-can"></i></button>
                </div>`;
            }).join('');
        }

        async function load() {
            try {
                const data = await api('/api/docs');
                docs = data.docs;
                summary.textContent = docs.length
                    ? `${docs.length} dokumen · terhapus otomatis bila 30 hari tidak dibuka`
                    : (data.office ? 'Buka Word, Excel, PowerPoint & PDF tanpa iklan' : 'Baru PDF yang bisa dibuka (LibreOffice belum terpasang)');
                render();
            } catch (e) {
                listBox.innerHTML = `<div class="empty-note">${esc(e.message)}</div>`;
            }
            clearTimeout(pollTimer);
            if (visible && docs.some(d => d.status === 'converting')) pollTimer = setTimeout(load, 2500);
        }

        function upload(file) {
            if (!file) return;
            const nameEl = document.getElementById('docs-progress-name');
            const pctEl = document.getElementById('docs-progress-pct');
            const bar = document.getElementById('docs-progress-bar');
            nameEl.textContent = file.name;
            pctEl.textContent = '0%';
            bar.style.width = '0';
            progress.hidden = false;
            const xhr = new XMLHttpRequest();
            xhr.open('POST', '/api/docs');
            xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name));
            if (file.type) xhr.setRequestHeader('Content-Type', file.type);
            xhr.upload.onprogress = e => {
                if (!e.lengthComputable) return;
                const pct = Math.round(e.loaded / e.total * 100);
                bar.style.width = pct + '%';
                pctEl.textContent = pct < 100 ? pct + '%' : 'Memproses…';
            };
            xhr.onload = () => {
                progress.hidden = true;
                let body = {};
                try { body = JSON.parse(xhr.responseText); } catch (e) {}
                if (xhr.status === 401) { location.href = '/login?next=' + encodeURIComponent('/#docs'); return; }
                if (xhr.status >= 300 || !body.id) { toast(body.error || `Gagal mengunggah (kode ${xhr.status})`); return; }
                location.href = '/docs/' + encodeURIComponent(body.id);
            };
            xhr.onerror = () => { progress.hidden = true; toast('Koneksi ke server terputus saat mengunggah.'); };
            xhr.send(file);
        }

        fileInput.addEventListener('change', () => { upload(fileInput.files[0]); fileInput.value = ''; });
        document.getElementById('btn-docs-refresh').addEventListener('click', load);
        listBox.addEventListener('click', async e => {
            const b = e.target.closest('[data-del]');
            if (!b) return;
            const doc = docs.find(d => d.id === b.dataset.del);
            if (!doc || !confirm(`Hapus "${doc.name}" dari server?`)) return;
            try {
                await api('/api/docs/' + encodeURIComponent(doc.id), { method: 'DELETE' });
                docs = docs.filter(d => d.id !== doc.id);
                render();
                toast('Dokumen dihapus');
            } catch (err) { toast(err.message); }
        });

        return {
            show: () => { visible = true; load(); },
            hide: () => { visible = false; clearTimeout(pollTimer); }
        };
    })();

    // ---------- Init ----------
    let initialTab = 'assistant';
    try { initialTab = (location.hash.replace('#', '') || localStorage.getItem('budi_tab') || 'assistant'); } catch (e) {}
    setTab(initialTab);

    return { onEvent, setTab, toast };
})();
