// =========================================================
// STB Smart Voice Assistant & Smart Display
// Universal HTML5 MediaRecorder Audio Stream (Mac/iOS/Android/Windows)
// =========================================================

let ws = null;
let reconnectTimer = null;
let smartHomeDevices = {};
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;

// DOM Elements
const orbWrapper = document.getElementById('orb-wrapper');
const coreOrb = document.getElementById('core-orb');
const statusText = document.getElementById('assistant-status-text');
const userSpeech = document.getElementById('user-speech');
const userSpeechText = document.getElementById('user-speech-text');
const assistantSpeech = document.getElementById('assistant-speech');
const assistantSpeechText = document.getElementById('assistant-speech-text');
const clockTime = document.getElementById('clock-time');
const clockDate = document.getElementById('clock-date');
const mediaTitle = document.getElementById('media-title');
const mediaArtist = document.getElementById('media-artist');
const mediaCard = document.getElementById('media-card');
const btnStopMusic = document.getElementById('btn-stop-music');
const textCmdForm = document.getElementById('text-cmd-form');
const textInput = document.getElementById('text-input');
const btnVoiceInput = document.getElementById('btn-voice-input');
const btnFullscreen = document.getElementById('btn-fullscreen');
const btnQuickLights = document.getElementById('btn-quick-lights');
const btnQuickSensors = document.getElementById('btn-quick-sensors');

// 1. Clock & Date Display
const DAYS_ID = ["Minggu", "Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu"];
const MONTHS_ID = [
    "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember"
];

function updateClock() {
    if (!clockTime || !clockDate) return;
    const now = new Date();
    const hours = String(now.getHours()).padStart(2, '0');
    const minutes = String(now.getMinutes()).padStart(2, '0');
    clockTime.textContent = `${hours}:${minutes}`;

    const day = DAYS_ID[now.getDay()];
    const date = now.getDate();
    const month = MONTHS_ID[now.getMonth()];
    const year = now.getFullYear();
    clockDate.textContent = `${day}, ${date} ${month} ${year}`;
}
setInterval(updateClock, 1000);
updateClock();

// 2. Set Orb State (idle, listening, thinking, speaking)
function setOrbState(state) {
    document.body.className = `theme-dark state-${state}`;
    if (statusText) {
        const labels = {
            "idle": "Standby (Klik 'Bicara' untuk memberi perintah)",
            "listening": "🔴 Sedang Merekam Suara Anda...",
            "thinking": "Sedang Berpikir & Memproses...",
            "speaking": "Membacakan Respon..."
        };
        statusText.textContent = labels[state] || state;
    }
}
setOrbState("idle");

// 3. Universal HTML5 MediaRecorder (Compatible with Mac Safari, Chrome, iPhone, Android)
async function startAudioRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioChunks = [];
        mediaRecorder = new MediaRecorder(stream);

        mediaRecorder.ondataavailable = (e) => {
            if (e.data.size > 0) {
                audioChunks.push(e.data);
            }
        };

        mediaRecorder.onstop = async () => {
            stream.getTracks().forEach(track => track.stop());
            const audioBlob = new Blob(audioChunks, { type: 'audio/wav' });
            const reader = new FileReader();
            reader.readAsDataURL(audioBlob);
            reader.onloadend = () => {
                const base64Audio = reader.result.split(',')[1];
                if (ws && ws.readyState === WebSocket.OPEN) {
                    setOrbState("thinking");
                    if (assistantSpeechText) assistantSpeechText.textContent = "Mentranskripsi suara Anda...";
                    ws.send(JSON.stringify({
                        type: "audio_command",
                        audio: base64Audio
                    }));
                }
            };
        };

        mediaRecorder.start();
        isRecording = true;
        setOrbState("listening");

        if (btnVoiceInput) {
            btnVoiceInput.innerHTML = '<i class="fa-solid fa-stop"></i> Selesai';
            btnVoiceInput.style.background = 'linear-gradient(135deg, #ff0844, #ffb199)';
            btnVoiceInput.style.color = '#fff';
        }
        if (userSpeech) userSpeech.style.display = "flex";
        if (userSpeechText) userSpeechText.textContent = "Silakan bicara ke mikrofon sekarang...";

    } catch (err) {
        console.error("[Mic Error]", err);
        alert("Akses mikrofon ditolak atau tidak tersedia. Pastikan Anda mengizinkan izin mic di browser.");
        isRecording = false;
        setOrbState("idle");
    }
}

function stopAudioRecording() {
    if (mediaRecorder && isRecording) {
        mediaRecorder.stop();
        isRecording = false;
        if (btnVoiceInput) {
            btnVoiceInput.innerHTML = '<i class="fa-solid fa-microphone"></i> Bicara';
            btnVoiceInput.style.background = 'linear-gradient(135deg, #00f2fe, #4facfe)';
            btnVoiceInput.style.color = '#000';
        }
    }
}

function toggleBrowserMic() {
    if (isRecording) {
        stopAudioRecording();
    } else {
        startAudioRecording();
    }
}

// 4. WebSocket Connection
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    console.log(`[WS] Menghubungkan ke: ${wsUrl}`);
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log("[WS] ✅ Terhubung ke STB Assistant & Smart Home Realtime DB.");
        if (reconnectTimer) {
            clearInterval(reconnectTimer);
            reconnectTimer = null;
        }
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleServerEvent(data);
        } catch (e) {
            console.error("[WS Error]", e);
        }
    };

    ws.onclose = () => {
        console.warn("[WS] ⚠️ Koneksi terputus. Mencoba reconnect dalam 3 detik...");
        setOrbState("idle");
        if (!reconnectTimer) {
            reconnectTimer = setInterval(connectWebSocket, 3000);
        }
    };
}

// 5. Smart Home Chip Controller
function toggleDevice(deviceId) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            type: "smart_home_toggle",
            id: deviceId
        }));
    }
}

function updateDeviceChip(device) {
    const chip = document.getElementById(`chip-${device.id}`);
    if (!chip) return;

    const isActive = device.state === "on";
    const statusSpan = chip.querySelector('.chip-status');

    if (device.type === "light") {
        if (isActive) {
            chip.classList.add('active');
            if (statusSpan) statusSpan.textContent = "ON";
        } else {
            chip.classList.remove('active');
            if (statusSpan) statusSpan.textContent = "OFF";
        }
    } else if (device.type === "ac") {
        if (isActive) {
            chip.classList.add('ac-active');
            if (statusSpan) statusSpan.textContent = `${device.temperature || 24}°C`;
        } else {
            chip.classList.remove('ac-active');
            if (statusSpan) statusSpan.textContent = "OFF";
        }
    } else if (device.type === "sensor_climate") {
        const suhuVal = document.getElementById('chip-suhu-val');
        if (suhuVal) suhuVal.textContent = `${device.temperature}°C`;
    } else if (device.type === "sensor_contact") {
        const pintuVal = document.getElementById('chip-pintu-val');
        if (pintuVal) {
            pintuVal.textContent = device.is_open ? "Terbuka" : "Aman";
            if (device.is_open) {
                pintuVal.classList.add('sensor-alert');
            } else {
                pintuVal.classList.remove('sensor-alert');
            }
        }
    }
}

function syncAllDevices(devicesList) {
    if (!Array.isArray(devicesList)) return;
    devicesList.forEach(dev => {
        smartHomeDevices[dev.id] = dev;
        updateDeviceChip(dev);
    });
}

// 6. Handle Incoming Events from STB
function handleServerEvent(data) {
    const type = data.type;

    if (type === "state_change") {
        setOrbState(data.state);
        if (data.state === "listening") {
            if (userSpeech) userSpeech.style.display = "flex";
            if (userSpeechText) userSpeechText.textContent = "Sedang mendengarkan ucapan Anda...";
        }
    } else if (type === "user_voice_command") {
        if (userSpeech) userSpeech.style.display = "flex";
        if (userSpeechText) userSpeechText.textContent = data.text;
    } else if (type === "assistant_response") {
        if (assistantSpeech) assistantSpeech.style.display = "flex";
        if (assistantSpeechText) assistantSpeechText.textContent = data.text;
    } else if (type === "smart_home_sync") {
        if (data.devices) {
            syncAllDevices(data.devices);
        }
    } else if (type === "smart_home_device_update") {
        if (data.device) {
            smartHomeDevices[data.device.id] = data.device;
            updateDeviceChip(data.device);
        }
    } else if (type === "now_playing") {
        if (mediaTitle) {
            mediaTitle.textContent = data.title || "Tidak ada musik diputar";
        }
        if (mediaArtist) {
            mediaArtist.textContent = data.artist || "STB Media Engine";
        }
        if (mediaCard) {
            mediaCard.style.opacity = data.title ? "1" : "0.7";
        }
    } else if (type === "usage_stat") {
        const tokEl = document.getElementById('token-count-val');
        const mdlEl = document.getElementById('active-model-val');
        const keyEl = document.getElementById('active-key-val');
        if (tokEl && data.total_tokens !== undefined) tokEl.textContent = data.total_tokens.toLocaleString();
        if (mdlEl && data.active_model) mdlEl.textContent = data.active_model;
        if (keyEl && data.key_count) keyEl.textContent = `Keys: ${data.key_count}`;
    } else if (type === "weather_update") {
        const tempEl = document.getElementById('weather-temp');
        const cityEl = document.getElementById('weather-city');
        if (tempEl) tempEl.textContent = `${data.temp}°C`;
        if (cityEl) cityEl.textContent = data.city;
    }
}

// 7. Send Commands to STB
function sendCommand(text) {
    if (!text || !text.trim()) return;
    if (ws && ws.readyState === WebSocket.OPEN) {
        if (userSpeech) userSpeech.style.display = "flex";
        if (userSpeechText) userSpeechText.textContent = text;
        if (assistantSpeechText) assistantSpeechText.textContent = "Memproses permintaan...";
        setOrbState("thinking");

        ws.send(JSON.stringify({
            type: "text_command",
            text: text
        }));
    }
}

// 8. Event Listeners
if (orbWrapper) {
    orbWrapper.style.cursor = "pointer";
    orbWrapper.title = "Klik untuk berbicara via Mic Mac / HP";
    orbWrapper.addEventListener('click', toggleBrowserMic);
}

if (btnVoiceInput) {
    btnVoiceInput.addEventListener('click', toggleBrowserMic);
}

if (textCmdForm) {
    textCmdForm.addEventListener('submit', (e) => {
        e.preventDefault();
        if (textInput && textInput.value) {
            sendCommand(textInput.value);
            textInput.value = "";
        }
    });
}

if (btnStopMusic) {
    btnStopMusic.addEventListener('click', () => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "media_control", action: "stop" }));
        }
    });
}

if (btnQuickLights) {
    btnQuickLights.addEventListener('click', () => {
        sendCommand("Nyalakan semua lampu");
    });
}

if (btnQuickSensors) {
    btnQuickSensors.addEventListener('click', () => {
        sendCommand("Cek status semua sensor");
    });
}

if (btnFullscreen) {
    btnFullscreen.addEventListener('click', () => {
        if (!document.fullscreenElement) {
            document.documentElement.requestFullscreen().catch(() => {});
        } else {
            document.exitFullscreen().catch(() => {});
        }
    });
}

// Inisialisasi
connectWebSocket();
