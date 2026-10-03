"""Opt-in CPU demonstration; never run by the image entrypoint."""
import argparse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.trials <= 1000:
        parser.error("--trials must be between 1 and 1000")
    import optuna

    study = optuna.create_study(
        study_name="ods-cpu-demonstration",
        storage="sqlite:////data/studies.db",
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=42),
        load_if_exists=True,
    )
    study.set_user_attr("purpose", "Synthetic CPU demonstration, not a model benchmark")

    def objective(trial):
        x = trial.suggest_float("x", -5.0, 5.0)
        y = trial.suggest_float("y", -5.0, 5.0)
        return (x - 1.0) ** 2 + (y + 2.0) ** 2

    study.optimize(objective, n_trials=args.trials, n_jobs=1)
    print(f"Completed {args.trials} additional trials in {study.study_name}.")
    print(f"Best objective: {study.best_value}; parameters: {study.best_params}")


if __name__ == "__main__":
    main()
