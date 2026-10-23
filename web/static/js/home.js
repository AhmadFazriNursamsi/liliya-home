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
            let msg = `Error ${res.status}`;
            try { const j = await res.json(); if (j.error) msg = j.error; } catch (e) {}
            throw new Error(msg);
        }
        return res.json();
    }

    // ---------- Tabs ----------
    function setTab(tab) {
        if (!['assistant', 'home', 'cctv'].includes(tab)) tab = 'assistant';
        appContainer.dataset.tab = tab;
        document.querySelectorAll('.tab-btn[data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
        try { localStorage.setItem('budi_tab', tab); } catch (e) {}
        if (tab === 'home') loadDevices();
        if (tab === 'cctv') Cctv.show(); else Cctv.hide();
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
                <div><div class="dev-name">${esc(d.name || d.id)}</div><div class="dev-state">${esc(describe(d))}${d.online === false ? ' <span class="dev-badge">Offline</span>' : ''}</div></div>
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
        const rooms = new Map();
        list.forEach(d => {
            const room = d.room || 'Lainnya';
            if (!rooms.has(room)) rooms.set(room, []);
            rooms.get(room).push(d);
        });
        homeGrid.innerHTML = [...rooms.entries()].map(([room, devs]) => `
            <section class="room-group"><h3>${esc(room)}</h3>
                <div class="device-grid">${devs.map(cardHtml).join('')}</div>
            </section>`).join('');

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
        return { openAdd, openEdit };
    })();


    // ---------- Toast ----------
    let toastTimer = null;
    function toast(msg) {
        let el = document.getElementById('budi-toast');
        if (!el) {
            el = document.createElement('div');
            el.id = 'budi-toast';
            el.style.cssText = 'position:fixed;left:50%;bottom:max(24px,env(safe-area-inset-bottom));transform:translateX(-50%);background:rgba(18,24,38,.95);border:1px solid rgba(255,255,255,.12);color:#f8fafc;padding:10px 16px;border-radius:12px;font-size:.85rem;z-index:99;max-width:90vw;text-align:center';
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
                const next = segments[idx + 1];
                if (next && !next.recording) playSegment(next.file);
            };
            v.addEventListener('ended', playNext);
            v.addEventListener('error', () => {
                toast(`Rekaman ${seg.start.slice(0, 5)} tidak bisa diputar, lanjut ke berikutnya.`);
                playNext();
            });
            stage.prepend(v);
            renderSegments();
            const end = new Date(seg.end_ts * 1000);
            meta.innerHTML = `<span>Memutar <strong>${esc(seg.start.slice(0, 5))} – ${String(end.getHours()).padStart(2, '0')}:${String(end.getMinutes()).padStart(2, '0')}</strong> · ${(seg.size / 1048576).toFixed(0)} MB</span>
                <a class="pill-btn" href="${v.src}?download=1" download><i class="fa-solid fa-download"></i> Unduh</a>`;
            meta.hidden = false;
        }

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
                dateRow.innerHTML = (data.dates || []).map(dt =>
                    `<button class="pill-btn ${dt === data.date ? 'active' : ''}" data-date="${esc(dt)}">${esc(dateLabel(dt))}</button>`).join('')
                    || '<span class="sub">Belum ada rekaman.</span>';
                renderSegments();
            } catch (e) {
                hoursBox.innerHTML = '<div class="empty-note">Gagal memuat daftar rekaman.</div>';
            }
        }

        function renderSegments() {
            if (!segments.length) {
                hoursBox.innerHTML = '<div class="empty-note">Belum ada rekaman untuk tanggal ini.</div>';
                return;
            }
            const byHour = new Map();
            segments.forEach(s => {
                const h = s.start.slice(0, 2);
                if (!byHour.has(h)) byHour.set(h, []);
                byHour.get(h).push(s);
            });
            hoursBox.innerHTML = [...byHour.entries()].sort((a, b) => b[0].localeCompare(a[0])).map(([h, list]) => `
                <div class="segment-hour"><span class="hour-label">${h}:00</span>
                    <div class="pill-row">${list.map(s => `<button class="pill-btn segment-btn ${s.file === currentFile ? 'active' : ''} ${s.recording ? 'recording' : ''}" data-file="${esc(s.file)}" ${s.recording ? 'disabled title="Sedang direkam"' : ''}>
                        ${s.recording ? '<i class="fa-solid fa-circle" style="color:var(--accent-red);font-size:.55rem"></i>' : ''}${esc(s.start.slice(0, 5))}</button>`).join('')}
                    </div></div>`).join('');
        }

        async function ptz(direction) {
            if (!cam) return;
            if (navigator.vibrate) navigator.vibrate(10);
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
        }

        camRow.addEventListener('click', e => { const b = e.target.closest('[data-cam]'); if (b) selectCamera(b.dataset.cam); });
        dateRow.addEventListener('click', e => { const b = e.target.closest('[data-date]'); if (b) loadRecordings(b.dataset.date); });
        hoursBox.addEventListener('click', e => { const b = e.target.closest('[data-file]'); if (b && !b.disabled) playSegment(b.dataset.file); });
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
            if (initialized) loadBackup();
        }

        function hide() {
            // Hentikan live stream & playback saat tab ditinggalkan agar hemat kuota & CPU
            if (liveEl) { liveEl.remove(); liveEl = null; }
            stage?.querySelectorAll('video.playback').forEach(v => v.pause());
        }

        return { show, hide };
    })();

    // ---------- Init ----------
    let initialTab = 'assistant';
    try { initialTab = (location.hash.replace('#', '') || localStorage.getItem('budi_tab') || 'assistant'); } catch (e) {}
    setTab(initialTab);

    return { onEvent, setTab, toast };
})();
