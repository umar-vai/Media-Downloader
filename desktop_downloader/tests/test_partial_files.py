import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from partial_files import delete_partial_files, find_partial_files  # noqa: E402


class PartialFilesTests(unittest.TestCase):
    def test_removes_only_unfinished_files_of_that_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            title = "The Classroom of a Black Cat Episode 24"
            leftovers = [f"{title}.mp4.part", f"{title}.mp4.ytdl", f"{title}.f137.mp4",
                         f"{title}.f137.mp4.part-Frag12", f"{title} (2).mp4.part"]
            keep = [f"{title}.mp4", f"{title} 2.mp4.part", f"{title}.final.mp4", "Other.mp4.part"]
            for name in leftovers + keep:
                (root / name).write_bytes(b"x")
            self.assertEqual({p.name for p in find_partial_files(root, title + ".mp4")}, set(leftovers))
            removed, problems = delete_partial_files(root, title)
            self.assertEqual((removed, problems), (len(leftovers), []))
            self.assertEqual({p.name for p in root.iterdir()}, set(keep))

    def test_missing_folder_or_name_is_harmless(self) -> None:
        self.assertEqual(find_partial_files("/no/such/folder", "x"), [])
        self.assertEqual(find_partial_files("", "x"), [])


if __name__ == "__main__":
    unittest.main()


class ScanTests(unittest.TestCase):
    def test_scan_finds_leftovers_and_honours_keep(self) -> None:
        from partial_files import scan_partial_files
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            leftovers = {"a.mp4.part", "b.mp4.ytdl", "c.f137.mp4", "d.mp4.part-Frag3",
                         "e.zip.mdpart", "f.fhls-1080.mp4", "g.temp.mp4", "h.mp4.unpng"}
            keep = {"keep.mp4.part", "ok.mp4", "x.final.mp4", "x.part two.mp4"}
            for name in leftovers | keep:
                (root / name).write_bytes(b"x")
            found = {p.name for p in scan_partial_files([root, root], {root / "keep.mp4.part"})}
            self.assertEqual(found, leftovers)
