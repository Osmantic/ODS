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
