"""Documentation catalog and registry for ODS Documentation Webpage.

Maps documentation categories, slugs, titles, and filesystem paths within
the repository. Paths are relative to the installation root (INSTALL_DIR).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from config import INSTALL_DIR

logger = logging.getLogger(__name__)

DOC_CATEGORIES = [
    {
        "id": "getting-started",
        "title": "Getting Started",
        "description": "Onboarding, quickstarts, hardware requirements, and FAQs.",
        "order": 1,
    },
    {
        "id": "platform",
        "title": "Platform & Hardware",
        "description": "OS setup guides for Linux, WSL2, Windows, macOS, and GPU acceleration.",
        "order": 2,
    },
    {
        "id": "architecture",
        "title": "Core Architecture",
        "description": "System architecture, service contracts, Switchboard, and runtime engines.",
        "order": 3,
    },
    {
        "id": "pixel",
        "title": "Pixel & Assistant",
        "description": "Pixel agent runtime, identity, sandboxed approval terminal, and diagnostics.",
        "order": 4,
    },
    {
        "id": "networking",
        "title": "Networking & Security",
        "description": "Proxy routing, Tailscale, Hermes SSO, OAuth authentication, and security model.",
        "order": 5,
    },
    {
        "id": "extensions",
        "title": "Extensions & Addons",
        "description": "Extension ecosystem, catalog reference, service manifests, and workflows.",
        "order": 6,
    },
    {
        "id": "operations",
        "title": "Operations & Runbooks",
        "description": "Troubleshooting runbooks, diagnostic repair, offline mode, and maintainer guides.",
        "order": 7,
    },
    {
        "id": "dev",
        "title": "Developer & Releases",
        "description": "Contributing, API development, test suites, validation matrix, and release notes.",
        "order": 8,
    },
]

DOC_REGISTRY: list[dict[str, Any]] = [
    # --- Getting Started ---
    {
        "slug": "getting-started/overview",
        "category": "getting-started",
        "title": "ODS Overview & Features",
        "description": "Overview of the Open Distributed Server (ODS) AI platform and architecture.",
        "path": "README.md",
        "order": 1,
    },
    {
        "slug": "getting-started/quickstart",
        "category": "getting-started",
        "title": "Quickstart Guide",
        "description": "Fast-path installation, running services, and initial verification.",
        "path": "QUICKSTART.md",
        "order": 2,
    },
    {
        "slug": "getting-started/edge-quickstart",
        "category": "getting-started",
        "title": "Edge & Minimal Quickstart",
        "description": "Deploying ODS on edge devices and low-spec environments.",
        "path": "EDGE-QUICKSTART.md",
        "order": 3,
    },
    {
        "slug": "getting-started/hardware-guide",
        "category": "getting-started",
        "title": "Hardware & System Requirements",
        "description": "GPU, CPU, and RAM sizing for local inference and multi-service workflows.",
        "path": "docs/HARDWARE-GUIDE.md",
        "order": 4,
    },
    {
        "slug": "getting-started/headless-setup",
        "category": "getting-started",
        "title": "Headless Server Setup",
        "description": "Configuring ODS for headless home servers and cloud instances.",
        "path": "docs/HEADLESS-SETUP.md",
        "order": 5,
    },
    {
        "slug": "getting-started/setup-card",
        "category": "getting-started",
        "title": "First-Boot Setup Card",
        "description": "Step-by-step checklist for configuring your ODS instance after install.",
        "path": "docs/SETUP-CARD.md",
        "order": 6,
    },
    {
        "slug": "getting-started/post-install-checklist",
        "category": "getting-started",
        "title": "Post-Install Checklist",
        "description": "Post-installation health checks and verification procedures.",
        "path": "docs/POST-INSTALL-CHECKLIST.md",
        "order": 7,
    },
    {
        "slug": "getting-started/faq",
        "category": "getting-started",
        "title": "Frequently Asked Questions",
        "description": "Answers to common questions regarding local models, ports, and networking.",
        "path": "FAQ.md",
        "order": 8,
    },
    {
        "slug": "getting-started/licensing",
        "category": "getting-started",
        "title": "Licensing & Third-Party Notices",
        "description": "Apache-2.0 and Pixel ODS-only licensing terms and boundaries.",
        "path": "LICENSING.md",
        "order": 9,
    },

    # --- Platform & Hardware ---
    {
        "slug": "platform/linux-portability",
        "category": "platform",
        "title": "Linux Portability & Host Requirements",
        "description": "System requirements, distribution support, and systemd service models.",
        "path": "docs/LINUX-PORTABILITY.md",
        "order": 1,
    },
    {
        "slug": "platform/wsl2-gpu",
        "category": "platform",
        "title": "WSL2 GPU Passthrough Setup",
        "description": "Configuring NVIDIA GPU passthrough and CUDA in Windows Subsystem for Linux.",
        "path": "docs/WSL2-GPU-PASSTHROUGH.md",
        "order": 2,
    },
    {
        "slug": "platform/windows-quickstart",
        "category": "platform",
        "title": "Windows Quickstart Guide",
        "description": "Quickstart and requirements for Windows 10/11 hosts.",
        "path": "docs/WINDOWS-QUICKSTART.md",
        "order": 3,
    },
    {
        "slug": "platform/windows-walkthrough",
        "category": "platform",
        "title": "Windows Installation Walkthrough",
        "description": "Complete setup walkthrough for Windows native and WSL environments.",
        "path": "docs/WINDOWS-INSTALL-WALKTHROUGH.md",
        "order": 4,
    },
    {
        "slug": "platform/windows-comfyui",
        "category": "platform",
        "title": "Windows ComfyUI Standalone",
        "description": "Setting up ComfyUI acceleration alongside ODS on Windows hosts.",
        "path": "docs/WINDOWS-COMFYUI-STANDALONE.md",
        "order": 5,
    },
    {
        "slug": "platform/macos-quickstart",
        "category": "platform",
        "title": "macOS Quickstart & Metal Acceleration",
        "description": "Deploying ODS on Apple Silicon with Metal unified memory support.",
        "path": "docs/MACOS-QUICKSTART.md",
        "order": 6,
    },
    {
        "slug": "platform/macos-models",
        "category": "platform",
        "title": "macOS Model Promotion & State",
        "description": "Lifecycle management and model state handling on macOS hosts.",
        "path": "docs/MACOS-MODEL-PROMOTION.md",
        "order": 7,
    },
    {
        "slug": "platform/intel-arc",
        "category": "platform",
        "title": "Intel Arc GPU Guide",
        "description": "Configuring Intel Arc discrete GPUs with oneAPI / SYCL acceleration.",
        "path": "docs/INTEL-ARC-GUIDE.md",
        "order": 8,
    },
    {
        "slug": "platform/hardware-classes",
        "category": "platform",
        "title": "Hardware Classes & Auto-Tuning",
        "description": "Automated hardware tiers, VRAM thresholds, and execution profiles.",
        "path": "docs/HARDWARE-CLASSES.md",
        "order": 9,
    },
    {
        "slug": "platform/vllm-setup",
        "category": "platform",
        "title": "vLLM High-Throughput Setup",
        "description": "Configuring vLLM backend for continuous batching and multi-user load.",
        "path": "docs/VLLM-SETUP.md",
        "order": 10,
    },

    # --- Core Architecture ---
    {
        "slug": "architecture/how-ods-works",
        "category": "architecture",
        "title": "How ODS Server Works",
        "description": "Deep dive into daemon lifecycle, container orchestrator, and service meshes.",
        "path": "docs/HOW-ODS-SERVER-WORKS.md",
        "order": 1,
    },
    {
        "slug": "architecture/backend-contract",
        "category": "architecture",
        "title": "Backend Contract & Service Bus",
        "description": "Inter-service protocols, REST APIs, and state persistence contracts.",
        "path": "docs/BACKEND-CONTRACT.md",
        "order": 2,
    },
    {
        "slug": "architecture/model-switchboard",
        "category": "architecture",
        "title": "Model Switchboard & LiteLLM Gateway",
        "description": "Dynamic routing, fallback providers, and model alias management.",
        "path": "docs/MODEL-SWITCHBOARD.md",
        "order": 3,
    },
    {
        "slug": "architecture/model-router",
        "category": "architecture",
        "title": "ODS Model Router Service",
        "description": "Low-latency multiplexer for GGUF and external inference runtimes.",
        "path": "extensions/services/model-router/README.md",
        "order": 4,
    },
    {
        "slug": "architecture/model-management",
        "category": "architecture",
        "title": "Model Management Architecture",
        "description": "Downloading, verifying, and activating models in ODS.",
        "path": "docs/MODEL-MANAGEMENT.md",
        "order": 5,
    },
    {
        "slug": "architecture/mode-switch",
        "category": "architecture",
        "title": "Mode Switch Engine",
        "description": "Switching between local inference, cloud egress, and hybrid modes.",
        "path": "docs/MODE-SWITCH.md",
        "order": 6,
    },
    {
        "slug": "architecture/migration-lemonade-llamacpp",
        "category": "architecture",
        "title": "Migration: Lemonade to llama.cpp",
        "description": "Transition from Lemonade runtime to llama.cpp for AMD and Intel.",
        "path": "docs/MIGRATION-LEMONADE-TO-LLAMACPP.md",
        "order": 7,
    },
    {
        "slug": "architecture/installer-architecture",
        "category": "architecture",
        "title": "Installer Architecture",
        "description": "Multi-phase installer, recovery checkpoints, and system tuning.",
        "path": "docs/INSTALLER-ARCHITECTURE.md",
        "order": 8,
    },
    {
        "slug": "architecture/installer-trust",
        "category": "architecture",
        "title": "Installer Trust & Security Boundaries",
        "description": "Integrity verification, cryptographic trust roots, and isolation planes.",
        "path": "docs/INSTALLER_TRUST.md",
        "order": 9,
    },
    {
        "slug": "architecture/compose-resolvers",
        "category": "architecture",
        "title": "Compose Resolver Contracts",
        "description": "Deterministic composition merging across hardware profile overlays.",
        "path": "docs/COMPOSE_RESOLVER_CONTRACTS.md",
        "order": 10,
    },

    # --- Pixel & Assistant ---
    {
        "slug": "pixel/overview",
        "category": "pixel",
        "title": "Pixel Agent Platform",
        "description": "Architecture of the ambient, sandboxed conversational assistant.",
        "path": "docs/PIXEL.md",
        "order": 1,
    },
    {
        "slug": "pixel/assistant-identity",
        "category": "pixel",
        "title": "Assistant Identity & Personality",
        "description": "Identity contracts, system prompts, memory layers, and personality profiles.",
        "path": "docs/portal-assistant-identity.md",
        "order": 2,
    },
    {
        "slug": "pixel/approval-terminal",
        "category": "pixel",
        "title": "Approval Terminal & Tool Sandboxing",
        "description": "Human-in-the-loop tool confirmation and containment security.",
        "path": "docs/pixel/APPROVAL-TERMINAL.md",
        "order": 3,
    },
    {
        "slug": "pixel/transitions",
        "category": "pixel",
        "title": "Model Transitions & Recovery",
        "description": "Graceful swap mechanics between lightweight and full reasoning models.",
        "path": "docs/PORTAL-MODEL-TRANSITIONS.md",
        "order": 4,
    },
    {
        "slug": "pixel/diagnostics",
        "category": "pixel",
        "title": "Managed Diagnostics & Repairs",
        "description": "Self-healing runtime checks, health monitors, and repair loops.",
        "path": "docs/pixel/MANAGED-DIAGNOSTICS.md",
        "order": 5,
    },
    {
        "slug": "pixel/routing-access",
        "category": "pixel",
        "title": "Connections & Routing Access",
        "description": "Pixel ingress, edge gateways, and container socket bridges.",
        "path": "docs/pixel/CONNECTIONS-ROUTING-ACCESS.md",
        "order": 6,
    },

    # --- Networking & Security ---
    {
        "slug": "networking/network-overview",
        "category": "networking",
        "title": "Network Topology & Ports",
        "description": "Detailed port allocations, internal docker networks, and ingress listeners.",
        "path": "docs/NETWORK.md",
        "order": 1,
    },
    {
        "slug": "networking/ods-proxy",
        "category": "networking",
        "title": "ODS Reverse Proxy & mDNS",
        "description": "Local domain resolution (<device>.local) and TLS certificate termination.",
        "path": "docs/ODS-PROXY.md",
        "order": 2,
    },
    {
        "slug": "networking/tailscale",
        "category": "networking",
        "title": "Remote Access with Tailscale",
        "description": "Secure mesh VPN integration for out-of-home and remote access.",
        "path": "docs/TAILSCALE.md",
        "order": 3,
    },
    {
        "slug": "networking/hermes-sso",
        "category": "networking",
        "title": "Hermes SSO & Authentication",
        "description": "Single sign-on, cookie issuance, and service gate protection.",
        "path": "docs/HERMES-SSO.md",
        "order": 4,
    },
    {
        "slug": "networking/oauth-providers",
        "category": "networking",
        "title": "OAuth & OIDC Setup",
        "description": "Configuring GitHub, Google, and OpenID Connect identity providers.",
        "path": "docs/OAUTH_PROVIDER_SETUP.md",
        "order": 5,
    },
    {
        "slug": "networking/security-model",
        "category": "networking",
        "title": "Security Remediation & Threat Model",
        "description": "Remediation policies, least-privilege containers, and boundary guards.",
        "path": "docs/SECURITY_REMEDIATION_2026-09-28.md",
        "order": 6,
    },
    {
        "slug": "networking/security-policy",
        "category": "networking",
        "title": "Security Policy & Reporting",
        "description": "Vulnerability reporting, response process, and security policies.",
        "path": "SECURITY.md",
        "order": 7,
    },

    # --- Extensions & Addons ---
    {
        "slug": "extensions/overview",
        "category": "extensions",
        "title": "Extensions Ecosystem Overview",
        "description": "How community and built-in extensions run inside ODS.",
        "path": "docs/EXTENSIONS.md",
        "order": 1,
    },
    {
        "slug": "extensions/catalog",
        "category": "extensions",
        "title": "Extension Catalog Reference",
        "description": "Complete catalog of officially qualified services and workflows.",
        "path": "extensions/CATALOG.md",
        "order": 2,
    },
    {
        "slug": "extensions/service-manifest",
        "category": "extensions",
        "title": "Service Manifest V2 Plan",
        "description": "Specification for manifest.yaml declarations, ports, and dependencies.",
        "path": "docs/SERVICE_MANIFEST_V2_PLAN.md",
        "order": 3,
    },
    {
        "slug": "extensions/swap-safe",
        "category": "extensions",
        "title": "Swap-Safe Extensions Policy",
        "description": "State preservation standards during model and extension upgrades.",
        "path": "docs/SWAP-SAFE-EXTENSIONS.md",
        "order": 4,
    },
    {
        "slug": "extensions/dashboard-api",
        "category": "extensions",
        "title": "Dashboard API Service",
        "description": "Core administrative backend service and system status coordinator.",
        "path": "extensions/services/dashboard-api/README.md",
        "order": 5,
    },

    # --- Operations & Runbooks ---
    {
        "slug": "operations/troubleshooting",
        "category": "operations",
        "title": "Troubleshooting Master Guide",
        "description": "Root-cause diagnostics for common installation, model, and port conflicts.",
        "path": "docs/TROUBLESHOOTING.md",
        "order": 1,
    },
    {
        "slug": "operations/wsl-troubleshooting",
        "category": "operations",
        "title": "WSL2 GPU Troubleshooting",
        "description": "Detailed resolution for WSL driver, memory, and dockerd mount issues.",
        "path": "docs/WSL2-GPU-TROUBLESHOOTING.md",
        "order": 2,
    },
    {
        "slug": "operations/multi-user-setup",
        "category": "operations",
        "title": "Multi-User & Enterprise Setup",
        "description": "Multi-user authentication, quotas, and shared inference management.",
        "path": "docs/MULTI-USER-SETUP.md",
        "order": 3,
    },
    {
        "slug": "operations/ods-doctor",
        "category": "operations",
        "title": "Diagnostic System (ods doctor)",
        "description": "Automated system diagnostics and health check reporting.",
        "path": "docs/ODS-DOCTOR.md",
        "order": 4,
    },
    {
        "slug": "operations/offline-mode",
        "category": "operations",
        "title": "Offline Mirroring & Airgap Usage",
        "description": "Packaging models and containers for zero-internet environments.",
        "path": "docs/OFFLINE_AND_MIRRORING.md",
        "order": 5,
    },
    {
        "slug": "operations/maintainer-runbook",
        "category": "operations",
        "title": "Maintainer Runbook",
        "description": "Release management, container updates, and incident response procedures.",
        "path": "docs/MAINTAINER_RUNBOOK.md",
        "order": 6,
    },

    # --- Developer & Releases ---
    {
        "slug": "dev/contributing",
        "category": "dev",
        "title": "Contributing Guidelines",
        "description": "Code conventions, commit styles, pull request workflows, and reviews.",
        "path": "CONTRIBUTING.md",
        "order": 1,
    },
    {
        "slug": "dev/api-development",
        "category": "dev",
        "title": "Dashboard API Development",
        "description": "Local development guidelines for FastAPI routes, models, and tests.",
        "path": "docs/DASHBOARD-API-DEVELOPMENT.md",
        "order": 2,
    },
    {
        "slug": "dev/integration-guide",
        "category": "dev",
        "title": "Integration Guide",
        "description": "Connecting external tools, agents, and apps to the ODS stack.",
        "path": "docs/INTEGRATION-GUIDE.md",
        "order": 3,
    },
    {
        "slug": "dev/build-on-ods-server",
        "category": "dev",
        "title": "Build on ODS Server",
        "description": "Building custom applications and services on the ODS server.",
        "path": "docs/BUILD-ON-ODS-SERVER.md",
        "order": 4,
    },
    {
        "slug": "dev/testing",
        "category": "dev",
        "title": "Testing Suites & BATS Harness",
        "description": "Unit, integration, and end-to-end BATS test suites for ODS.",
        "path": "docs/TESTING.md",
        "order": 5,
    },
    {
        "slug": "dev/validation-matrix",
        "category": "dev",
        "title": "Validation Matrix",
        "description": "Supported OS, GPU, driver, and container runtime combinations.",
        "path": "docs/VALIDATION-MATRIX.md",
        "order": 6,
    },
    {
        "slug": "dev/support-matrix",
        "category": "dev",
        "title": "Platform Support Matrix",
        "description": "Detailed support matrix for GPUs, distributions, and hypervisors.",
        "path": "docs/SUPPORT-MATRIX.md",
        "order": 7,
    },
    {
        "slug": "dev/platform-truth-table",
        "category": "dev",
        "title": "Platform Truth Table",
        "description": "Canonical matrix of platform support, tiers, and capabilities.",
        "path": "docs/PLATFORM-TRUTH-TABLE.md",
        "order": 8,
    },
    {
        "slug": "dev/known-good-versions",
        "category": "dev",
        "title": "Known Good Versions",
        "description": "Pinned dependencies, image shas, and verified package versions.",
        "path": "docs/KNOWN-GOOD-VERSIONS.md",
        "order": 9,
    },
    {
        "slug": "dev/verified-install-preview",
        "category": "dev",
        "title": "Verified Install Preview",
        "description": "Signed source releases and verified installation preview.",
        "path": "docs/VERIFIED_INSTALL_PREVIEW.md",
        "order": 10,
    },
    {
        "slug": "dev/changelog",
        "category": "dev",
        "title": "Changelog & Version History",
        "description": "Detailed log of changes across all ODS releases.",
        "path": "CHANGELOG.md",
        "order": 11,
    },
    {
        "slug": "dev/release-3-0",
        "category": "dev",
        "title": "Release Notes 3.0.0",
        "description": "Feature highlights, deprecations, and upgrade guide for ODS 3.0.0.",
        "path": "docs/RELEASE_NOTES_3.0.0.md",
        "order": 12,
    },
    # --- Additional Registered Documents ---
    # Getting Started
    {
        "slug": "getting-started/third-party-licensing",
        "category": "getting-started",
        "title": "Third-Party Licensing Review",
        "description": "Third-party licensing, component notices, and distribution grant.",
        "path": "docs/THIRD-PARTY-LICENSING.md",
        "order": 10,
    },
    {
        "slug": "getting-started/asset-provenance",
        "category": "getting-started",
        "title": "Dashboard Artwork Provenance",
        "description": "Origin and attribution of dashboard visuals and audio-visual assets.",
        "path": "docs/ASSET-PROVENANCE.md",
        "order": 11,
    },
    {
        "slug": "getting-started/compatibility-matrix",
        "category": "getting-started",
        "title": "ODS Compatibility Matrix",
        "description": "Comprehensive hardware, OS, and GPU model compatibility matrix.",
        "path": "docs/COMPATIBILITY-MATRIX.md",
        "order": 12,
    },
    # Platform & Hardware
    {
        "slug": "platform/windows-wsl2-gpu",
        "category": "platform",
        "title": "Windows WSL2 GPU Guide for ODS",
        "description": "Detailed setup and tuning for NVIDIA GPU passthrough in Windows WSL2.",
        "path": "docs/WINDOWS-WSL2-GPU-GUIDE.md",
        "order": 11,
    },
    {
        "slug": "platform/docker-desktop-optimization",
        "category": "platform",
        "title": "Docker Desktop Optimization Guide",
        "description": "Memory and disk optimization for Docker Desktop on Windows and WSL2.",
        "path": "docs/DOCKER-DESKTOP-OPTIMIZATION.md",
        "order": 12,
    },
    {
        "slug": "platform/wsl-lifecycle",
        "category": "platform",
        "title": "ODS inside WSL: Owned Windows Lifetime",
        "description": "Process supervision and lifecycle coordination between Windows and WSL.",
        "path": "docs/WSL-LIFECYCLE.md",
        "order": 13,
    },
    {
        "slug": "platform/dgx-spark",
        "category": "platform",
        "title": "DGX Spark GB10 llama.cpp Notes",
        "description": "Technical notes and tuning for NVIDIA GB10 / DGX Spark systems.",
        "path": "docs/DGX-SPARK-GB10-LLAMA-CPP-NOTES.md",
        "order": 14,
    },
    {
        "slug": "platform/macos-private-dashboard",
        "category": "platform",
        "title": "Private Dashboard State on macOS",
        "description": "Handling private dashboard credentials and state on macOS.",
        "path": "docs/MACOS-PRIVATE-DASHBOARD-STATE.md",
        "order": 15,
    },
    {
        "slug": "platform/macos-held-model-recovery",
        "category": "platform",
        "title": "Recovering Held Mac Model Promotion",
        "description": "Diagnostics and recovery for held models during macOS promotion.",
        "path": "docs/MACOS_HELD_MODEL_RECOVERY.md",
        "order": 16,
    },
    # Core Architecture
    {
        "slug": "architecture/installer-phase-contracts",
        "category": "architecture",
        "title": "Installer Phase Contracts",
        "description": "Phase boundaries, state validation, and error contract of the installer.",
        "path": "docs/INSTALLER_PHASE_CONTRACTS.md",
        "order": 11,
    },
    {
        "slug": "architecture/preflight-engine",
        "category": "architecture",
        "title": "Installer Preflight Engine",
        "description": "Hardware and environment qualification engine specifications.",
        "path": "docs/PREFLIGHT-ENGINE.md",
        "order": 12,
    },
    {
        "slug": "architecture/profiles",
        "category": "architecture",
        "title": "Docker Compose Service Selection & Profiles",
        "description": "Dynamic service composition profiles and dependency graph.",
        "path": "docs/PROFILES.md",
        "order": 13,
    },
    {
        "slug": "architecture/engine-provider-modes",
        "category": "architecture",
        "title": "Engine Provider Modes",
        "description": "Local, hybrid, and remote model provider routing specifications.",
        "path": "docs/ENGINE-PROVIDER-MODES.md",
        "order": 14,
    },
    {
        "slug": "architecture/migration-openclaw-to-hermes",
        "category": "architecture",
        "title": "Migration: OpenClaw to Hermes",
        "description": "Architectural transition from legacy OpenClaw to Hermes gateway.",
        "path": "docs/MIGRATION-OPENCLAW-TO-HERMES.md",
        "order": 15,
    },
    {
        "slug": "architecture/host-agent-api",
        "category": "architecture",
        "title": "ODS Host Agent API",
        "description": "API contract for host supervisor, hardware monitors, and process controls.",
        "path": "docs/HOST-AGENT-API.md",
        "order": 16,
    },
    {
        "slug": "architecture/model-stores-mtp",
        "category": "architecture",
        "title": "Additional Model Stores & MTP",
        "description": "Multi-tier storage and model package repository specifications.",
        "path": "docs/MODEL-STORES-MTP.md",
        "order": 17,
    },
    # Pixel & Assistant
    {
        "slug": "pixel/portal-agent-experience",
        "category": "pixel",
        "title": "Portal Task Interface and Goal Execution",
        "description": "Autonomous execution, user approval loop, and task management.",
        "path": "docs/portal-agent-experience.md",
        "order": 7,
    },
    {
        "slug": "pixel/portal-context",
        "category": "pixel",
        "title": "Conversation Context in Portal",
        "description": "Context window tracking, message summarization, and context compaction.",
        "path": "docs/PORTAL-CONTEXT.md",
        "order": 8,
    },
    {
        "slug": "pixel/portal-harness-migration",
        "category": "pixel",
        "title": "Portal Harness Migration",
        "description": "Modernization of the Portal testing harness and test suites.",
        "path": "docs/PORTAL-HARNESS-MIGRATION.md",
        "order": 9,
    },
    {
        "slug": "pixel/portal-project-build",
        "category": "pixel",
        "title": "Managed Project Builds in Portal",
        "description": "How Pixel compiles, runs, and monitors managed projects.",
        "path": "docs/portal-project-build.md",
        "order": 10,
    },
    {
        "slug": "pixel/portal-source-review",
        "category": "pixel",
        "title": "Review Project Source and Published Output",
        "description": "Auditing and inspecting generated artifacts and code before promotion.",
        "path": "docs/portal-source-review.md",
        "order": 11,
    },
    {
        "slug": "pixel/macos-pixel-retirement",
        "category": "pixel",
        "title": "Retiring Native Pixel After Stopped Gateway",
        "description": "Recovery steps when native Pixel background service is stopped.",
        "path": "docs/MACOS_PIXEL_RETIREMENT_RECOVERY.md",
        "order": 12,
    },
    # Networking & Security
    {
        "slug": "networking/hermes",
        "category": "networking",
        "title": "Hermes Agent & Gateway",
        "description": "Hermes single sign-on gateway, cookie delegation, and proxy architecture.",
        "path": "docs/HERMES.md",
        "order": 8,
    },
    {
        "slug": "networking/mdns",
        "category": "networking",
        "title": "ODS mDNS — ods.local Discovery",
        "description": "Zero-configuration local network discovery for ODS endpoints.",
        "path": "docs/MDNS.md",
        "order": 9,
    },
    {
        "slug": "networking/ap-mode",
        "category": "networking",
        "title": "AP Mode — First-Boot Wi-Fi Access Point",
        "description": "Headless first-boot configuration via onboard Wi-Fi hotspot.",
        "path": "docs/AP-MODE.md",
        "order": 10,
    },
    {
        "slug": "networking/healthcheck-redirects",
        "category": "networking",
        "title": "Healthcheck Redirects & Proxy Probes",
        "description": "Guidelines for testing redirected healthcheck endpoints.",
        "path": "docs/HEALTHCHECK-REDIRECTS.md",
        "order": 11,
    },
    # Extensions & Addons
    {
        "slug": "extensions/readiness",
        "category": "extensions",
        "title": "Extension Catalog Source & Qualification Status",
        "description": "Qualification gates and readiness criteria for marketplace extensions.",
        "path": "docs/EXTENSION-READINESS.md",
        "order": 6,
    },
    {
        "slug": "extensions/pr-branching",
        "category": "extensions",
        "title": "Extension Changes: Branch Targets",
        "description": "Branching rules and contribution targets for extension services.",
        "path": "docs/EXTENSION-PR-BRANCHING.md",
        "order": 7,
    },
    # Operations & Runbooks
    {
        "slug": "operations/install-troubleshooting",
        "category": "operations",
        "title": "Installation Troubleshooting Guide",
        "description": "Diagnosing boot failures, permission errors, and container crashes.",
        "path": "docs/INSTALL-TROUBLESHOOTING.md",
        "order": 7,
    },
    {
        "slug": "operations/linux-troubleshooting",
        "category": "operations",
        "title": "Linux Troubleshooting Guide",
        "description": "Systemd, Docker rootless, and driver troubleshooting on Linux.",
        "path": "docs/LINUX-TROUBLESHOOTING-GUIDE.md",
        "order": 8,
    },
    {
        "slug": "operations/windows-troubleshooting",
        "category": "operations",
        "title": "Windows Troubleshooting Guide",
        "description": "Hyper-V, WSL2 memory exhaustion, and GPU passthrough fixes for Windows.",
        "path": "docs/WINDOWS-TROUBLESHOOTING-GUIDE.md",
        "order": 9,
    },
    {
        "slug": "operations/support-bundle",
        "category": "operations",
        "title": "Support Bundle & Diagnostic Logs",
        "description": "Generating sanitized diagnostic bundles for issue reporting.",
        "path": "docs/SUPPORT-BUNDLE.md",
        "order": 10,
    },
    {
        "slug": "operations/m1-offline-mode",
        "category": "operations",
        "title": "M1 Offline Mode — Air-Gapped Operation",
        "description": "Running Apple Silicon devices completely detached from internet access.",
        "path": "docs/M1-OFFLINE-MODE.md",
        "order": 11,
    },
    {
        "slug": "operations/field-install-report-linux",
        "category": "operations",
        "title": "Field Install Report (Linux)",
        "description": "Linux distributions field validation and hardware edge cases.",
        "path": "docs/FIELD-INSTALL-REPORT-LINUX.md",
        "order": 12,
    },
    # Developer & Releases
    {
        "slug": "dev/release-validation",
        "category": "dev",
        "title": "Release Validation Process",
        "description": "Pre-release testing checklist, qualification matrices, and signs-offs.",
        "path": "docs/RELEASE_VALIDATION.md",
        "order": 13,
    },
    {
        "slug": "dev/release-channels",
        "category": "dev",
        "title": "ODS Release Channels",
        "description": "Nightly, beta, and stable release promotion workflows.",
        "path": "docs/RELEASE_CHANNELS.md",
        "order": 14,
    },
    {
        "slug": "dev/release-notes-2-6",
        "category": "dev",
        "title": "Release Notes 2.6.0",
        "description": "Changelog and release notes for ODS 2.6.0.",
        "path": "docs/RELEASE_NOTES_2.6.0.md",
        "order": 15,
    },
    {
        "slug": "dev/signed-source-releases",
        "category": "dev",
        "title": "Signed Source Release Pipeline",
        "description": "GPG and Sigstore release artifact signing and validation.",
        "path": "docs/SIGNED_SOURCE_RELEASES.md",
        "order": 16,
    },
    {
        "slug": "dev/validation-reproducibility",
        "category": "dev",
        "title": "Validation Reproducibility Guide",
        "description": "Ensuring deterministic testing environments across development machines.",
        "path": "docs/VALIDATION_REPRODUCIBILITY.md",
        "order": 17,
    },
    {
        "slug": "dev/source-updates",
        "category": "dev",
        "title": "Source Update Limits & Security Boundaries",
        "description": "Security constraints on auto-updating local code checkouts.",
        "path": "docs/SOURCE-UPDATES.md",
        "order": 18,
    },
    {
        "slug": "dev/forkability",
        "category": "dev",
        "title": "Forkability & Independent Operation",
        "description": "Principles and procedures for independently maintaining an ODS fork.",
        "path": "docs/FORKABILITY.md",
        "order": 19,
    },
    {
        "slug": "dev/high-risk-change-map",
        "category": "dev",
        "title": "High-Risk Change Map",
        "description": "Critical files and systems requiring dual maintainer review.",
        "path": "docs/HIGH_RISK_CHANGE_MAP.md",
        "order": 20,
    },
    {
        "slug": "dev/ai-workflow-guardrails",
        "category": "dev",
        "title": "AI Workflow Guardrails",
        "description": "Development guardrails for code authored or assisted by AI agents.",
        "path": "docs/AI_WORKFLOW_GUARDRAILS.md",
        "order": 21,
    },
    {
        "slug": "dev/branch-hygiene",
        "category": "dev",
        "title": "Branch Hygiene Standards",
        "description": "Git branch naming, rebasing, and cleanliness standards.",
        "path": "docs/BRANCH_HYGIENE.md",
        "order": 22,
    },
    {
        "slug": "dev/contributor-ledger",
        "category": "dev",
        "title": "Main-Promotion Contributor Ledger",
        "description": "Historical record of community contributions to mainline ODS.",
        "path": "docs/CONTRIBUTOR-LEDGER-2026-09.md",
        "order": 23,
    },
    {
        "slug": "dev/documentation-hygiene",
        "category": "dev",
        "title": "Public Documentation Hygiene",
        "description": "Style guide, link validation, and editorial standards for docs.",
        "path": "docs/DOCUMENTATION-HYGIENE.md",
        "order": 24,
    },
    {
        "slug": "dev/maintenance-audit",
        "category": "dev",
        "title": "Hygiene Audit September 2026",
        "description": "Comprehensive audit of documentation, dependencies, and code hygiene.",
        "path": "docs/MAINTENANCE-AUDIT-2026-09.md",
        "order": 25,
    },
    {
        "slug": "dev/model-terms-inventory",
        "category": "dev",
        "title": "Model Terms Source Inventory",
        "description": "License terms and acceptable use policies for distributed model weights.",
        "path": "docs/MODEL-TERMS-INVENTORY.md",
        "order": 26,
    },
    {
        "slug": "dev/ods-cli-decomposition",
        "category": "dev",
        "title": "ODS CLI Decomposition Plan",
        "description": "Architectural refactoring of the monolithic ods CLI into modular units.",
        "path": "docs/ODS_CLI_DECOMPOSITION.md",
        "order": 27,
    },
    {
        "slug": "dev/oss-launch-checklist",
        "category": "dev",
        "title": "ODS OSS Launch Checklist",
        "description": "Open-source release readiness checklist and legal verification.",
        "path": "docs/OSS-LAUNCH-CHECKLIST.md",
        "order": 28,
    },
    {
        "slug": "dev/public-beta-promotion",
        "category": "dev",
        "title": "Public-Beta Promotion Record",
        "description": "Audit record and qualification sign-offs for public beta status.",
        "path": "docs/PUBLIC_BETA_PROMOTION_2026-09.md",
        "order": 29,
    },
    {
        "slug": "dev/recipe-source-register",
        "category": "dev",
        "title": "Recipe Source & Terms Register",
        "description": "License terms and attribution register for containerized recipes.",
        "path": "docs/RECIPE-SOURCE-REGISTER.md",
        "order": 30,
    },
    {
        "slug": "dev/runtime-dependency-locks",
        "category": "dev",
        "title": "Runtime Dependency Locks",
        "description": "Hermetic dependency lockfiles and runtime pinning specifications.",
        "path": "docs/RUNTIME_DEPENDENCY_LOCKS.md",
        "order": 31,
    },
    {
        "slug": "dev/secret-scanning",
        "category": "dev",
        "title": "Repository Secret Scanning",
        "description": "Secret detection rules, CI scan hooks, and credential handling.",
        "path": "docs/SECRET-SCANNING.md",
        "order": 32,
    },
    {
        "slug": "dev/capability-profile",
        "category": "dev",
        "title": "Capability Profile Contract",
        "description": "Machine capability reporting schema and hardware detection contract.",
        "path": "docs/CAPABILITY-PROFILE.md",
        "order": 33,
    },
    {
        "slug": "dev/integration-snapshots",
        "category": "dev",
        "title": "Integration Snapshots",
        "description": "Automated system test snapshots and regression baseline records.",
        "path": "docs/INTEGRATION-SNAPSHOTS.md",
        "order": 34,
    },
    {
        "slug": "dev/adr-image-tag-pinning",
        "category": "dev",
        "title": "ADR: Docker Image Tag Pinning Policy",
        "description": "Architecture decision record for image tagging and digest pinning.",
        "path": "docs/ADR-IMAGE-TAG-PINNING.md",
        "order": 35,
    },
    {
        "slug": "dev/llama-runtime-release-policy",
        "category": "dev",
        "title": "llama.cpp Runtime Release Boundaries",
        "description": "Policy and update boundaries for bundled llama.cpp engine binaries.",
        "path": "docs/llama-runtime-release-policy.md",
        "order": 36,
    },
]


def normalize_link_target(target: str) -> str:
    """Normalize a markdown file path or link target for lookup."""
    clean = target.strip().replace("\\", "/").lstrip("/")
    if clean.startswith("./"):
        clean = clean[2:]
    while clean.startswith("../"):
        clean = clean[3:]
    return clean.lower()


def get_path_to_slug_map() -> dict[str, str]:
    """Return dictionary mapping multiple path/filename variants to canonical slug."""
    mapping: dict[str, str] = {
        "readme.md": "getting-started/overview",
        "readme": "getting-started/overview",
        "docs/readme.md": "getting-started/overview",
        "docs/readme": "getting-started/overview",
        "quickstart.md": "getting-started/quickstart",
        "quickstart": "getting-started/quickstart",
        "docs/quickstart.md": "getting-started/quickstart",
        "docs/quickstart": "getting-started/quickstart",
        "faq.md": "getting-started/faq",
        "faq": "getting-started/faq",
        "docs/faq.md": "getting-started/faq",
        "docs/faq": "getting-started/faq",
        "licensing.md": "getting-started/licensing",
        "licensing": "getting-started/licensing",
        "license": "getting-started/licensing",
        "license.md": "getting-started/licensing",
        "security.md": "networking/security-policy",
        "security": "networking/security-policy",
        "changelog.md": "dev/changelog",
        "changelog": "dev/changelog",
        "contributing.md": "dev/contributing",
        "contributing": "dev/contributing",
        "docs/contributing.md": "dev/contributing",
        "code_of_conduct.md": "dev/code-of-conduct",
        "code_of_conduct": "dev/code-of-conduct",
        # Getting Started shortcuts
        "setup-card.md": "getting-started/setup-card",
        "setup-card": "getting-started/setup-card",
        "docs/setup-card.md": "getting-started/setup-card",
        "hardware-guide.md": "getting-started/hardware-guide",
        "hardware-guide": "getting-started/hardware-guide",
        "docs/hardware-guide.md": "getting-started/hardware-guide",
        "third-party-licensing.md": "getting-started/third-party-licensing",
        "third-party-licensing": "getting-started/third-party-licensing",
        "docs/third-party-licensing.md": "getting-started/third-party-licensing",
        # Platform shortcuts
        "intel-arc.md": "platform/intel-arc",
        "intel-arc": "platform/intel-arc",
        "docs/intel-arc.md": "platform/intel-arc",
        "intel-arc-guide.md": "platform/intel-arc",
        "docs/intel-arc-guide.md": "platform/intel-arc",
        "wsl2-gpu.md": "platform/wsl2-gpu",
        "wsl2-gpu": "platform/wsl2-gpu",
        "docs/wsl2-gpu.md": "platform/wsl2-gpu",
        "wsl2-gpu-passthrough.md": "platform/wsl2-gpu",
        "docs/wsl2-gpu-passthrough.md": "platform/wsl2-gpu",
        "linux-portability.md": "platform/linux-portability",
        "linux-portability": "platform/linux-portability",
        "docs/linux-portability.md": "platform/linux-portability",
        "macos-quickstart.md": "platform/macos-quickstart",
        "macos-quickstart": "platform/macos-quickstart",
        "docs/macos-quickstart.md": "platform/macos-quickstart",
        # Architecture shortcuts
        "architecture.md": "architecture/how-ods-works",
        "architecture": "architecture/how-ods-works",
        "docs/architecture.md": "architecture/how-ods-works",
        "model-router.md": "architecture/model-router",
        "model-router": "architecture/model-router",
        "docs/model-router.md": "architecture/model-router",
        "model-switchboard.md": "architecture/model-switchboard",
        "model-switchboard": "architecture/model-switchboard",
        "docs/model-switchboard.md": "architecture/model-switchboard",
        # Extensions shortcuts
        "extensions.md": "extensions/overview",
        "extensions": "extensions/overview",
        "docs/extensions.md": "extensions/overview",
        "extensions/readme.md": "extensions/catalog",
        "extensions/readme": "extensions/catalog",
        "extensions/catalog.md": "extensions/catalog",
        # Developer shortcuts
        "api/readme.md": "dev/api-overview",
        "api/readme": "dev/api-overview",
        "api-overview.md": "dev/api-overview",
        "docs/api.md": "dev/api-overview",
        "api.md": "dev/api-overview",
    }

    for doc in DOC_REGISTRY:
        slug = doc["slug"]
        path = doc.get("path", "")
        if not path:
            continue

        norm_path = normalize_link_target(path)
        filename = Path(path).name.lower()
        stem = Path(path).stem.lower()

        # Path variations
        mapping[path] = slug
        mapping[path.lower()] = slug
        mapping[norm_path] = slug
        if norm_path.endswith(".md"):
            mapping[norm_path[:-3]] = slug

        # Filename variations: only assign if not already claimed by a higher-priority file
        if filename not in mapping or path.count("/") == 0:
            mapping[filename] = slug
        if stem not in mapping or path.count("/") == 0:
            mapping[stem] = slug

        # Slug variations
        mapping[slug] = slug
        mapping[slug.lower()] = slug
        slug_leaf = slug.split("/")[-1].lower()
        if slug_leaf not in mapping:
            mapping[slug_leaf] = slug

    # Guarantee canonical root priorities
    canonical_roots = {
        "readme.md": "getting-started/overview",
        "readme": "getting-started/overview",
        "quickstart.md": "getting-started/quickstart",
        "quickstart": "getting-started/quickstart",
        "licensing.md": "getting-started/licensing",
        "licensing": "getting-started/licensing",
        "faq.md": "getting-started/faq",
        "faq": "getting-started/faq",
    }
    mapping.update(canonical_roots)

    return mapping


def get_docs_catalog() -> list[dict[str, Any]]:
    """Return documentation categories populated with sorted document entries."""
    category_map: dict[str, dict[str, Any]] = {
        cat["id"]: {**cat, "documents": []}
        for cat in sorted(DOC_CATEGORIES, key=lambda c: c["order"])
    }

    for doc in sorted(DOC_REGISTRY, key=lambda d: d.get("order", 0)):
        cat_id = doc.get("category")
        if cat_id in category_map:
            category_map[cat_id]["documents"].append({
                "slug": doc["slug"],
                "category": cat_id,
                "title": doc["title"],
                "description": doc.get("description", ""),
                "path": doc.get("path", ""),
                "order": doc.get("order", 0),
            })

    return list(category_map.values())


def find_doc_by_slug(slug: str) -> dict[str, Any] | None:
    """Find a document entry by its slug or path alias."""
    # 1. Exact slug match
    for doc in DOC_REGISTRY:
        if doc["slug"] == slug:
            return doc

    # 2. Lookup via canonical path mapping
    clean_target = normalize_link_target(slug)
    path_map = get_path_to_slug_map()

    if clean_target in path_map:
        target_slug = path_map[clean_target]
        for doc in DOC_REGISTRY:
            if doc["slug"] == target_slug:
                return doc

    if clean_target.endswith(".md"):
        stripped = clean_target[:-3]
        if stripped in path_map:
            target_slug = path_map[stripped]
            for doc in DOC_REGISTRY:
                if doc["slug"] == target_slug:
                    return doc

    # 3. Check if target had extra prefix e.g. "getting-started/docs/MACOS-QUICKSTART.md"
    parts = clean_target.split("/")
    for i in range(1, len(parts)):
        subpath = "/".join(parts[i:])
        if subpath in path_map:
            target_slug = path_map[subpath]
            for doc in DOC_REGISTRY:
                if doc["slug"] == target_slug:
                    return doc
        if subpath.endswith(".md") and subpath[:-3] in path_map:
            target_slug = path_map[subpath[:-3]]
            for doc in DOC_REGISTRY:
                if doc["slug"] == target_slug:
                    return doc

    filename = parts[-1]
    if filename in path_map:
        target_slug = path_map[filename]
        for doc in DOC_REGISTRY:
            if doc["slug"] == target_slug:
                return doc

    return None


def get_all_slugs() -> list[str]:
    """Return ordered list of all registered document slugs."""
    return [doc["slug"] for doc in sorted(DOC_REGISTRY, key=lambda d: (d.get("category", ""), d.get("order", 0)))]


def resolve_doc_path(rel_path: str) -> Path:
    """Resolve and validate a relative document path strictly within INSTALL_DIR.

    Raises ValueError if path traverses outside INSTALL_DIR or does not point to a markdown file.
    """
    clean_path = rel_path.strip().replace("\\", "/").lstrip("/")
    if ".." in clean_path.split("/"):
        raise ValueError("Directory traversal is not allowed")

    base = Path(INSTALL_DIR).resolve()
    target = (base / clean_path).resolve()

    # Must be within base directory
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError("Document path is outside allowed installation boundary") from exc

    if target.suffix.lower() not in (".md", ".markdown"):
        raise ValueError("Only markdown documents are supported")

    return target
