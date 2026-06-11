#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
skills_src="${repo_dir}/skills"
codex_home="${CODEX_HOME:-${HOME}/.codex}"
skills_dst="${codex_home}/skills"
copy_mode=0

usage() {
  printf 'Usage: %s [--copy]\n' "$0"
  printf 'Installs skills from %s into %s.\n' "$skills_src" "$skills_dst"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --copy)
      copy_mode=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

if [ ! -d "$skills_src" ]; then
  printf 'BLOCKER: skills source directory is missing: %s\n' "$skills_src" >&2
  exit 78
fi

mkdir -p "$skills_dst"

for skill_dir in "$skills_src"/*; do
  [ -d "$skill_dir" ] || continue
  skill_name="$(basename "$skill_dir")"
  target="${skills_dst}/${skill_name}"

  if [ -e "$target" ] || [ -L "$target" ]; then
    rm -rf "$target"
  fi

  if [ "$copy_mode" -eq 1 ]; then
    cp -R "$skill_dir" "$target"
    printf 'Copied %s -> %s\n' "$skill_name" "$target"
  else
    ln -sfn "$skill_dir" "$target"
    printf 'Linked %s -> %s\n' "$skill_name" "$target"
  fi
done

printf 'Done. Installed skills into %s\n' "$skills_dst"
