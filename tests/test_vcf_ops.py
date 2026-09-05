"""Tests for bcftools argument construction and private transient manifests."""
import subprocess
from pathlib import Path

import pytest

from biocore.io import vcf_ops


def test_reheader_uses_private_temporary_name_file_and_removes_it(monkeypatch, tmp_path):
    seen = []

    def fake_run(cmd):
        if cmd[1] == "reheader":
            namefile = Path(cmd[cmd.index("-s") + 1])
            seen.append((namefile, namefile.read_text(), namefile.stat().st_mode & 0o777))

    monkeypatch.setattr(vcf_ops, "_run", fake_run)
    out = tmp_path / "renamed.vcf.gz"
    vcf_ops.reheader("source.vcf.gz", "SYNTHETIC_PATIENT_NAME", str(out))
    assert seen[0][1:] == ("SYNTHETIC_PATIENT_NAME\n", 0o600)
    assert not seen[0][0].exists()
    assert not out.with_suffix(".name.txt").exists()


def test_transient_manifest_is_removed_when_bcftools_fails(monkeypatch, tmp_path):
    seen = []

    def fail(cmd):
        manifest = Path(cmd[cmd.index("-s") + 1])
        seen.append(manifest)
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(vcf_ops, "_run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        vcf_ops.reheader("source.vcf.gz", "SYNTHETIC_PATIENT_NAME", str(tmp_path / "out.vcf.gz"))
    assert seen and not seen[0].exists()


def test_merge_keeps_list_argument_together_and_removes_private_manifest(monkeypatch, tmp_path):
    seen = []

    def fake_run(cmd):
        if cmd[1] == "merge":
            list_index = cmd.index("-l")
            manifest = Path(cmd[list_index + 1])
            seen.append((cmd, manifest, manifest.read_text(), manifest.stat().st_mode & 0o777))

    monkeypatch.setattr(vcf_ops, "_run", fake_run)
    vcfs = ["/private/SYNTHETIC_ALICE.vcf.gz", "/private/SYNTHETIC_BOB.vcf.gz"]
    out = tmp_path / "merged.vcf.gz"
    vcf_ops.merge(vcfs, str(out))
    cmd, manifest, contents, mode = seen[0]
    assert cmd[cmd.index("-l") + 1] == str(manifest)
    assert cmd.index("-0") < cmd.index("-l")
    assert contents == "\n".join(vcfs) + "\n"
    assert mode == 0o600
    assert not manifest.exists()
    assert not out.with_suffix(".merge_list.txt").exists()


def test_merge_manifest_is_removed_when_bcftools_fails(monkeypatch, tmp_path):
    seen = []

    def fail(cmd):
        manifest = Path(cmd[cmd.index("-l") + 1])
        seen.append(manifest)
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(vcf_ops, "_run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        vcf_ops.merge(["one.vcf.gz", "two.vcf.gz"], str(tmp_path / "out.vcf.gz"))
    assert seen and not seen[0].exists()
