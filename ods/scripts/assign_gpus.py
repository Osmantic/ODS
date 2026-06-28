#!/usr/bin/env python3
"""
assign_gpus.py — GPU assignment algorithm for ODS

Usage:
    python3 assign_gpus.py --topology topo.json --model-size 70000
    python3 assign_gpus.py --topology topo.json --model-size 70000 --enabled-services llama_server,whisper

Output: gpu_assignment JSON to stdout
Errors: to stderr, exit code 1
"""

import argparse
import json
import math
import sys
from dataclasses import dataclass
from itertools import combinations
from typing import Optional


#  Constants

HIGH_BW_THRESHOLD = 80   # min rank for NVLink / XGMI
DEFAULT_SERVICES  = ["llama_server", "whisper", "comfyui", "embeddings"]
NON_LLAMA         = ["whisper", "comfyui", "embeddings"]


#  Data Models

@dataclass
class GPU:
    index:       int
    uuid:        str
    name:        str
    memory_mb:   float
    memory_type: str = "discrete"  # "discrete" or "unified" (APU)

@dataclass
class Link:
    gpu_a:      int
    gpu_b:      int
    link_type:  str
    link_label: str
    rank:       int

@dataclass
class Subset:
    gpus:             list
    min_link_rank:    int
    total_vram_mb:    float
    all_pairs_highbw: bool

@dataclass
class LlamaParallelism:
    mode:                     str
    tensor_parallel_size:     int
    pipeline_parallel_size:   int
    gpu_memory_utilization:   float
    tensor_split:             Optional[list] = None

@dataclass
class ServiceAssignment:
    gpus:        list
    parallelism: Optional[LlamaParallelism] = None

@dataclass
class AssignmentResult:
    strategy: str
    services: dict


#  Phase 1: Topology Analysis

def parse_gpus(topology: dict) -> list:
    gpus = []
    for g in topology["gpus"]:
        gpus.append(GPU(
            index=g["index"],
            uuid=g["uuid"],
            name=g["name"],
            memory_mb=g["memory_gb"] * 1024,
            memory_type=g.get("memory_type", "discrete"),
        ))
    return gpus


def parse_links(topology: dict) -> list:
    links = []
    for link in topology.get("links", []):
        links.append(Link(
            gpu_a=link["gpu_a"],
            gpu_b=link["gpu_b"],
            link_type=link["link_type"],
            link_label=link["link_label"],
            rank=link["rank"],
        ))
    return links


def build_rank_matrix(links: list) -> dict:
    """
    rank_matrix[(min_idx, max_idx)] = rank
    Pairs not in links default to 0.
    """
    matrix = {}
    for link in links:
        key = (min(link.gpu_a, link.gpu_b), max(link.gpu_a, link.gpu_b))
        matrix[key] = link.rank
    return matrix


def get_rank(rank_matrix: dict, a: int, b: int) -> int:
    return rank_matrix.get((min(a, b), max(a, b)), 0)


def compute_subset(gpus: list, rank_matrix: dict) -> Subset:
    """
    Compute a Subset from a list of GPUs.
    Single GPU: min_link_rank=0, all_pairs_highbw=True (no links needed).
    """
    if len(gpus) == 1:
        return Subset(
            gpus=gpus,
            min_link_rank=0,
            total_vram_mb=gpus[0].memory_mb,
            all_pairs_highbw=True,
        )

    indices = [g.index for g in gpus]
    ranks = [get_rank(rank_matrix, a, b) for a, b in combinations(indices, 2)]
    min_rank = min(ranks)

    return Subset(
        gpus=gpus,
        min_link_rank=min_rank,
        total_vram_mb=sum(g.memory_mb for g in gpus),
        all_pairs_highbw=(min_rank >= HIGH_BW_THRESHOLD),
    )


def enumerate_subsets(gpus: list, rank_matrix: dict) -> list:
    """
    Generate all non-empty subsets of GPUs, ordered by:
      1. min_link_rank DESC  (topology quality)
      2. subset size ASC     (prefer fewer GPUs, leave more for services)
      3. total_vram DESC     (tiebreaker)
    """
    all_subsets = []
    for size in range(1, len(gpus) + 1):
        for combo in combinations(gpus, size):
            all_subsets.append(compute_subset(list(combo), rank_matrix))

    return sorted(
        all_subsets,
        key=lambda s: (s.min_link_rank, -len(s.gpus), s.total_vram_mb),
        reverse=True,
    )


#  Phase 2: GPU Assignment

def find_llama_subset(ordered_subsets: list, model_size_mb: float) -> Subset:
    """
    Pick the best-ranked subset whose total VRAM covers model_size_mb.
    Returns the first match (best topology, smallest size, most VRAM).
    """
    for subset in ordered_subsets:
        if subset.total_vram_mb >= model_size_mb:
            return subset
    return None


def span_subsets(all_gpus: list, rank_matrix: dict, model_size_mb: float, ordered_subsets: list) -> Subset:
    """
    No single subset covers model_size_mb.
    Take the best subset, then greedily add GPUs from the remaining pool
    (ordered by memory_mb DESC) until VRAM is covered.
    Recomputes min_link_rank on the combined set.
    """
    best = ordered_subsets[0]
    accumulated = list(best.gpus)
    used = {g.index for g in accumulated}

    remaining = sorted(
        [g for g in all_gpus if g.index not in used],
        key=lambda g: g.memory_mb,
        reverse=True,
    )

    for gpu in remaining:
        accumulated.append(gpu)
        candidate = compute_subset(accumulated, rank_matrix)
        if candidate.total_vram_mb >= model_size_mb:
            return candidate

    raise ValueError(
        f"Model size {model_size_mb:.0f}MB exceeds total available VRAM "
        f"({sum(g.memory_mb for g in all_gpus):.0f}MB across all GPUs)."
    )


def assign_services(all_gpus: list, llama_gpus: list, rank_matrix: dict, enabled_services: list, vendor: str = "nvidia") -> tuple:
    """
    Assign remaining GPUs to non-llama services.
    Returns (service_assignments dict, final_llama_gpus list, strategy str).

    Rules:
      remaining == 0  → all 3 services share llama's last GPU       → colocated
      remaining == 1  → all 3 services share remaining[0]           → colocated
      remaining == 2  → whisper → [0], comfyui+embeddings → [1]    → colocated
      remaining >= 3  → whisper → [0], comfyui → [1], emb → [2]    → dedicated
                        remaining[3:] → back to llama

    AMD APU+dGPU hybrid: if mixed memory types (unified + discrete), prefer APU
    GPUs for auxiliary services (lower bandwidth but sufficient for whisper/embeddings).
    """
    llama_indices = {g.index for g in llama_gpus}
    remaining = sorted(
        [g for g in all_gpus if g.index not in llama_indices],
        key=lambda g: g.memory_mb,
        reverse=True,
    )

    # AMD hybrid strategy: sort remaining so APU GPUs come first for auxiliary
    # services (they're better suited for lightweight tasks like whisper/embeddings,
    # freeing discrete GPUs for LLM inference)
    if vendor == "amd" and any(g.memory_type == "unified" for g in remaining):
        remaining = sorted(
            remaining,
            key=lambda g: (0 if g.memory_type == "unified" else 1, -g.memory_mb),
        )

    active_non_llama = [s for s in NON_LLAMA if s in enabled_services]
    assignments = {}
    final_llama_gpus = list(llama_gpus)

    if len(remaining) == 0:
        fallback = llama_gpus[-1]
        for s in active_non_llama:
            assignments[s] = ServiceAssignment(gpus=[fallback])
        strategy = "colocated"

    elif len(remaining) == 1:
        for s in active_non_llama:
            assignments[s] = ServiceAssignment(gpus=[remaining[0]])
        strategy = "colocated"

    elif len(remaining) == 2:
        if "whisper" in enabled_services:
            assignments["whisper"] = ServiceAssignment(gpus=[remaining[0]])
        if "comfyui" in enabled_services:
            assignments["comfyui"] = ServiceAssignment(gpus=[remaining[1]])
        if "embeddings" in enabled_services:
            assignments["embeddings"] = ServiceAssignment(gpus=[remaining[1]])
        strategy = "colocated"

    else:
        if "whisper" in enabled_services:
            assignments["whisper"] = ServiceAssignment(gpus=[remaining[0]])
        if "comfyui" in enabled_services:
            assignments["comfyui"] = ServiceAssignment(gpus=[remaining[1]])
        if "embeddings" in enabled_services:
            assignments["embeddings"] = ServiceAssignment(gpus=[remaining[2]])
        # Push extras back to llama so no GPU sits idle
        if len(remaining) > 3:
            final_llama_gpus = final_llama_gpus + remaining[3:]
        strategy = "dedicated"

    assignments["llama_server"] = ServiceAssignment(gpus=final_llama_gpus)
    return assignments, final_llama_gpus, strategy


#  Phase 3: Llama Parallelism

def largest_pow2_divisor(n: int) -> int:
    """
    Find the largest power of 2 p such that:
      - p divides n evenly
      - p <= sqrt(n)  (keeps tensor_size <= pipeline_size for balance)
    Minimum return value is 2 (hybrid requires at least 2 tensor groups).
    """
    p = 1
    while True:
        candidate = p * 2
        if candidate > n or n % candidate != 0:
            break
        if candidate > math.sqrt(n):
            break
        p = candidate
    return max(2, p)


def is_heterogeneous(gpus: list) -> bool:
    vrams = [g.memory_mb for g in gpus]
    return max(vrams) != min(vrams)


def compute_tensor_split(gpus: list) -> list:
    """Proportional VRAM weights, rounded to 4 decimal places."""
    total = sum(g.memory_mb for g in gpus)
    return [round(g.memory_mb / total, 4) for g in gpus]


def select_parallelism(subset: Subset) -> LlamaParallelism:
    """
    Select parallelism mode based on GPU count and min_link_rank.

    Thresholds:
      rank >= 80  → NVLink / XGMI  → tensor or hybrid
      rank 11-79  → same-NUMA PCIe → pipeline, or hybrid if rank >= 40 and >= 4 GPUs
      rank <= 10  → cross-NUMA     → pipeline only
    """
    gpus  = subset.gpus
    n     = len(gpus)
    rank  = subset.min_link_rank
    split = compute_tensor_split(gpus) if is_heterogeneous(gpus) else None

    # Single GPU
    if n == 1:
        return LlamaParallelism(
            mode="none",
            tensor_parallel_size=1,
            pipeline_parallel_size=1,
            gpu_memory_utilization=0.95,
        )

    # High-bandwidth (NVLink / XGMI)
    if rank >= HIGH_BW_THRESHOLD:
        if n <= 3:
            return LlamaParallelism(
                mode="tensor",
                tensor_parallel_size=n,
                pipeline_parallel_size=1,
                gpu_memory_utilization=0.92,
                tensor_split=split,
            )
        else:
            tp = largest_pow2_divisor(n)
            pp = n // tp
            return LlamaParallelism(
                mode="hybrid",
                tensor_parallel_size=tp,
                pipeline_parallel_size=pp,
                gpu_memory_utilization=0.93,
                tensor_split=split,
            )

    # Cross-NUMA PCIe
    if rank <= 10:
        return LlamaParallelism(
            mode="pipeline",
            tensor_parallel_size=1,
            pipeline_parallel_size=n,
            gpu_memory_utilization=0.95,
        )

    # Same-NUMA PCIe (rank 11-79)
    if n <= 3:
        return LlamaParallelism(
            mode="pipeline",
            tensor_parallel_size=1,
            pipeline_parallel_size=n,
            gpu_memory_utilization=0.95,
        )
    else:
        if rank >= 40:
            tp = largest_pow2_divisor(n)
            pp = n // tp
            return LlamaParallelism(
                mode="hybrid",
                tensor_parallel_size=tp,
                pipeline_parallel_size=pp,
                gpu_memory_utilization=0.93,
                tensor_split=split,
            )
        else:
            return LlamaParallelism(
                mode="pipeline",
                tensor_parallel_size=1,
                pipeline_parallel_size=n,
                gpu_memory_utilization=0.95,
            )


#  Phase 4: Build Output JSON

def build_output(result: AssignmentResult) -> dict:
    services = {}

    for name, assignment in result.services.items():
        entry = {
            "gpus": [g.uuid for g in assignment.gpus],
            "gpu_indices": [g.index for g in assignment.gpus],
        }

        if assignment.parallelism:
            p = assignment.parallelism
            para = {
                "mode":                   p.mode,
                "tensor_parallel_size":   p.tensor_parallel_size,
                "pipeline_parallel_size": p.pipeline_parallel_size,
                "gpu_memory_utilization": p.gpu_memory_utilization,
            }
            if p.tensor_split is not None:
                para["tensor_split"] = p.tensor_split
            entry["parallelism"] = para

        services[name] = entry

    return {
        "gpu_assignment": {
            "version":  "1.0",
            "strategy": result.strategy,
            "services": services,
        }
    }


#  Entry Point

def main():
    parser = argparse.ArgumentParser(description="GPU assignment algorithm for ODS")
    parser.add_argument("--topology",         required=True,  help="Path to topology JSON file")
    parser.add_argument("--model-size",       required=True,  type=float, help="Model size in MB")
    parser.add_argument("--enabled-services", default=",".join(DEFAULT_SERVICES),
                        help="Comma-separated list of enabled services")
    args = parser.parse_args()

    # Load topology
    try:
        with open(args.topology) as f:
            topology = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: topology file not found: {args.topology}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON in topology file: {e}", file=sys.stderr)
        sys.exit(1)

    enabled_services = [s.strip() for s in args.enabled_services.split(",")]
    model_size_mb    = args.model_size
    gpu_count        = topology.get("gpu_count", 0)

    if gpu_count == 0:
        print("ERROR: no GPUs found in topology", file=sys.stderr)
        sys.exit(1)

    #  Early exit: single GPU
    if gpu_count == 1:
        gpu = parse_gpus(topology)[0]
        if model_size_mb > gpu.memory_mb:
            print(
                f"ERROR: Model size {model_size_mb:.0f}MB exceeds total available VRAM "
                f"({gpu.memory_mb:.0f}MB across all GPUs).",
                file=sys.stderr,
            )
            sys.exit(1)
        parallelism = LlamaParallelism(
            mode="none",
            tensor_parallel_size=1,
            pipeline_parallel_size=1,
            gpu_memory_utilization=0.95,
        )
        services = {}
        for s in enabled_services:
            services[s] = ServiceAssignment(gpus=[gpu])
        # llama_server always runs on the single GPU, even when the caller's
        # --enabled-services list omits it. This mirrors the multi-GPU path,
        # where assign_services() assigns llama_server unconditionally; without
        # it, the line below raised KeyError: 'llama_server'.
        services.setdefault("llama_server", ServiceAssignment(gpus=[gpu]))
        services["llama_server"].parallelism = parallelism
        result = AssignmentResult(strategy="single", services=services)
        print(json.dumps(build_output(result), indent=2))
        return

    #  Phase 1: Topology analysis
    gpus        = parse_gpus(topology)
    links       = parse_links(topology)
    rank_matrix = build_rank_matrix(links)
    ordered     = enumerate_subsets(gpus, rank_matrix)

    #  Phase 2: GPU assignment
    try:
        llama_subset = find_llama_subset(ordered, model_size_mb)
        if llama_subset is None:
            llama_subset = span_subsets(gpus, rank_matrix, model_size_mb, ordered)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    vendor = topology.get("vendor", "nvidia")

    # AMD APU+dGPU hybrid: if topology has mixed memory types, ensure LLM
    # gets discrete GPUs. Re-sort llama subset to prefer discrete over unified.
    if vendor == "amd":
        has_mixed = (
            any(g.memory_type == "unified" for g in gpus)
            and any(g.memory_type == "discrete" for g in gpus)
        )
        if has_mixed:
            discrete_gpus = [g for g in llama_subset.gpus if g.memory_type == "discrete"]
            if discrete_gpus:
                # Rebuild llama subset using only discrete GPUs if they cover model size
                discrete_subset = compute_subset(discrete_gpus, rank_matrix)
                if discrete_subset.total_vram_mb >= model_size_mb:
                    llama_subset = discrete_subset

    service_assignments, final_llama_gpus, strategy = assign_services(
        gpus, llama_subset.gpus, rank_matrix, enabled_services, vendor=vendor
    )

    #  Phase 3: Llama parallelism
    final_subset = compute_subset(final_llama_gpus, rank_matrix)
    parallelism  = select_parallelism(final_subset)
    service_assignments["llama_server"].parallelism = parallelism

    #  Phase 4: Emit JSON
    result = AssignmentResult(strategy=strategy, services=service_assignments)
    print(json.dumps(build_output(result), indent=2))


if __name__ == "__main__":
    main()
