"""SEM0: the hardware preflight decides PASS or FAIL from measured numbers and picks the model cache drive.
Made-up measurements only; the real machine is not asserted on."""
import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GB = 1024 ** 3


def load():
    spec = importlib.util.spec_from_file_location("check_env", ROOT / "scripts" / "check_env.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CheckEnvTests(unittest.TestCase):
    def setUp(self):
        self.env = load()

    def drive(self, letter, free_gb, kind="fixed", bus="NVMe", total_gb=500):
        return self.env.Drive(letter=letter, kind=kind, bus=bus, total=total_gb * GB, free=int(free_gb * GB))

    def measured(self, **changes):
        base = dict(cpu_cores=8, ram_total=16 * GB, ram_free=6 * GB, gpu="none", drives=[self.drive("C", 40), self.drive("F", 200)],
                    repo_drive="C", cache_path=Path("C:/Users/x/.cache/huggingface"), cache_size=0)
        return self.env.Measured(**{**base, **changes})

    def test_every_requirement_passes_on_a_roomy_machine_and_states_its_number(self):
        lines = self.env.verdicts(self.measured())
        self.assertEqual([(line.name, line.verdict) for line in lines],
                         [("RAM total", "PASS"), ("Cache drive free", "PASS"), ("Repo drive free", "PASS"), ("GPU", "PASS")])
        self.assertIn("16.0 GB", lines[0].measured)
        self.assertIn("not required", lines[3].requirement)

    def test_each_threshold_fails_just_below_it(self):
        cases = {"RAM total": dict(ram_total=int(7.4 * GB)),
                 "Cache drive free": dict(drives=[self.drive("C", 7.9)]), "Repo drive free": dict(drives=[self.drive("C", 1.9)])}
        for name, change in cases.items():
            failed = [line.name for line in self.env.verdicts(self.measured(**change)) if line.verdict == "FAIL"]
            self.assertIn(name, failed, name)
        exactly = self.env.verdicts(self.measured(ram_total=int(7.5 * GB), drives=[self.drive("C", 8)]))
        self.assertTrue(all(line.verdict == "PASS" for line in exactly))

    def test_an_8_gb_machine_that_reports_7_7_passes_and_free_ram_is_not_a_gate(self):
        # Decided 2026-10-03: usable RAM on an 8 GB machine is about 7.7 GB, and free RAM is checked by the embedding
        # script when it runs (it needs about 1 GB), not by this preflight.
        lines = self.env.verdicts(self.measured(ram_total=int(7.7 * GB), ram_free=int(0.8 * GB)))
        self.assertTrue(all(line.verdict == "PASS" for line in lines))
        self.assertNotIn("RAM free", [line.name for line in lines])

    def test_the_gpu_line_never_fails_and_never_suggests_buying_anything(self):
        for gpu in ("none", "NVIDIA GeForce RTX 3050 (4.0 GB)"):
            line = self.env.verdicts(self.measured(gpu=gpu))[-1]
            self.assertEqual((line.name, line.verdict), ("GPU", "PASS"))
        source = (ROOT / "scripts" / "check_env.py").read_text(encoding="utf-8").casefold()
        for word in ("buy", "purchase", "upgrade your"):
            self.assertNotIn(word, source)

    def test_the_cache_stays_where_it_is_when_its_drive_has_room(self):
        decision = self.env.decide_cache(self.measured())
        self.assertEqual((decision.action, decision.drive), ("keep", "C"))

    def test_a_full_cache_drive_moves_the_cache_to_the_internal_drive_with_most_room(self):
        drives = [self.drive("C", 5), self.drive("D", 300, bus="USB"), self.drive("E", 50), self.drive("F", 120),
                  self.drive("G", 900, kind="removable", bus="USB"), self.drive("Z", 2000, kind="network", bus=None)]
        decision = self.env.decide_cache(self.measured(drives=drives))
        self.assertEqual((decision.action, decision.drive, decision.path), ("move", "F", Path("F:/ai-cache/huggingface")))
        self.assertIn("120.0 GB", decision.reason)
        external_only = [self.drive("C", 5), self.drive("D", 300, bus="USB"), self.drive("Z", 2000, kind="network", bus=None)]
        self.assertEqual(self.env.decide_cache(self.measured(drives=external_only)).drive, "D")     # external beats nothing; never Z

    def test_no_drive_with_room_means_stop(self):
        decision = self.env.decide_cache(self.measured(drives=[self.drive("C", 5), self.drive("F", 7.5),
                                                               self.drive("Z", 2000, kind="network", bus=None)]))
        self.assertEqual((decision.action, decision.drive), ("stop", None))

    def test_the_cache_path_follows_the_same_variables_huggingface_hub_reads(self):
        home = Path("C:/Users/x")
        self.assertEqual(self.env.hf_cache_path({}, home), home / ".cache" / "huggingface")
        self.assertEqual(self.env.hf_cache_path({"XDG_CACHE_HOME": "D:/xdg"}, home), Path("D:/xdg/huggingface"))
        self.assertEqual(self.env.hf_cache_path({"HF_HOME": "F:/ai-cache/huggingface", "XDG_CACHE_HOME": "D:/xdg"}, home),
                         Path("F:/ai-cache/huggingface"))

    def test_the_report_has_the_table_the_verdict_and_the_drive_decision(self):
        measured = self.measured(ram_total=6 * GB, ram_free=2 * GB, drives=[self.drive("C", 4), self.drive("F", 200)],
                                 cache_path=Path("F:/huggingface"))
        report = self.env.render(measured, self.env.verdicts(measured), self.env.decide_cache(measured), packages={"torch": None},
                                 python="3.11.16", conda_env="job-agent", checked_at="2026-10-03 10:00")
        self.assertIn("| RAM total | 6.0 GB | >= 7.5 GB | FAIL |", report)
        self.assertIn("| RAM free | 2.0 GB |", report)                 # shown under Machine, not judged
        self.assertNotIn("| RAM free | 2.0 GB | >=", report)
        self.assertIn("runtime guard", report)
        self.assertIn("Warning: C: has 4.0 GB free. Below 2 GB, stop and tell Rajdeep.", report)
        self.assertIn("No PyTorch", report)
        self.assertIn("FastEmbed", report)
        self.assertIn("BAAI/bge-small-en-v1.5", report)
        self.assertIn("| torch | not installed |", report)
        self.assertIn("Overall: FAIL", report)
        self.assertIn("Cache placement: keep", report)
        self.assertIn("| C: | fixed |", report)
        roomy = self.measured()
        self.assertNotIn("Warning:", self.env.render(roomy, self.env.verdicts(roomy), self.env.decide_cache(roomy), packages={},
                                                     python="3.11.16", conda_env="job-agent", checked_at="2026-10-03 10:00"))

    def test_the_script_measures_this_machine_without_installing_or_deleting(self):
        source = (ROOT / "scripts" / "check_env.py").read_text(encoding="utf-8")
        for forbidden in ("pip install", "conda install", "setx", "rmtree", "os.remove", ".unlink(", "urlopen", "requests.", "httpx"):
            self.assertNotIn(forbidden, source, forbidden)
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "env_check.md"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = self.env.main(["--report", str(report)])
            self.assertIn(code, (0, 1))                 # 1 when this machine fails a requirement
            self.assertIn("RAM total", output.getvalue())
            self.assertIn("## Requirements", report.read_text(encoding="utf-8"))
            self.assertEqual(sorted(path.name for path in Path(directory).iterdir()), ["env_check.md"])


if __name__ == "__main__":
    unittest.main()
