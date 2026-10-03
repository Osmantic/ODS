"""Observed and adversarial mount graphs for WSL Pixel socket migration."""

import importlib.util
from pathlib import Path
import sys
import unittest


MODULE = Path(__file__).resolve().parents[1] / "installers/lib/wsl_pixel_mount_plan.py"
spec = importlib.util.spec_from_file_location("wsl_pixel_mount_plan", MODULE)
plan = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plan
spec.loader.exec_module(plan)


def row(mount_id, root, target, device="0:106", shared="shared:47"):
    return f"{mount_id} 345 {device} {root} {target} rw,nosuid,nodev {shared} - tmpfs none rw"


ORDINARY = "\n".join(
    [
        row(1375, "/ods-pixel", plan.INGRESS),
        row(754, "/ods-pixel-preview", plan.PREVIEW),
    ]
)
IDENTITIES = {
    "/run/ods-pixel": (106, 703, "directory"),
    plan.INGRESS: (106, 703, "directory"),
    "/run/ods-pixel-preview": (106, 710, "directory"),
    plan.PREVIEW: (106, 710, "directory"),
}
PROXY_INGRESS = plan.PROXY_PREFIX + "Ubuntu-24.04/" + "a" * 64
PROXY_PREVIEW = plan.PROXY_PREFIX + "Ubuntu-24.04/" + "b" * 64


class MountPlanTests(unittest.TestCase):
    def test_current_observed_ordinary_pair(self):
        result = plan.classify(ORDINARY, IDENTITIES, True)
        self.assertEqual(result["status"], "ordinary")
        self.assertEqual(
            {row["root"] for row in result["rows"]},
            {"/ods-pixel", "/ods-pixel-preview"},
        )

    def test_active_edge_projections_need_owned_container_stop(self):
        contents = ORDINARY + "\n" + row(1157, "/ods-pixel-preview", PROXY_PREVIEW)
        contents += "\n" + row(1224, "/ods-pixel", PROXY_INGRESS)
        identities = dict(IDENTITIES)
        identities[PROXY_PREVIEW] = identities["/run/ods-pixel-preview"]
        identities[PROXY_INGRESS] = identities["/run/ods-pixel"]
        result = plan.classify(contents, identities, True)
        self.assertEqual(result["status"], "needs-owned-edge-stop")
        self.assertEqual(len(result["rows"]), 4)
        identities[PROXY_INGRESS] = (106, 999, "directory")
        self.assertEqual(plan.classify(contents, identities, True)["status"], "refuse")

    def test_clear_without_old_targets(self):
        result = plan.classify(row(1, "/", "/"), {}, True)
        self.assertEqual(result["status"], "clear")

    def test_incident_graph_refused_before_migration(self):
        lines = [
            row(i, "/ods-portal-runtime/ingress", plan.INGRESS) for i in range(1, 512)
        ]
        lines += [
            row(i, "/ods-portal-runtime/preview", plan.PREVIEW)
            for i in range(512, 1023)
        ]
        for number, root in enumerate(
            ("/ods-portal-runtime/ingress", "/ods-portal-runtime/preview")
        ):
            target = f"{plan.PROXY_PREFIX}Ubuntu-24.04/{number:064d}"
            lines += [
                row(i, root, target)
                for i in range(1023 + number * 512, 1535 + number * 512)
            ]
        result = plan.classify("\n".join(lines), IDENTITIES, True)
        self.assertEqual(result["status"], "refuse")
        self.assertTrue(any("stacked" in reason for reason in result["reasons"]))
        self.assertTrue(any("projection" in reason for reason in result["reasons"]))
        self.assertEqual(result["counts"]["old_targets"], 1022)
        self.assertEqual(result["counts"]["desktop_projections"], 1024)
        self.assertLessEqual(len(result["rows"]), 8)

    def test_proxy_only_is_not_clear(self):
        result = plan.classify(
            row(5, "/ods-pixel", plan.PROXY_PREFIX + "Ubuntu-24.04/hash"), {}, True
        )
        self.assertEqual(result["status"], "refuse")

    def test_projection_requires_same_shared_group_as_owned_target(self):
        identities = dict(IDENTITIES)
        identities[PROXY_INGRESS] = identities["/run/ods-pixel"]
        identities[PROXY_PREVIEW] = identities["/run/ods-pixel-preview"]
        for propagation in ("shared:48", "master:47", "shared:"):
            contents = (
                ORDINARY
                + "\n"
                + row(10, "/ods-pixel", PROXY_INGRESS, shared=propagation)
            )
            contents += "\n" + row(11, "/ods-pixel-preview", PROXY_PREVIEW)
            result = plan.classify(contents, identities, True)
            self.assertEqual(result["status"], "refuse")
            self.assertTrue(
                any(
                    "projection propagation differs" in reason
                    for reason in result["reasons"]
                )
            )

    def test_partial_or_foreign_target_refused(self):
        self.assertEqual(
            plan.classify(row(5, "/ods-pixel", plan.INGRESS), IDENTITIES, True)[
                "status"
            ],
            "refuse",
        )
        foreign = ORDINARY.replace("/ods-pixel-preview", "/other-preview")
        self.assertEqual(plan.classify(foreign, IDENTITIES, True)["status"], "refuse")

    def test_nested_mount_and_namespace_drift_refused(self):
        nested = ORDINARY + "\n" + row(9, "/foreign", plan.INGRESS + "/nested")
        self.assertEqual(plan.classify(nested, IDENTITIES, True)["status"], "refuse")
        source_nested = ORDINARY + "\n" + row(9, "/foreign", "/run/ods-pixel/nested")
        self.assertEqual(
            plan.classify(source_nested, IDENTITIES, True)["status"], "refuse"
        )
        projected = ORDINARY + "\n" + row(10, "/ods-pixel", PROXY_INGRESS)
        projected += "\n" + row(11, "/ods-pixel-preview", PROXY_PREVIEW)
        projected += "\n" + row(12, "/foreign", PROXY_INGRESS + "/nested")
        identities = dict(IDENTITIES)
        identities[PROXY_INGRESS] = identities["/run/ods-pixel"]
        identities[PROXY_PREVIEW] = identities["/run/ods-pixel-preview"]
        self.assertEqual(plan.classify(projected, identities, True)["status"], "refuse")
        self.assertEqual(plan.classify(ORDINARY, IDENTITIES, False)["status"], "refuse")

    def test_inode_or_device_mismatch_refused(self):
        bad = dict(IDENTITIES)
        bad[plan.PREVIEW] = (106, 999, "directory")
        self.assertEqual(plan.classify(ORDINARY, bad, True)["status"], "refuse")
        self.assertEqual(
            plan.classify(ORDINARY.replace("0:106", "0:107"), IDENTITIES, True)[
                "status"
            ],
            "refuse",
        )
        self.assertEqual(plan.classify(ORDINARY, None, True)["status"], "refuse")

    def test_mountinfo_escape_and_malformed_input(self):
        escaped = row(1, "/a\\040b", "/foreign\\134path")
        self.assertEqual(plan.parse_mountinfo(escaped)[0].root, "/a b")
        self.assertEqual(plan.parse_mountinfo(escaped)[0].target, "/foreign\\path")
        for malformed in (
            "",
            "1 2 0:1 / / rw",
            row(1, "/a\\999", "/foreign"),
            row(1, "/a\\777", "/foreign"),
        ):
            self.assertEqual(plan.classify(malformed, {}, True)["status"], "refuse")


if __name__ == "__main__":
    unittest.main()
