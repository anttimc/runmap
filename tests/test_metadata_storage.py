"""Packed node/segment ownership, public identity, and sparse overlays."""

from copy import deepcopy
import hashlib
import json
from tempfile import TemporaryDirectory
import unittest

from test_integer_graph import fixture
from runmap.paths.exploration import Snap
from runmap.paths.graph import write_graph_outputs
from runmap.paths.measurements import graph_digest
from runmap.routing.metadata_strings import pack_string_rows, string_row
from runmap.routing.node_storage import MetadataLookup, NodeRecords
from runmap.routing.profiles import exploration_profile
from runmap.routing_reference.run_planner import endpoint_overlay
from runmap.routing.graph import apply_profile, build_graph_index
from runmap.routing.segment_storage import SegmentRecords
from runmap.routing.serialization import prepared_json


class MetadataStorageTests(unittest.TestCase):
    def test_optional_fields_preserve_absence_independently(self) -> None:
        graph = fixture()
        template = graph['segments'][0]
        template.pop('forward', None)
        template.pop('backward', None)
        template.pop('ascent_m', None)
        template.pop('descent_m', None)
        for number in range(64):
            item = {**template, 'id': f'optional-{number}'}
            if number & 1:
                item['ascent_m'] = 0
            if number & 2:
                item['descent_m'] = 0.25
            if number & 4:
                item['terrain_valid_fraction'] = 0
            if number & 8:
                item['terrain_sample_count'] = 0
            if number & 16:
                item['forward'] = False
            if number & 32:
                item['backward'] = True
                item['terrain_totals_complete'] = True
            graph['segments'].append(item)
        original = deepcopy(graph)
        build_graph_index(graph)
        self.assertEqual(graph['segments'], original['segments'])
        for packed, source in zip(graph['segments'], original['segments']):
            self.assertEqual(set(packed), set(source))
            for key in ('length_m', 'ascent_m', 'descent_m', 'terrain_valid_fraction', 'terrain_sample_count'):
                if key in source:
                    self.assertIs(type(packed[key]), type(source[key]))

    def test_canonical_json_and_optional_numeric_spelling(self) -> None:
        graph = fixture()
        node = graph['nodes'][0]
        node.update(longitude=24, latitude=60.0, barrier='gate', access='', foot=' YES ',
                    entrance='main', crossing='uncontrolled', highway='crossing')
        segment = graph['segments'][0]
        segment.update(osm_way_id='', ascent_m=0, descent_m=-0.0, terrain_valid_fraction=1,
                       terrain_sample_count=2**53+1, terrain_totals_complete=False)
        # Cross the checksum batching boundary for nodes and segments as well as arcs.
        for number in range(1025):
            graph['nodes'].append({'id': f'isolated-{number}', 'longitude': 24.0, 'latitude': 60})
            graph['segments'].append({**segment, 'id': f'parallel-{number}',
                                      'tags': {'future:tag': ' Unnormalized ', 'name': 'Åäö'}})
        original = deepcopy(graph)
        expected = json.dumps(original, sort_keys=True, separators=(',', ':'), allow_nan=False)
        index = build_graph_index(graph)
        self.assertIsInstance(graph['nodes'], NodeRecords)
        self.assertIsInstance(graph['segments'], SegmentRecords)
        self.assertIsInstance(index.nodes, MetadataLookup)
        self.assertIsInstance(index.segments, MetadataLookup)
        self.assertEqual(json.dumps(graph, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False, default=prepared_json), expected)
        self.assertEqual(graph_digest(graph), hashlib.sha256(expected.encode()).hexdigest())
        self.assertEqual(graph['nodes'][::-1], original['nodes'][::-1])
        self.assertEqual(graph['segments'][::-1], original['segments'][::-1])
        self.assertEqual(graph['segments'][0]['terrain_sample_count'], 2**53+1)
        self.assertNotIn('osm_way_id', graph['segments'][1])
        with TemporaryDirectory() as directory:
            export = deepcopy(graph)
            export.setdefault('summary', {})
            for item in export['segments']:
                item.setdefault('osm_way_id', '')
            published = write_graph_outputs(export, directory)
            exported = json.loads(published['graph'].read_text())
            self.assertEqual(exported['nodes'], original['nodes'])
            self.assertEqual(exported['segments'], export['segments'])

    def test_source_and_returned_records_are_detached(self) -> None:
        graph = fixture()
        source_node, source_segment = graph['nodes'][0], graph['segments'][0]
        expected_node, expected_segment = deepcopy(source_node), deepcopy(source_segment)
        index = build_graph_index(graph)
        source_node['longitude'] = 0
        source_segment['length_m'] = 0
        source_segment['tags']['foot'] = 'no'
        returned_node = index.nodes[expected_node['id']]
        returned_segment = index.segments[expected_segment['id']]
        returned_node['foot'] = 'no'
        returned_segment['tags']['foot'] = 'private'
        returned_segment['geometry'][0][0] = 0
        self.assertEqual(index.nodes[expected_node['id']], expected_node)
        self.assertEqual(index.segments[expected_segment['id']], expected_segment)
        copy = deepcopy(graph)
        copy['nodes'][0]['foot'] = 'no'
        copy['segments'][0]['tags']['foot'] = 'no'
        self.assertEqual(index.nodes[expected_node['id']], expected_node)
        self.assertEqual(index.segments[expected_segment['id']], expected_segment)
        for records in (graph['nodes'], graph['segments']):
            self.assertIsInstance(deepcopy(records), list)
            for number in (len(records), -len(records)-1):
                with self.assertRaises(IndexError):
                    _ = records[number]

    def test_buffers_and_base_views_are_shared_across_policies_and_overlays(self) -> None:
        graph = fixture()
        base = build_graph_index(graph)
        policy = apply_profile(base, exploration_profile(avoid_stairs=True))
        home, turnaround = (Snap('sa', 0, 0, 24.8, 60.2, 0),
                            Snap('ad', 0, 0, 24.801, 60.2, 0))
        overlay = endpoint_overlay(base, home, turnaround).graph
        self.assertIs(base.nodes, policy.nodes)
        self.assertIs(base.segments, policy.segments)
        self.assertIs(overlay.nodes.maps[-1], base.nodes)
        self.assertIs(overlay.segments.maps[-1], base.segments)
        nodes, segments = graph['nodes'].data, graph['segments'].data
        self.assertIs(nodes.ids, base.integer.node_ids)
        self.assertIs(segments.ids, base.integer.segment_ids)
        self.assertIs(segments.node_ids, base.integer.node_ids)
        self.assertIs(segments.geometry, base.projected.wgs84)
        buffers = (nodes.coordinates, nodes.integers, segments.sources, segments.targets,
                   segments.metrics, segments.integers, segments.counts, segments.present,
                   segments.booleans, segments.tags.offsets, segments.tags.keys, segments.tags.values)
        for buffer in buffers:
            self.assertTrue(buffer.readonly)
            if buffer:
                with self.assertRaises(TypeError):
                    buffer[0] = 0

    def test_empty_graph_and_pooled_string_row_semantics(self) -> None:
        empty = build_graph_index({'nodes': [], 'segments': [], 'arcs': []})
        self.assertEqual(list(empty.nodes), [])
        self.assertEqual(list(empty.segments), [])
        rows = [{'one': 'same', 'two': ''}, {}, {'one': 'same'}]
        packed = pack_string_rows(rows)
        self.assertEqual([string_row(packed, i) for i in range(3)], rows)
        self.assertEqual(packed.strings.count('same'), 1)
        rows[0]['one'] = 'changed'
        self.assertEqual(string_row(packed, 0)['one'], 'same')
        for number in (-1, 3):
            with self.assertRaises(IndexError):
                string_row(packed, number)
        with self.assertRaises(ValueError):
            pack_string_rows([{'invalid': None}])

    def test_unsupported_metadata_is_rejected_instead_of_discarded(self) -> None:
        graph = fixture()
        graph['nodes'][0]['future'] = 'unknown'
        with self.assertRaisesRegex(ValueError, 'Unsupported node'):
            build_graph_index(graph)
        graph = fixture()
        graph['segments'][0]['future'] = 'unknown'
        with self.assertRaisesRegex(ValueError, 'Unsupported segment'):
            build_graph_index(graph)
        graph = fixture()
        graph['segments'][0]['forward'] = 1
        with self.assertRaisesRegex(ValueError, 'must be booleans'):
            build_graph_index(graph)
