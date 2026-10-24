package id.laravelia.budi;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.net.Uri;
import android.os.Build;

import androidx.annotation.NonNull;

import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Map;

/**
 * Menerima notifikasi FCM dari server Liliya (mis. gerakan CCTV).
 * Saat aplikasi di latar belakang, Android menampilkan notifikasi sendiri memakai channel "motion";
 * saat aplikasi sedang dibuka, notifikasi dibuat di sini agar tetap muncul.
 */
public class LiliyaMessagingService extends FirebaseMessagingService {

    static final String CHANNEL_MOTION = "motion";
    static final String PREF_TOKEN = "fcm_token";

    static void createChannels(Context ctx) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager nm = ctx.getSystemService(NotificationManager.class);
        NotificationChannel ch = new NotificationChannel(CHANNEL_MOTION, "Gerakan CCTV", NotificationManager.IMPORTANCE_HIGH);
        ch.setDescription("Pemberitahuan saat kamera mendeteksi gerakan atau orang");
        ch.enableVibration(true);
        ch.setLockscreenVisibility(android.app.Notification.VISIBILITY_PRIVATE);
        nm.createNotificationChannel(ch);
    }

    @Override
    public void onNewToken(@NonNull String token) {
        // Didaftarkan ke server oleh MainActivity saat halaman Liliya dibuka (butuh sesi login)
        getSharedPreferences("budi", MODE_PRIVATE).edit().putString(PREF_TOKEN, token).apply();
    }

    @Override
    public void onMessageReceived(@NonNull RemoteMessage message) {
        RemoteMessage.Notification n = message.getNotification();
        if (n == null) return;
        createChannels(this);
        Map<String, String> data = message.getData();

        Intent open = new Intent(this, MainActivity.class)
                .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        for (Map.Entry<String, String> e : data.entrySet()) open.putExtra(e.getKey(), e.getValue());
        PendingIntent pi = PendingIntent.getActivity(this, (int) System.currentTimeMillis(), open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        android.app.Notification.Builder b = new android.app.Notification.Builder(this, CHANNEL_MOTION)
                .setSmallIcon(R.drawable.ic_stat_liliya)
                .setContentTitle(n.getTitle())
                .setContentText(n.getBody())
                .setAutoCancel(true)
                .setCategory(android.app.Notification.CATEGORY_EVENT)
                .setContentIntent(pi);
        Bitmap picture = download(n.getImageUrl());
        if (picture != null) {
            b.setLargeIcon(picture).setStyle(new android.app.Notification.BigPictureStyle().bigPicture(picture));
        }
        String tag = CHANNEL_MOTION + "-" + (data.containsKey("cam") ? data.get("cam") : "");
        getSystemService(NotificationManager.class).notify(tag, 1, b.build());
    }

    private static Bitmap download(Uri uri) {
        if (uri == null) return null;
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(uri.toString()).openConnection();
            c.setConnectTimeout(5000);
            c.setReadTimeout(5000);
            try (InputStream in = c.getInputStream()) {
                return BitmapFactory.decodeStream(in);
            }
        } catch (Exception e) {
            return null;
        } finally {
            if (c != null) c.disconnect();
        }
    }
}
