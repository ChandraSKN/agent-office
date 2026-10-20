from pathlib import Path

from office import needs_approval

P = Path("/tmp/proj")


def test_approval_policy():
    safe = ["ls -la", "git diff", "python -m pytest -q", "pio run", "arduino-cli compile --fqbn esp32:esp32:esp32 .",
            "openscad -o case.stl case.scad", "ngspice -b divider.cir", "kicad-cli pcb export gerbers board.kicad_pcb",
            "kicad-cli sch erc main.kicad_sch", "prusa-slicer --export-gcode -o case.gcode case.stl"]
    risky = ["rm -rf build", "pio run -t upload", "git push", "pip install x", "ls; rm x",
             "cat a > b", "curl -X POST http://x", "esptool.py write_flash 0x0 fw.bin",
             "pio run --target upload", "arduino-cli upload -p /dev/ttyUSB0", "prusa-slicer case.stl"]
    for c in safe:
        assert not needs_approval("Bash", {"command": c}, P), c
    for c in risky:
        assert needs_approval("Bash", {"command": c}, P), c
    assert not needs_approval("Write", {"file_path": "/tmp/proj/bom.md"}, P)
    assert needs_approval("Write", {"file_path": "/home/x/.bashrc"}, P)
    assert needs_approval("Edit", {"file_path": "/tmp/proj/../etc"}, P)
    assert not needs_approval("Read", {"file_path": "/etc/passwd"}, P)


def test_valid_project(tmp_path):
    from office import valid_project
    home = Path.home()
    assert valid_project(home) is None                       # not the whole home folder
    assert valid_project("/etc") is None                     # outside home
    assert valid_project(home / "definitely-not-here-xyz") is None
    here = Path(__file__).resolve().parent
    if here.is_relative_to(home):
        assert valid_project(here) == here


def test_upload_path(tmp_path):
    from office import upload_path
    p = upload_path(tmp_path, "My Idea (v2).PDF")
    assert p.parent.parent == tmp_path / "briefs" and p.name == "My-Idea-v2.pdf"
    p.parent.mkdir(parents=True); p.write_text("x")
    assert upload_path(tmp_path, "My Idea (v2).PDF").name == "My-Idea-v2-2.pdf"    # never overwrites
    evil = upload_path(tmp_path, "../../../.bashrc.png")
    assert evil.parent.parent == tmp_path / "briefs"                                 # stays in briefs/
    assert upload_path(tmp_path, "run.sh") is None and upload_path(tmp_path, "x.exe") is None
