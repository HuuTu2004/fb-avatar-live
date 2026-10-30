import unittest

from fb_avatar_live.video import rotate_cycle, scene_frame_map, smooth_bboxes


class VideoTests(unittest.TestCase):
    def test_rotate_cycle_keeps_latent_and_output_frame_in_phase(self):
        self.assertEqual(rotate_cycle([0, 1, 2, 3], 2), [2, 3, 0, 1])
        self.assertEqual(rotate_cycle([0, 1, 2, 3], 6), [2, 3, 0, 1])

    def test_scene_frame_map_reuses_unique_scenes_in_timeline_order(self):
        self.assertEqual(scene_frame_map([2, 0, 2, 1], 3, 3), [
            6, 7, 8, 0, 1, 2, 6, 7, 8, 3, 4, 5,
        ])
        with self.assertRaises(ValueError):
            scene_frame_map([3], 3, 3)

    def test_smooth_bboxes_removes_single_frame_detector_jump(self):
        stable = (10, 10, 20, 20)
        coords = [stable, stable, stable, (10, 22, 20, 20), stable, stable, stable]
        self.assertEqual(smooth_bboxes(coords, 3)[3], stable)

    def test_smooth_bboxes_preserves_missing_face_marker(self):
        self.assertEqual(smooth_bboxes([(0, 0, 0, 0)], 7), [(0, 0, 0, 0)])


if __name__ == "__main__":
    unittest.main()
