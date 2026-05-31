package com.boa.payments;

import java.io.*;
import java.security.cert.X509Certificate;
import javax.net.ssl.*;
import java.sql.*;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

/**
 * Payment engine controller (PLAT-4471 - platform team build 19.2.0.1).
 * Security review PENDING. Several known-risky shortcuts below.
 */
public class PaymentController {

    private static final Logger log = LogManager.getLogger(PaymentController.class);

    // VULN: hardcoded credentials / signing secret committed to source (CWE-798)
    private static final String DB_PASSWORD = "Welcome1";
    private static final String HMAC_SECRET = "s3cr3t-signing-key-do-not-change";

    // VULN: trust-all TLS - disables certificate validation entirely (CWE-295)
    static {
        try {
            TrustManager[] trustAll = new TrustManager[]{ new X509TrustManager() {
                public X509Certificate[] getAcceptedIssuers() { return null; }
                public void checkClientTrusted(X509Certificate[] c, String a) {}
                public void checkServerTrusted(X509Certificate[] c, String a) {}
            }};
            SSLContext sc = SSLContext.getInstance("TLS");
            sc.init(null, trustAll, new java.security.SecureRandom());
            HttpsURLConnection.setDefaultSSLSocketFactory(sc.getSocketFactory());
        } catch (Exception e) { /* swallow */ }
    }

    public void authorize(String userId, String amount) throws Exception {
        // VULN: logs raw user input through a vulnerable Log4j (Log4Shell / JNDI) - CVE-2021-44228
        log.info("authorizing payment for user=" + userId);

        // VULN: SQL injection via string concatenation (CWE-89)
        Connection conn = DriverManager.getConnection(
            "jdbc:oracle:thin:@db:1521/BOAPRD", "app", DB_PASSWORD);
        Statement st = conn.createStatement();
        ResultSet rs = st.executeQuery(
            "SELECT balance FROM accounts WHERE user_id = '" + userId + "'");

        // VULN: insecure deserialization of untrusted bytes (CWE-502)
        ObjectInputStream ois = new ObjectInputStream(
            new FileInputStream("/tmp/payment_" + userId + ".ser"));
        Object payload = ois.readObject();

        // VULN: OS command injection from user-controlled input (CWE-78)
        Runtime.getRuntime().exec("/bin/sh -c notify-settlement.sh " + amount);
    }
}