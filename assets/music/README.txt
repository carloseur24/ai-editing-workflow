Music beds live here. Audio is gitignored: this is public source, not a media dump.

Drop a 118/120 BPM instrumental track in this folder. `config/pipeline.json ->
music.file` names the default; if that file is absent, beats/sfx/render fall back
to the first audio file in this folder. The measured beat grid wins over the BPM
label.

No track? `fenix doctor` and `fenix job <project>` report the missing path;
either add one or set `music.file` in config/pipeline.local.json.

Full asset map: ../README.md
