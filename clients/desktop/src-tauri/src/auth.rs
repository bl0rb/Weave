//! Sign-in through the organisation's SSO, as a native app (RFC 8252):
//! the browser runs Weave's normal login, Weave redirects to a one-shot
//! listener on `127.0.0.1:<random port>/callback` with a one-time code, and
//! the app trades that code plus its PKCE verifier for a session token.
//! Needs `NATIVE_LOGIN_ENABLED=true` on weave-api.

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use rand::RngCore;
use sha2::{Digest, Sha256};
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

const LOGIN_TIMEOUT: Duration = Duration::from_secs(300);

pub struct Pkce {
    pub verifier: String,
    pub challenge: String,
}

pub fn pkce() -> Pkce {
    let mut bytes = [0u8; 48];
    rand::thread_rng().fill_bytes(&mut bytes);
    let verifier = URL_SAFE_NO_PAD.encode(bytes);
    let challenge = URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()));
    Pkce { verifier, challenge }
}

/// A listener on a free loopback port plus the `return_to` URL pointing at it.
pub async fn listen() -> Result<(TcpListener, String), String> {
    let listener = TcpListener::bind("127.0.0.1:0").await.map_err(|e| e.to_string())?;
    let port = listener.local_addr().map_err(|e| e.to_string())?.port();
    Ok((listener, format!("http://127.0.0.1:{port}/callback")))
}

/// The login URL on weave-api. "Sign in with Weave" (federated via
/// Weave-Ingest) is preferred; the direct OIDC path is the fallback, the
/// same order the chat UI uses. Whichever is not configured answers 404.
pub async fn login_url(server: &str, return_to: &str, challenge: &str) -> Result<String, String> {
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|e| e.to_string())?;
    for path in ["/v1/auth/ingest/login", "/v1/auth/oidc/login"] {
        let response = client.get(format!("{server}{path}")).send().await.map_err(|e| crate::api::describe(&e))?;
        if response.status().is_redirection() {
            let url = url::Url::parse_with_params(
                &format!("{server}{path}"),
                &[("return_to", return_to), ("code_challenge", challenge)],
            )
            .map_err(|e| e.to_string())?;
            return Ok(url.into());
        }
    }
    Err("Auf diesem Weave-Server ist keine SSO-Anmeldung eingerichtet.".into())
}

/// Waits for the browser to arrive at `/callback?code=…` and returns the code.
pub async fn wait_for_code(listener: TcpListener) -> Result<String, String> {
    tokio::time::timeout(LOGIN_TIMEOUT, accept_code(listener))
        .await
        .map_err(|_| "Die Anmeldung wurde nicht innerhalb von 5 Minuten abgeschlossen.".to_string())?
}

async fn accept_code(listener: TcpListener) -> Result<String, String> {
    loop {
        let (mut stream, _) = listener.accept().await.map_err(|e| e.to_string())?;
        let mut buffer = vec![0u8; 8192];
        let mut read = 0;
        while read < buffer.len() {
            let n = stream.read(&mut buffer[read..]).await.map_err(|e| e.to_string())?;
            if n == 0 {
                break;
            }
            read += n;
            if buffer[..read].windows(4).any(|w| w == b"\r\n\r\n") {
                break;
            }
        }
        let request = String::from_utf8_lossy(&buffer[..read]);
        let target = request.lines().next().and_then(|line| line.split_whitespace().nth(1)).unwrap_or("");
        let code = url::Url::parse(&format!("http://127.0.0.1{target}"))
            .ok()
            .filter(|url| url.path() == "/callback")
            .and_then(|url| url.query_pairs().find(|(k, _)| k == "code").map(|(_, v)| v.into_owned()));

        let (status, body) = match &code {
            Some(_) => ("200 OK", DONE_PAGE),
            None => ("404 Not Found", ""),
        };
        let response = format!(
            "HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
        let _ = stream.write_all(response.as_bytes()).await;
        let _ = stream.shutdown().await;
        if let Some(code) = code {
            return Ok(code);
        }
    }
}

const DONE_PAGE: &str = r#"<!doctype html><html lang="de"><meta charset="utf-8"><title>Weave</title>
<body style="font:15px -apple-system,'Segoe UI',sans-serif;display:grid;place-items:center;height:90vh;color:#1d1d1f">
<div style="text-align:center"><h1 style="font-size:20px">Bei Weave angemeldet</h1>
<p>Sie können dieses Fenster schließen und zur Weave-App zurückkehren.</p>
<p style="color:#6e6e73">Signed in to Weave. You can close this window.</p></div></body></html>"#;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pkce_challenge_is_s256_of_the_verifier() {
        let p = pkce();
        assert_eq!(p.verifier.len(), 64);
        assert_eq!(p.challenge.len(), 43);
        assert_eq!(p.challenge, URL_SAFE_NO_PAD.encode(Sha256::digest(p.verifier.as_bytes())));
    }

    #[tokio::test]
    async fn listener_returns_the_code_and_ignores_other_requests() {
        let (listener, return_to) = listen().await.unwrap();
        let wait = tokio::spawn(wait_for_code(listener));
        let client = reqwest::Client::new();
        let base = return_to.trim_end_matches("/callback");
        assert_eq!(client.get(format!("{base}/favicon.ico")).send().await.unwrap().status(), 404);
        let ok = client.get(format!("{return_to}?code=abc%2B1")).send().await.unwrap();
        assert_eq!(ok.status(), 200);
        assert_eq!(wait.await.unwrap().unwrap(), "abc+1");
    }
}
