/*
 * capture_savefamily.js — Frida capture harness for the SaveFamily / tgelec app.
 *
 * Goal: recover everything the reverse-engineered HA integration needs to talk to
 * the new mutual-TLS API (see ../SERVER_LOCKOUT.md):
 *   1. native login constants  -> NativeUtils.getAppId / getVersion / getFlag
 *   2. the mTLS CLIENT identity -> client certificate chain + private key
 *   3. the real login endpoint  -> host/port/path/params actually used
 *
 * Usage (rooted device or rooted emulator with frida-server running):
 *   frida -U -f com.tgelec.savefamily -l capture_savefamily.js --no-pause
 * then log in inside the app so the TLS handshake and login request fire.
 *
 * Notes:
 *   - The APK is Baidu-packed and may ship anti-Frida / anti-debug. If the process
 *     dies on launch, try a Frida gadget / re-packed debuggable build, or run on an
 *     older app build first. Spawn (-f) is preferred so hooks land before packing
 *     restores the real classes.
 *   - Every hook is wrapped in try/catch: class/method names vary across builds.
 *     Read the "[ ok ]" / "[skip]" lines at startup to see what attached.
 *   - Extracted PEM blocks are printed to the console. Copy them into files:
 *       client-cert.pem  (the CERTIFICATE block, full chain)
 *       client-key.pem   (the PRIVATE KEY block, if extractable)
 */

'use strict';

function b64(bytes) {
  // bytes: Java byte[] -> base64 string, wrapped at 64 cols
  const Base64 = Java.use('android.util.Base64');
  const NO_WRAP = 2;
  const s = Base64.encodeToString(bytes, NO_WRAP);
  return s.replace(/(.{64})/g, '$1\n');
}

function pemCert(der) {
  return '-----BEGIN CERTIFICATE-----\n' + b64(der) + '\n-----END CERTIFICATE-----';
}
function pemKey(der) {
  return '-----BEGIN PRIVATE KEY-----\n' + b64(der) + '\n-----END PRIVATE KEY-----';
}

function tryHook(label, fn) {
  try { fn(); console.log('[ ok ] ' + label); }
  catch (e) { console.log('[skip] ' + label + '  (' + e.message + ')'); }
}

Java.perform(function () {
  console.log('\n===== SaveFamily capture harness attached =====\n');

  // ---------------------------------------------------------------------------
  // 1) Native login constants
  // ---------------------------------------------------------------------------
  tryHook('NativeUtils.getAppId/getVersion/getFlag', function () {
    const N = Java.use('com.tgelec.aqsh.utils.NativeUtils');
    // Call them directly (they are static-ish getters). Wrap individually.
    ['getAppId', 'getVersion', 'getFlag', 'getNativeKey', 'getSignFlag'].forEach(function (m) {
      try { console.log('  NativeUtils.' + m + '() = ' + N[m]()); }
      catch (e) { /* method may not exist / need args */ }
    });
  });

  // ---------------------------------------------------------------------------
  // 2a) mTLS client identity via the Java KeyManager (most likely path for OkHttp)
  // ---------------------------------------------------------------------------
  tryHook('X509KeyManager.getCertificateChain / getPrivateKey', function () {
    // Hook every concrete X509 key manager the app installs.
    const KMF = Java.use('javax.net.ssl.KeyManagerFactory');
    const orig = KMF.getKeyManagers;
    orig.implementation = function () {
      const kms = orig.call(this);
      console.log('[tls ] KeyManagerFactory.getKeyManagers -> ' + kms.length + ' manager(s)');
      return kms;
    };

    // The chain/key are surfaced when the socket picks a client alias.
    const X509KM = Java.use('javax.net.ssl.X509KeyManager');
    // Can't hook an interface directly; hook common impls instead.
    ['com.android.org.conscrypt.KeyManagerImpl',
     'sun.security.ssl.SunX509KeyManagerImpl',
     'sun.security.ssl.X509KeyManagerImpl'].forEach(function (cls) {
      try {
        const Impl = Java.use(cls);
        Impl.getCertificateChain.implementation = function (alias) {
          const chain = this.getCertificateChain(alias);
          if (chain) {
            console.log('\n[tls ] client certificate chain (alias=' + alias + '):');
            for (let i = 0; i < chain.length; i++) {
              console.log(pemCert(chain[i].getEncoded()));
            }
          }
          return chain;
        };
        Impl.getPrivateKey.implementation = function (alias) {
          const key = this.getPrivateKey(alias);
          try {
            if (key) {
              const der = key.getEncoded();               // PKCS#8 DER if extractable
              if (der) { console.log('\n[tls ] client private key (alias=' + alias + '):'); console.log(pemKey(der)); }
              else { console.log('[tls ] private key alias=' + alias + ' is non-extractable (hardware-backed)'); }
            }
          } catch (e) { console.log('[tls ] private key not extractable: ' + e.message); }
          return key;
        };
        console.log('  hooked ' + cls);
      } catch (e) { /* class absent on this build */ }
    });
  });

  // ---------------------------------------------------------------------------
  // 2b) Dump any KeyStore the app loads (client identity may live in an asset .p12/.bks)
  // ---------------------------------------------------------------------------
  tryHook('KeyStore.load(InputStream, char[])', function () {
    const KeyStore = Java.use('java.security.KeyStore');
    const load = KeyStore.load.overload('java.io.InputStream', '[C');
    load.implementation = function (is, pw) {
      let pwStr = null;
      try { if (pw) pwStr = Java.use('java.lang.String').$new(pw); } catch (e) {}
      console.log('[ks  ] KeyStore.load type=' + this.getType() + ' password=' + pwStr);
      const r = load.call(this, is, pw);
      try {
        const aliases = this.aliases();
        while (aliases.hasMoreElements()) {
          const a = aliases.nextElement();
          console.log('[ks  ]   alias=' + a + ' isKeyEntry=' + this.isKeyEntry(a));
        }
      } catch (e) {}
      return r;
    };
  });

  // ---------------------------------------------------------------------------
  // 2c) SSLContext.init — see which KeyManagers/TrustManagers get wired in
  // ---------------------------------------------------------------------------
  tryHook('SSLContext.init', function () {
    const SSLContext = Java.use('javax.net.ssl.SSLContext');
    SSLContext.init.overload(
      '[Ljavax.net.ssl.KeyManager;', '[Ljavax.net.ssl.TrustManager;', 'java.security.SecureRandom'
    ).implementation = function (km, tm, sr) {
      console.log('[tls ] SSLContext.init keyManagers=' + (km ? km.length : 0) +
                  ' trustManagers=' + (tm ? tm.length : 0));
      return this.init(km, tm, sr);
    };
  });

  // ---------------------------------------------------------------------------
  // 3) Login endpoint + parameters (OkHttp and HttpURLConnection)
  // ---------------------------------------------------------------------------
  tryHook('OkHttp Request logging', function () {
    const Request = Java.use('okhttp3.Request');
    Request.$init.overload('okhttp3.Request$Builder').implementation = function (b) {
      const r = this.$init(b);
      try {
        const url = this.url().toString();
        if (/login|userLogin|myaqsh|:1100/i.test(url)) {
          console.log('[http] OkHttp -> ' + this.method() + ' ' + url);
          const body = this.body();
          if (body) {
            const Buffer = Java.use('okio.Buffer');
            const buf = Buffer.$new();
            body.writeTo(buf);
            console.log('[http]   body: ' + buf.readUtf8());
          }
        }
      } catch (e) {}
      return r;
    };
  });

  tryHook('HttpURLConnection host logging', function () {
    const URL = Java.use('java.net.URL');
    URL.openConnection.overload().implementation = function () {
      const c = this.openConnection();
      try {
        const s = this.toString();
        if (/login|myaqsh|:1100/i.test(s)) console.log('[http] URL.openConnection -> ' + s);
      } catch (e) {}
      return c;
    };
  });

  // ---------------------------------------------------------------------------
  // 4) Native fallback: hook libssl.so client-cert setters if present at runtime
  //    (Baidu-packed apps sometimes bundle their own BoringSSL under a renamed .so)
  // ---------------------------------------------------------------------------
  tryHook('native SSL_use_certificate / SSL_use_PrivateKey', function () {
    ['SSL_use_certificate', 'SSL_use_PrivateKey', 'SSL_CTX_use_certificate', 'SSL_CTX_use_PrivateKey']
      .forEach(function (sym) {
        const p = Module.findExportByName(null, sym);
        if (p) {
          Interceptor.attach(p, { onEnter: function () { console.log('[nat ] ' + sym + ' called'); } });
          console.log('  attached ' + sym + ' @ ' + p);
        }
      });
  });

  console.log('\n>>> Now LOG IN inside the app. Watch for [tls]/[http]/[ks] lines. <<<\n');
});
