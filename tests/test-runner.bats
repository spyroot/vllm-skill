#!/usr/bin/env bats

setup() {
  export REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)"
}

@test "test runner reports blocker when shellcheck is missing" {
  export SHELLCHECK_BIN="$BATS_TEST_TMPDIR/no-such-shellcheck"

  run "$REPO_ROOT/scripts/test.sh"

  [ "$status" -eq 78 ]
  [[ "$output" == *"BLOCKER: shellcheck is required"* ]]
}

@test "test runner reports blocker when bats is missing" {
  export TEST_BIN="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$TEST_BIN"
  export BATS_BIN="$BATS_TEST_TMPDIR/no-such-bats"
  export SHELLCHECK_BIN="$TEST_BIN/shellcheck"
  export RG_BIN="$TEST_BIN/rg"
  cat > "$TEST_BIN/shellcheck" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
  cat > "$TEST_BIN/rg" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
  chmod +x "$TEST_BIN/shellcheck" "$TEST_BIN/rg"

  run "$REPO_ROOT/scripts/test.sh"

  [ "$status" -eq 78 ]
  [[ "$output" == *"BLOCKER: bats is required"* ]]
}

@test "test runner reports blocker when ripgrep is missing" {
  export TEST_BIN="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$TEST_BIN"
  export SHELLCHECK_BIN="$TEST_BIN/shellcheck"
  export RG_BIN="$BATS_TEST_TMPDIR/no-such-rg"
  cat > "$TEST_BIN/shellcheck" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
  chmod +x "$TEST_BIN/shellcheck"

  run "$REPO_ROOT/scripts/test.sh"

  [ "$status" -eq 78 ]
  [[ "$output" == *"BLOCKER: ripgrep is required"* ]]
}

@test "test runner reports blocker when pytest is missing" {
  export TEST_BIN="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$TEST_BIN"
  export SHELLCHECK_BIN="$TEST_BIN/shellcheck"
  export RG_BIN="$TEST_BIN/rg"
  export BATS_BIN="$TEST_BIN/bats"
  export PYTHON_BIN="$TEST_BIN/python"
  cat > "$TEST_BIN/shellcheck" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
  cat > "$TEST_BIN/rg" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
  cat > "$TEST_BIN/bats" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
  cat > "$TEST_BIN/python" <<'STUB'
#!/usr/bin/env bash
if [ "$1" = "-c" ]; then
  exit 1
fi
exit 0
STUB
  chmod +x "$TEST_BIN/shellcheck" "$TEST_BIN/rg" "$TEST_BIN/bats" "$TEST_BIN/python"

  run "$REPO_ROOT/scripts/test.sh"

  [ "$status" -eq 78 ]
  [[ "$output" == *"BLOCKER: pytest is required"* ]]
}

@test "test runner honors tool overrides" {
  export TEST_BIN="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$TEST_BIN"
  shellcheck_log="$BATS_TEST_TMPDIR/shellcheck.log"
  bats_log="$BATS_TEST_TMPDIR/bats.log"
  python_log="$BATS_TEST_TMPDIR/python.log"
  cat > "$TEST_BIN/shellcheck" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$@" > "$shellcheck_log"
exit 0
STUB
  cat > "$TEST_BIN/rg" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
  cat > "$TEST_BIN/custom-bats" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$@" > "$bats_log"
exit 0
STUB
  cat > "$TEST_BIN/python" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$@" >> "$python_log"
if [ "\$1" = "-c" ]; then
  exit 0
fi
if [ "\$1" = "-m" ] && [ "\$2" = "pytest" ]; then
  exit 0
fi
exit 64
STUB
  chmod +x "$TEST_BIN/shellcheck" "$TEST_BIN/rg" "$TEST_BIN/custom-bats" "$TEST_BIN/python"
  export SHELLCHECK_BIN="$TEST_BIN/shellcheck"
  export RG_BIN="$TEST_BIN/rg"
  export BATS_BIN="$TEST_BIN/custom-bats"
  export PYTHON_BIN="$TEST_BIN/python"

  run "$REPO_ROOT/scripts/test.sh"

  [ "$status" -eq 0 ]
  [[ "$output" == *"All checks passed."* ]]
  grep -q "scripts/install.sh" "$shellcheck_log"
  grep -q "tests/install.bats" "$bats_log"
  grep -q -- "-m" "$python_log"
  grep -q "pytest" "$python_log"
}
