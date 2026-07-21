#!/usr/bin/env bats

@test "Bats runner does not install dependencies at test time" {
	runner="$BATS_TEST_DIRNAME/../run-bats.sh"

	if grep -Eq '(git[[:space:]]+clone|curl[[:space:]]|wget[[:space:]]|pip3?[[:space:]]+install|uv[[:space:]]+pip[[:space:]]+install|npm[[:space:]]+(install|ci)|pnpm[[:space:]]+(install|add)|yarn[[:space:]]+(install|add)|brew[[:space:]]+install|apt(-get)?[[:space:]]+install|dnf[[:space:]]+install|yum[[:space:]]+install|mise[[:space:]]+install)' "$runner"; then
		echo "test runner contains a network or package installation command" >&2
		return 1
	fi
}

@test "Bats dependencies are pinned Git submodules" {
	repo_root="$(git -C "$BATS_TEST_DIRNAME" rev-parse --show-toplevel)"

	for dependency in bats-core bats-support bats-assert; do
		mode="$(
			git -C "$repo_root" ls-files --stage -- "ods/tests/bats/$dependency" |
				awk '{print $1}'
		)"
		if [[ "$mode" != "160000" ]]; then
			echo "ods/tests/bats/$dependency is not a tracked Git submodule" >&2
			return 1
		fi
	done
}

@test "Bats runner fails clearly when submodules are missing" {
	isolated_tests="$BATS_TEST_TMPDIR/tests"
	mkdir -p "$isolated_tests"
	cp "$BATS_TEST_DIRNAME/../run-bats.sh" "$isolated_tests/run-bats.sh"

	run bash "$isolated_tests/run-bats.sh"

	[[ "$status" -eq 2 ]]
	[[ "$output" == *"git submodule update --init --recursive"* ]]
}
