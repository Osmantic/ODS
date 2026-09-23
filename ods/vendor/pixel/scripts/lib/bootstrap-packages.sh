#!/usr/bin/env bash

pixel_packages_for_commands() {
  local command package
  local -a packages=()
  local -A seen=()
  for command in "$@"; do
    case "$command" in
      age) packages=(age) ;;
      curl) packages=(ca-certificates curl) ;;
      git) packages=(git) ;;
      jq) packages=(jq) ;;
      python3) packages=(python3) ;;
      openssl) packages=(openssl) ;;
      docker) packages=(docker.io) ;;
      rg) packages=(ripgrep) ;;
      setfacl) packages=(acl) ;;
      flock) packages=(util-linux) ;;
      *) printf 'Unknown bootstrap command: %s\n' "$command" >&2; return 2 ;;
    esac
    for package in "${packages[@]}"; do
      [[ ${seen[$package]:-0} == 1 ]] && continue
      seen[$package]=1
      printf '%s\n' "$package"
    done
  done
}
