# Optional FFmpeg Binaries

ViriaRevive checks this folder before the system `PATH`.

For a portable build, place both files here before running `build.bat`:

- `ffmpeg.exe`
- `ffprobe.exe`
- `FFMPEG_BUILD.json`

Use a reviewed GPL or LGPL FFmpeg build only. Do not ship a build configured
with `--enable-nonfree`.

`FFMPEG_BUILD.json` must record the provider, build variant, license, download
URL, build source URL, upstream FFmpeg source URL, and SHA256 hashes for both
executables. Start from `FFMPEG_BUILD.example.json` and replace every
placeholder. `build.bat` copies that metadata into the release and
`scripts/check_release_compliance.py` fails the build if the hashes or required
fields do not match.

Release packages that bundle FFmpeg must include the applicable GPL/LGPL
license terms and clear corresponding-source information for the exact binary
build. For GPL builds, publish or link to complete corresponding source in the
manner required by the build's license terms.

Do not commit downloaded binaries unless the release owner has reviewed the
license and distribution terms for the exact FFmpeg build.
