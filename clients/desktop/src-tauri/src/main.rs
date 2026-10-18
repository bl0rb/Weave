// Weave desktop: a tray / menu-bar app that asks Weave. Click the icon (or
// press the hotkey), pick an agent, ask or search. Sign-in through the
// organisation's SSO; the session token stays in the OS credential store.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod api;
mod auth;
mod settings;
mod update;

use api::{Weave, UNAUTHORIZED};
use serde::Serialize;
use serde_json::Value;
use settings::{Managed, Settings};
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::async_runtime::JoinHandle;
use tauri::image::Image;
use tauri::ipc::Channel;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{
    AppHandle, Emitter, LogicalSize, Manager, PhysicalPosition, State, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
    WindowEvent, Wry,
};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt as _};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
use tauri_plugin_opener::OpenerExt;
use update::{UpdateView, Updates};

const KEYRING_SERVICE: &str = "Weave Desktop";
/// Passed by the login item: start quietly in the tray, without the app window.
const AUTOSTART_ARG: &str = "--autostart";
/// First automatic update check after start, then this often.
const UPDATE_FIRST_CHECK: Duration = Duration::from_secs(20);
const UPDATE_INTERVAL: Duration = Duration::from_secs(12 * 60 * 60);
/// Logical width of the quick overlay window.
const QUICK_WIDTH: f64 = 720.0;

#[derive(Clone)]
struct Session {
    server: String,
    token: String,
    username: Option<String>,
}

struct AppState {
    settings_path: PathBuf,
    settings: Mutex<Settings>,
    managed: Managed,
    session: Mutex<Option<Session>>,
    login_task: Mutex<Option<JoinHandle<()>>>,
    chat_abort: Mutex<Option<tokio::task::AbortHandle>>,
    quick_hidden_at: Mutex<Option<Instant>>,
    updates: Updates,
    update_menu_item: Mutex<Option<MenuItem<Wry>>>,
}

impl AppState {
    fn effective(&self) -> Settings {
        self.settings.lock().unwrap().effective(&self.managed)
    }

    fn updates_enabled(&self) -> bool {
        self.managed.update_check != Some(false)
    }

    fn weave(&self) -> Result<Weave, String> {
        let session = self.session.lock().unwrap().clone().ok_or(UNAUTHORIZED)?;
        Ok(Weave::new(&session.server, &session.token))
    }
}

#[derive(Serialize)]
struct StateView {
    server_url: String,
    server_managed: bool,
    hotkey: String,
    hotkey_managed: bool,
    default_agent: String,
    autostart: bool,
    signed_in: bool,
    username: Option<String>,
    login_pending: bool,
    version: String,
}

fn keyring_entry(server: &str) -> Option<keyring::Entry> {
    keyring::Entry::new(KEYRING_SERVICE, server).ok()
}

fn notify_state(app: &AppHandle) {
    let _ = app.emit("state-changed", ());
}

/// Drops the session after weave-api rejected it, so the UI offers sign-in.
fn on_result<T>(app: &AppHandle, result: Result<T, String>) -> Result<T, String> {
    if matches!(&result, Err(e) if e == UNAUTHORIZED) {
        let state = app.state::<AppState>();
        if let Some(session) = state.session.lock().unwrap().take() {
            if let Some(entry) = keyring_entry(&session.server) {
                let _ = entry.delete_credential();
            }
        }
        notify_state(app);
    }
    result
}

// MARK: Commands

#[tauri::command]
async fn get_state(app: AppHandle, state: State<'_, AppState>) -> Result<StateView, String> {
    // Fill in the user name once per session (and notice an expired token).
    let pending = state.session.lock().unwrap().clone().filter(|s| s.username.is_none());
    if let Some(session) = pending {
        let me = on_result(&app, Weave::new(&session.server, &session.token).me().await);
        if let Ok(me) = me {
            if let Some(current) = state.session.lock().unwrap().as_mut() {
                current.username = me.get("username").and_then(Value::as_str).map(str::to_string);
            }
        }
    }
    let effective = state.effective();
    let session = state.session.lock().unwrap().clone();
    Ok(StateView {
        server_url: effective.server_url,
        server_managed: state.managed.server_url.is_some(),
        hotkey: effective.hotkey,
        hotkey_managed: state.managed.hotkey.is_some(),
        default_agent: effective.default_agent,
        autostart: app.autolaunch().is_enabled().unwrap_or(false),
        signed_in: session.is_some(),
        username: session.and_then(|s| s.username),
        login_pending: state.login_task.lock().unwrap().as_ref().is_some_and(|t| !t.inner().is_finished()),
        version: app.package_info().version.to_string(),
    })
}

#[tauri::command]
fn save_settings(
    app: AppHandle,
    state: State<'_, AppState>,
    server_url: String,
    hotkey: String,
    default_agent: String,
) -> Result<(), String> {
    let server_url = settings::normalize_server_url(&server_url);
    if !server_url.is_empty() && !settings::is_secure_server_url(&server_url) {
        return Err("Die Adresse muss mit https:// beginnen (http:// nur für localhost).".into());
    }
    let old = state.effective();
    {
        let mut current = state.settings.lock().unwrap();
        current.server_url = server_url;
        current.hotkey = hotkey;
        current.default_agent = default_agent;
        current.save(&state.settings_path)?;
    }
    let new = state.effective();
    if new.server_url != old.server_url {
        *state.session.lock().unwrap() = load_session(&new.server_url);
    }
    if new.hotkey != old.hotkey {
        register_hotkey(&app, &new.hotkey)?;
    }
    notify_state(&app);
    Ok(())
}

#[tauri::command]
fn set_autostart(app: AppHandle, enabled: bool) -> Result<(), String> {
    let launcher = app.autolaunch();
    let result = if enabled { launcher.enable() } else { launcher.disable() };
    result.map_err(|e| e.to_string())?;
    notify_state(&app);
    Ok(())
}

#[tauri::command]
async fn test_connection(server_url: String) -> Result<(), String> {
    let server_url = settings::normalize_server_url(&server_url);
    if !settings::is_secure_server_url(&server_url) {
        return Err("Die Adresse muss mit https:// beginnen (http:// nur für localhost).".into());
    }
    let response = reqwest::Client::new()
        .get(format!("{server_url}/health"))
        .timeout(Duration::from_secs(10))
        .send()
        .await
        .map_err(|e| api::describe(&e))?;
    if response.status().is_success() {
        Ok(())
    } else {
        Err(format!("Der Server antwortet mit HTTP {}.", response.status().as_u16()))
    }
}

#[tauri::command]
fn sign_in(app: AppHandle, state: State<'_, AppState>) -> Result<(), String> {
    let server = state.effective().server_url;
    if server.is_empty() {
        return Err("Bitte zuerst die Adresse des Weave-Servers eintragen.".into());
    }
    if let Some(previous) = state.login_task.lock().unwrap().take() {
        previous.abort();
    }
    let handle = app.clone();
    let task = tauri::async_runtime::spawn(async move {
        let result = run_sign_in(&handle, &server).await;
        let _ = handle.emit("login-result", result.err());
        notify_state(&handle);
    });
    *state.login_task.lock().unwrap() = Some(task);
    notify_state(&app);
    Ok(())
}

async fn run_sign_in(app: &AppHandle, server: &str) -> Result<(), String> {
    let pkce = auth::pkce();
    let (listener, return_to) = auth::listen().await?;
    let url = auth::login_url(server, &return_to, &pkce.challenge).await?;
    app.opener().open_url(url, None::<&str>).map_err(|e| e.to_string())?;
    let code = auth::wait_for_code(listener).await?;

    let response = reqwest::Client::new()
        .post(format!("{server}/v1/auth/session/exchange"))
        .json(&serde_json::json!({ "code": code, "code_verifier": pkce.verifier }))
        .send()
        .await
        .map_err(|e| api::describe(&e))?;
    if !response.status().is_success() {
        return Err("Die Anmeldung wurde abgelehnt. Ist NATIVE_LOGIN_ENABLED auf dem Server aktiv?".into());
    }
    let body: Value = response.json().await.map_err(|e| e.to_string())?;
    let token = body.get("session_token").and_then(Value::as_str).ok_or("Ungültige Antwort von Weave")?;

    if let Some(entry) = keyring_entry(server) {
        let _ = entry.set_password(token);
    }
    *app.state::<AppState>().session.lock().unwrap() =
        Some(Session { server: server.to_string(), token: token.to_string(), username: None });
    if !panel(app).is_some_and(|p| p.is_visible().unwrap_or(false)) {
        show_quick(app);
    }
    Ok(())
}

#[tauri::command]
fn cancel_sign_in(app: AppHandle, state: State<'_, AppState>) {
    if let Some(task) = state.login_task.lock().unwrap().take() {
        task.abort();
    }
    notify_state(&app);
}

#[tauri::command]
async fn sign_out(app: AppHandle, state: State<'_, AppState>) -> Result<(), String> {
    let session = state.session.lock().unwrap().take();
    if let Some(session) = session {
        Weave::new(&session.server, &session.token).logout().await;
        if let Some(entry) = keyring_entry(&session.server) {
            let _ = entry.delete_credential();
        }
    }
    notify_state(&app);
    Ok(())
}

#[tauri::command]
async fn list_bots(app: AppHandle, state: State<'_, AppState>) -> Result<Value, String> {
    let weave = on_result(&app, state.weave())?;
    on_result(&app, weave.bots().await)
}

#[tauri::command]
async fn list_collections(app: AppHandle, state: State<'_, AppState>) -> Result<Value, String> {
    let weave = on_result(&app, state.weave())?;
    on_result(&app, weave.collections().await)
}

#[tauri::command]
async fn search(app: AppHandle, state: State<'_, AppState>, query: String, collection: Option<String>) -> Result<Value, String> {
    let weave = on_result(&app, state.weave())?;
    on_result(&app, weave.search(&query, collection.as_deref()).await)
}

#[tauri::command]
async fn ask(
    app: AppHandle,
    state: State<'_, AppState>,
    bot_id: String,
    conversation_id: Option<String>,
    message: String,
    collections: Option<Vec<String>>,
    on_event: Channel<Value>,
) -> Result<(), String> {
    let weave = on_result(&app, state.weave())?;
    let task = tauri::async_runtime::spawn(async move {
        weave.chat_stream(&bot_id, conversation_id.as_deref(), &message, collections, &on_event).await
    });
    if let Some(previous) = state.chat_abort.lock().unwrap().replace(task.inner().abort_handle()) {
        previous.abort();
    }
    match task.await {
        Ok(result) => on_result(&app, result),
        Err(_) => Ok(()), // stopped by `stop_answer` or replaced by a newer question
    }
}

#[tauri::command]
fn stop_answer(state: State<'_, AppState>) {
    if let Some(task) = state.chat_abort.lock().unwrap().take() {
        task.abort();
    }
}

#[tauri::command]
fn get_update_state(state: State<'_, AppState>) -> UpdateView {
    state.updates.view(state.updates_enabled())
}

#[tauri::command]
async fn check_for_updates(app: AppHandle) {
    run_update_check(&app).await;
}

#[tauri::command]
async fn install_update(app: AppHandle, state: State<'_, AppState>) -> Result<(), String> {
    let _ = app.emit("update-changed", ());
    let result = state.updates.install(&app).await;
    let _ = app.emit("update-changed", ());
    result
}

async fn run_update_check(app: &AppHandle) {
    let state = app.state::<AppState>();
    if !state.updates_enabled() {
        return;
    }
    let notify = |app: &AppHandle| {
        let _ = app.emit("update-changed", ());
    };
    let feed = state.managed.update_url.clone();
    let task = state.updates.check(app, feed.as_deref());
    notify(app);
    if task.await {
        refresh_update_menu(app);
        notify(app);
    }
}

/// The tray entry offers the install once a newer version is known.
fn refresh_update_menu(app: &AppHandle) {
    let state = app.state::<AppState>();
    let text = match state.updates.available_version() {
        Some(version) => format!("Update auf {version} installieren …"),
        None => "Nach Updates suchen …".to_string(),
    };
    let item = state.update_menu_item.lock().unwrap().clone();
    if let Some(item) = item {
        let _ = item.set_text(text);
    }
}

#[tauri::command]
fn open_url(app: AppHandle, url: String) -> Result<(), String> {
    if !(url.starts_with("https://") || url.starts_with("http://")) {
        return Err("Nur http(s)-Links".into());
    }
    app.opener().open_url(url, None::<&str>).map_err(|e| e.to_string())
}

#[tauri::command]
fn open_settings(app: AppHandle) -> Result<(), String> {
    show_settings(&app).map_err(|e| e.to_string())
}

/// Hides whichever window called it (tray panel or quick overlay).
#[tauri::command]
fn hide_panel(window: WebviewWindow) {
    let _ = window.hide();
}

/// The quick overlay grows with its content; its top edge stays put.
#[tauri::command]
fn fit_quick(app: AppHandle, height: f64) {
    if let Some(quick) = app.get_webview_window("quick") {
        let _ = quick.set_size(LogicalSize::new(QUICK_WIDTH, height.clamp(56.0, 640.0)));
        refresh_shadow(&quick);
    }
}

/// macOS derives a transparent window's shadow from its content once; after
/// the card changes size, the shadow has to be recomputed or it keeps the old
/// outline.
fn refresh_shadow(window: &WebviewWindow) {
    #[cfg(target_os = "macos")]
    {
        let window = window.clone();
        let _ = window.clone().run_on_main_thread(move || {
            if let Ok(pointer) = window.ns_window() {
                // SAFETY: on the main thread; the pointer is the live NSWindow.
                let ns_window = unsafe { &*(pointer as *const objc2_app_kit::NSWindow) };
                ns_window.invalidateShadow();
            }
        });
    }
    #[cfg(not(target_os = "macos"))]
    let _ = window;
}

// MARK: Windows, tray, hotkey
//
// Two faces: the quick overlay (a slim bar like Claude's, opened by a click
// on the tray icon or the hotkey) and the app window `panel` with everything
// (opened when the app itself is launched, from the Dock / Start menu, or via
// "Weave öffnen" in the tray menu).

fn panel(app: &AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window("panel")
}

/// Shows the app window and brings Weave to the front.
fn show_main(app: &AppHandle) {
    let Some(panel) = panel(app) else { return };
    if let Some(quick) = app.get_webview_window("quick") {
        let _ = quick.hide();
    }
    let _ = panel.unminimize();
    let _ = panel.show();
    update_dock(app);
    let _ = panel.set_focus();
    let _ = panel.emit("panel-shown", ());
}

/// macOS: Weave is a menu-bar app; it shows in the Dock and Cmd+Tab only
/// while its app window or the settings are open.
fn update_dock(app: &AppHandle) {
    #[cfg(target_os = "macos")]
    {
        let visible = ["panel", "settings"]
            .iter()
            .filter_map(|label| app.get_webview_window(label))
            .any(|window| window.is_visible().unwrap_or(false));
        let policy = if visible { tauri::ActivationPolicy::Regular } else { tauri::ActivationPolicy::Accessory };
        let _ = app.set_activation_policy(policy);
    }
    #[cfg(not(target_os = "macos"))]
    let _ = app;
}

/// Opens the quick overlay centred on the screen with the mouse pointer,
/// a little above the middle -- where Spotlight and Claude put theirs.
fn show_quick(app: &AppHandle) {
    let Some(quick) = app.get_webview_window("quick") else { return };
    let monitor = app
        .cursor_position()
        .ok()
        .and_then(|p| app.monitor_from_point(p.x, p.y).ok().flatten())
        .or_else(|| quick.primary_monitor().ok().flatten());
    if let Some(monitor) = monitor {
        let (origin, size) = (monitor.position(), monitor.size());
        let width = (QUICK_WIDTH * monitor.scale_factor()) as i32;
        let x = origin.x + (size.width as i32 - width) / 2;
        let y = origin.y + (size.height as f64 * 0.22) as i32;
        let _ = quick.set_position(PhysicalPosition::new(x, y));
    }
    let _ = quick.show();
    refresh_shadow(&quick);
    let _ = quick.set_focus();
    let _ = quick.emit("panel-shown", ());
}

fn toggle_quick(app: &AppHandle) {
    let Some(quick) = app.get_webview_window("quick") else { return };
    // A click on the tray icon first blurs (and so hides) the open overlay;
    // that click must not re-open it right away.
    let just_hidden = app
        .state::<AppState>()
        .quick_hidden_at
        .lock()
        .unwrap()
        .is_some_and(|at| at.elapsed() < Duration::from_millis(300));
    if quick.is_visible().unwrap_or(false) {
        let _ = quick.hide();
    } else if !just_hidden {
        show_quick(app);
    }
}

/// macOS: show the window on every Space and above full-screen apps, like a
/// menu-bar popover; otherwise it would open on the desktop Space instead.
#[cfg(target_os = "macos")]
fn float_over_full_screen(window: &WebviewWindow) {
    use objc2_app_kit::{NSStatusWindowLevel, NSWindow, NSWindowCollectionBehavior};
    let Ok(pointer) = window.ns_window() else { return };
    // SAFETY: called on the main thread (setup); the pointer is the live
    // NSWindow behind this Tauri window.
    let ns_window = unsafe { &*(pointer as *const NSWindow) };
    ns_window.setCollectionBehavior(
        NSWindowCollectionBehavior::CanJoinAllSpaces
            | NSWindowCollectionBehavior::FullScreenAuxiliary
            | NSWindowCollectionBehavior::IgnoresCycle,
    );
    ns_window.setLevel(NSStatusWindowLevel);
}

#[cfg(not(target_os = "macos"))]
fn float_over_full_screen(_window: &WebviewWindow) {}

fn show_settings(app: &AppHandle) -> tauri::Result<()> {
    if let Some(quick) = app.get_webview_window("quick") {
        let _ = quick.hide();
    }
    let window = match app.get_webview_window("settings") {
        Some(window) => window,
        None => WebviewWindowBuilder::new(app, "settings", WebviewUrl::App("settings.html".into()))
            .title("Weave – Einstellungen")
            .inner_size(620.0, 660.0)
            .min_inner_size(520.0, 420.0)
            .center()
            .build()?,
    };
    window.show()?;
    update_dock(app);
    window.set_focus()
}

fn register_hotkey(app: &AppHandle, hotkey: &str) -> Result<(), String> {
    let shortcuts = app.global_shortcut();
    shortcuts.unregister_all().map_err(|e| e.to_string())?;
    if hotkey.trim().is_empty() {
        return Ok(());
    }
    shortcuts
        .register(hotkey)
        .map_err(|e| format!("Tastenkürzel „{hotkey}“ lässt sich nicht verwenden: {e}"))
}

fn load_session(server: &str) -> Option<Session> {
    if server.is_empty() {
        return None;
    }
    let token = keyring_entry(server)?.get_password().ok()?;
    Some(Session { server: server.to_string(), token, username: None })
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let state = app.state::<AppState>();
    let version = MenuItem::with_id(app, "version", format!("Weave {}", app.package_info().version), false, None::<&str>)?;
    let update_item = MenuItem::with_id(app, "update", "Nach Updates suchen …", state.updates_enabled(), None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &MenuItem::with_id(app, "ask", "Weave fragen …", true, None::<&str>)?,
            &MenuItem::with_id(app, "open", "Weave öffnen", true, None::<&str>)?,
            &MenuItem::with_id(app, "settings", "Einstellungen …", true, None::<&str>)?,
            &PredefinedMenuItem::separator(app)?,
            &version,
            &update_item,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "quit", "Weave beenden", true, None::<&str>)?,
        ],
    )?;
    *state.update_menu_item.lock().unwrap() = Some(update_item);
    // macOS: a monochrome template glyph, 36 px = the menu bar's 18 pt @2x,
    // tinted by the system. Windows has no template icons; a black glyph would
    // vanish on a dark taskbar, so it gets the coloured app icon.
    #[cfg(target_os = "macos")]
    let icon = Image::from_bytes(include_bytes!("../icons/tray.png"))?;
    #[cfg(not(target_os = "macos"))]
    let icon = Image::from_bytes(include_bytes!("../icons/32x32.png"))?;
    TrayIconBuilder::with_id("weave")
        .icon(icon)
        .icon_as_template(cfg!(target_os = "macos"))
        .tooltip("Weave")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "ask" => show_quick(app),
            "open" => show_main(app),
            "settings" => {
                let _ = show_settings(app);
            }
            "update" => {
                // With a known update: install it. Otherwise check, and show
                // the result in the settings window's "About" section.
                let app = app.clone();
                if app.state::<AppState>().updates.available_version().is_some() {
                    tauri::async_runtime::spawn(async move {
                        let _ = app.emit("update-changed", ());
                        let _ = app.state::<AppState>().updates.install(&app).await;
                        let _ = app.emit("update-changed", ());
                    });
                } else {
                    let _ = show_settings(&app);
                    tauri::async_runtime::spawn(async move { run_update_check(&app).await });
                }
            }
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                toggle_quick(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

fn main() {
    tauri::Builder::default()
        // Launching Weave again (Start menu, Finder) opens the app window of
        // the running instance instead of a second tray icon.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_main(app)))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, Some(vec![AUTOSTART_ARG])))
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, _shortcut, event| {
                    if event.state() == ShortcutState::Pressed {
                        toggle_quick(app);
                    }
                })
                .build(),
        )
        .setup(|app| {
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);

            let settings_path = app.path().app_config_dir()?.join("settings.json");
            let settings = Settings::load(&settings_path);
            let managed = Managed::load();
            let effective = settings.effective(&managed);
            app.manage(AppState {
                settings_path,
                settings: Mutex::new(settings),
                managed,
                session: Mutex::new(load_session(&effective.server_url)),
                login_task: Mutex::new(None),
                chat_abort: Mutex::new(None),
                quick_hidden_at: Mutex::new(None),
                updates: Updates::default(),
                update_menu_item: Mutex::new(None),
            });

            let handle = app.handle().clone();
            if let Some(quick) = handle.get_webview_window("quick") {
                float_over_full_screen(&quick);
            }
            build_tray(&handle)?;
            if let Err(error) = register_hotkey(&handle, &effective.hotkey) {
                eprintln!("{error}");
            }
            if effective.server_url.is_empty() {
                show_settings(&handle)?;
            } else if !std::env::args().any(|arg| arg == AUTOSTART_ARG) {
                show_main(&handle);
            }
            let updater = handle.clone();
            tauri::async_runtime::spawn(async move {
                tokio::time::sleep(UPDATE_FIRST_CHECK).await;
                loop {
                    run_update_check(&updater).await;
                    tokio::time::sleep(UPDATE_INTERVAL).await;
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| match (window.label(), event) {
            ("quick", WindowEvent::Focused(false)) => {
                *window.app_handle().state::<AppState>().quick_hidden_at.lock().unwrap() = Some(Instant::now());
                let _ = window.hide();
            }
            // Closing the app window keeps Weave in the tray.
            ("quick" | "panel", WindowEvent::CloseRequested { api, .. }) => {
                api.prevent_close();
                let _ = window.hide();
                update_dock(window.app_handle());
            }
            ("settings", WindowEvent::Destroyed) => update_dock(window.app_handle()),
            _ => {}
        })
        .invoke_handler(tauri::generate_handler![
            get_state,
            save_settings,
            set_autostart,
            test_connection,
            sign_in,
            cancel_sign_in,
            sign_out,
            list_bots,
            list_collections,
            search,
            ask,
            stop_answer,
            open_url,
            open_settings,
            hide_panel,
            fit_quick,
            get_update_state,
            check_for_updates,
            install_update,
        ])
        .build(tauri::generate_context!())
        .expect("error while building Weave")
        .run(|app, event| {
            // macOS: clicking Weave in the Dock, Finder or Launchpad while it runs.
            #[cfg(target_os = "macos")]
            if let tauri::RunEvent::Reopen { .. } = event {
                show_main(app);
            }
            #[cfg(not(target_os = "macos"))]
            let _ = (app, event);
        });
}
