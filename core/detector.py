"""Pengenal objek CCTV (orang / hewan) yang hanya bekerja saat kamera melaporkan gerakan.

STB tidak menganalisis video terus-menerus: saat event ONVIF "ada gerakan" masuk, ffmpeg mengambil stream SD
beberapa detik pada fps rendah, lalu YOLO (ONNX, CPU) memeriksa tiap gambar. Live view dan rekaman tidak
tersentuh karena go2rtc tetap meneruskan stream apa adanya.

Pencegah alarm palsu:
- objek harus terlihat di >= 2 gambar,
- kotak objek harus berisi piksel yang benar-benar berubah (benda diam seperti jemuran/sapu diabaikan),
- zona abaikan per kamera (mis. area jemuran yang tertiup angin).

Gambar event juga dikumpulkan (data/cctv_dataset) sebagai bahan melatih model khusus kamera ini nanti.
"""
import asyncio
import datetime
import io
import json
import os
import shutil
import time

import numpy as np

try:
    import onnxruntime as ort
    from PIL import Image, ImageDraw
except ImportError:          # fitur dimatikan bila library belum terpasang
    ort = None

# Kelas model COCO yang dipakai -> (kunci, label Indonesia, emoji, ambang keyakinan)
COCO_CLASSES = {
    0: ("person", "Orang", "🚶", 0.50),
    14: ("bird", "Burung/ayam", "🐔", 0.40),
    15: ("cat", "Kucing", "🐈", 0.40),
    16: ("dog", "Anjing", "🐕", 0.45),
    17: ("horse", "Kuda", "🐎", 0.45),
    18: ("sheep", "Kambing", "🐐", 0.40),
    19: ("cow", "Sapi", "🐄", 0.45),
}
LABELS = {key: (name, emoji) for key, name, emoji, _ in COCO_CLASSES.values()}
FRAME_W, FRAME_H = 640, 360      # gambar dari stream SD (juga ukuran simpanan dataset)


def _nms(boxes, scores, iou=0.45):
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        ovl = inter / (area[i] + area[order[1:]] - inter + 1e-9)
        order = order[1:][ovl < iou]
    return keep


class ObjectDetector:
    def __init__(self, config: dict, base_dir: str):
        cfg = (config.get("cctv", {}) or {}).get("detection", {}) or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.model_path = os.path.join(base_dir, cfg.get("model", "data/models/yolo11n_320.onnx"))
        self.imgsz = int(cfg.get("imgsz", 320))
        self.fps = float(cfg.get("fps", 3))
        self.window_s = float(cfg.get("window_seconds", 5))
        self.threads = int(cfg.get("threads", 2))
        self.min_hits = int(cfg.get("min_hits", 2))
        self.min_motion = float(cfg.get("min_motion", 0.02))     # porsi piksel kotak yang harus berubah
        self.dataset_dir = os.path.join(base_dir, "data", "cctv_dataset")
        self.collect = bool(cfg.get("collect_dataset", True))
        self.dataset_max_mb = int(cfg.get("dataset_max_mb", 3000))
        self.dataset_per_day = int(cfg.get("dataset_events_per_day", 400))
        self._session = None
        self._saved_today = {}
        self.stats = {"events": 0, "frames": 0, "found": 0, "infer_ms": None}
        if self.enabled and ort is None:
            print("[Detector Warning] onnxruntime / Pillow belum terpasang. Deteksi objek dimatikan.")
            self.enabled = False
        if self.enabled and not os.path.isfile(self.model_path):
            print(f"[Detector Warning] Model tidak ditemukan: {self.model_path}. Deteksi objek dimatikan.")
            self.enabled = False
        if self.enabled:
            print(f"[Detector] 🧠 Deteksi orang & hewan aktif ({os.path.basename(self.model_path)}, {self.imgsz}px)")

    # ---------- model ----------
    def _model(self):
        if self._session is None:
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = self.threads
            opts.inter_op_num_threads = 1
            self._session = ort.InferenceSession(self.model_path, opts, providers=["CPUExecutionProvider"])
            self._input = self._session.get_inputs()[0].name
        return self._session

    def infer(self, rgb: np.ndarray):
        """Gambar RGB (H, W, 3) -> daftar (kunci, skor, kotak ternormalisasi x1,y1,x2,y2)."""
        h, w = rgb.shape[:2]
        r = self.imgsz / max(w, h)
        nw, nh = round(w * r), round(h * r)
        canvas = np.full((self.imgsz, self.imgsz, 3), 114, np.uint8)
        canvas[:nh, :nw] = np.asarray(Image.fromarray(rgb).resize((nw, nh), Image.BILINEAR))
        x = (canvas.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        t0 = time.time()
        out = self._model().run(None, {self._input: x})[0][0].T          # (anchors, 4 + kelas)
        self.stats["infer_ms"] = round((time.time() - t0) * 1000)
        self.stats["frames"] += 1
        scores = out[:, 4:]
        cls = scores.argmax(1)
        conf = scores[np.arange(len(cls)), cls]
        dets = []
        for c, (key, _, _, thr) in COCO_CLASSES.items():
            m = (cls == c) & (conf >= thr)
            if not m.any():
                continue
            b = out[m, :4]
            boxes = np.stack([b[:, 0] - b[:, 2] / 2, b[:, 1] - b[:, 3] / 2, b[:, 0] + b[:, 2] / 2, b[:, 1] + b[:, 3] / 2], 1) / r
            for i in _nms(boxes, conf[m]):
                x1, y1, x2, y2 = boxes[i]
                dets.append((key, float(conf[m][i]), (max(0, x1 / w), max(0, y1 / h), min(1, x2 / w), min(1, y2 / h))))
        return dets

    # ---------- satu event gerakan ----------
    async def analyze(self, rtsp_url: str, ignore_zones=(), stop=None):
        """Ambil stream beberapa detik dan cari objek. Return dict hasil (label terkonfirmasi, gambar terbaik)."""
        self.stats["events"] += 1
        proc = await asyncio.create_subprocess_exec(
            "nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-threads", "1",
            "-rtsp_transport", "tcp", "-i", rtsp_url, "-t", str(self.window_s + 1),
            "-vf", f"fps={self.fps},scale={FRAME_W}:{FRAME_H}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        size = FRAME_W * FRAME_H * 3
        loop = asyncio.get_running_loop()
        hits, best, frames, prev = {}, {}, [], None
        deadline = time.time() + self.window_s + 6
        try:
            while time.time() < deadline and not (stop and stop()):
                try:
                    raw = await asyncio.wait_for(proc.stdout.readexactly(size), timeout=8)
                except (asyncio.IncompleteReadError, asyncio.TimeoutError):
                    break
                rgb = np.frombuffer(raw, np.uint8).reshape(FRAME_H, FRAME_W, 3)
                dets = await loop.run_in_executor(None, self.infer, rgb)
                gray = rgb[::4, ::4].mean(2)
                kept = []
                for key, score, box in dets:
                    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
                    if any(z[0] <= cx <= z[2] and z[1] <= cy <= z[3] for z in ignore_zones):
                        continue
                    if prev is not None and not self._moving(gray, prev, box):
                        continue
                    kept.append((key, score, box))
                    hits[key] = hits.get(key, 0) + 1
                    if score > best.get(key, (0,))[0]:
                        best[key] = (score, box, rgb)
                frames.append((rgb, kept))
                prev = gray
                # cukup bila semua objek yang terlihat sudah terkonfirmasi
                if hits and all(n >= self.min_hits for n in hits.values()) and len(frames) >= 4:
                    break
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            await proc.wait()
        found = sorted((k for k, n in hits.items() if n >= self.min_hits), key=lambda k: -best[k][0])
        if found:
            self.stats["found"] += 1
        result = {"labels": found, "scores": {k: round(best[k][0], 2) for k in found}, "frames": len(frames)}
        if found:
            score, box, rgb = best[found[0]]
            result["jpeg"] = self._annotated_jpeg(rgb, [(k, best[k][0], best[k][1]) for k in found])
        if self.collect and frames:
            await loop.run_in_executor(None, self._save_dataset, frames, found)
        return result

    def _moving(self, gray, prev, box, thr=18):
        h, w = gray.shape
        x1, y1, x2, y2 = int(box[0] * w), int(box[1] * h), int(np.ceil(box[2] * w)), int(np.ceil(box[3] * h))
        if x2 <= x1 or y2 <= y1:
            return False
        diff = np.abs(gray[y1:y2, x1:x2] - prev[y1:y2, x1:x2]) > thr
        return diff.mean() >= self.min_motion

    @staticmethod
    def _annotated_jpeg(rgb, dets):
        im = Image.fromarray(rgb)
        d = ImageDraw.Draw(im)
        for key, score, (x1, y1, x2, y2) in dets:
            name = LABELS.get(key, (key, ""))[0]
            box = [x1 * FRAME_W, y1 * FRAME_H, x2 * FRAME_W, y2 * FRAME_H]
            d.rectangle(box, outline=(255, 200, 0), width=3)
            d.rectangle([box[0], max(0, box[1] - 16), box[0] + 8 * len(name) + 40, box[1]], fill=(255, 200, 0))
            d.text((box[0] + 3, max(0, box[1] - 15)), f"{name} {score:.0%}", fill=(0, 0, 0))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return buf.getvalue()

    # ---------- bahan latihan model khusus ----------
    def _save_dataset(self, frames, found):
        day = datetime.date.today().isoformat()
        if self._saved_today.get(day, 0) >= self.dataset_per_day:
            return
        self._saved_today = {day: self._saved_today.get(day, 0) + 1}
        folder = os.path.join(self.dataset_dir, day)
        os.makedirs(folder, exist_ok=True)
        stamp = time.strftime("%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
        # simpan gambar dengan deteksi terbanyak + satu gambar awal (contoh tanpa objek / latar)
        picks = {0}
        with_obj = [i for i, (_, k) in enumerate(frames) if k]
        if with_obj:
            picks.add(max(with_obj, key=lambda i: max(s for _, s, _ in frames[i][1])))
        if len(frames) > 2:
            picks.add(len(frames) // 2)
        meta = {"labels": found, "frames": {}}
        for i in sorted(picks):
            rgb, kept = frames[i]
            name = f"{stamp}_{i}.jpg"
            Image.fromarray(rgb).save(os.path.join(folder, name), "JPEG", quality=85)
            meta["frames"][name] = [{"k": k, "s": round(s, 3), "b": [round(v, 4) for v in b]} for k, s, b in kept]
        with open(os.path.join(folder, stamp + ".json"), "w") as f:
            json.dump(meta, f)
        self._prune_dataset()

    def _prune_dataset(self):
        try:
            days = sorted(d for d in os.listdir(self.dataset_dir) if len(d) == 10)
            total = 0
            sizes = {}
            for d in days:
                p = os.path.join(self.dataset_dir, d)
                sizes[d] = sum(os.path.getsize(os.path.join(p, f)) for f in os.listdir(p))
                total += sizes[d]
            while days and total > self.dataset_max_mb * 1024 * 1024 and len(days) > 1:
                d = days.pop(0)
                shutil.rmtree(os.path.join(self.dataset_dir, d), ignore_errors=True)
                total -= sizes[d]
        except OSError:
            pass
