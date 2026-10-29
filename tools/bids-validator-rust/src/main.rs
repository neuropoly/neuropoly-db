use bids_validate::{validate_dataset, validate_derivative_path};
use serde_json::json;
use std::path::PathBuf;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let argument = std::env::args_os().nth(1).ok_or("Usage: bids-validator-rust <dataset>")?;
    if argument == "--version" {
        println!("bids-validator-rust 0.0.3 (bids-validate 0.0.3)");
        return Ok(());
    }
    if argument == "--help" {
        println!("Usage: bids-validator-rust <dataset>\nValidates the raw root and each immediate derivative dataset; prints JSON.\nStructural checks only, not full sidecar or NIfTI semantic validation.");
        return Ok(());
    }
    let root = PathBuf::from(argument);
    if !root.is_dir() {
        return Err(format!("Dataset is not a directory: {}", root.display()).into());
    }
    let mut roots = vec![root.clone()];
    if root.join("derivatives").is_dir() {
        for entry in std::fs::read_dir(root.join("derivatives"))? {
            let path = entry?.path();
            if path.is_dir() {
                roots.push(path);
            }
        }
    }
    roots[1..].sort();
    let mut results = Vec::new();
    let mut failed = false;
    for (index, path) in roots.iter().enumerate() {
        let result = validate_dataset(path)?;
        let mut issues = result.issues.iter().map(|issue| json!({
            "severity": issue.severity, "code": issue.code,
            "message": issue.message, "path": issue.path,
        })).collect::<Vec<_>>();
        let mut errors = result.error_count();
        if index > 0 {
            if let Err(error) = validate_derivative_path(path) {
                errors += 1;
                issues.push(json!({"severity": "error", "code": "DERIVATIVE_PROVENANCE", "message": error.to_string()}));
            }
        }
        failed |= errors > 0;
        results.push(json!({
            "dataset": path, "errors": errors, "warnings": result.warning_count(), "issues": issues,
        }));
    }
    println!("{}", serde_json::to_string_pretty(&json!({
        "validator": "bids-validate 0.0.3 (Rust)",
        "coverage": "Structural validation; not full sidecar or NIfTI semantic validation",
        "datasets": results,
    }))?);
    if failed {
        std::process::exit(1);
    }
    Ok(())
}