# Label Studio

Open-source data labeling tool for machine learning. Label images, audio, text, time series, and more — with multi-user collaboration, quality control, and export to common ML formats.

## Requirements

- **GPU:** CPU only — no GPU required
- **Dependencies:** None

## Enable / Disable

```bash
ods enable label-studio
ods disable label-studio
```

Your data is preserved when disabling. To re-enable later: `ods enable label-studio`

## Access

- **URL:** `http://localhost:8086`

## First-Time Setup

1. Enable the service: `ods enable label-studio`
2. Open `http://localhost:8086`
3. Create an account on first launch
4. Create a project and import data for labeling

## Local dataset imports

The local-files API is restricted to `/label-studio/upload`, backed by the
existing `ods/upload/` directory. Put each dataset in a subdirectory such as
`ods/upload/my-dataset/`, then configure a Local Files source with absolute
path `/label-studio/upload/my-dataset`. File URLs are relative to that root,
for example `/data/local-files/?d=my-dataset/image.png`. The application still
checks project membership for each local-file request.

This prevents a project user from registering an arbitrary directory such as
`/tmp` or the application's configuration directory as a local dataset.
It does not isolate the Label Studio process from its own mounted files, and
operators must not place secrets or links to sensitive paths in the dataset
directory. Existing upload, media, static and database mounts are preserved.

In upstream 1.22.0, a crafted parent-directory traversal is rejected by Django
but its API exception handler reports HTTP 500 with a path error. This profile
does not patch that upstream response mapping. Ordinary old root-relative file
URLs outside the dataset mount return 404, and outside storage registration
returns 400; the regression checks that no outside file content is returned.

### Existing local-file projects

Earlier profiles enabled local-file serving with the image's `/` default.
Before recreating the service, back up the database and datasets and inspect
Local Files source paths and stored task URLs. Move or copy intended datasets
under `ods/upload/<dataset>/`, update their source paths, and update/reimport
task URLs relative to `/label-studio/upload`. Verify an image/audio/text sample
in each affected project. Browser-uploaded data uses the existing media path;
the new root applies to Local Files storage.

Recreate the container to activate the new environment value. Disabling and
reenabling alone must not be assumed to replace an existing container. The
image remains version 1.22.0 and is now pinned to the tested digest. Rollback
requires restoring the prior Compose configuration and any intentionally
changed task/source records from the backup; restoring the `/` default also
restores the broad file-serving exposure.

## Regression verification

`pytest ods/tests/test_label_studio_local_files.py -q` validates the rendered
mount contract. `ODS_TEST_LABEL_STUDIO=1 pytest
ods/tests/test_label_studio_local_files.py -q -s` also runs the shipped image's
actual Django/DRF project, storage and file-serving APIs against synthetic
inside/outside datasets. It seeds a normal user via the test authentication
adapter; it does not claim to test the browser login flow. The one-shot
container has no network or host ports and owns only temporary volumes.

Upstream [storage validation](https://github.com/HumanSignal/label-studio/blob/1.22.0/label_studio/io_storages/localfiles/models.py)
and [file-serving view](https://github.com/HumanSignal/label-studio/blob/1.22.0/label_studio/io_storages/localfiles/views.py)
enforce the configured document root.
