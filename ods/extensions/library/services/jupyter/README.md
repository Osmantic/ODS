# Jupyter

Interactive computing environment for data science and machine learning. Comes with Python, NumPy, SciPy, Pandas, Matplotlib, and Scikit-learn pre-installed.

## Requirements

- **GPU:** NVIDIA or AMD (min 4 GB VRAM)
- **Dependencies:** None

## Enable / Disable

```bash
ods enable jupyter
ods disable jupyter
```

Your data is preserved when disabling. To re-enable later: `ods enable jupyter`

## Notebook directory permissions

The shipped image runs notebooks as `jovyan` (UID 1000). On root-operated
Linux/WSL installs, the host agent uses the manifest's container UID to prepare
`data/jupyter/workspace` and `data/jupyter/notebooks` before starting Jupyter.
This allows notebooks to be saved in both persistent mounts. After updating the
installed manifest, disable and re-enable Jupyter from the Extensions page to
run this preparation again; a plain container restart does not prepare mounts.

Preparation changes the two directory owners only; it does not recursively
change existing notebook files. For an imported notebook owned by another user,
back it up and grant UID 1000 write access to that file separately. Direct
Docker Compose users must prepare writable bind directories themselves. Custom
container users and rootless user-namespace mappings require matching host
permissions; this manifest describes the shipped image's default user.

## Access

- **URL:** `http://localhost:8889`

## First-Time Setup

1. Enable the service: `ods enable jupyter`
2. Open `http://localhost:8889`
3. Enter the access token to log in
4. Click "New" then "Python 3" to create a notebook

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `JUPYTER_TOKEN` | Access token for authentication (auto-generated) | _(required)_ |
