#!/usr/bin/env bash
# Build one release binary of the engine for a Rust target triple, cross-compiling
# from Linux with cargo-zigbuild, and write it to dist/ under its release asset name.
# CI runs exactly this script; run it locally to check a target before tagging.
#
#   scripts/build-engine.sh x86_64-unknown-linux-musl
set -euo pipefail

target=${1:?usage: build-engine.sh <rust-target-triple>}
root=$(cd "$(dirname "$0")/.." && pwd)
dist="$root/dist"
exe=""
[[ $target == *windows* ]] && exe=".exe"

rustup target add "$target" >/dev/null
cd "$root/engine"
cargo zigbuild --release --locked --target "$target"

mkdir -p "$dist"
asset="solverforge-planner-$target$exe"
cp "target/$target/release/solverforge-planner$exe" "$dist/$asset"
echo "$dist/$asset"
