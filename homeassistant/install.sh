#!/bin/sh
# Run inside the HA Terminal & SSH app. No HomeButler secrets are read.
set -eu
revision="${1:-}"
case "$revision" in *[!a-f0-9]*|"") echo "Supply a full 40-character commit SHA"; exit 1;; esac
[ "${#revision}" -eq 40 ] || exit 1
[ -d /config ] || { echo "Run this in the Home Assistant Terminal app"; exit 1; }
stage="$(mktemp -d /tmp/home-butler.XXXXXX)"
trap 'rm -rf "$stage"' EXIT HUP INT TERM
curl --fail --silent --show-error --location "https://codeload.github.com/CZLin-TW/home-butler/zip/$revision" -o "$stage/source.zip"
unzip -q "$stage/source.zip" "home-butler-$revision/homeassistant/custom_components/home_butler/*" -d "$stage"
source_dir="$stage/home-butler-$revision/homeassistant/custom_components/home_butler"
[ -f "$source_dir/manifest.json" ] && [ -f "$source_dir/config_flow.py" ] && [ -f "$source_dir/transport.py" ]
mkdir -p /config/custom_components
next_dir="$(mktemp -d /config/custom_components/.home_butler.XXXXXX)"
cp -R "$source_dir/." "$next_dir/"
if [ -e /config/custom_components/home_butler ]; then
  backup="/config/home_butler-backup-$(date +%Y%m%d-%H%M%S)-$$"
  mv /config/custom_components/home_butler "$backup"
  echo "Previous integration saved at $backup"
fi
mv "$next_dir" /config/custom_components/home_butler
echo "Installed Home Butler from $revision. Restart Home Assistant, then add the Home Butler integration."
