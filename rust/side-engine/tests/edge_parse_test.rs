// Implementation: Plan 01-04 Task 1 (Wave 2)

use std::path::PathBuf;

fn fixture(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join(name)
}

#[test]
fn edge_parse_sample_fixture_returns_two_edges() {
    let edges = side_engine::edges::parse_file(&fixture("edges_sample.json"))
        .expect("edges_sample.json should parse");
    assert_eq!(edges.len(), 2, "fixture contains exactly two edges");
    assert_eq!(edges[0].entry_minute, 0);
    assert_eq!(edges[0].direction, "long");
    assert_eq!(edges[0].hold_h_candidates, vec![1, 3]);
    assert_eq!(edges[0].asset, "USDJPY");
    assert_eq!(edges[0].timeframe, "1h");
    assert_eq!(edges[1].entry_minute, 55);
    assert_eq!(edges[1].direction, "short");
    assert_eq!(edges[1].hold_h_candidates, vec![2]);
    assert_eq!(edges[1].timeframe, "1m");
}

#[test]
fn edge_parse_dsr_p_defaults_to_none() {
    let edges = side_engine::edges::parse_file(&fixture("edges_sample.json")).unwrap();
    assert!(
        edges.iter().all(|e| e.dsr_p.is_none()),
        "sample fixture has dsr_p=null for both edges"
    );

    // Also verify #[serde(default)] kicks in when the field is omitted entirely.
    let without_field = r#"[{
        "entry_minute": 0,
        "direction": "long",
        "hold_h_candidates": [1],
        "t_stat": 4.5,
        "bh_q": 0.02,
        "source_query": "t",
        "asset": "USDJPY",
        "timeframe": "1h"
    }]"#;
    let parsed = side_engine::edges::parse_str(without_field).unwrap();
    assert!(parsed[0].dsr_p.is_none());
}

#[test]
fn edge_parse_rejects_invalid_direction() {
    let bad = r#"[{
        "entry_minute": 10,
        "direction": "sideways",
        "hold_h_candidates": [1],
        "t_stat": 1.0,
        "bh_q": 0.5,
        "source_query": "t",
        "asset": "USDJPY",
        "timeframe": "1h"
    }]"#;
    let err = side_engine::edges::parse_str(bad).expect_err("sideways is not a valid direction");
    assert!(
        err.to_string().contains("direction"),
        "error should mention direction: {err}"
    );
}
