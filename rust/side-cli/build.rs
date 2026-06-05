use std::process::Command;

fn main() {
    println!("cargo:rerun-if-changed=build.rs");

    // Robustly locate .git dir (workspace-safe + worktree-safe).
    // NOTE: `rerun-if-changed` on a DIRECTORY does not recursively watch
    // files inside it — watching `.git/refs/heads` is a silent no-op.
    // We watch `HEAD` (unpacked ref pointer) and `packed-refs` (packed refs).
    if let Some(git_dir) = git_output(&["rev-parse", "--git-dir"]) {
        let git_dir = git_dir.trim();
        println!("cargo:rerun-if-changed={git_dir}/HEAD");
        println!("cargo:rerun-if-changed={git_dir}/packed-refs");
    }

    let rev = git_output(&["rev-parse", "HEAD"])
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|| "unknown".to_string());
    println!("cargo:rustc-env=BINARY_BUILD_REV={rev}");

    let dirty = git_output(&["status", "--porcelain"])
        .map(|s| !s.trim().is_empty())
        .unwrap_or(false);
    println!("cargo:rustc-env=BINARY_BUILD_DIRTY={dirty}");

    let built_at = chrono::Utc::now().to_rfc3339();
    println!("cargo:rustc-env=BINARY_BUILT_AT={built_at}");
}

fn git_output(args: &[&str]) -> Option<String> {
    let output = Command::new("git").args(args).output().ok()?;
    if !output.status.success() {
        return None;
    }
    String::from_utf8(output.stdout).ok()
}
