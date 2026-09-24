"""Keep the model inventory aligned with llama.cpp's modern file types."""
import pytest
from gguf_inspector import inspect_gguf
from performance_oracle import build_models_payload
from test_gguf_inspector import STR, U32, build_gguf


# llama_ftype from llama.cpp 7ceed8737fdb4eb09b4760e77bd12d38012de5a8.
# These are file types, not ggml tensor type IDs.
@pytest.mark.parametrize("file_type,label", [
    (36, "TQ1_0"), (37, "TQ2_0"), (38, "MXFP4_MOE"),
    (39, "NVFP4"), (40, "Q1_0"), (41, "Q2_0"),
])
def test_modern_quantization_reaches_model_inventory(tmp_path, data_dir, file_type, label):
    install = tmp_path / "ods"
    models = install / "data/models"
    models.mkdir(parents=True)
    path = models / "imported.gguf"
    path.write_bytes(build_gguf([
        ("general.architecture", STR, "llama"),
        ("general.file_type", U32, file_type),
    ]))
    catalog = [{"id":"modern", "name":"Imported 1B model", "gguf_file":path.name,
                "size_mb":500, "vram_required_gb":1, "context_length":8192,
                "quantization":"unknown"}]

    result = build_models_payload(None, None, 0, install, install / "data", catalog=catalog, evidence=[])

    assert result["models"][0]["quantization"] == label


@pytest.mark.parametrize("file_type", [33, 34, 35, 999])
def test_removed_repack_types_are_not_mislabelled_as_ternary(tmp_path, file_type):
    path = tmp_path / "legacy.gguf"
    path.write_bytes(build_gguf([("general.file_type", U32, file_type)]))
    assert inspect_gguf(path)["quantization"] == str(file_type)
