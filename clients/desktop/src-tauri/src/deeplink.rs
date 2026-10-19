//! `weave://setup?server=<url>` -- the link Weave-Chat's "Desktop-App
//! einrichten" button opens so the app picks up the server address without
//! typing. Only the address travels in the link, never a token or code, and
//! the app asks the user before saving it: any web page can open a
//! `weave://` link, so the link alone must never change the configuration.

use crate::settings::{is_secure_server_url, normalize_server_url};

/// The URL scheme registered for the app (tauri.conf.json, `plugins.deep-link`).
pub const SCHEME: &str = "weave";

/// The server address proposed by a `weave://setup?server=...` link, already
/// normalised and checked like a typed address. Anything else -- another
/// action, a missing or insecure address, a stray token parameter -- is
/// rejected with a message for the log.
pub fn parse_setup_url(link: &str) -> Result<String, String> {
    let url = url::Url::parse(link.trim()).map_err(|e| format!("ungültiger Link: {e}"))?;
    if url.scheme() != SCHEME {
        return Err(format!("unbekanntes Schema „{}“", url.scheme()));
    }
    if url.host_str() != Some("setup") || !matches!(url.path(), "" | "/") {
        return Err(format!("unbekannte Aktion „{}{}“", url.host_str().unwrap_or(""), url.path()));
    }
    let mut server = None;
    for (key, value) in url.query_pairs() {
        match key.as_ref() {
            "server" => server = Some(value.into_owned()),
            other => return Err(format!("unerwarteter Parameter „{other}“")),
        }
    }
    let server = normalize_server_url(&server.ok_or("Parameter „server“ fehlt")?);
    if server.is_empty() || !is_secure_server_url(&server) {
        return Err("die Adresse muss mit https:// beginnen (http:// nur für localhost)".into());
    }
    Ok(server)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_a_secure_server_and_normalises_it() {
        assert_eq!(parse_setup_url("weave://setup?server=https%3A%2F%2Fweave.example.com%2Fapi%2F"), Ok("https://weave.example.com/api".into()));
        assert_eq!(parse_setup_url("weave://setup/?server=http://localhost:8000"), Ok("http://localhost:8000".into()));
    }

    #[test]
    fn rejects_insecure_missing_or_foreign_links() {
        assert!(parse_setup_url("weave://setup?server=http://weave.example.com").is_err());
        assert!(parse_setup_url("weave://setup?server=").is_err());
        assert!(parse_setup_url("weave://setup").is_err());
        assert!(parse_setup_url("weave://login?server=https://weave.example.com").is_err());
        assert!(parse_setup_url("weave://setup/extra?server=https://weave.example.com").is_err());
        assert!(parse_setup_url("https://setup?server=https://weave.example.com").is_err());
        assert!(parse_setup_url("not a url").is_err());
    }

    #[test]
    fn rejects_any_parameter_beyond_the_server() {
        assert!(parse_setup_url("weave://setup?server=https://weave.example.com&token=abc").is_err());
    }
}
