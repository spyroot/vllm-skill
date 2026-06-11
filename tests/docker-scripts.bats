#!/usr/bin/env bats

setup() {
  export REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)"
}

@test "docker test help exits 0" {
  run "$REPO_ROOT/scripts/docker-test.sh" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
  [[ "$output" == *"--build-only"* ]]
}

@test "docker test script builds and runs expected image" {
  export TEST_BIN="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$TEST_BIN"
  docker_log="$BATS_TEST_TMPDIR/docker.log"
  cat > "$TEST_BIN/docker" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$@" >> "$docker_log"
exit 0
STUB
  chmod +x "$TEST_BIN/docker"
  PATH="$TEST_BIN:$PATH" run "$REPO_ROOT/scripts/docker-test.sh"

  [ "$status" -eq 0 ]
  grep -q 'build' "$docker_log"
  grep -q 'run' "$docker_log"
  grep -q 'vllm-skill-test:local' "$docker_log"
}

@test "docker test build-only does not run container" {
  export TEST_BIN="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$TEST_BIN"
  docker_log="$BATS_TEST_TMPDIR/docker.log"
  cat > "$TEST_BIN/docker" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$@" >> "$docker_log"
exit 0
STUB
  chmod +x "$TEST_BIN/docker"
  PATH="$TEST_BIN:$PATH" run "$REPO_ROOT/scripts/docker-test.sh" --build-only

  [ "$status" -eq 0 ]
  grep -q 'build' "$docker_log"
  ! grep -q 'run' "$docker_log"
}
