// =========================================================
// STB Smart Voice Assistant & Smart Display (BUDI)
// Universal HTML5 SpeechRecognition & WebUI Controller
// =========================================================

let ws = null;
let reconnectTimer = null;
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
const btnQuickMusic = document.getElementById('btn-quick-music');
const btnQuickPrayer = document.getElementById('btn-quick-prayer');
const btnQuickWeather = document.getElementById('btn-quick-weather');
const btnQuickNews = document.getElementById('btn-quick-news');

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
            "idle": "Standby",
            "listening": "🔴 Merekam...",
            "thinking": "Memproses...",
            "speaking": "Membacakan Respon..."
        };
        statusText.textContent = labels[state] || state;
    }
}
setOrbState("idle");

// 3. Real-Time Streaming Speech Recognition with Live Subtitles & Auto-Stop
let recognition = null;
let finalRecordedText = "";

function initSpeechEngine() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SpeechRecognition) {
        recognition = new SpeechRecognition();
        recognition.lang = 'id-ID';
        recognition.continuous = false;
        recognition.interimResults = true;

        recognition.onstart = () => {
            isRecording = true;
            finalRecordedText = "";
            setOrbState("listening");
            if (btnVoiceInput) {
                btnVoiceInput.innerHTML = '<i class="fa-solid fa-microphone-lines fa-fade"></i> <span>Merekam...</span>';
                btnVoiceInput.style.background = 'linear-gradient(135deg, #ff0844, #ffb199)';
                btnVoiceInput.style.color = '#fff';
            }
            if (userSpeech) {
                userSpeech.style.display = "flex";
                userSpeech.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
            if (userSpeechText) userSpeechText.textContent = "Mendengarkan ucapan Anda...";
        };

        recognition.onresult = (event) => {
            let liveText = "";
            for (let i = 0; i < event.results.length; ++i) {
                liveText += event.results[i][0].transcript;
            }
            if (liveText) {
                finalRecordedText = liveText;
                if (userSpeech) userSpeech.style.display = "flex";
                if (userSpeechText) userSpeechText.textContent = liveText;
            }
        };

        recognition.onerror = (event) => {
            console.warn("[Speech Error]", event.error);
            isRecording = false;
            setOrbState("idle");
            resetVoiceButton();
        };

        recognition.onend = () => {
            isRecording = false;
            resetVoiceButton();
            if (finalRecordedText && finalRecordedText.trim()) {
                console.log(`[Voice Complete] 🗣️ Perintah: "${finalRecordedText}"`);
                sendCommand(finalRecordedText.trim());
            } else {
                setOrbState("idle");
            }
        };
    }
}
initSpeechEngine();

function resetVoiceButton() {
    if (btnVoiceInput) {
        btnVoiceInput.innerHTML = '<i class="fa-solid fa-microphone"></i> <span>Bicara</span>';
        btnVoiceInput.style.background = 'linear-gradient(135deg, #00f2fe, #4facfe)';
        btnVoiceInput.style.color = '#000';
    }
}

function toggleBrowserMic() {
    if (!recognition) {
        initSpeechEngine();
    }
    if (isRecording) {
        try { recognition.stop(); } catch (e) {}
        isRecording = false;
        resetVoiceButton();
    } else {
        try {
            recognition.start();
        } catch (e) {
            console.warn("Recognition start error:", e);
            try {
                recognition.abort();
                setTimeout(() => {
                    try { recognition.start(); } catch (err) {}
                }, 100);
            } catch (err) {}
        }
    }
}

// 4. WebSocket Connection
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    console.log(`[WS] Menghubungkan ke: ${wsUrl}`);
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log("[WS] ✅ Terhubung ke STB Assistant Server.");
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

// 5. Handle Incoming Events from STB
function handleServerEvent(data) {
    const type = data.type;

    if (type === "state_change") {
        setOrbState(data.state);
        if (data.state === "listening") {
            if (userSpeech) {
                userSpeech.style.display = "flex";
                userSpeech.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
            if (userSpeechText) userSpeechText.textContent = "Sedang mendengarkan ucapan Anda...";
        }
    } else if (type === "user_voice_command") {
        if (userSpeech) {
            userSpeech.style.display = "flex";
            userSpeech.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
        if (userSpeechText) userSpeechText.textContent = data.text;
    } else if (type === "assistant_response") {
        if (assistantSpeech) {
            assistantSpeech.style.display = "flex";
            assistantSpeech.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
        if (assistantSpeechText) assistantSpeechText.textContent = data.text;
    } else if (type === "open_url") {
        if (data.url) {
            console.log(`[Redirect] 🚀 Membuka URL di browser: ${data.url}`);
            try {
                window.open(data.url, '_blank');
            } catch (e) {
                console.warn("[Redirect Error]", e);
            }
            if (assistantSpeech) {
                assistantSpeech.style.display = "flex";
                assistantSpeech.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
            if (assistantSpeechText) {
                assistantSpeechText.innerHTML = `${assistantSpeechText.textContent || 'Membuka YouTube...'} <br><a href="${data.url}" target="_blank" class="yt-mobile-btn"><i class="fa-brands fa-youtube"></i> Buka di YouTube</a>`;
            }
        }
    } else if (type === "now_playing") {
        const hasMusic = data.title && data.title !== "Tidak ada musik diputar" && data.title.trim() !== "";
        if (mediaTitle) {
            mediaTitle.textContent = data.title || "Tidak ada musik diputar";
        }
        if (mediaArtist) {
            mediaArtist.textContent = data.artist || "STB Media Engine";
        }
        if (mediaCard) {
            if (hasMusic) {
                mediaCard.classList.add('is-playing');
                mediaCard.style.opacity = "1";
            } else {
                mediaCard.classList.remove('is-playing');
                mediaCard.style.opacity = "0.7";
            }
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

// 6. Send Commands to STB
function sendCommand(text) {
    if (!text || !text.trim()) return;
    if (ws && ws.readyState === WebSocket.OPEN) {
        if (userSpeech) {
            userSpeech.style.display = "flex";
            userSpeech.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
        if (userSpeechText) userSpeechText.textContent = text;
        if (assistantSpeechText) assistantSpeechText.textContent = "Memproses permintaan...";
        setOrbState("thinking");

        ws.send(JSON.stringify({
            type: "text_command",
            text: text
        }));
    }
}

// 7. Event Listeners
if (orbWrapper) {
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

if (btnQuickMusic) {
    btnQuickMusic.addEventListener('click', () => {
        sendCommand("Putar musik santai");
    });
}

if (btnQuickPrayer) {
    btnQuickPrayer.addEventListener('click', () => {
        sendCommand("Jadwal sholat hari ini");
    });
}

if (btnQuickWeather) {
    btnQuickWeather.addEventListener('click', () => {
        sendCommand("Bagaimana cuaca hari ini?");
    });
}

if (btnQuickNews) {
    btnQuickNews.addEventListener('click', () => {
        sendCommand("Ada berita menarik apa hari ini?");
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
