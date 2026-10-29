"""Multi-process scenarios: a server, headless clients, HASH comparison, cleanup, and the
determinism opt-out.

    python3 -m unittest tests/test_topology.py -v

Needs Godot. The server is the fixture's Python echo server (tests/fixture/net/echo_server.py);
the clients are the fixture's net_client scene, which joins it and prints HASH lines.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
FIXTURE = KIT / "tests" / "fixture"
NET = FIXTURE / "net"
sys.path.insert(0, str(KIT))

from godot_agent import runner, topology  # noqa: E402
from godot_agent.env import GodotAgentError  # noqa: E402

SERVER = {"cmd": ["{python}", "net/echo_server.py", "--port", "0"], "ready": r"listening on port (?P<port>\d+)"}
JOIN = ["--", "--join=127.0.0.1:{port}"]


def alive(pid: int) -> bool:
    """True while the process exists and is not a zombie waiting to be reaped."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


def gone_within(pid: int, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return False


class TopologyTest(unittest.TestCase):
    def run_fixture(self, paths=None, inline=None, **kwargs) -> dict:
        out = tempfile.mkdtemp(prefix="ga-topology-")
        self.addCleanup(shutil.rmtree, out, True)
        return runner.run_scenarios(FIXTURE, paths=paths, inline=inline, headless=True, out=out, **kwargs)

    def test_clients_join_the_port_the_server_printed_and_their_hashes_agree(self):
        summary = self.run_fixture([str(NET / "hashes_agree.json")])
        record = summary["scenarios"][0]
        self.assertTrue(record["passed"], record.get("reason"))
        self.assertEqual([c["name"] for c in record["clients"]], ["client1", "client2"])
        self.assertTrue(all(c["passed"] for c in record["clients"]))
        port = record["server"]["port"]
        self.assertTrue(port and port.isdigit())
        self.assertTrue(record["server"]["stopped_by_kit"])
        self.assertIn(f"--join=127.0.0.1:{port}", record["clients"][0]["args"])
        # Client 2 ran its own steps file, with {client} filled in.
        self.assertEqual(record["clients"][1]["assertions"], 2)
        self.assertEqual(record["hashes"]["compared_ticks"], 5)
        self.assertIsNone(record["hashes"]["first_divergence"])
        for client in record["clients"]:
            self.assertTrue((Path(client["out_dir"]) / "godot.log").is_file())
        self.assertTrue(Path(record["server"]["log"]).read_text().startswith("echo server listening on port"))
        text = runner.format_summary(summary)
        self.assertIn("hashes: 5 shared ticks agree", text)

    def test_a_disagreeing_hash_fails_and_names_the_first_tick(self):
        summary = self.run_fixture([str(NET / "hashes_disagree.json")])
        record = summary["scenarios"][0]
        self.assertFalse(record["passed"])
        # Each client passed its own steps: only the comparison fails the scenario.
        self.assertTrue(all(c["passed"] for c in record["clients"]))
        divergence = record["hashes"]["first_divergence"]
        self.assertEqual(divergence["tick"], 120)
        self.assertEqual(record["hashes"]["last_agreed_tick"], 60)
        self.assertNotEqual(divergence["hashes"]["client1"], divergence["hashes"]["client2"])
        self.assertTrue(record["reason"].startswith("hashes diverge at tick 120"), record["reason"])
        self.assertIn("first divergence at tick 120", runner.format_summary(summary))

    def test_timeout_stops_clients_server_and_the_servers_children(self):
        started = time.monotonic()
        summary = self.run_fixture([str(NET / "orphans.json")])
        self.assertLess(time.monotonic() - started, 30)
        record = summary["scenarios"][0]
        self.assertFalse(record["passed"])
        self.assertIn("timed out after 4s", record["reason"])
        self.assertTrue(all("topology timeout" in c["reason"] for c in record["clients"]))
        orphan = int((Path(record["out_dir"]) / "orphan.pid").read_text())
        pids = [record["server"]["pid"], orphan] + [c["pid"] for c in record["clients"]]
        for pid in pids:
            self.assertTrue(gone_within(pid), f"process {pid} outlived the topology")
        self.assertEqual(topology._LIVE, set())

    def test_a_server_that_never_gets_ready_fails_without_starting_clients(self):
        summary = self.run_fixture(inline={
            "name": "never_ready", "scene": "res://net/net_client.tscn",
            "topology": {"server": {"cmd": ["{python}", "net/echo_server.py", "--exit-early"],
                                    "ready": r"listening on port (?P<port>\d+)"},
                         "clients": [{"args": JOIN}]},
            "steps": [{"wait": 1}]})
        record = summary["scenarios"][0]
        self.assertFalse(record["passed"])
        self.assertEqual(record["reason"], "server exited with 3 before its output matched ready")
        self.assertEqual(record["clients"], [])
        self.assertIn("refusing to start", "\n".join(record["server"]["log_tail"]))

    def test_compare_hashes_fails_when_no_tick_was_shared(self):
        summary = self.run_fixture(inline={
            "name": "no_hashes", "scene": "res://net/net_client.tscn", "compare_hashes": True,
            "topology": {"server": SERVER, "clients": [{"args": JOIN}, {"args": JOIN}]},
            "steps": [{"wait": 2}]})
        record = summary["scenarios"][0]
        self.assertFalse(record["passed"])
        self.assertIn("no tick was hashed by two clients", record["reason"])

    def test_clients_without_a_server_and_a_port_placeholder_without_a_port(self):
        summary = self.run_fixture(inline={
            "name": "serverless", "scene": "res://net/net_client.tscn",
            "topology": {"clients": [{"steps": [{"assert": "'{client}' == '1'"}]},
                                     {"steps": [{"assert": "{clients} == 2"}]}]}})
        record = summary["scenarios"][0]
        self.assertTrue(record["passed"], record.get("reason"))
        self.assertIsNone(record["server"])

        summary = self.run_fixture(inline={
            "name": "portless", "scene": "res://net/net_client.tscn",
            "topology": {"server": {"cmd": SERVER["cmd"], "ready": "listening"}, "clients": [{"args": JOIN}]}})
        self.assertIn("captured no (?P<port>", summary["scenarios"][0]["reason"])

    def test_determinism_skips_topologies_and_opted_out_scenarios(self):
        summary = self.run_fixture([str(NET / "hashes_agree.json")], determinism=True)
        record = summary["scenarios"][0]
        self.assertTrue(record["passed"], record.get("reason"))
        self.assertIsNone(record["deterministic"])
        self.assertIn("real sockets", record["determinism"])
        self.assertFalse((Path(record["out_dir"]) / "a").exists())

        summary = self.run_fixture(inline={"name": "opted_out", "deterministic": False, "steps": [{"wait": 1}]},
                                   determinism=True)
        record = summary["scenarios"][0]
        self.assertIsNone(record["deterministic"])
        self.assertTrue((Path(record["out_dir"]) / "result.json").is_file())
        self.assertIn("not compared", runner.format_summary(summary))

    def test_a_topology_that_declares_itself_deterministic_is_run_twice_and_compared(self):
        scenario = json.loads((NET / "hashes_agree.json").read_text())
        scenario["deterministic"] = True
        scenario["topology"]["clients"][1].pop("scenario")
        summary = self.run_fixture(inline=scenario, determinism=True)
        record = summary["scenarios"][0]
        self.assertTrue(record["passed"], record.get("reason"))
        self.assertIs(record["deterministic"], True)
        self.assertTrue((Path(record["out_dir"]).parent / "b" / "client2" / "result.json").is_file())

    def test_validation(self):
        good = {"topology": {"server": SERVER, "clients": [{"args": JOIN, "scenario": "x.json"}],
                             "timeout": 30, "max_fps": 0, "compare_hashes": True}}
        runner.validate_scenario(good, "test")
        bad_topologies = [
            [], {"clients": []}, {"clients": [{"args": "--join=1"}]}, {"clients": ["client"]},
            {"server": {"cmd": [], "ready": "x"}, "clients": [{}]},
            {"server": {"cmd": ["srv"]}, "clients": [{}]},
            {"server": {"cmd": ["srv"], "ready": "("}, "clients": [{}]},
            {"clients": [{}], "timeout": 0}, {"clients": [{}], "max_fps": -1},
            {"clients": [{}], "compare_hashes": "yes"},
        ]
        for topo in bad_topologies:
            with self.subTest(topology=topo), self.assertRaises(GodotAgentError):
                runner.validate_scenario({"topology": topo}, "test")
        with self.assertRaises(GodotAgentError):
            runner.validate_scenario({"deterministic": "no"}, "test")

    def test_hash_streams(self):
        log = "noise\nHASH 60 ABCDEF\n\x1b[0mHASH 120 00ff\nHASH x 1\nHASH 180 zz\nHASH 60 abcdee\n"
        self.assertEqual(runner.hash_stream(log), {60: "abcdee", 120: "00ff"})
        result = topology.compare_hash_streams({"a": {60: "1", 120: "2", 180: "3"}, "b": {60: "1", 180: "4"},
                                                "c": {120: "2", 240: "5"}})
        self.assertEqual(result["compared_ticks"], 3)
        self.assertEqual(result["first_divergence"], {"tick": 180, "hashes": {"a": "3", "b": "4"}})
        self.assertEqual(result["last_agreed_tick"], 120)

    def test_determinism_compares_hash_streams_of_two_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            records = []
            for run, last in (("a", "aa"), ("b", "bb")):
                folder = Path(tmp) / run
                folder.mkdir()
                (folder / "result.json").write_text(json.dumps({"frames": 9, "final_watches": {}}))
                (folder / "godot.log").write_text(f"HASH 60 11\nHASH 120 {last}\n")
                records.append({"name": "x", "out_dir": str(folder), "passed": True})
            merged = runner._merge_determinism(*records)
            self.assertFalse(merged["passed"])
            self.assertEqual(merged["nondeterminism"], ["HASH stream differs first at tick 120: aa vs bb"])

    def test_args_and_placeholders(self):
        self.assertEqual(topology._split_args(["--verbose", "--", "--join=x"]), (["--verbose"], ["--join=x"]))
        self.assertEqual(topology._split_args(["--join=x"]), ([], ["--join=x"]))
        filled = topology._fill({"eval": "{'mode': {client}}", "args": ["--join=h:{port}"]},
                                {"{client}": "2", "{port}": "9"})
        self.assertEqual(filled, {"eval": "{'mode': 2}", "args": ["--join=h:9"]})


if __name__ == "__main__":
    unittest.main()
