# README images

Three images are wanted for the README. None exist yet, and none is linked
on purpose: a broken image on the front page is worse than none.
`tests/test_readme_images.py` fails the moment the README links an image
that is not in this folder.

| File | What it shows | How to capture it |
|---|---|---|
| `preview-spread.png` | The main window with a document loaded, the **Both** toggle on, showing the front/back spread side by side with the red imageable-area guide and the dashed blue content box both visible. The single most persuasive image available. | Launch with `./run.sh` (or `run.bat`) on a real desktop. Import a numbered dummy (`./run.sh cli dummy -o dummy.pdf --pages 16`) and capture the window. **Not** with `QT_QPA_PLATFORM=offscreen`: an offscreen grab has no window decoration and no real theme, and the point is what the app looks like. |
| `print-dialog.png` | The Print dialog mid-job, showing the reload instruction for pass 2 in plain words. | As above, with a printer attached, partway through a two-pass job. |
| `schedule.png` | A printed binding schedule on a bench beside a folded signature. | A **photograph**, not a screenshot. It is the one image that shows the artefact rather than the software. |

When one exists, put it here under exactly that name and add it to the
README's "SCREENSHOTS WANTED" comment's place.
