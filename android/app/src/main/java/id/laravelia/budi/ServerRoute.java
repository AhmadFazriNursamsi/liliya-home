package id.laravelia.budi;

import android.content.Context;
import android.content.SharedPreferences;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Alamat server Liliya: langsung lewat WiFi rumah bila STB terjangkau (jauh lebih cepat), selain itu
 * lewat Cloudflare. Dipakai bersama oleh MainActivity dan DocumentActivity.
 */
final class ServerRoute {
    static final String PREFS = "budi";
    static final String KEY_URL = "server_url";
    static final String DEFAULT_URL = "https://smarthome.laravelia.my.id";
    static final String LAN_URL = "http://192.168.1.5:8080";
    private static final String OLD_LAN_URL = "http://192.168.1.3:8080";   // IP STB sebelum 24 Okt 2026

    private ServerRoute() { }

    /** Alamat yang diatur pengguna, atau DEFAULT_URL untuk mode otomatis. */
    static String serverUrl(Context context) {
        SharedPreferences p = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String url = p.getString(KEY_URL, DEFAULT_URL);
        if (OLD_LAN_URL.equals(url)) {
            url = LAN_URL;
            p.edit().putString(KEY_URL, url).apply();
        }
        return url;
    }

    /** Mode otomatis aktif selama pengguna tidak mengisi alamat server sendiri. */
    static boolean autoRoute(Context context) {
        return DEFAULT_URL.equals(serverUrl(context));
    }

    /** Alamat yang dipakai sekarang. Memeriksa jaringan, jadi jangan dipanggil di UI thread. */
    static String resolveBlocking(Context context) {
        if (!autoRoute(context)) return serverUrl(context);
        return isLiliyaReachable(LAN_URL) ? LAN_URL : DEFAULT_URL;
    }

    static boolean isLiliyaReachable(String base) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(base + "/").openConnection();
            c.setConnectTimeout(1200);
            c.setReadTimeout(1500);
            c.setInstanceFollowRedirects(false);
            if (c.getResponseCode() != 200) return false;
            // Pastikan benar-benar server Liliya, bukan perangkat lain yang kebetulan memakai IP sama
            byte[] buf = new byte[2048];
            try (InputStream in = c.getInputStream()) {
                int n = in.read(buf);
                return n > 0 && new String(buf, 0, n, "UTF-8").contains("Liliya");
            }
        } catch (Exception e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
    }
}
