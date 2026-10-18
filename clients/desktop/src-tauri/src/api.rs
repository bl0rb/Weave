//! Calls to weave-api with the signed-in session. The session token travels
//! exactly like the chat UI's: as the `weave_api_session` cookie.

use futures_util::StreamExt;
use reqwest::{Method, RequestBuilder, Response, StatusCode};
use serde_json::{json, Value};
use tauri::ipc::Channel;

/// Error text the UI recognises as "signed out".
pub const UNAUTHORIZED: &str = "unauthorized";

/// A network error as a person can act on it: the cause, not just "error
/// sending request". Walks the error's source chain for the root cause.
pub fn describe(error: &reqwest::Error) -> String {
    let mut chain = vec![error.to_string()];
    let mut source = std::error::Error::source(error);
    while let Some(cause) = source {
        chain.push(cause.to_string());
        source = cause.source();
    }
    let all = chain.join(": ").to_lowercase();
    let hint = if all.contains("dns") || all.contains("failed to lookup") || all.contains("nodename nor servname") {
        "Servername nicht gefunden (DNS). Stimmt die Adresse, und sind Sie im Firmennetz bzw. VPN?"
    } else if all.contains("certificate") || all.contains("ssl") || all.contains("tls") {
        "Dem Zertifikat des Servers wird nicht vertraut."
    } else if error.is_timeout() || all.contains("timed out") {
        "Keine Antwort vom Server (Zeitüberschreitung)."
    } else if error.is_connect() {
        "Keine Verbindung zum Server."
    } else {
        "Netzwerkfehler."
    };
    format!("{hint} ({})", chain.last().map(String::as_str).unwrap_or_default())
}

pub struct Weave {
    http: reqwest::Client,
    base: String,
    token: String,
}

impl Weave {
    pub fn new(base: &str, token: &str) -> Self {
        Weave { http: reqwest::Client::new(), base: base.to_string(), token: token.to_string() }
    }

    fn request(&self, method: Method, path: &str) -> RequestBuilder {
        self.http
            .request(method, format!("{}{}", self.base, path))
            .header(reqwest::header::COOKIE, format!("weave_api_session={}", self.token))
    }

    async fn checked(response: Result<Response, reqwest::Error>) -> Result<Response, String> {
        let response = response.map_err(|e| describe(&e))?;
        match response.status() {
            StatusCode::UNAUTHORIZED => Err(UNAUTHORIZED.into()),
            status if status.is_success() => Ok(response),
            status => {
                let detail = response
                    .json::<Value>()
                    .await
                    .ok()
                    .and_then(|v| v.get("detail").and_then(Value::as_str).map(str::to_string))
                    .unwrap_or_default();
                Err(format!("Weave meldet HTTP {}{}", status.as_u16(), if detail.is_empty() { String::new() } else { format!(": {detail}") }))
            }
        }
    }

    async fn get_json(&self, path: &str) -> Result<Value, String> {
        let response = Self::checked(self.request(Method::GET, path).send().await).await?;
        response.json().await.map_err(|e| e.to_string())
    }

    pub async fn me(&self) -> Result<Value, String> {
        self.get_json("/v1/me").await
    }

    pub async fn bots(&self) -> Result<Value, String> {
        self.get_json("/v1/bots").await
    }

    pub async fn collections(&self) -> Result<Value, String> {
        self.get_json("/v1/collections").await
    }

    pub async fn search(&self, query: &str, collection: Option<&str>) -> Result<Value, String> {
        let body = json!({ "query": query, "collection": collection, "top_k": 12 });
        let response = Self::checked(self.request(Method::POST, "/v1/search").json(&body).send().await).await?;
        response.json().await.map_err(|e| e.to_string())
    }

    pub async fn logout(&self) {
        let _ = self.request(Method::POST, "/v1/auth/logout").send().await;
    }

    /// One chat turn over `POST /v1/chat/stream`. Every SSE event
    /// (`trace`/`status`/`delta`/`sources`/`done`/`error`) is passed to
    /// `channel` as-is, after a first `{type: "conversation", id}` event.
    pub async fn chat_stream(
        &self,
        bot_id: &str,
        conversation_id: Option<&str>,
        message: &str,
        collections: Option<Vec<String>>,
        channel: &Channel<Value>,
    ) -> Result<(), String> {
        let mut body = json!({ "bot_id": bot_id, "message": message, "conversation_id": conversation_id });
        if let Some(collections) = collections {
            body["collections"] = json!(collections);
        }
        let response = Self::checked(self.request(Method::POST, "/v1/chat/stream").json(&body).send().await).await?;
        if let Some(id) = response.headers().get("x-conversation-id").and_then(|v| v.to_str().ok()) {
            let _ = channel.send(json!({ "type": "conversation", "id": id }));
        }

        let mut stream = response.bytes_stream();
        let mut buffer = String::new();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(|e| describe(&e))?;
            buffer.push_str(&String::from_utf8_lossy(&chunk).replace("\r\n", "\n"));
            while let Some(end) = buffer.find("\n\n") {
                let frame: String = buffer.drain(..end + 2).collect();
                for event in parse_frame(&frame) {
                    let _ = channel.send(event);
                }
            }
        }
        Ok(())
    }
}

/// The JSON events in one SSE frame (`data: <json>` lines).
fn parse_frame(frame: &str) -> Vec<Value> {
    frame
        .lines()
        .filter_map(|line| line.strip_prefix("data:"))
        .filter_map(|data| serde_json::from_str(data.trim()).ok())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn unknown_host_is_reported_as_dns() {
        let error = reqwest::get("https://weave-api.does-not-exist.invalid/health").await.unwrap_err();
        assert!(describe(&error).starts_with("Servername nicht gefunden (DNS)"), "{}", describe(&error));
    }

    #[test]
    fn parses_data_lines_and_skips_garbage() {
        let events = parse_frame("data: {\"type\":\"delta\",\"text\":\"Hi\"}\n\n");
        assert_eq!(events, vec![json!({"type": "delta", "text": "Hi"})]);
        assert!(parse_frame(": keepalive\n\n").is_empty());
        assert!(parse_frame("data: not json\n\n").is_empty());
    }
}
