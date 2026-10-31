package id.laravelia.budi;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.app.UiModeManager;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ActivityInfo;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.Color;
import android.media.MediaRecorder;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.text.InputType;
import android.util.Base64;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.URLUtil;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import com.google.firebase.FirebaseApp;
import com.google.firebase.messaging.FirebaseMessaging;

import org.json.JSONObject;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.function.Consumer;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Aplikasi Liliya: membungkus dashboard server BUDI (asisten, saklar rumah & CCTV) dalam WebView,
 * ditambah pengenal suara native Android karena WebView tidak mendukung Web Speech API.
 */
public class MainActivity extends Activity {

    private static final String PREFS = ServerRoute.PREFS;
    private static final String KEY_URL = ServerRoute.KEY_URL;
    private static final String DEFAULT_URL = ServerRoute.DEFAULT_URL;
    private static final String LAN_URL = ServerRoute.LAN_URL;
    private static final int REQ_MIC = 1;
    private static final int REQ_NOTIF = 2;

    private FrameLayout root;
    private WebView webView;
    private View errorView;
    private View customView;
    private WebChromeClient.CustomViewCallback customViewCallback;
    private SpeechRecognizer recognizer;
    private MediaRecorder talkRecorder;   // "tahan untuk bicara" ke speaker CCTV
    private File talkFile;
    private PermissionRequest pendingWebPermission;
    private boolean pendingSpeechStart;
    private String activeBase;   // alamat server yang sedang dimuat (WiFi rumah atau Cloudflare)

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.parseColor("#FBF6EE"));
        root.setFitsSystemWindows(true);
        setContentView(root);

        webView = new WebView(this);
        root.addView(webView, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setupWebView();

        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState);
        } else {
            String link = sharedLink(getIntent());
            String hash = link != null ? "/?unduh=" + Uri.encode(link) + "#unduh"
                    : opensCctv(getIntent()) ? "/#cctv" : "";
            resolveBase(base -> loadBase(base, hash));
        }

        LiliyaMessagingService.createChannels(this);
        requestNotificationPermission();
        initPush();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        // Notifikasi gerakan diketuk saat aplikasi sudah terbuka: langsung ke tab CCTV
        if (opensCctv(intent) && webView != null) {
            webView.evaluateJavascript("window.BudiHome ? BudiHome.setTab('cctv') : (location.hash = 'cctv')", null);
        }
        // Link dibagikan dari TikTok dll. saat aplikasi sudah terbuka: langsung unduh di tab Unduh
        String link = sharedLink(intent);
        if (link != null && webView != null) {
            String q = JSONObject.quote(link);
            webView.evaluateJavascript("window.BudiHome && BudiHome.unduh ? BudiHome.unduh(" + q + ")"
                    + " : location.assign('/?unduh=' + encodeURIComponent(" + q + ") + '#unduh')", null);
        }
    }

    // ---------------- Jalur otomatis: WiFi rumah vs Cloudflare ----------------

    private boolean autoRoute() {
        return ServerRoute.autoRoute(this);
    }

    /**
     * Di rumah, video & data langsung dari STB lewat WiFi (jauh lebih cepat & stabil daripada
     * memutar lewat Cloudflare). Di luar rumah memakai alamat Cloudflare.
     */
    private void resolveBase(Consumer<String> done) {
        if (!autoRoute()) {
            done.accept(getServerUrl());
            return;
        }
        new Thread(() -> {
            String base = ServerRoute.resolveBlocking(this);
            runOnUiThread(() -> done.accept(base));
        }).start();
    }

    private void loadBase(String base, String pathAndHash) {
        boolean switched = activeBase != null && !activeBase.equals(base);
        activeBase = base;
        hideError();
        webView.loadUrl(base + pathAndHash);
        if (switched || LAN_URL.equals(base)) {
            Toast.makeText(this, LAN_URL.equals(base) ? "Terhubung langsung lewat WiFi rumah" : "Terhubung lewat internet",
                    Toast.LENGTH_SHORT).show();
        }
    }

    /** Pindah jalur bila jaringan berubah (mis. keluar rumah / sampai di rumah), tetap di tab yang sama. */
    private void checkRouteChange() {
        if (!autoRoute() || activeBase == null) return;
        resolveBase(base -> {
            if (base.equals(activeBase)) return;
            String current = webView.getUrl();
            String hash = (current != null && current.contains("#")) ? "/" + current.substring(current.indexOf('#')) : "";
            loadBase(base, hash);
        });
    }

    private static boolean opensCctv(Intent intent) {
        return intent != null && "cctv".equals(intent.getStringExtra("open"));
    }

    private static final Pattern LINK = Pattern.compile("https?://\\S+", Pattern.CASE_INSENSITIVE);

    /** Link dari "Bagikan → Liliya" (TikTok mengirim teks berisi kalimat + link). */
    private static String sharedLink(Intent intent) {
        if (intent == null || !Intent.ACTION_SEND.equals(intent.getAction())) return null;
        CharSequence text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
        if (text == null) return null;
        Matcher m = LINK.matcher(text);
        return m.find() ? m.group() : null;
    }

    // ---------------- Notifikasi (FCM) ----------------

    private void requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQ_NOTIF);
        }
    }

    private static final int[] PUSH_RETRY_SEC = {3, 10, 30, 60, 120};
    private int pushAttempt;
    private boolean pushPending;

    private void initPush() {
        if (pushPending) return;
        try {
            if (FirebaseApp.getApps(this).isEmpty() && FirebaseApp.initializeApp(this) == null) return;
            pushPending = true;
            FirebaseMessaging.getInstance().getToken().addOnSuccessListener(token -> {
                pushPending = false;
                pushAttempt = 0;
                getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(LiliyaMessagingService.PREF_TOKEN, token).apply();
                registerPushToken();
            }).addOnFailureListener(e -> {
                pushPending = false;
                // SERVICE_NOT_AVAILABLE sering sementara (Layanan Google Play sibuk): coba ulang bertahap
                if (pushAttempt < PUSH_RETRY_SEC.length && webView != null) {
                    webView.postDelayed(this::initPush, PUSH_RETRY_SEC[pushAttempt++] * 1000L);
                } else {
                    pushError("Token FCM gagal", e);
                }
            });
        } catch (Exception e) {
            // APK dibangun tanpa google-services.json: notifikasi tidak tersedia, aplikasi tetap jalan
            pushError("Firebase tidak aktif", e);
        }
    }

    /** Daftarkan token FCM ke server lewat halaman yang sudah login (memakai cookie sesi WebView). */
    private void registerPushToken() {
        String token = getSharedPreferences(PREFS, MODE_PRIVATE).getString(LiliyaMessagingService.PREF_TOKEN, null);
        if (token == null || webView == null) return;
        String device = Build.MANUFACTURER + " " + Build.MODEL;
        String js = "(function(){if(location.pathname.indexOf('/login')===0)return;"
                + "fetch('/api/push/register',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},"
                + "body:JSON.stringify({token:" + JSONObject.quote(token) + ",device:" + JSONObject.quote(device) + "})})"
                + ".then(function(r){BudiAndroid.pushResult(r.status);}).catch(function(e){BudiAndroid.pushResult(-1);});})()";
        runOnUiThread(() -> webView.evaluateJavascript(js, null));
    }

    /** Tampilkan penyebab asli (bukan pembungkus ExecutionException) dalam dialog yang bisa dibaca penuh. */
    private void pushError(String title, Throwable e) {
        Throwable root = e;
        while (root.getCause() != null && root.getCause() != root) root = root.getCause();
        String detail = root.getClass().getSimpleName() + ": " + root.getMessage();
        String hint = "SERVICE_NOT_AVAILABLE".equals(root.getMessage())
                ? "\n\nDi HP Xiaomi/Redmi: Info apl Liliya → nyalakan Autostart, Penghemat baterai → Tanpa batasan, "
                + "lalu tutup dan buka lagi aplikasi." : "";
        SharedPreferences sp = getSharedPreferences(PREFS, MODE_PRIVATE);
        if (detail.equals(sp.getString("push_error", ""))) return;
        sp.edit().putString("push_error", detail).apply();
        runOnUiThread(() -> new AlertDialog.Builder(this).setTitle(title).setMessage(detail + hint)
                .setPositiveButton("OK", null).show());
    }

    /** Status pendaftaran notifikasi: Toast hanya saat berubah (gagal / berhasil pertama kali). */
    private void pushStatus(String msg) {
        SharedPreferences sp = getSharedPreferences(PREFS, MODE_PRIVATE);
        if (msg.equals(sp.getString("push_status", ""))) return;
        sp.edit().putString("push_status", msg).apply();
        runOnUiThread(() -> Toast.makeText(this, msg, Toast.LENGTH_LONG).show());
    }

    // ---------------- WebView ----------------

    private void setupWebView() {
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setSupportMultipleWindows(false);
        // Penanda "LiliyaTV" membuat dashboard menyalakan navigasi remote (tv-nav.js); HP tidak berubah
        s.setUserAgentString(s.getUserAgentString() + " BudiAndroid/" + appVersion() + (isTv() ? " LiliyaTV" : ""));
        webView.setBackgroundColor(Color.parseColor("#FBF6EE"));
        if (isTv()) {
            // Di TV tidak ada sentuhan: WebView harus memegang fokus agar tombol panah remote sampai ke halaman
            webView.setFocusable(true);
            webView.setFocusableInTouchMode(true);
            webView.requestFocus();
        }

        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.setAcceptThirdPartyCookies(webView, false);

        webView.addJavascriptInterface(new Bridge(), "BudiAndroid");

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (isOwnServer(uri)) {
                    return false;
                }
                openExternal(uri.toString());
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                CookieManager.getInstance().flush();
                registerPushToken();
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    showError("Tidak bisa terhubung ke server.\n" + error.getDescription());
                }
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onShowCustomView(View view, CustomViewCallback callback) {
                if (customView != null) {
                    callback.onCustomViewHidden();
                    return;
                }
                customView = view;
                customViewCallback = callback;
                root.addView(view, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
                webView.setVisibility(View.GONE);
                setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE);
                getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
            }

            @Override
            public void onHideCustomView() {
                exitFullscreen();
            }

            @Override
            public void onPermissionRequest(PermissionRequest request) {
                // Halaman web meminta mikrofon (getUserMedia) -> minta izin Android dulu bila perlu
                for (String r : request.getResources()) {
                    if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(r)) {
                        if (hasMicPermission()) {
                            request.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});
                        } else {
                            pendingWebPermission = request;
                            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_MIC);
                        }
                        return;
                    }
                }
                request.deny();
            }
        });

        webView.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {
            try {
                String fileName = URLUtil.guessFileName(url, contentDisposition, mimeType);
                DownloadManager.Request req = new DownloadManager.Request(Uri.parse(url));
                req.addRequestHeader("Cookie", CookieManager.getInstance().getCookie(url));
                req.addRequestHeader("User-Agent", userAgent);
                req.setTitle(fileName);
                req.setDescription(url.contains("/api/unduh/") ? "Video dari Liliya" : "Rekaman CCTV Liliya");
                req.setMimeType(mimeType);
                req.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                req.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "Liliya/" + fileName);
                ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(req);
                Toast.makeText(this, "Mengunduh " + fileName, Toast.LENGTH_SHORT).show();
            } catch (Exception e) {
                Toast.makeText(this, "Gagal mengunduh: " + e.getMessage(), Toast.LENGTH_LONG).show();
            }
        });
    }

    private boolean isOwnServer(Uri uri) {
        String host = uri.getHost();
        if (host == null) return false;
        for (String base : new String[]{activeBase, getServerUrl(), LAN_URL}) {
            if (base == null) continue;
            Uri server = Uri.parse(base);
            if (host.equalsIgnoreCase(server.getHost()) && uri.getPort() == server.getPort()) return true;
        }
        return false;
    }

    private void exitFullscreen() {
        if (customView == null) return;
        root.removeView(customView);
        customView = null;
        if (customViewCallback != null) customViewCallback.onCustomViewHidden();
        customViewCallback = null;
        webView.setVisibility(View.VISIBLE);
        setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED);
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_VISIBLE);
    }

    private void openExternal(String url) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
        } catch (ActivityNotFoundException e) {
            Toast.makeText(this, "Tidak ada aplikasi untuk membuka link ini", Toast.LENGTH_SHORT).show();
        }
    }

    // ---------------- Halaman error & pengaturan ----------------

    private void showError(String message) {
        hideError();
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        box.setPadding(dp(32), dp(32), dp(32), dp(32));
        box.setBackgroundColor(Color.parseColor("#FBF6EE"));

        TextView title = new TextView(this);
        title.setText("Liliya tidak terjangkau");
        title.setTextColor(Color.parseColor("#3A241C"));
        title.setTextSize(20);
        title.setGravity(Gravity.CENTER);
        box.addView(title);

        TextView msg = new TextView(this);
        msg.setText(message + "\n\nServer: " + (activeBase != null ? activeBase : getServerUrl()));
        msg.setTextColor(Color.parseColor("#85665A"));
        msg.setGravity(Gravity.CENTER);
        msg.setPadding(0, dp(12), 0, dp(24));
        box.addView(msg);

        Button retry = new Button(this);
        retry.setText("Coba Lagi");
        retry.setOnClickListener(v -> {
            hideError();
            resolveBase(base -> loadBase(base, ""));
        });
        box.addView(retry);

        Button settings = new Button(this);
        settings.setText("Ubah Alamat Server");
        settings.setOnClickListener(v -> showSettings());
        box.addView(settings);

        errorView = box;
        root.addView(errorView, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
    }

    private void hideError() {
        if (errorView != null) {
            root.removeView(errorView);
            errorView = null;
        }
    }

    private void showSettings() {
        EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setText(getServerUrl());
        input.setSelectAllOnFocus(true);
        FrameLayout wrap = new FrameLayout(this);
        wrap.setPadding(dp(20), dp(8), dp(20), 0);
        wrap.addView(input);

        new AlertDialog.Builder(this)
                .setTitle("Alamat Server")
                .setMessage("Otomatis (disarankan): langsung lewat WiFi rumah bila terjangkau, selain itu lewat "
                        + DEFAULT_URL + "\n\nAtau isi alamat server sendiri.")
                .setView(wrap)
                .setPositiveButton("Simpan", (d, w) -> saveServerUrl(input.getText().toString()))
                .setNeutralButton("Otomatis", (d, w) -> saveServerUrl(DEFAULT_URL))
                .setNegativeButton("Batal", null)
                .show();
    }

    private void saveServerUrl(String url) {
        url = url.trim();
        if (url.endsWith("/")) url = url.substring(0, url.length() - 1);
        if (!url.startsWith("http://") && !url.startsWith("https://")) url = "https://" + url;
        getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY_URL, url).apply();
        hideError();
        if (DEFAULT_URL.equals(url)) {
            resolveBase(base -> loadBase(base, ""));
        } else {
            activeBase = url;
            webView.loadUrl(url);
        }
    }

    private String getServerUrl() {
        return ServerRoute.serverUrl(this);
    }

    /** Berjalan di Android TV / Google TV (dikendalikan remote, bukan layar sentuh). */
    private boolean isTv() {
        UiModeManager ui = (UiModeManager) getSystemService(UI_MODE_SERVICE);
        return (ui != null && ui.getCurrentModeType() == Configuration.UI_MODE_TYPE_TELEVISION)
                || getPackageManager().hasSystemFeature(PackageManager.FEATURE_LEANBACK);
    }

    private String appVersion() {
        try {
            return getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (PackageManager.NameNotFoundException e) {
            return "1.0";
        }
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    // ---------------- Pengenal suara native ----------------

    private boolean hasMicPermission() {
        return checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
    }

    private void startSpeech() {
        if (!hasMicPermission()) {
            pendingSpeechStart = true;
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_MIC);
            return;
        }
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            jsSpeech("error", "Pengenal suara tidak tersedia. Pasang/aktifkan aplikasi Google.");
            return;
        }
        if (recognizer == null) {
            recognizer = SpeechRecognizer.createSpeechRecognizer(this);
            recognizer.setRecognitionListener(new RecognitionListener() {
                @Override public void onReadyForSpeech(Bundle params) { jsSpeech("start", null); }
                @Override public void onBeginningOfSpeech() { }
                @Override public void onRmsChanged(float rmsdB) { }
                @Override public void onBufferReceived(byte[] buffer) { }
                @Override public void onEndOfSpeech() { }
                @Override public void onEvent(int eventType, Bundle params) { }

                @Override
                public void onPartialResults(Bundle partial) {
                    String text = firstResult(partial);
                    if (text != null) jsSpeech("partial", text);
                }

                @Override
                public void onResults(Bundle results) {
                    jsSpeech("result", firstResult(results));
                }

                @Override
                public void onError(int error) {
                    String msg;
                    switch (error) {
                        case SpeechRecognizer.ERROR_NO_MATCH:
                        case SpeechRecognizer.ERROR_SPEECH_TIMEOUT:
                            msg = "Suara tidak terdengar, coba lagi.";
                            break;
                        case SpeechRecognizer.ERROR_NETWORK:
                        case SpeechRecognizer.ERROR_NETWORK_TIMEOUT:
                            msg = "Koneksi internet bermasalah untuk pengenal suara.";
                            break;
                        case SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS:
                            msg = "Izin mikrofon belum diberikan.";
                            break;
                        default:
                            msg = "Pengenal suara gagal (kode " + error + ").";
                    }
                    jsSpeech("error", msg);
                }
            });
        }
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "id-ID");
        intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        intent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1);
        recognizer.startListening(intent);
    }

    private void stopSpeech() {
        if (recognizer != null) recognizer.stopListening();
    }

    private static String firstResult(Bundle bundle) {
        if (bundle == null) return null;
        ArrayList<String> list = bundle.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        return (list != null && !list.isEmpty()) ? list.get(0) : null;
    }

    private void jsSpeech(String fn, String arg) {
        String js = "window.budiNativeSpeech && window.budiNativeSpeech." + fn + "(" + (arg == null ? "" : JSONObject.quote(arg)) + ")";
        runOnUiThread(() -> webView.evaluateJavascript(js, null));
    }

    // ---------------- Bicara lewat speaker CCTV (rekam native) ----------------

    /** Rekam suara saat tombol bicara ditahan. Native karena di WiFi rumah halaman memakai http (tanpa getUserMedia). */
    private void startTalk() {
        if (!hasMicPermission()) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_MIC);
            jsTalk("error", "Izinkan mikrofon, lalu tahan tombol bicara lagi.");
            return;
        }
        releaseTalk();
        try {
            talkFile = new File(getCacheDir(), "talk.m4a");
            MediaRecorder r = Build.VERSION.SDK_INT >= 31 ? new MediaRecorder(this) : new MediaRecorder();
            talkRecorder = r;
            // VOICE_COMMUNICATION: peredam gema & bising bawaan HP
            r.setAudioSource(MediaRecorder.AudioSource.VOICE_COMMUNICATION);
            r.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4);
            r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC);
            r.setAudioChannels(1);
            r.setAudioSamplingRate(16000);
            r.setAudioEncodingBitRate(48000);
            r.setOutputFile(talkFile.getAbsolutePath());
            r.prepare();
            r.start();
        } catch (Exception e) {
            releaseTalk();
            jsTalk("error", "Mikrofon tidak bisa dipakai: " + e.getMessage());
        }
    }

    private void stopTalk(boolean send) {
        if (talkRecorder == null) return;
        boolean recorded = true;
        try {
            talkRecorder.stop();
        } catch (RuntimeException e) {
            recorded = false;   // dilepas terlalu cepat: belum ada data suara
        }
        talkRecorder.release();
        talkRecorder = null;
        File file = talkFile;
        if (!send || !recorded) {
            file.delete();
            return;
        }
        new Thread(() -> {
            try {
                String b64 = Base64.encodeToString(Files.readAllBytes(file.toPath()), Base64.NO_WRAP);
                runOnUiThread(() -> webView.evaluateJavascript(
                        "window.budiNativeTalk && budiNativeTalk.audio('" + b64 + "','audio/mp4')", null));
            } catch (IOException e) {
                jsTalk("error", "Rekaman gagal dibaca.");
            } finally {
                file.delete();
            }
        }).start();
    }

    private void releaseTalk() {
        if (talkRecorder != null) {
            try { talkRecorder.release(); } catch (RuntimeException ignored) { }
            talkRecorder = null;
        }
        if (talkFile != null) talkFile.delete();
    }

    private void jsTalk(String fn, String arg) {
        String js = "window.budiNativeTalk && window.budiNativeTalk." + fn + "(" + JSONObject.quote(arg) + ")";
        runOnUiThread(() -> webView.evaluateJavascript(js, null));
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        if (requestCode == REQ_NOTIF) return;   // ditolak pun aplikasi tetap jalan, hanya tanpa notifikasi
        if (requestCode != REQ_MIC) return;
        boolean granted = grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED;
        if (pendingWebPermission != null) {
            if (granted) pendingWebPermission.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});
            else pendingWebPermission.deny();
            pendingWebPermission = null;
        }
        if (pendingSpeechStart) {
            pendingSpeechStart = false;
            if (granted) startSpeech();
            else jsSpeech("error", "Izin mikrofon ditolak. Aktifkan di Pengaturan Android.");
        }
    }

    // ---------------- Jembatan JavaScript ----------------

    private class Bridge {
        @JavascriptInterface
        public void startSpeech() { runOnUiThread(MainActivity.this::startSpeech); }

        @JavascriptInterface
        public void pushResult(int status) {
            pushStatus(status == 200 ? "Notifikasi gerakan: HP terdaftar" : "Pendaftaran notifikasi gagal (kode " + status + ")");
        }

        @JavascriptInterface
        public void stopSpeech() { runOnUiThread(MainActivity.this::stopSpeech); }

        @JavascriptInterface
        public void startTalk() { runOnUiThread(MainActivity.this::startTalk); }

        @JavascriptInterface
        public void stopTalk(boolean send) { runOnUiThread(() -> MainActivity.this.stopTalk(send)); }

        @JavascriptInterface
        public void openSettings() { runOnUiThread(MainActivity.this::showSettings); }

        @JavascriptInterface
        public void openExternal(String url) {
            if (url != null && (url.startsWith("http://") || url.startsWith("https://"))) {
                runOnUiThread(() -> MainActivity.this.openExternal(url));
            }
        }
    }

    // ---------------- Lifecycle ----------------

    @Override
    public void onBackPressed() {
        if (customView != null) {
            exitFullscreen();
        } else if (webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        webView.saveState(outState);
    }

    @Override
    protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush();
        stopTalk(false);
        webView.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        webView.onResume();
        checkRouteChange();
        // Belum dapat token (mis. gagal saat dibuka pertama): coba lagi tiap aplikasi dibuka kembali
        if (getSharedPreferences(PREFS, MODE_PRIVATE).getString(LiliyaMessagingService.PREF_TOKEN, null) == null) {
            pushAttempt = 0;
            initPush();
        }
    }

    @Override
    protected void onDestroy() {
        if (recognizer != null) recognizer.destroy();
        releaseTalk();
        webView.destroy();
        super.onDestroy();
    }
}
