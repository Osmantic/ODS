// Prevents additional console window on Windows in release
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod commands;
mod docker;
mod gpu;
mod installer;
mod platform;
mod state;

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            commands::check_system,
            commands::check_prerequisites,
            commands::install_prerequisites,
            commands::detect_gpu,
            commands::start_install,
            commands::get_install_progress,
            commands::get_install_state,
            commands::open_ods,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
