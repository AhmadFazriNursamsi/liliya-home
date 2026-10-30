package id.laravelia.budi;

import android.app.Activity;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.OpenableColumns;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.URLUtil;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.FileNotFoundException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

/**
 * Menerima dokumen dari aplikasi lain (WhatsApp: "Buka dengan" / "Bagikan" → Liliya), mengunggahnya
 * ke server Liliya di STB, lalu menampilkannya lewat penampil /docs/{id} (LibreOffice + pdf.js) tanpa iklan.
 * Tombol kembali menutup layar ini sehingga pengguna langsung kembali ke WhatsApp.
 */
public class DocumentActivity extends Activity {

    private static final long MAX_BYTES = 50L * 1024 * 1024;   // sama dengan batas server (docs.max_upload_mb)
    private static final String RESUME_PATH = "/docs/login-ok";

    private static final Set<String> WORD = new HashSet<>(Arrays.asList("doc", "docx", "docm", "dot", "dotx", "odt", "ott", "rtf", "txt"));
    private static final Set<String> SHEET = new HashSet<>(Arrays.asList("xls", "xlsx", "xlsm", "xlsb", "ods", "csv", "tsv"));
    private static final Set<String> SLIDES = new HashSet<>(Arrays.asList("ppt", "pptx", "pptm", "pps", "ppsx", "odp"));

    private FrameLayout root;
    private WebView webView;
    private View card;
    private TextView badge, nameView, statusView;
    private ProgressBar progress;
    private Button primaryBtn, secondaryBtn;

    private Uri docUri;
    private String docName = "dokumen";
    private String docMime;
    private long docSize = -1;
    private String base;
    private volatile boolean cancelled;
    private boolean awaitingLogin;
    private boolean viewerShown;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.parseColor("#EFE6DA"));
        root.setFitsSystemWindows(true);
        setContentView(root);

        webView = new WebView(this);
        webView.setVisibility(View.GONE);
        root.addView(webView, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setupWebView();
        buildCard();

        if (!readIntent(getIntent())) {
            showProblem("Tidak ada dokumen", "Buka dokumen dari WhatsApp, lalu pilih Buka dengan → Liliya.", false);
            return;
        }
        start(true);
    }

    // ---------------- Dokumen dari intent ----------------

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
        docUri = uri;
        docMime = intent.getType();
        if (docMime == null || docMime.contains("*")) {
            String t = getContentResolver().getType(uri);
            if (t != null) docMime = t;
        }
        try (Cursor c = getContentResolver().query(uri, new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE}, null, null, null)) {
            if (c != null && c.moveToFirst()) {
                int n = c.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                int s = c.getColumnIndex(OpenableColumns.SIZE);
                if (n >= 0 && !c.isNull(n)) docName = c.getString(n);
                if (s >= 0 && !c.isNull(s)) docSize = c.getLong(s);
            }
        } catch (Exception ignored) {
            // Sebagian penyedia tidak mendukung query: pakai nama dari URI
        }
        if ("dokumen".equals(docName) && uri.getLastPathSegment() != null) docName = uri.getLastPathSegment();
        return true;
    }

    private String ext() {
        int dot = docName.lastIndexOf('.');
        return dot >= 0 ? docName.substring(dot + 1).toLowerCase(Locale.ROOT) : "";
    }

    // ---------------- Unggah ----------------

    private void start(boolean resolve) {
        cancelled = false;
        if (docSize > MAX_BYTES) {
            showProblem("File terlalu besar", "Maksimal 50 MB. Ukuran file ini " + (docSize / 1048576) + " MB.", false);
            return;
        }
        showProgress("Menghubungkan ke Liliya…", -1);
        new Thread(() -> {
            if (resolve || base == null) base = ServerRoute.resolveBlocking(this);
            upload();
        }).start();
    }

    private void upload() {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(base + "/api/docs").openConnection();
            c.setRequestMethod("POST");
            c.setDoOutput(true);
            c.setConnectTimeout(10000);
            c.setReadTimeout(120000);
            c.setInstanceFollowRedirects(false);
            c.setChunkedStreamingMode(64 * 1024);   // ukuran dari penyedia tidak selalu akurat
            String cookie = CookieManager.getInstance().getCookie(base);
            if (cookie != null) c.setRequestProperty("Cookie", cookie);
            c.setRequestProperty("Content-Type", docMime != null ? docMime : "application/octet-stream");
            c.setRequestProperty("X-Filename", URLEncoder.encode(docName, "UTF-8").replace("+", "%20"));
            c.setRequestProperty("User-Agent", "BudiAndroid/" + appVersion());

            long sent = 0;
            int lastPct = -1;
            try (InputStream in = getContentResolver().openInputStream(docUri); OutputStream out = c.getOutputStream()) {
                if (in == null) throw new FileNotFoundException();
                byte[] buf = new byte[64 * 1024];
                int n;
                while ((n = in.read(buf)) > 0) {
                    if (cancelled) return;
                    out.write(buf, 0, n);
                    sent += n;
                    if (sent > MAX_BYTES) {
                        ui(() -> showProblem("File terlalu besar", "Maksimal 50 MB.", false));
                        return;
                    }
                    int pct = docSize > 0 ? (int) Math.min(100, sent * 100 / docSize) : -1;
                    if (pct != lastPct) {
                        lastPct = pct;
                        long s = sent;
                        ui(() -> showProgress(pct >= 0 ? "Mengirim ke Liliya… " + pct + "%" : "Mengirim ke Liliya… " + (s / 1024) + " KB", pct));
                    }
                }
            }
            ui(() -> showProgress("Menyiapkan dokumen…", -1));

            int code = c.getResponseCode();
            String body = readBody(code >= 400 ? c.getErrorStream() : c.getInputStream());
            if (cancelled) return;
            if (code == 401) {
                ui(this::promptLogin);
            } else if (code == 200 || code == 201) {
                String id = new JSONObject(body).getString("id");
                ui(() -> openViewer(id));
            } else {
                String msg = "Server menjawab kode " + code + ".";
                try { msg = new JSONObject(body).optString("error", msg); } catch (Exception ignored) { }
                String m = msg;
                ui(() -> showProblem("Dokumen tidak bisa dibuka", m, code >= 500));
            }
        } catch (SecurityException | FileNotFoundException e) {
            ui(() -> showProblem("File tidak bisa dibaca", "Liliya tidak mendapat izin membaca file ini. Coba lewat tombol Bagikan di WhatsApp.", false));
        } catch (Exception e) {
            if (cancelled) return;
            String where = ServerRoute.LAN_URL.equals(base) ? "lewat WiFi rumah" : "lewat internet";
            ui(() -> showProblem("Liliya tidak terjangkau", "Gagal terhubung ke server " + where + ".\n" + e.getMessage(), true));
        } finally {
            if (c != null) c.disconnect();
        }
    }

    private static String readBody(InputStream in) {
        if (in == null) return "";
        try (InputStream s = in) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = s.read(buf)) > 0) out.write(buf, 0, n);
            return out.toString("UTF-8");
        } catch (Exception e) {
            return "";
        }
    }

    /** Lewat internet tanpa sesi: tampilkan halaman login, unggahan dilanjutkan setelah berhasil masuk. */
    private void promptLogin() {
        awaitingLogin = true;
        card.setVisibility(View.GONE);
        webView.setVisibility(View.VISIBLE);
        webView.loadUrl(base + "/login?next=" + Uri.encode(RESUME_PATH));
        Toast.makeText(this, "Masuk dulu untuk membuka dokumen", Toast.LENGTH_SHORT).show();
    }

    private void openViewer(String id) {
        viewerShown = true;
        card.setVisibility(View.GONE);
        webView.setVisibility(View.VISIBLE);
        webView.loadUrl(base + "/docs/" + Uri.encode(id) + "?app=1");
    }

    // ---------------- Tampilan status (native, tampil instan) ----------------

    private void buildCard() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER_HORIZONTAL);
        box.setPadding(dp(24), dp(28), dp(24), dp(24));
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(Color.WHITE);
        bg.setCornerRadius(dp(22));
        box.setBackground(bg);
        box.setElevation(dp(3));

        badge = new TextView(this);
        badge.setTextColor(Color.WHITE);
        badge.setTextSize(13);
        badge.setTypeface(Typeface.DEFAULT_BOLD);
        badge.setGravity(Gravity.CENTER);
        box.addView(badge, new LinearLayout.LayoutParams(dp(60), dp(60)));

        nameView = text(17, "#3A241C", true);
        nameView.setPadding(0, dp(16), 0, dp(4));
        nameView.setMaxLines(2);
        nameView.setEllipsize(android.text.TextUtils.TruncateAt.MIDDLE);
        box.addView(nameView);

        statusView = text(14, "#85665A", false);
        box.addView(statusView);

        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        progress.setProgressTintList(android.content.res.ColorStateList.valueOf(Color.parseColor("#F7996A")));
        progress.setIndeterminateTintList(android.content.res.ColorStateList.valueOf(Color.parseColor("#F7996A")));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(10));
        lp.topMargin = dp(18);
        box.addView(progress, lp);

        LinearLayout row = new LinearLayout(this);
        row.setGravity(Gravity.CENTER);
        row.setPadding(0, dp(16), 0, 0);
        secondaryBtn = button("Batal", false);
        primaryBtn = button("Coba lagi", true);
        row.addView(secondaryBtn);
        row.addView(primaryBtn);
        box.addView(row);

        secondaryBtn.setOnClickListener(v -> { cancelled = true; finish(); });
        primaryBtn.setOnClickListener(v -> start(true));

        FrameLayout.LayoutParams fp = new FrameLayout.LayoutParams(Math.min(dp(380), getResources().getDisplayMetrics().widthPixels - dp(40)),
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER);
        card = box;
        root.addView(card, fp);
    }

    private TextView text(int sp, String color, boolean bold) {
        TextView t = new TextView(this);
        t.setTextSize(sp);
        t.setTextColor(Color.parseColor(color));
        t.setGravity(Gravity.CENTER);
        if (bold) t.setTypeface(Typeface.DEFAULT_BOLD);
        return t;
    }

    private Button button(String label, boolean primary) {
        Button b = new Button(this);
        b.setText(label);
        b.setAllCaps(false);
        b.setTextColor(Color.parseColor("#3A241C"));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(999));
        bg.setColor(Color.parseColor(primary ? "#FFB88E" : "#FFFFFF"));
        bg.setStroke(dp(1), Color.parseColor(primary ? "#FFB88E" : "#E3CDB6"));
        b.setBackground(bg);
        b.setPadding(dp(20), 0, dp(20), 0);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(44));
        lp.setMargins(dp(4), 0, dp(4), 0);
        b.setLayoutParams(lp);
        return b;
    }

    private void paintBadge() {
        String e = ext();
        String color = "#8C8C8C";
        if (WORD.contains(e)) color = "#2B5797";
        else if (SHEET.contains(e)) color = "#1E7145";
        else if (SLIDES.contains(e)) color = "#D04423";
        else if ("pdf".equals(e)) color = "#C9302C";
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(Color.parseColor(color));
        bg.setCornerRadius(dp(16));
        badge.setBackground(bg);
        badge.setText(e.isEmpty() ? "DOC" : e.toUpperCase(Locale.ROOT).substring(0, Math.min(4, e.length())));
        nameView.setText(docName);
    }

    private void showProgress(String status, int pct) {
        paintBadge();
        card.setVisibility(View.VISIBLE);
        statusView.setText(status);
        progress.setVisibility(View.VISIBLE);
        progress.setIndeterminate(pct < 0);
        if (pct >= 0) progress.setProgress(pct);
        primaryBtn.setVisibility(View.GONE);
        secondaryBtn.setText("Batal");
        secondaryBtn.setVisibility(View.VISIBLE);
    }

    private void showProblem(String title, String message, boolean canRetry) {
        paintBadge();
        if (docUri == null) nameView.setText(title);
        card.setVisibility(View.VISIBLE);
        webView.setVisibility(View.GONE);
        statusView.setText(docUri == null ? message : title + "\n" + message);
        progress.setVisibility(View.GONE);
        primaryBtn.setVisibility(canRetry ? View.VISIBLE : View.GONE);
        secondaryBtn.setText("Tutup");
        secondaryBtn.setVisibility(View.VISIBLE);
    }

    private void ui(Runnable r) {
        if (!cancelled) runOnUiThread(r);
    }

    // ---------------- WebView (penampil & login) ----------------

    private void setupWebView() {
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setSupportMultipleWindows(false);
        s.setUserAgentString(s.getUserAgentString() + " BudiAndroid/" + appVersion());
        webView.setBackgroundColor(Color.parseColor("#EFE6DA"));
        CookieManager.getInstance().setAcceptCookie(true);
        webView.addJavascriptInterface(new Bridge(), "LiliyaDoc");

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                Uri server = Uri.parse(base != null ? base : ServerRoute.DEFAULT_URL);
                if (uri.getHost() != null && uri.getHost().equalsIgnoreCase(server.getHost()) && uri.getPort() == server.getPort()) {
                    return false;
                }
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, uri));
                } catch (ActivityNotFoundException e) {
                    Toast.makeText(DocumentActivity.this, "Tidak ada aplikasi untuk membuka link ini", Toast.LENGTH_SHORT).show();
                }
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                CookieManager.getInstance().flush();
                if (awaitingLogin && url != null && url.contains(RESUME_PATH)) {
                    awaitingLogin = false;
                    webView.setVisibility(View.GONE);
                    start(false);
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    showProblem("Liliya tidak terjangkau", String.valueOf(error.getDescription()), true);
                }
            }
        });

        webView.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {
            try {
                String fileName = URLUtil.guessFileName(url, contentDisposition, mimeType);
                DownloadManager.Request req = new DownloadManager.Request(Uri.parse(url));
                req.addRequestHeader("Cookie", CookieManager.getInstance().getCookie(url));
                req.addRequestHeader("User-Agent", userAgent);
                req.setTitle(fileName);
                req.setDescription("Dokumen Liliya");
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

    private class Bridge {
        @JavascriptInterface
        public void close() { runOnUiThread(DocumentActivity.this::finish); }
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

    // ---------------- Lifecycle ----------------

    @Override
    public void onBackPressed() {
        if (webView.getVisibility() == View.VISIBLE && webView.canGoBack() && !viewerShown) {
            webView.goBack();
        } else {
            cancelled = true;
            super.onBackPressed();
        }
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
        cancelled = true;
        webView.destroy();
        super.onDestroy();
    }
}
