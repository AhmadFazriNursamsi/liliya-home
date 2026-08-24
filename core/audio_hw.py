import subprocess

def auto_unmute_hardware_audio():
    """
    Otomatis membuka dan memaksimalkan volume hardware DAC STB (S905X & USB)
    pada setiap kali asisten BUDI dinyalakan.
    """
    cmds = [
        # S905X Built-in AV Jack Codec Routing (Card 0 dan Card 1)
        "amixer -c 0 cset name='AIU ACODEC SRC' 'I2S' 2>/dev/null || true",
        "amixer -c 0 sset 'AIU ACODEC OUT EN' on 2>/dev/null || true",
        "amixer -c 0 cset name='ACODEC Playback Volume' 255 2>/dev/null || true",
        "amixer -c 0 set 'Master' 100% unmute 2>/dev/null || true",
        "amixer -c 0 set 'PCM' 100% unmute 2>/dev/null || true",
        "amixer -c 0 set 'Speaker' 100% unmute 2>/dev/null || true",
        "amixer -c 0 set 'ACODEC' 100% unmute 2>/dev/null || true",

        "amixer -c 1 cset name='AIU ACODEC SRC' 'I2S' 2>/dev/null || true",
        "amixer -c 1 sset 'AIU ACODEC OUT EN' on 2>/dev/null || true",
        "amixer -c 1 cset name='ACODEC Playback Volume' 255 2>/dev/null || true",
        "amixer -c 1 set 'Master' 100% unmute 2>/dev/null || true",
        "amixer -c 1 set 'PCM' 100% unmute 2>/dev/null || true",
        "amixer -c 1 set 'Speaker' 100% unmute 2>/dev/null || true",
        "amixer -c 1 set 'ACODEC' 100% unmute 2>/dev/null || true",
    ]
    for cmd in cmds:
        try:
            subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

def find_active_playback_device() -> str:
    """Mencari kartu suara output aktif (S905X AV jack atau USB Audio)."""
    try:
        res = subprocess.run(['aplay', '-l'], capture_output=True, text=True)
        out = res.stdout
        for line in out.splitlines():
            if 'card ' in line and 'S905X' in line:
                card_num = line.split()[1].replace(':', '')
                return f"plughw:{card_num},0"
            elif 'card ' in line and ('USB' in line or 'Audio' in line):
                card_num = line.split()[1].replace(':', '')
                return f"plughw:{card_num},0"
    except Exception:
        pass
    return "plughw:1,0"
