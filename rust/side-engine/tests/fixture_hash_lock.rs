/// Integration test: verify that usdjpy_1h_sample.parquet sha256 matches FIXTURE_HASHES.txt.
///
/// This guards against silent fixture drift — if the parquet file is regenerated
/// with different parameters (compression, schema, page size), this test will fail
/// and force an explicit hash update.
use hex::encode as hex_encode;
use sha2::{Digest, Sha256};
use std::io::Read;

fn fixtures_dir() -> std::path::PathBuf {
    let manifest = std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR");
    std::path::Path::new(&manifest).join("tests/fixtures")
}

#[test]
fn usdjpy_1h_sample_parquet_hash_matches_lock() {
    let fixtures = fixtures_dir();
    let parquet_path = fixtures.join("usdjpy_1h_sample.parquet");
    let lock_path = fixtures.join("FIXTURE_HASHES.txt");

    // Read and hash the parquet file
    let mut file = std::fs::File::open(&parquet_path)
        .unwrap_or_else(|e| panic!("failed to open parquet fixture: {e}"));
    let mut buf = Vec::new();
    file.read_to_end(&mut buf)
        .unwrap_or_else(|e| panic!("failed to read parquet fixture: {e}"));
    let actual_hash = hex_encode(Sha256::digest(&buf));

    // Parse the lock file for the expected hash
    let lock_content = std::fs::read_to_string(&lock_path)
        .unwrap_or_else(|e| panic!("failed to read FIXTURE_HASHES.txt: {e}"));

    let expected_hash = lock_content
        .lines()
        .filter(|l| !l.starts_with('#') && !l.trim().is_empty())
        .find(|l| l.contains("usdjpy_1h_sample.parquet"))
        .and_then(|l| l.split_whitespace().next())
        .unwrap_or_else(|| panic!("usdjpy_1h_sample.parquet not found in FIXTURE_HASHES.txt"));

    assert_eq!(
        actual_hash, expected_hash,
        "parquet fixture hash drift detected!\n  actual:   {}\n  expected: {}\nRegenerate the fixture and update FIXTURE_HASHES.txt.",
        actual_hash, expected_hash
    );
}
