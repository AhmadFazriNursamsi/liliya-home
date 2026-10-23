package id.laravelia.budi;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ActivityInfo;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.text.InputType;
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

import org.json.JSONObject;

import java.util.ArrayList;

/**
 * Aplikasi Liliya: membungkus dashboard server BUDI (asisten, saklar rumah & CCTV) dalam WebView,
 * ditambah pengenal suara native Android karena WebView tidak mendukung Web Speech API.
 */
public class MainActivity extends Activity {

    private static final String PREFS = "budi";
    private static final String KEY_URL = "server_url";
    private static final String DEFAULT_URL = "https://smarthome.laravelia.my.id";
    private static final String LAN_URL = "http://192.168.1.3:8080";
    private static final int REQ_MIC = 1;

    private FrameLayout root;
    private WebView webView;
    private View errorView;
    private View customView;
    private WebChromeClient.CustomViewCallback customViewCallback;
    private SpeechRecognizer recognizer;
    private PermissionRequest pendingWebPermission;
    private boolean pendingSpeechStart;

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
            webView.loadUrl(getServerUrl());
        }
    }

    // ---------------- WebView ----------------

    private void setupWebView() {
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setSupportMultipleWindows(false);
        s.setUserAgentString(s.getUserAgentString() + " BudiAndroid/" + appVersion());
        webView.setBackgroundColor(Color.parseColor("#FBF6EE"));

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
                req.setDescription("Rekaman CCTV Liliya");
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
        Uri server = Uri.parse(getServerUrl());
        String host = uri.getHost();
        return host != null && host.equalsIgnoreCase(server.getHost()) && uri.getPort() == server.getPort();
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
        msg.setText(message + "\n\nServer: " + getServerUrl());
        msg.setTextColor(Color.parseColor("#85665A"));
        msg.setGravity(Gravity.CENTER);
        msg.setPadding(0, dp(12), 0, dp(24));
        box.addView(msg);

        Button retry = new Button(this);
        retry.setText("Coba Lagi");
        retry.setOnClickListener(v -> {
            hideError();
            webView.loadUrl(getServerUrl());
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
                .setMessage("Dari mana saja: " + DEFAULT_URL + "\nWiFi rumah: " + LAN_URL)
                .setView(wrap)
                .setPositiveButton("Simpan", (d, w) -> saveServerUrl(input.getText().toString()))
                .setNeutralButton("Pakai WiFi Rumah", (d, w) -> saveServerUrl(LAN_URL))
                .setNegativeButton("Batal", null)
                .show();
    }

    private void saveServerUrl(String url) {
        url = url.trim();
        if (url.endsWith("/")) url = url.substring(0, url.length() - 1);
        if (!url.startsWith("http://") && !url.startsWith("https://")) url = "https://" + url;
        getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY_URL, url).apply();
        hideError();
        webView.loadUrl(url);
    }

    private String getServerUrl() {
        SharedPreferences p = getSharedPreferences(PREFS, MODE_PRIVATE);
        return p.getString(KEY_URL, DEFAULT_URL);
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

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
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
        public void stopSpeech() { runOnUiThread(MainActivity.this::stopSpeech); }

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
        webView.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        webView.onResume();
    }

    @Override
    protected void onDestroy() {
        if (recognizer != null) recognizer.destroy();
        webView.destroy();
        super.onDestroy();
    }
}
