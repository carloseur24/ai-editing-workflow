SFX live here. Audio and the generated procedural core are gitignored.

Legacy fallback
---------------
Drop a short air-swish / glass-tap sample here as whoosh_short.wav (or point
config/pipeline.json -> sfx.file at your file). `fenix render` pre-rolls it so the
loudest point lands on the cut when no fresh cue sheet exists.

Cue-sheet library
-----------------
library.json maps semantic roles (impact_sub, riser_tension, whoosh_air,
click_tactile, thud_body, tick_micro, chime_lock, paper_slice, ui_data,
env_room, ...) to files inside config/pipeline.json -> sfx.library_dir.

The portable default is library_dir: "assets/sfx". The procedural core is
synthesized into assets/sfx/fenix_sfx/ (ignored):

    ./fenix sfx --install-library

Commercial samples listed in library.json are optional; if they are absent, the
generated fenix_sfx/ files for that role are used. Add your own licensed files to
the same library_dir or set a machine-local path in config/pipeline.local.json.

Then:

    ./fenix sfx <project> --dry-run   # review the cue sheet
    ./fenix sfx <project>             # sfx_bus.wav + music_bed.wav
    ./fenix render <project>          # consumes the fresh artifacts

Full asset map: ../README.md
