#!/usr/bin/env bats

setup() {
  export REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)"
  export INSTALL_SCRIPT="$REPO_ROOT/scripts/install.sh"
}

@test "help exits 0 and prints usage" {
  run "$INSTALL_SCRIPT" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
  [[ "$output" == *"--copy"* ]]
}

@test "unknown option exits 2 and prints usage" {
  run "$INSTALL_SCRIPT" --unknown
  [ "$status" -eq 2 ]
  [[ "$output" == *"Usage:"* ]]
}

@test "missing skills source directory reports blocker" {
  fake_repo="$BATS_TEST_TMPDIR/repo-without-skills"
  mkdir -p "$fake_repo/scripts"
  cp "$INSTALL_SCRIPT" "$fake_repo/scripts/install.sh"
  chmod +x "$fake_repo/scripts/install.sh"
  export CODEX_HOME="$BATS_TEST_TMPDIR/codex-home"

  run "$fake_repo/scripts/install.sh"

  [ "$status" -eq 78 ]
  [[ "$output" == *"BLOCKER: skills source directory is missing"* ]]
}

@test "install links daedalus and preserves unrelated target skills" {
  export CODEX_HOME="$BATS_TEST_TMPDIR/codex-home"
  mkdir -p "$CODEX_HOME/skills/daedalus" "$CODEX_HOME/skills/other-skill"
  echo "old" > "$CODEX_HOME/skills/daedalus/OLD"
  echo "keep" > "$CODEX_HOME/skills/other-skill/KEEP"

  run "$INSTALL_SCRIPT"

  [ "$status" -eq 0 ]
  [ -L "$CODEX_HOME/skills/daedalus" ]
  [ -f "$CODEX_HOME/skills/other-skill/KEEP" ]
  [ "$(readlink "$CODEX_HOME/skills/daedalus")" = "$REPO_ROOT/skills/daedalus" ]
}

@test "install replaces stale symlink target" {
  export CODEX_HOME="$BATS_TEST_TMPDIR/codex-home"
  mkdir -p "$CODEX_HOME/skills"
  ln -s "$BATS_TEST_TMPDIR/no-such-target" "$CODEX_HOME/skills/daedalus"

  run "$INSTALL_SCRIPT"

  [ "$status" -eq 0 ]
  [ -L "$CODEX_HOME/skills/daedalus" ]
  [ "$(readlink "$CODEX_HOME/skills/daedalus")" = "$REPO_ROOT/skills/daedalus" ]
}

@test "install replaces regular file target" {
  export CODEX_HOME="$BATS_TEST_TMPDIR/codex-home"
  mkdir -p "$CODEX_HOME/skills"
  echo "old" > "$CODEX_HOME/skills/daedalus"

  run "$INSTALL_SCRIPT"

  [ "$status" -eq 0 ]
  [ -L "$CODEX_HOME/skills/daedalus" ]
}

@test "install supports CODEX_HOME paths with spaces" {
  export CODEX_HOME="$BATS_TEST_TMPDIR/codex home"

  run "$INSTALL_SCRIPT"

  [ "$status" -eq 0 ]
  [ -L "$CODEX_HOME/skills/daedalus" ]
  [ "$(readlink "$CODEX_HOME/skills/daedalus")" = "$REPO_ROOT/skills/daedalus" ]
}

@test "copy mode supports CODEX_HOME paths with spaces" {
  export CODEX_HOME="$BATS_TEST_TMPDIR/codex home"

  run "$INSTALL_SCRIPT" --copy

  [ "$status" -eq 0 ]
  [ -f "$CODEX_HOME/skills/daedalus/SKILL.md" ]
  [ ! -L "$CODEX_HOME/skills/daedalus" ]
}

@test "install defaults to HOME .codex when CODEX_HOME is unset" {
  export HOME="$BATS_TEST_TMPDIR/home"
  unset CODEX_HOME

  run "$INSTALL_SCRIPT"

  [ "$status" -eq 0 ]
  [ -L "$HOME/.codex/skills/daedalus" ]
  [ "$(readlink "$HOME/.codex/skills/daedalus")" = "$REPO_ROOT/skills/daedalus" ]
}

@test "copy mode replaces same-name skill with a real directory" {
  export CODEX_HOME="$BATS_TEST_TMPDIR/codex-home"
  mkdir -p "$CODEX_HOME/skills/daedalus" "$CODEX_HOME/skills/other-skill"
  echo "old" > "$CODEX_HOME/skills/daedalus/OLD"
  echo "keep" > "$CODEX_HOME/skills/other-skill/KEEP"

  run "$INSTALL_SCRIPT" --copy

  [ "$status" -eq 0 ]
  [ -f "$CODEX_HOME/skills/daedalus/SKILL.md" ]
  [ ! -e "$CODEX_HOME/skills/daedalus/OLD" ]
  [ -f "$CODEX_HOME/skills/other-skill/KEEP" ]
  [ ! -L "$CODEX_HOME/skills/daedalus" ]
}
