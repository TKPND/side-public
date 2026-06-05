use std::collections::HashMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::wfd::ExitConfig;

use super::param_space::{sample_exit, sample_params, StrategyParamSpace};

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Direction {
    Maximize,
    Minimize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrialState {
    Complete,
    Pruned,
}

#[derive(Debug, Clone)]
pub struct Trial {
    pub id: usize,
    pub params: HashMap<String, Value>,
    pub exit_config: Option<ExitConfig>,
    pub exit_meta: HashMap<String, Value>,
    pub state: TrialState,
    pub values: Vec<f64>,
    pub user_attrs: HashMap<String, Value>,
}

pub struct TrialPruned;

// ---------------------------------------------------------------------------
// Multi-Objective Study
// ---------------------------------------------------------------------------

pub struct MultiObjectiveStudy {
    directions: Vec<Direction>,
    trials: Vec<Trial>,
    next_id: usize,
}

impl MultiObjectiveStudy {
    pub fn new(directions: Vec<Direction>) -> Self {
        Self {
            directions,
            trials: Vec::new(),
            next_id: 0,
        }
    }

    /// Ask for a new trial with sampled parameters.
    pub fn ask(&mut self, space: &StrategyParamSpace, rng: &mut impl rand::Rng) -> Trial {
        let params = sample_params(space, rng);
        let (exit_config, exit_meta) = sample_exit(rng);
        let id = self.next_id;
        self.next_id += 1;

        Trial {
            id,
            params,
            exit_config,
            exit_meta,
            state: TrialState::Pruned, // default until told
            values: Vec::new(),
            user_attrs: HashMap::new(),
        }
    }

    /// Tell the study the result of a trial.
    pub fn tell(&mut self, mut trial: Trial, result: Result<Vec<f64>, TrialPruned>) {
        match result {
            Ok(values) => {
                trial.state = TrialState::Complete;
                trial.values = values;
            }
            Err(_) => {
                trial.state = TrialState::Pruned;
            }
        }
        self.trials.push(trial);
    }

    /// Get the Pareto front (non-dominated set) of completed trials.
    pub fn pareto_front(&self) -> Vec<&Trial> {
        let completed: Vec<&Trial> = self
            .trials
            .iter()
            .filter(|t| t.state == TrialState::Complete && !t.values.is_empty())
            .collect();

        if completed.is_empty() {
            return Vec::new();
        }

        non_dominated_sort(&completed, &self.directions)
    }

    pub fn trials(&self) -> &[Trial] {
        &self.trials
    }

    pub fn completed_trials(&self) -> Vec<&Trial> {
        self.trials
            .iter()
            .filter(|t| t.state == TrialState::Complete)
            .collect()
    }

    pub fn n_trials(&self) -> usize {
        self.trials.len()
    }
}

// ---------------------------------------------------------------------------
// Pareto dominance
// ---------------------------------------------------------------------------

/// Returns true if `a` dominates `b` (a is at least as good in all objectives
/// and strictly better in at least one).
pub fn dominates(a: &[f64], b: &[f64], directions: &[Direction]) -> bool {
    debug_assert_eq!(a.len(), b.len());
    debug_assert_eq!(a.len(), directions.len());

    let mut dominated_in_any = false;

    for i in 0..a.len() {
        let (va, vb) = match directions[i] {
            Direction::Maximize => (a[i], b[i]),
            Direction::Minimize => (-a[i], -b[i]),
        };

        if va < vb {
            return false; // a is worse in this objective
        }
        if va > vb {
            dominated_in_any = true;
        }
    }

    dominated_in_any
}

/// Extract the first non-dominated front from a set of trials.
fn non_dominated_sort<'a>(trials: &[&'a Trial], directions: &[Direction]) -> Vec<&'a Trial> {
    let n = trials.len();
    let mut is_dominated = vec![false; n];

    for i in 0..n {
        if is_dominated[i] {
            continue;
        }
        for j in (i + 1)..n {
            if is_dominated[j] {
                continue;
            }
            if dominates(&trials[i].values, &trials[j].values, directions) {
                is_dominated[j] = true;
            } else if dominates(&trials[j].values, &trials[i].values, directions) {
                is_dominated[i] = true;
                break;
            }
        }
    }

    trials
        .iter()
        .enumerate()
        .filter(|(i, _)| !is_dominated[*i])
        .map(|(_, t)| *t)
        .collect()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use rand::rngs::StdRng;
    use rand::Rng;
    use rand::SeedableRng;

    fn max3() -> Vec<Direction> {
        vec![
            Direction::Maximize,
            Direction::Maximize,
            Direction::Maximize,
        ]
    }

    #[test]
    fn test_dominates_2d_maximize() {
        let dirs = vec![Direction::Maximize, Direction::Maximize];
        // (3,4) dominates (2,3)
        assert!(dominates(&[3.0, 4.0], &[2.0, 3.0], &dirs));
        // (2,3) does not dominate (3,4)
        assert!(!dominates(&[2.0, 3.0], &[3.0, 4.0], &dirs));
        // (3,3) does not dominate (2,4) — incomparable
        assert!(!dominates(&[3.0, 3.0], &[2.0, 4.0], &dirs));
        // Equal → does not dominate (no strictly better)
        assert!(!dominates(&[3.0, 3.0], &[3.0, 3.0], &dirs));
    }

    #[test]
    fn test_dominates_3d_mixed() {
        let dirs = vec![
            Direction::Maximize,
            Direction::Maximize,
            Direction::Minimize,
        ];
        // a = (5, 3, 1.0), b = (4, 3, 2.0)
        // Max: 5>4 ✓, 3=3, Min: 1<2 ✓ (better for minimize)
        assert!(dominates(&[5.0, 3.0, 1.0], &[4.0, 3.0, 2.0], &dirs));
        // a = (5, 3, 3.0), b = (4, 3, 2.0)
        // Max: 5>4, 3=3, Min: 3>2 → a is worse in 3rd → no domination
        assert!(!dominates(&[5.0, 3.0, 3.0], &[4.0, 3.0, 2.0], &dirs));
    }

    #[test]
    fn test_pareto_front_simple() {
        let dirs = vec![Direction::Maximize, Direction::Maximize];
        let trials: Vec<Trial> = vec![
            make_trial(0, vec![1.0, 5.0]),
            make_trial(1, vec![5.0, 1.0]),
            make_trial(2, vec![3.0, 3.0]),
            make_trial(3, vec![2.0, 2.0]), // dominated by (3,3)
        ];
        let refs: Vec<&Trial> = trials.iter().collect();
        let front = non_dominated_sort(&refs, &dirs);

        let front_ids: Vec<usize> = front.iter().map(|t| t.id).collect();
        // (1,5), (5,1), (3,3) are non-dominated; (2,2) is dominated
        assert!(front_ids.contains(&0));
        assert!(front_ids.contains(&1));
        assert!(front_ids.contains(&2));
        assert!(!front_ids.contains(&3));
    }

    #[test]
    fn test_pareto_front_3d() {
        let dirs = max3();
        let trials: Vec<Trial> = vec![
            make_trial(0, vec![5.0, 1.0, 1.0]),
            make_trial(1, vec![1.0, 5.0, 1.0]),
            make_trial(2, vec![1.0, 1.0, 5.0]),
            make_trial(3, vec![2.0, 2.0, 2.0]), // non-dominated (incomparable with all)
            make_trial(4, vec![1.0, 1.0, 1.0]), // dominated by 3
        ];
        let refs: Vec<&Trial> = trials.iter().collect();
        let front = non_dominated_sort(&refs, &dirs);
        let front_ids: Vec<usize> = front.iter().map(|t| t.id).collect();

        assert_eq!(front_ids.len(), 4);
        assert!(!front_ids.contains(&4));
    }

    #[test]
    fn test_ask_tell_cycle() {
        let space: super::super::param_space::StrategyParamSpace = serde_json::from_str(
            r#"{
                "params": {
                    "period": { "type": "int", "low": 5, "high": 50 },
                    "mult": { "type": "float", "low": 0.5, "high": 3.0 }
                }
            }"#,
        )
        .unwrap();

        let mut study = MultiObjectiveStudy::new(max3());
        let mut rng = StdRng::seed_from_u64(42);

        // Run 100 ask/tell cycles
        for i in 0..100 {
            let trial = study.ask(&space, &mut rng);
            assert_eq!(trial.id, i);

            if i % 5 == 0 {
                // Prune every 5th
                study.tell(trial, Err(TrialPruned));
            } else {
                let pf = rng.random_range(0.5..5.0);
                let sr = rng.random_range(-1.0..3.0);
                let dd = rng.random_range(-0.5..0.0);
                study.tell(trial, Ok(vec![pf, sr, dd]));
            }
        }

        assert_eq!(study.n_trials(), 100);
        let completed = study.completed_trials();
        assert_eq!(completed.len(), 80); // 100 - 20 pruned

        let front = study.pareto_front();
        assert!(!front.is_empty());
        // All front members should be complete
        for t in &front {
            assert_eq!(t.state, TrialState::Complete);
        }
    }

    #[test]
    fn test_pruned_not_in_pareto() {
        let space: super::super::param_space::StrategyParamSpace = serde_json::from_str(
            r#"{ "params": { "x": { "type": "int", "low": 1, "high": 10 } } }"#,
        )
        .unwrap();

        let mut study = MultiObjectiveStudy::new(vec![Direction::Maximize]);
        let mut rng = StdRng::seed_from_u64(1);

        // Tell one pruned, one complete
        let t1 = study.ask(&space, &mut rng);
        study.tell(t1, Err(TrialPruned));

        let t2 = study.ask(&space, &mut rng);
        study.tell(t2, Ok(vec![1.0]));

        let front = study.pareto_front();
        assert_eq!(front.len(), 1);
        assert_eq!(front[0].id, 1);
    }

    fn make_trial(id: usize, values: Vec<f64>) -> Trial {
        Trial {
            id,
            params: HashMap::new(),
            exit_config: None,
            exit_meta: HashMap::new(),
            state: TrialState::Complete,
            values,
            user_attrs: HashMap::new(),
        }
    }
}
