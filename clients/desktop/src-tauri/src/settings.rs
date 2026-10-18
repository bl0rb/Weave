//! User settings (`settings.json` in the app config folder) plus values IT
//! can enforce: macOS managed preferences for `io.github.bl0rb.weave.desktop`
//! (read through CFPreferences, so `/Library/Managed Preferences` wins);
//! Windows registry values under `HKLM\SOFTWARE\Policies\Weave\Desktop`
//! (or HKCU). Supported names: `ServerUrl`, `Hotkey`.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

pub const DEFAULT_HOTKEY: &str = "CmdOrCtrl+Shift+Space";

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    pub server_url: String,
    pub hotkey: String,
    pub default_agent: String,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct Managed {
    pub server_url: Option<String>,
    pub hotkey: Option<String>,
}

impl Settings {
    pub fn load(path: &PathBuf) -> Self {
        let mut settings: Settings = std::fs::read(path)
            .ok()
            .and_then(|bytes| serde_json::from_slice(&bytes).ok())
            .unwrap_or_default();
        if settings.hotkey.is_empty() {
            settings.hotkey = DEFAULT_HOTKEY.into();
        }
        settings
    }

    pub fn save(&self, path: &PathBuf) -> Result<(), String> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
        }
        let bytes = serde_json::to_vec_pretty(self).map_err(|e| e.to_string())?;
        std::fs::write(path, bytes).map_err(|e| e.to_string())
    }

    /// The settings with every IT-enforced value applied.
    pub fn effective(&self, managed: &Managed) -> Settings {
        let mut out = self.clone();
        if let Some(url) = &managed.server_url {
            out.server_url = url.clone();
        }
        if let Some(hotkey) = &managed.hotkey {
            out.hotkey = hotkey.clone();
        }
        out.server_url = normalize_server_url(&out.server_url);
        out
    }
}

/// Trims whitespace and trailing slashes so paths can be appended.
pub fn normalize_server_url(url: &str) -> String {
    url.trim().trim_end_matches('/').to_string()
}

/// https everywhere; plain http only for a server on this machine.
pub fn is_secure_server_url(url: &str) -> bool {
    let Ok(parsed) = url::Url::parse(url) else { return false };
    match parsed.scheme() {
        "https" => parsed.host().is_some(),
        "http" => matches!(parsed.host_str(), Some("localhost" | "127.0.0.1" | "[::1]")),
        _ => false,
    }
}

impl Managed {
    pub fn load() -> Self {
        let read = |name: &str| policy::read_string(name).map(|v| v.trim().to_string()).filter(|v| !v.is_empty());
        Managed { server_url: read("ServerUrl"), hotkey: read("Hotkey") }
    }
}

#[cfg(target_os = "macos")]
mod policy {
    use core_foundation::base::TCFType;
    use core_foundation::string::CFString;
    use core_foundation::base::CFType;
    use core_foundation_sys::preferences::CFPreferencesCopyAppValue;

    pub fn read_string(name: &str) -> Option<String> {
        let key = CFString::new(name);
        let domain = CFString::new("io.github.bl0rb.weave.desktop");
        let raw = unsafe { CFPreferencesCopyAppValue(key.as_concrete_TypeRef(), domain.as_concrete_TypeRef()) };
        if raw.is_null() {
            return None;
        }
        let value = unsafe { CFType::wrap_under_create_rule(raw) };
        value.downcast::<CFString>().map(|s| s.to_string())
    }
}

#[cfg(windows)]
mod policy {
    use winreg::enums::{HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE};
    use winreg::RegKey;

    pub fn read_string(name: &str) -> Option<String> {
        // Machine policy wins over user policy.
        for hive in [HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER] {
            if let Ok(key) = RegKey::predef(hive).open_subkey(r"SOFTWARE\Policies\Weave\Desktop") {
                if let Ok(value) = key.get_value::<String, _>(name) {
                    return Some(value);
                }
            }
        }
        None
    }
}

#[cfg(not(any(target_os = "macos", windows)))]
mod policy {
    pub fn read_string(_name: &str) -> Option<String> {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_https_or_local_http_is_secure() {
        assert!(is_secure_server_url("https://weave.example.com"));
        assert!(is_secure_server_url("http://localhost:8000"));
        assert!(!is_secure_server_url("http://weave.example.com"));
        assert!(!is_secure_server_url("weave.example.com"));
    }

    #[test]
    fn managed_values_win() {
        let settings = Settings { server_url: "https://mine/".into(), hotkey: "Alt+W".into(), default_agent: String::new() };
        let managed = Managed { server_url: Some("https://it.example".into()), hotkey: None };
        let effective = settings.effective(&managed);
        assert_eq!(effective.server_url, "https://it.example");
        assert_eq!(effective.hotkey, "Alt+W");
    }
}
