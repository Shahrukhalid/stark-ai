# Third-party software in the desktop app

| Component | Used for | License |
|---|---|---|
| [FFmpeg](https://ffmpeg.org) (static build shipped by imageio-ffmpeg) | Video studio: trim, resize, convert, captions, thumbnails | GPL v3 (the builds include GPL components such as x264) |
| [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) | Provides the FFmpeg binary | BSD-2-Clause |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) via [pywhispercpp](https://github.com/absadiki/pywhispercpp) | Speech to text in the video studio | MIT |
| Whisper models (ggml, downloaded on request) | Speech recognition weights | MIT (OpenAI Whisper) |
| [NumPy](https://numpy.org) | Audio samples for speech to text | BSD-3-Clause |
| [pywebview](https://github.com/r0x0r/pywebview) | Native window | BSD-3-Clause |
| [PyInstaller](https://pyinstaller.org) | Packaging | GPL v2 with bootloader exception |

FFmpeg runs as a separate program; STARK AI calls it with command-line arguments and does not link to it.
FFmpeg source code: https://ffmpeg.org/download.html · build details: https://github.com/imageio/imageio-binaries

## Desktop credential protection

- [keyring 25.7.0](https://github.com/jaraco/keyring) — MIT. Native macOS Keychain / Windows Credential Manager adapters.
- [cryptography 50.0.2](https://github.com/pyca/cryptography) — Apache-2.0 OR BSD-3-Clause. AES-GCM authenticated encryption. Bundled native components retain their upstream notices.

Distributors must retain these packages' license files and transitive dependency notices. This does not change the application's existing MIT license.

## MakeHuman base mesh and targets (CC0 1.0)

The feminine and masculine presence figures (`apps/web/lib/presence-data.ts`) are point clouds baked from the
MakeHuman base mesh and macro targets, released into the public domain under CC0 1.0 by the MakeHuman team
(Data Collection AB, Joel Palmius, Jonas Hauquier; original work by Manuel Bastioni). They are synthetic humans and
do not depict any real person. https://github.com/makehumancommunity/makehuman

## Noto Nastaliq Urdu (SIL Open Font License 1.1)

Urdu text is shown with Noto Nastaliq Urdu (`apps/web/app/fonts/`, Arabic-script subset), © Google LLC, under the
SIL Open Font License 1.1; the license is in `apps/web/app/fonts/OFL-NotoNastaliqUrdu.txt`.

## Computer control (desktop app)

- [PyObjC ApplicationServices](https://github.com/ronaldoussoren/pyobjc) — MIT. macOS Accessibility API.
- [comtypes](https://github.com/enthought/comtypes) — MIT. Windows UI Automation.
