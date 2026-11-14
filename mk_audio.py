#!/usr/bin/env python3
"""Synthesize alfabet/audio clips from the letter data in index.html."""

import ctypes
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = ROOT / "index.html"
OUT = ROOT / "audio"
VOICES = {"pl": "zlw/pl", "en": "gmw/en", "de": "gmw/de", "it": "roa/it"}
ESPEAK_UTF8 = 1
ESPEAK_RATE = 1

def clips_from_page():
    html = HTML.read_text()
    start = html.index("<script>") + len("<script>")
    end = html.index("const COPY")
    code = html[start:end]
    script = code + r"""
const clips = [];
const seen = new Map();
function add(bcp, text) {
  const lang = bcp.slice(0, 2);
  const slug = clipSlug(text);
  const key = lang + "/" + slug;
  const prev = seen.get(key);
  if (prev) {
    if (prev.text.toLowerCase() !== text.toLowerCase()) {
      throw new Error("slug clash " + key + " [" + prev.text + "] vs [" + text + "]");
    }
    return;
  }
  seen.set(key, { bcp, text });
  clips.push({ bcp, lang, text, slug });
}
for (const id of LANGS) {
  const pack = ALPHABETS[id];
  for (const ch of pack.koch) {
    const letter = pack.by[ch];
    if (!letter.say) throw new Error("no say " + id + " " + ch);
    add(pack.bcp, letter.say);
    const adapt = letter.adapt || {};
    for (const ui of ["pl", "en", "de", "it"]) {
      if (ui === id) continue;
      if (!adapt[ui]) throw new Error("no adapt " + id + " " + ch + " " + ui);
      add(UI_BCP[ui], adapt[ui]);
    }
  }
}
process.stdout.write(JSON.stringify(clips));
"""
    proc = subprocess.run(["node", "-e", script], text=True, capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(proc.returncode)
    return json.loads(proc.stdout)


def synth_all(clips):
    lib = ctypes.CDLL("libespeak-ng.so.1")
    lib.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    lib.espeak_Initialize.restype = ctypes.c_int
    rate = lib.espeak_Initialize(1, 0, b"/usr/share/espeak-ng-data", 0)
    if rate <= 0:
        raise SystemExit("espeak_Initialize failed")
    lib.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
    lib.espeak_SetVoiceByName.restype = ctypes.c_int
    lib.espeak_SetParameter.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
    lib.espeak_SetParameter.restype = ctypes.c_int
    lib.espeak_SetParameter(ESPEAK_RATE, 150, 0)
    lib.espeak_TextToPhonemes.argtypes = [
        ctypes.POINTER(ctypes.c_char_p), ctypes.c_int, ctypes.c_int
    ]
    lib.espeak_TextToPhonemes.restype = ctypes.c_char_p
    chunks = []

    @ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p)
    def on_audio(wav, count, _user):
        if count > 0:
            chunks.append(ctypes.string_at(wav, count * 2))
        return 0

    lib.espeak_SetSynthCallback.argtypes = [type(on_audio)]
    lib.espeak_SetSynthCallback(on_audio)
    lib.espeak_Synth.argtypes = [
        ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint, ctypes.c_int,
        ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint), ctypes.c_void_p,
    ]
    lib.espeak_Synth.restype = ctypes.c_int
    lib.espeak_Synchronize.argtypes = []
    lib.espeak_Synchronize.restype = ctypes.c_int

    def phonemes(voice, text):
        lib.espeak_SetVoiceByName(voice.encode())
        raw = ctypes.create_string_buffer(text.encode())
        ptr = ctypes.cast(raw, ctypes.c_char_p)
        out = lib.espeak_TextToPhonemes(ctypes.pointer(ptr), ESPEAK_UTF8, 1)
        return out.decode() if out else ""

    def speak(voice, text):
        chunks.clear()
        lib.espeak_SetVoiceByName(voice.encode())
        raw = text.encode()
        ident = ctypes.c_uint(0)
        rc = lib.espeak_Synth(raw, len(raw) + 1, 0, 0, 0, ESPEAK_UTF8, ctypes.byref(ident), None)
        lib.espeak_Synchronize()
        if rc != 0 or not chunks:
            raise RuntimeError("synth failed %s %r rc=%s" % (voice, text, rc))
        return b"".join(chunks)

    odd = []
    for clip in clips:
        voice = VOICES[clip["lang"]]
        pho = phonemes(voice, clip["text"])
        if "," in pho or "(en)" in pho or "(de)" in pho or "(it)" in pho or "(pl)" in pho:
            odd.append("%s %-18r %s" % (clip["lang"], clip["text"], pho))
        pcm = speak(voice, clip["text"])
        dest = OUT / clip["lang"] / (clip["slug"] + ".mp3")
        dest.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
                "-codec:a", "libmp3lame", "-q:a", "7",
                str(dest),
            ],
            input=pcm,
            capture_output=True,
        )
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr.decode() or "")
            raise SystemExit("ffmpeg failed for " + str(dest))
    print("clips", len(clips))
    if odd:
        print("check phonemes:")
        print("\n".join(odd))


if __name__ == "__main__":
    synth_all(clips_from_page())
