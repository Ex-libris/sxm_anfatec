"""Position provenance and passive Select reads, including a separate Windows process."""
from pathlib import Path
import multiprocessing
import time
import unittest
from unittest.mock import patch

from sxm_anfatec.selected_point import read_point, read_tree, parse_coordinates
from sxm_anfatec.spectroscopy_metadata import instrument_fields, enriched_bytes, original_bytes, summaries


class FakeTree:
    def __init__(self):
        self.selection = 2
        self.labels = {1: 'Topo Fwd | 20.000', 2: 'Point', 3: 'X | -273.038',
                       4: 'Y | -667.761', 5: 'Z | 143.604', 6: 'Point', 7: 'X | 99', 8: 'Y | 88'}
        self.parents = {1: 0, 2: 1, 3: 2, 4: 2, 5: 2, 6: 1, 7: 6, 8: 6}
    def selected(self): return self.selection
    def text(self, item): return self.labels[item]
    def parent(self, item): return self.parents[item]
    def children(self, item): return [key for key, value in self.parents.items() if value == item]


def native_tree_process(pipe):
    """Test-only window. Writes affect this stand-in, never SXM."""
    import win32gui, win32con, win32gui_struct, commctrl
    win32gui.InitCommonControls()
    root = win32gui.CreateWindowEx(0, 'Static', 'Select test', 0, 0, 0, 300, 400, 0, 0, 0, None)
    tree = win32gui.CreateWindowEx(0, 'SysTreeView32', '', win32con.WS_CHILD,
                                 0, 0, 250, 350, root, 0, 0, None)
    def insert(parent, label):
        data, extra = win32gui_struct.PackTVINSERTSTRUCT(parent, commctrl.TVI_LAST,
                                                       (None, None, None, label, None, None, None, None))
        return win32gui.SendMessage(tree, commctrl.TVM_INSERTITEMW, 0, data)
    try:
        image = insert(commctrl.TVI_ROOT, 'Topo Fwd | 20.000')
        point = insert(image, 'Point')
        for label in ('X | -273.038', 'Y | -667.761', 'Z | 143.604'):
            insert(point, label)
        win32gui.SendMessage(tree, commctrl.TVM_SELECTITEM, commctrl.TVGN_CARET, point)
        pipe.send((root, tree, point))
        while not pipe.poll():
            win32gui.PumpWaitingMessages()
            time.sleep(.005)
    finally:
        win32gui.DestroyWindow(root)
        pipe.close()


class PositionTests(unittest.TestCase):
    def test_point_comes_from_selected_branch_and_preserves_unlabelled_units(self):
        result = read_point(FakeTree())
        self.assertEqual(result['coordinates']['X']['value'], -273.038)
        self.assertEqual(result['coordinates']['Y']['value'], -667.761)
        self.assertEqual(result['coordinates']['Z']['value'], 143.604)
        self.assertIsNone(result['coordinates']['X']['unit_displayed'])
        self.assertEqual(result['parent_path'], ['Topo Fwd | 20.000'])

    def test_coordinate_selection_resolves_its_own_point(self):
        tree = FakeTree()
        tree.selection = 7
        self.assertEqual(read_point(tree)['coordinates']['X']['value'], 99)

    def test_selected_image_is_not_replaced_with_arbitrary_point(self):
        tree = FakeTree()
        tree.selection = 1
        with self.assertRaisesRegex(ValueError, 'not a Point'):
            read_point(tree)

    def test_changed_selection_is_rejected(self):
        tree = FakeTree()
        with patch.object(tree, 'selected', side_effect=[2, 6]):
            with self.assertRaisesRegex(ValueError, 'changed'):
                read_point(tree)

    def test_changed_coordinate_is_rejected(self):
        tree = FakeTree()
        original = tree.text
        reads = []
        def read(item):
            if item == 3:
                reads.append(item)
                return 'X | 12' if len(reads) > 1 else original(item)
            return original(item)
        tree.text = read
        with self.assertRaisesRegex(ValueError, 'coordinates changed'):
            read_point(tree)

    def test_duplicate_missing_or_nonfinite_coordinates_are_rejected(self):
        for labels in (['X | 1', 'X | 2', 'Y | 3'], ['X | 1'], ['X | 1e999', 'Y | 1']):
            with self.assertRaises(ValueError):
                parse_coordinates(labels)
        self.assertEqual(parse_coordinates(['X: 1e-3 nm', 'Y: -2 nm'])['X']['unit_displayed'], 'nm')

    def test_headers_preserve_scan_select_and_raw_as_separate_sources(self):
        capture = {'trigger': {'kind': 'dde', 'item': 'SpectSave', 'utc': 't'},
                   'read_start': {'utc': 't', 'monotonic_ns': 0},
                   'read_end': {'utc': 't', 'monotonic_ns': 1}, 'trigger_to_read_ms': 0,
                   'gui': {'parameters': {'Scan': {'Angle': 30, 'x-Center': -267.1,
                           'y-Center': -658.62, 'Range': 20, 'Pixel': 256},
                           'Select': read_point(FakeTree())}}}
        raw = b';x/y-Pos: 1/2\r\ntime\tdz\tBias\r\n1\t0\t-1\r\n2\t0\t1\r\n'
        fields = summaries(raw)
        fields.update(instrument_fields(capture))
        self.assertEqual(fields['GUI.Scan.Angle_deg'], 30)
        self.assertEqual(fields['GUI.Scan.CenterX_nm'], -267.1)
        self.assertEqual(fields['GUI.Select.XDisplayed'], -273.038)
        self.assertEqual(fields['Acquisition.Position_xy'], '1/2')
        self.assertFalse(fields['Position.TransformApplied'])
        self.assertEqual(original_bytes(enriched_bytes(raw, fields)), raw)
        capture['gui']['parameters']['Select'] = {'_error': 'No selection'}
        missing = instrument_fields(capture)
        self.assertIsNone(missing['GUI.Select.XDisplayed'])
        self.assertIn('No selection', missing['GUI.Select.Status'])

    def test_optional_snapshot_errors_preserve_other_parameters(self):
        from sxm_anfatec.bridge import AnfatecSXMBridge, SXMBridgeError
        bridge = AnfatecSXMBridge(strict=False)
        with patch.object(bridge, '_read_all', return_value={'Scan': {'Angle': 15}}), patch.object(bridge, 'selected_point', side_effect=SXMBridgeError('missing Select')):
            self.assertNotIn('Select', bridge.snapshot()['parameters'])
            snapshot = bridge.snapshot(include_selection=True)
            self.assertEqual(snapshot['parameters']['Scan']['Angle'], 15)
            self.assertIn('missing Select', snapshot['parameters']['Select']['_error'])

    def test_real_cross_process_tree_read_does_not_expand_or_select(self):
        from sxm_anfatec.bridge import _msg
        import commctrl
        parent, child = multiprocessing.Pipe()
        process = multiprocessing.get_context('spawn').Process(target=native_tree_process, args=(child,))
        process.start()
        child.close()
        try:
            self.assertTrue(parent.poll(10), 'Native stand-in did not start')
            root, tree, point = parent.recv()
            before = _msg(tree, 0x1127, point, commctrl.TVIS_EXPANDED | commctrl.TVIS_SELECTED)  # TVM_GETITEMSTATE
            result = read_tree(tree, _msg)
            after = _msg(tree, 0x1127, point, commctrl.TVIS_EXPANDED | commctrl.TVIS_SELECTED)
            self.assertEqual(before, after)
            self.assertEqual(_msg(tree, commctrl.TVM_GETNEXTITEM, commctrl.TVGN_CARET), point)
            self.assertEqual(result['coordinates']['Y']['value'], -667.761)
            self.assertEqual(result['parent_path'], ['Topo Fwd | 20.000'])
        finally:
            parent.send('stop')
            process.join(10)
            if process.is_alive():
                process.terminate()
                process.join(5)
            parent.close()
        self.assertEqual(process.exitcode, 0)


if __name__ == '__main__':
    unittest.main()
