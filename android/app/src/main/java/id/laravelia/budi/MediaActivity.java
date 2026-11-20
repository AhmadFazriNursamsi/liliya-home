package id.laravelia.budi;

import android.app.Activity;
import android.content.Intent;
import android.database.Cursor;
import android.graphics.Color;
import android.graphics.Typeface;
import android.media.AudioManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.OpenableColumns;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.FrameLayout;
import android.widget.TextView;

import androidx.annotation.OptIn;
import androidx.media3.common.AudioAttributes;
import androidx.media3.common.C;
import androidx.media3.common.MediaItem;
import androidx.media3.common.PlaybackException;
import androidx.media3.common.Player;
import androidx.media3.common.Tracks;
import androidx.media3.common.util.UnstableApi;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.ui.PlayerView;

import java.util.Locale;

/**
 * Memutar video/musik dari aplikasi lain (WhatsApp, File Manager, Galeri: "Buka dengan" / "Bagikan" → Liliya)
 * langsung di HP, tanpa diunggah ke STB. Tombol kembali menutup layar ini sehingga pengguna kembali ke aplikasi asal.
 */
@OptIn(markerClass = UnstableApi.class)
public class MediaActivity extends Activity {

    private FrameLayout root;
    private PlayerView playerView;
    private TextView titleView, messageView;
    private ExoPlayer player;

    private Uri mediaUri;
    private String mediaName = "media";
    private String mediaMime;
    private boolean audioOnly;
    private boolean resumeOnStart;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setVolumeControlStream(AudioManager.STREAM_MUSIC);
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        setContentView(root);

        playerView = new PlayerView(this);
        playerView.setShowNextButton(false);
        playerView.setShowPreviousButton(false);
        playerView.setKeepScreenOn(true);
        root.addView(playerView, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        titleView = text(16, true);
        titleView.setMaxLines(2);
        titleView.setEllipsize(TextUtils.TruncateAt.MIDDLE);
        titleView.setPadding(dp(20), dp(16), dp(20), dp(16));
        titleView.setBackgroundColor(Color.parseColor("#66000000"));
        root.addView(titleView, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP));

        messageView = text(15, false);
        messageView.setPadding(dp(32), 0, dp(32), 0);
        messageView.setVisibility(View.GONE);
        root.addView(messageView, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER));

        // Judul ikut muncul/hilang bersama tombol kontrol (video); untuk musik selalu tampil
        playerView.setControllerVisibilityListener((PlayerView.ControllerVisibilityListener) visibility -> {
            if (!audioOnly) titleView.setVisibility(visibility);
        });

        if (!readIntent(getIntent())) {
            showMessage("Tidak ada video atau musik.\nBuka file dari WhatsApp atau File Manager, lalu pilih Buka dengan → Liliya.");
            return;
        }
        titleView.setText(mediaName);
        audioOnly = isAudioName();
        startPlayer();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        if (readIntent(intent)) {
            titleView.setText(mediaName);
            audioOnly = isAudioName();
            messageView.setVisibility(View.GONE);
            releasePlayer();
            startPlayer();
        }
    }

    // ---------------- File dari intent ----------------

    private boolean readIntent(Intent intent) {
        if (intent == null) return false;
        Uri uri = null;
        if (Intent.ACTION_VIEW.equals(intent.getAction())) {
            uri = intent.getData();
        } else if (Intent.ACTION_SEND.equals(intent.getAction())) {
            uri = Build.VERSION.SDK_INT >= 33
                    ? intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri.class)
                    : intent.getParcelableExtra(Intent.EXTRA_STREAM);
        }
        if (uri == null) return false;
        // Hanya content:// (SAF/penyedia berkas). file:// bisa menunjuk data privat aplikasi ini.
        if (!"content".equals(uri.getScheme())) return false;
        mediaUri = uri;
        mediaName = "media";
        try (Cursor c = getContentResolver().query(uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (c != null && c.moveToFirst()) {
                int n = c.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (n >= 0 && !c.isNull(n)) mediaName = c.getString(n);
            }
        } catch (Exception ignored) {
            // Sebagian penyedia tidak mendukung query: pakai nama dari URI
        }
        if ("media".equals(mediaName) && uri.getLastPathSegment() != null) mediaName = uri.getLastPathSegment();
        mediaMime = intent.getType();
        if (mediaMime == null || mediaMime.contains("*")) mediaMime = getContentResolver().getType(uri);
        return true;
    }

    private boolean isAudioName() {
        if (mediaMime != null && (mediaMime.startsWith("audio/") || mediaMime.startsWith("video/"))) {
            return mediaMime.startsWith("audio/");
        }
        String n = mediaName.toLowerCase(Locale.ROOT);
        return n.endsWith(".mp3") || n.endsWith(".m4a") || n.endsWith(".aac") || n.endsWith(".ogg")
                || n.endsWith(".opus") || n.endsWith(".wav") || n.endsWith(".flac") || n.endsWith(".amr");
    }

    // ---------------- Pemutar ----------------

    private void startPlayer() {
        player = new ExoPlayer.Builder(this).build();
        // Jeda otomatis saat ada telepon/aplikasi lain memutar suara, dan saat headset dicabut
        player.setAudioAttributes(new AudioAttributes.Builder()
                .setUsage(C.USAGE_MEDIA)
                .setContentType(audioOnly ? C.AUDIO_CONTENT_TYPE_MUSIC : C.AUDIO_CONTENT_TYPE_MOVIE)
                .build(), true);
        player.setHandleAudioBecomingNoisy(true);
        player.addListener(new Player.Listener() {
            @Override
            public void onTracksChanged(Tracks tracks) {
                if (!tracks.isEmpty()) applyMode(!tracks.containsType(C.TRACK_TYPE_VIDEO));
            }

            @Override
            public void onPlayerError(PlaybackException error) {
                showMessage("Tidak bisa memutar file ini.\nFormatnya mungkin tidak didukung atau file rusak.");
            }
        });
        playerView.setPlayer(player);
        applyMode(audioOnly);
        player.setMediaItem(MediaItem.fromUri(mediaUri));
        player.prepare();
        player.play();
    }

    private void applyMode(boolean audio) {
        audioOnly = audio;
        if (audio) {
            // Musik: tidak ada gambar, jadi judul dan tombol kontrol selalu tampil
            playerView.setControllerShowTimeoutMs(0);
            playerView.setControllerHideOnTouch(false);
            playerView.showController();
            titleView.setVisibility(View.VISIBLE);
            titleView.setBackgroundColor(Color.TRANSPARENT);
            titleView.setGravity(Gravity.CENTER);
            ((FrameLayout.LayoutParams) titleView.getLayoutParams()).gravity = Gravity.CENTER;
            titleView.requestLayout();
            getWindow().clearFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN);
        } else {
            playerView.setControllerShowTimeoutMs(3000);
            playerView.setControllerHideOnTouch(true);
            getWindow().addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN);
        }
    }

    private void showMessage(String msg) {
        messageView.setText(msg);
        messageView.setVisibility(View.VISIBLE);
    }

    private void releasePlayer() {
        if (player != null) {
            playerView.setPlayer(null);
            player.release();
            player = null;
        }
    }

    @Override
    protected void onStart() {
        super.onStart();
        if (player != null && resumeOnStart) player.play();
        resumeOnStart = false;
    }

    @Override
    protected void onStop() {
        super.onStop();
        // Layar dimatikan / pindah aplikasi: jeda, lalu lanjut saat kembali
        if (player != null && player.isPlaying()) {
            resumeOnStart = true;
            player.pause();
        }
    }

    @Override
    protected void onDestroy() {
        releasePlayer();
        super.onDestroy();
    }

    private TextView text(int sp, boolean bold) {
        TextView t = new TextView(this);
        t.setTextSize(sp);
        t.setTextColor(Color.WHITE);
        t.setGravity(Gravity.CENTER);
        if (bold) t.setTypeface(Typeface.DEFAULT_BOLD);
        return t;
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
