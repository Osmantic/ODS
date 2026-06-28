# ODS Hardware Guide

*Last updated: 2026-02-09 (merged M6 value research)*

What to buy for local AI at different budgets.

---

## TL;DR Recommendations

| Tier | GPU | RAM | What You Get |
|------|-----|-----|--------------|
| Lightweight (any) | Any GPU or CPU-only | 4GB+ | 2B model, personal chat |
| Entry ($800-1,200) | RTX 3060 12GB | 32GB | 7B-14B models, basic chat |
| Prosumer ($2,000-3,000) | RTX 4070 Ti Super 16GB | 64GB | 32B models, voice, 5-8 users |
| Pro ($4,000-6,000) | RTX 4090 24GB | 128GB | 70B models, 10-20 users |
| Enterprise ($12,000-18,000) | 2x RTX 4090 | 256GB | 40+ concurrent users |

---

## Tier 1: Entry ($800-1,200)

**Goal:** Get started with local AI, personal use

### Recommended Build
- **GPU:** RTX 3060 12GB (used: $200-250)
- **CPU:** Any modern 6+ core (i5-12400, Ryzen 5 5600)
- **RAM:** 32GB DDR4
- **Storage:** 500GB NVMe SSD
- **PSU:** 550W 80+ Bronze

### What Runs
- ✅ 7B-14B models (Qwen2.5-7B, Llama-3-8B)
- ✅ Basic voice (Whisper small/medium)
- ✅ Single user, personal projects
- ⚠️ Slow with complex prompts (~30 tok/s)

### Buy Used
Look for:
- Dell Precision/HP Z workstations with RTX 3060
- Avoid: GTX cards (no FP16)

---

## Tier 2: Prosumer ($2,000-3,000)

**Goal:** Serious local AI, small team use

### Recommended Build
- **GPU:** RTX 4070 Ti Super 16GB ($800) or RTX 4080 16GB ($1000)
- **CPU:** i7-13700 or Ryzen 7 7700X
- **RAM:** 64GB DDR5
- **Storage:** 1TB NVMe Gen4
- **PSU:** 750W 80+ Gold

### What Runs
- ✅ 32B AWQ quantized models (Qwen2.5-32B-AWQ)
- ✅ Full voice pipeline (Whisper medium + Kokoro)
- ✅ 5-8 concurrent users
- ✅ ~50-60 tok/s generation

### Best Value
RTX 4070 Ti Super at $800 is the sweet spot for:
- 16GB VRAM (critical for 32B models)
- Good efficiency (200W TDP)
- DLSS 3 for future-proofing

---

## Tier 3: Pro ($4,000-6,000)

**Goal:** Production workloads, growing business

### Recommended Build
- **GPU:** RTX 4090 24GB ($1800-2000)
- **CPU:** i9-14900K or Ryzen 9 7950X
- **RAM:** 128GB DDR5
- **Storage:** 2TB NVMe Gen4
- **PSU:** 1000W 80+ Platinum
- **Cooling:** AIO or custom loop (4090 runs hot)

### What Runs
- ✅ 70B AWQ models (Llama-3-70B-AWQ, Qwen2.5-72B-AWQ)
- ✅ Multiple models simultaneously
- ✅ 10-15 concurrent users
- ✅ Full RAG + embeddings + voice

### Alternative: Dual 4070 Ti
Two RTX 4070 Ti Super (32GB total) can be better than one 4090 for:
- Running separate specialized models
- Redundancy
- But: More complex setup, higher power

---

## Tier 4: Enterprise ($12,000-18,000)

**Goal:** Full production, organization-wide

### Option A: Dual RTX 4090
- 2x RTX 4090 (48GB VRAM total)
- Requires: PCIe bifurcation, 1500W+ PSU
- Good for: Separate model instances

### Option B: RTX 6000 Ada (48GB)
- Single GPU, 48GB VRAM
- Runs: 70B at FP16 (no quantization)
- Pro: Simpler than dual-GPU
- Con: $6000+

### Option C: Dual RTX PRO 6000 Blackwell (What We Run)
- 2x 96GB VRAM (192GB total)
- Runs: Multiple 70B models, 40+ users
- Cost: ~$15-20k total build

### Capacity (Our Real Numbers)
From M8 benchmarks on dual PRO 6000:

| Use Case | Per GPU | Both GPUs |
|----------|---------|-----------|
| Voice agents (<2s) | 10-20 | 20-40 |
| Interactive chat (<5s) | ~50 | ~100 |
| Batch processing | 100+ | 200+ |

---

## Best Value Picks (M6 Research)

Based on price/performance analysis (see `research/M6-CONSUMER-GPU-BENCHMARKS-2026-02-09.md`):

### Hidden Gem: Used RTX 3090 ($700-900)
At used prices, the RTX 3090 offers:
- 24GB VRAM (same as 4090!)
- 936 GB/s bandwidth (better than new 4080 SUPER)
- Runs 32B+ models that 16GB cards can't
- ~75% of 4090 performance at ~50% cost

**Trade-off:** Higher power (350W), older architecture

### Memory Bandwidth Insight
Token generation is **memory-bound**, not compute-bound. This is why:
- RTX 3080 Ti (912 GB/s) matches newer cards in inference
- Used high-bandwidth cards punch above their weight

### Quick Value Table

| Budget | Best Pick | Why |
|--------|-----------|-----|
| $250 | Used RTX 3060 12GB | Entry, can run 7B-14B |
| $500 | Used RTX 3080 Ti 12GB | Great bandwidth for price |
| $700-900 | **Used RTX 3090** | **Best overall value** |
| $800 | New RTX 4070 Ti SUPER | Best new 16GB card |
| $1,600 | RTX 4090 | Maximum single-GPU |

---

## Key Specs Explained

### VRAM (Most Important)
VRAM determines what models fit. Rough guide:

| VRAM | Max Model (AWQ 4-bit) |
|------|----------------------|
| 8GB | 7B |
| 12GB | 14B |
| 16GB | 32B |
| 24GB | 70B |
| 48GB | 70B FP16 or 2x 32B |

### Memory Bandwidth
Faster bandwidth = faster inference

| GPU | Bandwidth | Relative Speed |
|-----|-----------|----------------|
| RTX 3060 | 360 GB/s | 1.0x |
| RTX 4070 Ti | 504 GB/s | 1.4x |
| RTX 4090 | 1008 GB/s | 2.8x |
| PRO 6000 | 1792 GB/s | 5.0x |

### System RAM
Rule: 2x your model size minimum

| Model | Min RAM | Recommended |
|-------|---------|-------------|
| 7B | 16GB | 32GB |
| 32B | 32GB | 64GB |
| 70B | 64GB | 128GB |

---

## What NOT to Buy

❌ **GTX 16xx/10xx** — No FP16 tensor cores
❌ **AMD discrete GPUs (RX 7900 etc.)** — ROCm support limited; AMD Strix Halo APUs are fully supported (see README)
❌ **Intel Arc** — Driver problems, limited support
❌ **Cloud GPUs (H100/A100)** — Can't buy, rental only
⚠️ **8GB cards** — Limited to smaller models (2B-7B) but functional with Tier 0

---

## Where to Buy

### New
- Newegg, Amazon, Micro Center
- EVGA B-Stock (refurbished)
- Manufacturer direct (MSI, ASUS)

### Used
- eBay (check seller ratings)
- r/hardwareswap
- Facebook Marketplace (local pickup)
- Mining cards: Usually fine, verify fans work

---

## Power Considerations

| GPU | TDP | PSU Needed |
|-----|-----|------------|
| RTX 3060 | 170W | 550W |
| RTX 4070 Ti | 285W | 700W |
| RTX 4090 | 450W | 1000W |
| Dual 4090 | 900W | 1500W |

Add 150-200W for CPU + system overhead.

---

## Cooling

- **Single GPU:** Good case airflow is enough
- **RTX 4090:** AIO or very good air cooling (315W slot power)
- **Dual GPU:** Custom loop or enterprise chassis

---

## Summary

1. **Entry:** RTX 3060 12GB — personal use, getting started
2. **Prosumer:** RTX 4070 Ti Super 16GB — serious work, small teams
3. **Pro:** RTX 4090 24GB — production workloads, 10-20 users
4. **Enterprise:** Dual 4090 — organization-wide, 40+ users

**VRAM is king.** Buy the most VRAM you can afford.

---

*Built by The Collective based on real-world testing*
