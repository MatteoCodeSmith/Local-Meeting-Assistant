# Uiverse Cyberpunk icon adaptation

## Local voice recognition

The optional voice runtime uses SpeechBrain (Apache-2.0), its official
`speechbrain/spkrec-ecapa-voxceleb` model (Apache-2.0), and Silero VAD (MIT).
ECAPA architecture parameters in `voice_engine.py` follow the official model's
`hyperparams.yaml`; weights are downloaded only by the explicit installation script,
not redistributed in the source tree. Upstream references and licenses:

- https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb
- https://github.com/speechbrain/speechbrain/blob/develop/LICENSE
- https://github.com/snakers4/silero-vad/blob/master/LICENSE

The app provides heuristic voice grouping and user-confirmed profile enrollment;
upstream speaker verification results do not establish accuracy for meeting diarization.

## Circuit halo visual reference

The circuit halo in `src/local_meeting_assistant/circuits.py` is an original compact
Qt drawing inspired by the CSS/SVG supplied by the user, attributed in its source
to **om_6153 (Uiverse.io)**. It retains the visual idea of angular circuit traces,
outer contacts and inward pulses; the source button and embedded image are not used.

## Cyberpunk

The main Cyberpunk icon's silhouette, colors and glitch keyframes in
`src/local_meeting_assistant/appearance.py` are adapted from:
https://uiverse.io/andrew-demchenk0/lucky-bobcat-25

MIT License

Copyright - 2026 andrew-demchenk0 (A)

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in the
Software without restriction, including without limitation the rights to use,
copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the
Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
