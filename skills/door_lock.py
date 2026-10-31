class DoorLockSkill:
    """Perintah suara untuk kunci pintu. Membuka kunci lewat suara sengaja tidak
    disediakan: siapa pun di dekat mikrofon (atau suara dari TV) bisa memicunya."""

    def __init__(self, manager):
        self.manager = manager

    def lock_door(self) -> str:
        """Mengunci pintu rumah (kunci pintu pintar). Gunakan saat pengguna meminta mengunci pintu."""
        res = self.manager.queue_command_threadsafe("lock", by="Suara Budi")
        if res.get("ok"):
            return f"{self.manager.name} sudah dikunci."
        return res.get("error") or "Pintu gagal dikunci."

    def unlock_door(self) -> str:
        """Dipanggil saat pengguna meminta MEMBUKA kunci pintu. Demi keamanan tidak dijalankan lewat suara."""
        return "Demi keamanan, buka kunci pintu hanya bisa dengan kartu atau tombol di aplikasi Liliya."

    def get_door_status(self) -> str:
        """Menjelaskan status kunci pintu rumah: terkunci atau tidak, dan pintunya terbuka atau tertutup."""
        return self.manager.describe()
