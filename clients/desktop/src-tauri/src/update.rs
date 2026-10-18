//! In-app updates through tauri-plugin-updater. The feed (`latest.json`) is
//! published by .github/workflows/desktop.yml on the release `desktop-latest`;
//! every package is signed with the key whose public half is in
//! tauri.conf.json, so a download that does not verify is never installed.
//! IT can point the app at its own feed (`UpdateUrl`) or switch the check off
//! (`UpdateCheck = false`) when it distributes updates itself.

use serde::Serialize;
use std::sync::Mutex;
use tauri::{AppHandle, Runtime};
use tauri_plugin_updater::{Update, UpdaterExt};

#[derive(Default)]
pub struct Updates {
    inner: Mutex<Inner>,
}

#[derive(Default)]
struct Inner {
    update: Option<Update>,
    checking: bool,
    installing: bool,
    checked: bool,
    error: Option<String>,
}

#[derive(Serialize)]
pub struct UpdateView {
    pub enabled: bool,
    pub checking: bool,
    pub installing: bool,
    pub checked: bool,
    pub available: Option<String>,
    pub notes: Option<String>,
    pub error: Option<String>,
}

impl Updates {
    pub fn view(&self, enabled: bool) -> UpdateView {
        let inner = self.inner.lock().unwrap();
        UpdateView {
            enabled,
            checking: inner.checking,
            installing: inner.installing,
            checked: inner.checked,
            available: inner.update.as_ref().map(|u| u.version.clone()),
            notes: inner.update.as_ref().and_then(|u| u.body.clone()),
            error: inner.error.clone(),
        }
    }

    pub fn available_version(&self) -> Option<String> {
        self.inner.lock().unwrap().update.as_ref().map(|u| u.version.clone())
    }

    /// Asks the feed for a newer version. Returns `false` when a check is
    /// already running.
    pub async fn check<R: Runtime>(&self, app: &AppHandle<R>, feed: Option<&str>) -> bool {
        {
            let mut inner = self.inner.lock().unwrap();
            if inner.checking || inner.installing {
                return false;
            }
            inner.checking = true;
            inner.error = None;
        }
        let result = find_update(app, feed).await;
        let mut inner = self.inner.lock().unwrap();
        inner.checking = false;
        inner.checked = true;
        match result {
            Ok(update) => inner.update = update,
            Err(error) => inner.error = Some(error),
        }
        true
    }

    /// Downloads, verifies and installs the update found by `check`, then
    /// restarts the app. Only returns on failure.
    pub async fn install<R: Runtime>(&self, app: &AppHandle<R>) -> Result<(), String> {
        let update = {
            let mut inner = self.inner.lock().unwrap();
            let update = inner.update.clone().ok_or("Kein Update verfügbar.")?;
            if inner.installing {
                return Ok(());
            }
            inner.installing = true;
            inner.error = None;
            update
        };
        let result = update.download_and_install(|_, _| {}, || {}).await;
        if let Err(error) = result {
            let mut inner = self.inner.lock().unwrap();
            inner.installing = false;
            inner.error = Some(format!("Installation fehlgeschlagen: {error}"));
            return Err(error.to_string());
        }
        app.restart()
    }
}

async fn find_update<R: Runtime>(app: &AppHandle<R>, feed: Option<&str>) -> Result<Option<Update>, String> {
    let mut builder = app.updater_builder();
    if let Some(feed) = feed {
        let url = url::Url::parse(feed).map_err(|e| format!("Ungültige Update-Adresse: {e}"))?;
        builder = builder.endpoints(vec![url]).map_err(|e| e.to_string())?;
    }
    let updater = builder.build().map_err(|e| e.to_string())?;
    updater.check().await.map_err(|e| format!("Update-Prüfung fehlgeschlagen: {e}"))
}
