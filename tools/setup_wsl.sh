#!/usr/bin/env bash
# setup_wsl.sh — idempotent WSL2/Linux setup for the Fenixsoft video pipeline.
#
# Safe to re-run. Installs only what is missing, creates the venv, fetches the
# Inter font family, and pre-downloads the Whisper model used for alignment.
# Run from anywhere:   bash tools/setup_wsl.sh
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

SKIP_MODEL=0
for arg in "$@"; do
  case "$arg" in
    --no-model) SKIP_MODEL=1 ;;
    -h|--help) echo "usage: bash tools/setup_wsl.sh [--no-model]"; exit 0 ;;
  esac
done

say() { printf "\n\033[1;32m==> %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*"; }

# ---------------------------------------------------------------- apt packages
missing=()
command -v ffmpeg  >/dev/null 2>&1 || missing+=(ffmpeg)
python3 -c "import venv" >/dev/null 2>&1 || missing+=(python3-venv)

if [ ${#missing[@]} -gt 0 ]; then
  say "Installing system packages: ${missing[*]}"
  if command -v sudo >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y "${missing[@]}" fonts-inter unzip curl
  else
    warn "sudo not available. Install manually: apt-get install ${missing[*]}"
    exit 1
  fi
else
  say "ffmpeg + python3-venv already present"
fi

ffmpeg -version | head -1

# ------------------------------------------------------------------- venv/pip
if [ ! -d .venv ]; then
  say "Creating virtualenv .venv"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip -q
say "Installing Python dependencies"
python -m pip install -r requirements.txt
python -m pip install -e . -q

# ---------------------------------------------------------------------- fonts
if [ ! -f assets/fonts/Inter-Black.ttf ]; then
  say "Fetching Inter font family"
  mkdir -p assets/fonts
  tmp_zip="$(mktemp -d)/inter.zip"
  if curl -fsSL --max-time 180 -o "$tmp_zip" \
      https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip; then
    unzip -o -j "$tmp_zip" \
      "extras/ttf/Inter-Black.ttf" \
      "extras/ttf/Inter-Bold.ttf" \
      "extras/ttf/Inter-SemiBold.ttf" -d assets/fonts/ >/dev/null
  else
    warn "Could not download Inter. Copy Inter-*.ttf into assets/fonts/ manually."
    if [ -d /usr/share/fonts/truetype/inter ]; then
      cp -f /usr/share/fonts/truetype/inter/Inter-*.ttf assets/fonts/ || true
    fi
  fi
fi
[ -f assets/fonts/Inter-Black.ttf ] && say "Inter fonts ready" || warn "Inter fonts missing"

# --------------------------------------------------------------------- folders
mkdir -p assets/music assets/sfx input output eval .cache/models
[ -f assets/music/README.txt ] || cat > assets/music/README.txt <<'EOF'
Drop 118 BPM (or 128 BPM) instrumental tracks here.
The renderer uses config/pipeline.json -> music.file, or the first audio file found.
EOF
[ -f assets/sfx/README.txt ] || cat > assets/sfx/README.txt <<'EOF'
Drop short air-swish / glass-tap SFX here (wav/mp3).
The renderer pre-rolls the sample so its loudest point lands on the cut.
EOF

# --------------------------------------------------------------------- models
if [ "$SKIP_MODEL" -eq 0 ]; then
  say "Pre-downloading faster-whisper 'small' (int8, ~460 MB, one time)"
  python - <<'PY'
from faster_whisper import WhisperModel
WhisperModel("small", device="cpu", compute_type="int8",
             download_root=".cache/models")
print("model ready")
PY
else
  warn "Skipping model download (--no-model). First align run will fetch it."
fi

chmod +x fenix
say "Setup complete"
cat <<'EOF'
Check the environment and paths:
  ./fenix doctor

Render the demo (the script lints as FAIL on purpose: 93 words > 84 budget):
  ./fenix align demo_iphone14_blue
  ./fenix render demo_iphone14_blue
  ./fenix qa --latest demo_iphone14_blue --strict

OpenCode:
  opencode   # /connect -> OpenCode Go, then /models -> DeepSeek V4.1 Flash
EOF
