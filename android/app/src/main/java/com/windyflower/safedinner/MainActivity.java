package com.windyflower.safedinner;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.View;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/**
 * SafeDinner: 사이트(GitHub Pages)를 그대로 띄우는 앱.
 * 화면·데이터는 매번 사이트에서 읽으므로, 깃에 올린 변경은 앱을 다시 설치하지 않아도 반영된다.
 */
public class MainActivity extends Activity {
    private static final String HOST = "windyflower1219-design.github.io";
    private static final String HOME = "https://" + HOST + "/Licensed-Restaurant/?app=1";
    private static final long RELOAD_AFTER_MS = 60 * 60 * 1000L;   // 1시간 넘게 백그라운드였다가 돌아오면 새로 읽기

    private WebView web;
    private long pausedAt = 0;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(Color.TRANSPARENT);
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_LAYOUT_STABLE | View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR);

        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#F5F6F8"));
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        // 사이트가 앱인지 알아보고(선택 화면 숨김, 공유·뒤로가기 연동) 앱 버전을 비교할 수 있게
        s.setUserAgentString(s.getUserAgentString() + " SafeDinnerApp/" + versionCode());

        web.addJavascriptInterface(new Bridge(), "Android");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri u = req.getUrl();
                String path = u.getPath() == null ? "" : u.getPath();
                if ("https".equals(u.getScheme()) && HOST.equals(u.getHost()) && path.startsWith("/Licensed-Restaurant")) {
                    return false;   // 우리 사이트는 앱 안에서
                }
                openExternal(u);    // 카카오맵·네이버지도·구글지도·전화·APK 내려받기는 해당 앱으로
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) {
                    String html = "<html><body style='font-family:sans-serif;text-align:center;padding:40% 24px;color:#1d2433'>"
                            + "<h3>인터넷 연결을 확인해 주세요</h3>"
                            + "<button style='padding:10px 18px;border-radius:10px;border:0;background:#26344f;color:#fff;font-size:15px'"
                            + " onclick=\"location.href='" + HOME + "'\">다시 시도</button></body></html>";
                    view.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
                }
            }
        });

        if (savedInstanceState != null) web.restoreState(savedInstanceState);
        else web.loadUrl(HOME);
    }

    private int versionCode() {
        try {
            PackageInfo p = getPackageManager().getPackageInfo(getPackageName(), 0);
            return (int) p.getLongVersionCode();
        } catch (Exception e) {
            return 0;
        }
    }

    private void openExternal(Uri u) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, u));
        } catch (ActivityNotFoundException ignored) {
        }
    }

    /** 페이지의 '링크 공유' → 안드로이드 공유창 */
    private class Bridge {
        @JavascriptInterface
        public void share(String text) {
            Intent i = new Intent(Intent.ACTION_SEND);
            i.setType("text/plain");
            i.putExtra(Intent.EXTRA_TEXT, text);
            startActivity(Intent.createChooser(i, "공유"));
        }
    }

    /** 뒤로가기: 페이지가 처리(상세 닫기 등)하면 그대로, 아니면 이전 페이지 → 앱 종료 */
    @Override
    public void onBackPressed() {
        web.evaluateJavascript("window.appBack ? window.appBack() : false", v -> {
            if ("true".equals(v)) return;
            if (web.canGoBack()) web.goBack();
            else finish();
        });
    }

    @Override
    protected void onPause() {
        super.onPause();
        pausedAt = SystemClock.elapsedRealtime();
        web.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
        if (pausedAt > 0 && SystemClock.elapsedRealtime() - pausedAt > RELOAD_AFTER_MS) web.reload();
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }
}
