#!/bin/sh
set -eu
cd "$(dirname "$0")"
rsync -avr .  dev:/home/rr/public_html/alfabet
